from app.values import number
from app.reasons import reject
from app.errors import DomainError
def evaluate(rows,context):
    good=[];bad=[];overrides=context.get("parameters",{}).get("previous_revisions",{});seen=set()
    for row in sorted(rows,key=lambda x:(x["item"],x["period"],str(x.get("segment","")),x["claim_id"])):
        # 同一清单周期可分段申报；责任量按分段各自独立，不许同段重复。
        key=(row["item"],row["period"],str(row.get("segment","")))
        if key in seen:bad.append(reject(row,"DUPLICATE_PERIOD","同清单同周期同分段重复申报"));continue
        seen.add(key)
        prev=number(overrides.get(row["claim_id"],row["previous_qty"]));current=number(row["cumulative_qty"])
        if prev<0:raise DomainError("BAD_PREVIOUS","前审累计量不能为负")
        incremental=current-prev
        if incremental<0 and row.get("reversal")!="approved":bad.append(reject(row,"REVERSAL_REQUIRED","负进度缺少已批冲回凭证"));continue
        good.append({**row,"approved_previous":str(prev),"increment_qty":str(incremental),"gross_amount":str(incremental*number(row["unit_price"]))})
    return {"rows":good,"quarantine":bad,"metrics":{"accepted":len(good),"negative_claims":sum(number(r["increment_qty"])<0 for r in good)}}
