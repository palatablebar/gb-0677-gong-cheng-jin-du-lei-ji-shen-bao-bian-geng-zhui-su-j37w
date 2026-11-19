from flask import Blueprint,request,jsonify
from app.errors import DomainError
from app.pipeline import contract_preview
from app import store

bp=Blueprint("contracts",__name__)

def _body():
    payload=request.get_json(silent=True)
    if not isinstance(payload,dict): raise DomainError("BAD_BODY","请求需要对象")
    return payload

@bp.route("/api/contracts",methods=["GET","POST"])
def preview():
    payload=_body() if request.method=="POST" else {}
    return jsonify(contract_preview(payload))

@bp.get("/api/contracts/state")
def state():
    return jsonify(store.contract_state(request.args.get("contract_key") or None))

@bp.post("/api/runs/<run_id>/contract/sign")
def sign(run_id):
    payload=_body();version=payload.get("version")
    if version is not None and not isinstance(version,int): raise DomainError("BAD_VERSION","版本号需要整数")
    return jsonify(store.sign_contract(run_id,version,payload.get("contract_key")))

@bp.post("/api/contracts/<path:contract_key>/restore")
def restore(contract_key):
    payload=_body();revision=payload.get("revision")
    if revision is not None and not isinstance(revision,int): raise DomainError("BAD_REVISION","修订号需要整数")
    return jsonify(store.restore_contract(contract_key,revision))
