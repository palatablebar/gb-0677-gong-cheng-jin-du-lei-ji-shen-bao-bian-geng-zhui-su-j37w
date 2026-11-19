"""合同控制对账。

把已还原的本期工程量按清单项与申报周期归入合同控制：
- 可签余量 = 合同控制量 - 前期已签量；
- 已批冲回先足额签认，冲回量回到余量池；
- 正增量按分段申报顺序签认，签认量止于剩余余量；
- 未签认量留在原分段，绝不把超额挪到另一个分段以凑满申报。

处理顺序很重要：冲回先结算，正增量后结算。否则同一控制池中
“先到先得”的顺序会让冲回失效，导致净签认量超过控制余量。
"""
import hashlib,json
from decimal import Decimal,ROUND_HALF_UP
from app.config import RULE_VERSION
from app.errors import DomainError
from app.modules.m2_billitems.rules import ITEMS
from app.reasons import sources
from app.values import number,money

ALIASES={"control_qty":("control_qty","contract_qty"),"prior_signed_qty":("prior_signed_qty","prior_qty")}
UNSEGMENTED="未分段"

def _value(raw,field):
    for name in ALIASES.get(field,(field,)):
        if name in raw and raw[name] not in (None,""): return raw[name]
    return None

def _quantize(value,precision): return value.quantize(precision,rounding=ROUND_HALF_UP)

def _ratio(params,name,default):
    value=number((params or {}).get(name,default))
    if not Decimal(0)<=value<=1: raise DomainError("BAD_DEDUCTION","扣回比例无效")
    return value

def parse_contracts(params):
    """把 parameters.contracts 解析为合同控制规则。"""
    raw=(params or {}).get("contracts")
    if raw in (None,"",[]): return []
    if not isinstance(raw,list): raise DomainError("BAD_CONTRACT","合同控制需要列表")
    result=[];seen=set()
    for entry in raw:
        if not isinstance(entry,dict): raise DomainError("BAD_CONTRACT","合同控制项需要对象")
        item_raw=str(entry.get("item","")).strip();period=str(entry.get("period","")).strip()
        item=ITEMS.get(item_raw,item_raw)
        if not item or not period: raise DomainError("BAD_CONTRACT","合同控制需要清单项和申报周期")
        key=item+"|"+period
        if key in seen: raise DomainError("DUPLICATE_CONTRACT","同一清单周期只能有一份合同控制")
        seen.add(key)
        control=_value(entry,"control_qty");prior=_value(entry,"prior_signed_qty")
        if control is None: raise DomainError("BAD_CONTRACT","合同控制量不能为空")
        if prior is None: prior="0"
        control=number(control);prior=number(prior)
        if control<0 or prior<0: raise DomainError("BAD_CONTRACT","合同控制量与前期已签量不能为负")
        try: precision=Decimal(str(entry.get("precision","0.001")))
        except Exception: raise DomainError("BAD_CONTRACT","精度格式错误")
        if not precision.is_finite() or precision<=0: raise DomainError("BAD_CONTRACT","精度必须为正")
        segments=entry.get("segments") or []
        if not isinstance(segments,list) or any(not str(s).strip() for s in segments): raise DomainError("BAD_CONTRACT","分段需要名称列表")
        order=[]
        for segment in segments:
            name=str(segment).strip()
            if name==UNSEGMENTED: raise DomainError("BAD_CONTRACT","分段名不能使用保留值")
            if name not in order: order.append(name)
        result.append({"key":key,"item":item,"period":period,"control_qty":control,"prior_signed_qty":prior,
                       "precision":precision,"segment_order":order})
    return result

def basis_id(raw,params,contract_key):
    """审批依据+输入+规则版本的唯一标识；依据不变重跑得到同一标识。"""
    payload=json.dumps({"parameters":params or {},"contract":contract_key,"rule":RULE_VERSION},sort_keys=True,ensure_ascii=False).encode()
    return hashlib.sha256(raw+payload).hexdigest()[:20]

def _requested(row,precision):
    if row.get("increment_qty") in (None,""):
        raise DomainError("EMPTY_VALUE","本期工程量不能为空")
    return _quantize(number(row["increment_qty"]),precision)

def _split_row(row,item):
    """按数量拆分本行金额。行金额以申报总额为准，尾差归未签认，保证按分守恒。"""
    total=Decimal(str(row.get("gross_amount","0")))
    if item["requested"]==0: return Decimal(0),total
    if item["unsigned"]==0: return total,Decimal(0)
    signed=(total*item["signed"]/item["requested"]).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)
    return signed,total-signed

def evaluate(rows,params=None):
    params=params or {}
    if not isinstance(params,dict): raise DomainError("BAD_PARAMETERS","参数需要对象")
    if "parameters" in params and "contracts" not in params: params=params["parameters"]
    contracts=parse_contracts(params)
    if not contracts: return {"contracts":[],"segments":[],"rows":[],"quarantine":[],
                              "uncontrolled":[],"metrics":{"contracts":0,"segments":0,"records":0,
                              "signed_qty":"0","unsigned_qty":"0","uncontrolled":0,"unsegmentable":0,
                              "balanced":True,"signable":False}}
    retention=_ratio(params,"retention_rate","0.05");advance=_ratio(params,"advance_rate","0.10")
    if retention+advance>1: raise DomainError("BAD_DEDUCTION","扣回比例无效")
    net_factor=Decimal(1)-retention-advance
    rules={rule["key"]:rule for rule in contracts}
    grouped={key:{} for key in rules};uncontrolled=[];quarantine=[]
    for row in rows:
        key=str(row.get("item"))+"|"+str(row.get("period"))
        if key not in rules:
            uncontrolled.append({**row,"control_status":"未纳入合同控制"});continue
        segment=str(row.get("segment") or "").strip()
        if segment==UNSEGMENTED:
            quarantine.append({**row,"reason":"UNSEGMENTED_CLAIM","detail":"分段值为占位符“未分段”，不能落责任量"})
            continue
        if not segment:
            if rules[key]["segment_order"]:
                quarantine.append({**row,"reason":"UNSEGMENTED_CLAIM","detail":"纳入合同控制的申报缺少分段，不能落责任量"})
                continue
            segment=UNSEGMENTED
        grouped[key].setdefault(segment,[]).append(row)

    decisions=[];segment_rows=[];summaries=[]
    for rule in contracts:
        key=rule["key"];precision=rule["precision"];present=grouped[key]
        order=list(rule["segment_order"])+[name for name in sorted(present) if name not in rule["segment_order"]]+[UNSEGMENTED]
        order=[name for i,name in enumerate(order) if name not in order[:i] and (name in present)]
        remaining_before=rule["control_qty"]-rule["prior_signed_qty"]
        available=remaining_before
        items=[]
        for segment in order:
            for row in present.get(segment,[]):
                try: requested=_requested(row,precision)
                except DomainError as error:
                    quarantine.append({**row,"reason":error.code,"detail":error.message});continue
                if requested<0 and row.get("reversal")!="approved":
                    quarantine.append({**row,"reason":"REVERSAL_REQUIRED","detail":"负进度缺少已批冲回凭证，不能签认也不能挪到其他分段"})
                    continue
                items.append({"row":row,"segment":segment,"requested":requested,
                              "reversal":requested<0 and row.get("reversal")=="approved"})
        # 1) 已批冲回先足额签认：负增量退回余量池，不能被后续正增量挤掉。
        for item in items:
            if not item["reversal"]: continue
            item["signed"]=item["requested"];item["unsigned"]=Decimal(0)
            available+=-item["requested"]
        # 2) 正增量按分段申报顺序签认，签认量止于当前余量；超额留在原分段。
        for item in items:
            if item["reversal"]: continue
            requested=item["requested"]
            if requested<0:
                # 负增量只有 overlap 未获批准才会走到这里；不签认也不改写申报量。
                item["signed"]=Decimal(0);item["unsigned"]=requested
                continue
            room=available if available>0 else Decimal(0)
            signed=requested if requested<=room else room
            item["signed"]=_quantize(signed,precision)
            item["unsigned"]=_quantize(requested-item["signed"],precision)
            available-=item["signed"]
        segment_stats={}
        for item in items:
            row=item["row"];signed_gross,unsigned_gross=_split_row(row,item)
            detail={**row,"segment":item["segment"],
                    "requested_qty":str(item["requested"]),
                    "signed_qty":str(_quantize(item["signed"],precision)),"unsigned_qty":str(_quantize(item["unsigned"],precision)),
                    "signed_gross":money(signed_gross),"unsigned_gross":money(unsigned_gross),
                    "signed_net":money(signed_gross*net_factor),"unsigned_net":money(unsigned_gross*net_factor),
                    "_sources":list(sources(row)),
                    "sign_status":"已批冲回" if item["reversal"] else
                             ("签足" if item["unsigned"]==0 else ("部分签认" if item["signed"]>0 else "未签认"))}
            decisions.append(detail)
            stats=segment_stats.setdefault(item["segment"],{"requested":Decimal(0),"signed":Decimal(0),"unsigned":Decimal(0),
                                                              "signed_gross":Decimal(0),"unsigned_gross":Decimal(0),
                                                              "sources":set(),"rows":0,"reversals":0})
            stats["requested"]+=item["requested"];stats["signed"]+=item["signed"];stats["unsigned"]+=item["unsigned"]
            stats["signed_gross"]+=signed_gross;stats["unsigned_gross"]+=unsigned_gross
            stats["sources"].update(sources(row));stats["rows"]+=1;stats["reversals"]+=1 if item["reversal"] else 0
        public=[]
        for name in order:
            if name not in present or name not in segment_stats: continue
            stats=segment_stats[name]
            net=stats["signed"]
            status=("已批冲回" if stats["reversals"]==stats["rows"] else
                    "签足" if stats["unsigned"]==0 and net!=0 else
                    "部分签认" if net>0 else
                    "未签认" if stats["unsigned"]!=0 else "无本期量")
            entry={"segment":name,"requested_qty":str(_quantize(stats["requested"],precision)),
                   "signed_qty":str(_quantize(net,precision)),"unsigned_qty":str(_quantize(stats["unsigned"],precision)),
                   "signed_gross":money(stats["signed_gross"]),
                   "unsigned_gross":money(stats["unsigned_gross"]),"rows":stats["rows"],
                   "reversals":stats["reversals"],"source_lines":sorted(stats["sources"]),
                   "declared":name in rule["segment_order"],"status":status}
            public.append(entry);segment_rows.append({"contract_key":key,**entry})
        signed_total=sum((i["signed"] for i in items),Decimal(0))
        unsigned_total=sum((i["unsigned"] for i in items),Decimal(0))
        requested_total=sum((i["requested"] for i in items),Decimal(0))
        reversal_total=sum((-i["signed"] for i in items if i["reversal"]),Decimal(0))
        signed_positive=sum((i["signed"] for i in items if not i["reversal"] and i["signed"]>0),Decimal(0))
        overdrawn=rule["prior_signed_qty"]>rule["control_qty"]
        remaining_after=available
        checks={
            "conservation":signed_total+unsigned_total==requested_total,
            "segment_conservation":(sum((s["signed"] for s in segment_stats.values()),Decimal(0))==signed_total
                                    and sum((s["unsigned"] for s in segment_stats.values()),Decimal(0))==unsigned_total),
            "remaining":remaining_after==remaining_before+reversal_total-signed_positive,
            "no_negative_remaining":remaining_after>=0,
            "cap_respected":signed_positive<=remaining_before+reversal_total,
            "reversal_full":all(i["signed"]==i["requested"] for i in items if i["reversal"]),
            "never_over_request":all(i["signed"]<=i["requested"] for i in items),
            "no_segment_shift":all(i["unsigned"]>=0 for i in items),
        }
        balanced=all(checks.values())
        if overdrawn: status="控制量已超"
        elif unsigned_total>0: status="部分签认"
        elif signed_total!=0: status="可签认"
        else: status="无可签认量"
        summaries.append({"contract_key":key,"item":rule["item"],"period":rule["period"],
                          "control_qty":str(_quantize(rule["control_qty"],precision)),
                          "prior_signed_qty":str(_quantize(rule["prior_signed_qty"],precision)),
                          "remaining_before":str(_quantize(remaining_before,precision)),"requested_qty":str(_quantize(requested_total,precision)),
                          "signed_positive_qty":str(_quantize(signed_positive,precision)),"reversal_qty":str(_quantize(reversal_total,precision)),
                          "signed_qty":str(_quantize(signed_total,precision)),"unsigned_qty":str(_quantize(unsigned_total,precision)),
                          "remaining_after":str(_quantize(remaining_after,precision)),"segments":public,
                          "checks":checks,"balanced":balanced,"overdrawn":overdrawn,"status":status,
                          "signable":balanced and not overdrawn})
    metrics={"contracts":len(summaries),"segments":len(segment_rows),"records":len(decisions),
             "signed_qty":str(sum((Decimal(s["signed_qty"]) for s in summaries),Decimal(0))),
             "unsigned_qty":str(sum((Decimal(s["unsigned_qty"]) for s in summaries),Decimal(0))),
             "uncontrolled":len(uncontrolled),"unsegmentable":len(quarantine),
             "balanced":all(s["balanced"] for s in summaries),
             "signable":bool(summaries) and all(s["signable"] for s in summaries)}
    return {"contracts":summaries,"segments":segment_rows,"rows":decisions,"quarantine":quarantine,
            "uncontrolled":uncontrolled,"metrics":metrics}
