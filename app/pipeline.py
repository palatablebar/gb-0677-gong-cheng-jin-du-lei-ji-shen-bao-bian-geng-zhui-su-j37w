import copy,time
from app.config import ROOT,MODULES,SAMPLE_PARAMETERS
from app.errors import DomainError
from app.values import scalar
from app.manifest import digest,manifest
from app import contracts,store
from app.modules.m1_claims.rules import evaluate as stage1
from app.modules.m2_billitems.rules import evaluate as stage2
from app.modules.m3_changes.rules import evaluate as stage3
from app.modules.m4_increment.rules import evaluate as stage4
from app.modules.m5_deductions.rules import evaluate as stage5
from app.modules.m6_valuation.rules import evaluate as stage6
from app.modules.m7_certificates.rules import evaluate as stage7
STAGES=[stage1,stage2,stage3,stage4,stage5,stage6,stage7]
SAMPLES={"dirty.csv","clean.csv","legacy.csv","contract.csv"}
def load_sample(name):
    if name not in SAMPLES: raise DomainError("BAD_SAMPLE","样例不存在",404)
    return (ROOT/"sample_data"/name).read_bytes()
def sample_parameters(name,params=None):
    """仓内样例自带审批依据；请求显式给出的键覆盖样例缺省，其余沿用。"""
    base=copy.deepcopy(SAMPLE_PARAMETERS.get(name,{}))
    if params:
        base.update(params)
    return base
def run_contracts(rows,params):
    """在进度对账之后、归档之前做合同控制对账；只读上游，不改写已还原工程量。"""
    return contracts.evaluate(rows,params)
def calculate(raw,params=None,stop=6):
    params=params or {};started=time.monotonic();context={"parameters":params,"raw":raw,"quarantine":[]};stages=[];rows=[]
    for index,stage in enumerate(STAGES[:stop+1]):
        result=stage(raw if index==0 else rows,context)
        result=scalar(result);rows=result["rows"]
        context["quarantine"].extend(result.get("quarantine",[]))
        if index==0: context["intake"]=result["metrics"]
        result["cumulative_quarantine"]=list(context["quarantine"])
        context[MODULES[index][0]]=result
        stages.append(result)
    contract=run_contracts(rows,params) if stop>=5 else None
    return {"run_id":digest(raw,params)[:20],"stages":stages,"contract":contract,
            "basis_id":contracts.basis_id(raw,params,"|".join(sorted(c["contract_key"] for c in (contract or {}).get("contracts",[])))),
            "manifest":manifest(raw,params,started),"parameters":params}
def execute(raw,params=None):
    result=calculate(raw,params);result.setdefault("contract",None)
    return store.save(result["run_id"],result,
                      basis=result["basis_id"],
                      contract_signed=(result.get("contract") or {}).get("metrics",{}).get("signed_qty"),
                      contract_unsigned=(result.get("contract") or {}).get("metrics",{}).get("unsigned_qty"))
def stage_preview(index,payload):
    if "rows" in payload and index>0:
        if not isinstance(payload["rows"],list) or any(not isinstance(row,dict) for row in payload["rows"]): raise DomainError("BAD_ROWS","记录需要对象列表")
        if not isinstance(payload.get("context",{}),dict): raise DomainError("BAD_CONTEXT","上下文需要对象")
        return scalar(STAGES[index](payload["rows"],payload.get("context",{"parameters":{},"quarantine":[]})))
    name=payload.get("sample","dirty.csv")
    raw=payload.get("csv")
    raw=raw.encode() if isinstance(raw,str) else load_sample(name)
    params=sample_parameters(name,payload.get("parameters"))
    return calculate(raw,params,index)["stages"][-1]
def contract_preview(payload):
    if not isinstance(payload,dict): raise DomainError("BAD_BODY","请求需要对象")
    if "rows" in payload:
        return scalar(run_contracts(payload["rows"],payload.get("parameters",{})))
    name=payload.get("sample","contract.csv");raw=payload.get("csv")
    raw=raw.encode() if isinstance(raw,str) else load_sample(name)
    params=sample_parameters(name,payload.get("parameters"))
    return calculate(raw,params,5)["contract"]
