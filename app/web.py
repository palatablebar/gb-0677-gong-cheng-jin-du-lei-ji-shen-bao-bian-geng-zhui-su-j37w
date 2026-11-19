from flask import render_template,jsonify
from app.config import MODULES,TITLE,SHORT
from app.pipeline import execute,load_sample
from app import store
COLUMNS=[
    ['claim_id','item','period','cumulative_qty','previous_qty','change_id','reported'],
    ['claim_id','item','unit','cumulative_qty','previous_qty'],
    ['claim_id','period','approved_change','unit_price'],
    ['claim_id','item','period','cumulative_qty','approved_previous','increment_qty','gross_amount','segment'],
    ['claim_id','item','gross_amount','retention','advance_recovery','expected'],
    ['claim_id','item','expected','reported','difference','status','segment'],
    ['claim_id','period','expected','status'],
]
LABELS={'claim_id': '申报编号', 'item': '清单项', 'period': '申报周期', 'cumulative_qty': '本次累计量', 'previous_qty': '前审累计量', 'change_id': '变更单', 'reported': '申报净额', 'unit': '单位', 'approved_change': '已批变更', 'unit_price': '单价 / 元', 'approved_previous': '修订前审量', 'increment_qty': '本期工程量', 'gross_amount': '本期总额', 'retention': '质保金', 'advance_recovery': '预付款扣回', 'expected': '实算净额', 'difference': '净额差异', 'status': '结论', 'gross_total': '本期总额合计', 'net_total': '实算净额合计', 'segment': '分段', 'requested_qty': '申报量', 'signed_qty': '签认量', 'unsigned_qty': '未签认量', 'signed_gross': '签认总额', 'unsigned_gross': '未签认总额', 'remaining_before': '期初余量', 'remaining_after': '期末余量', 'control_qty': '合同控制量', 'prior_signed_qty': '前期已签量', 'source_lines': '来源行', 'reversals': '冲回行', 'rows': '记录数', 'declared': '声明分段'}
LABELS.update({'accepted_inputs': '采纳来源行', 'quarantine_inputs': '隔离来源行', 'output_records': '结果记录', 'input': '输入行', 'excluded': '排除行', 'balanced': '行数平衡', 'expected_total': '预期合计', 'reported_total': '申报合计', 'differences': '差异项', 'blank': '空行', 'repeated_headers': '分段表头', 'encoding': '编码', 'duplicate': '重复行', 'quarantine': '隔离行', 'accepted': '采纳行', 'sha256': '校验摘要', 'ready': '可归档', 'uncontrolled': '未纳入控制', 'unsegmentable': '缺分段'})

def current_run():
    return store.latest() or execute(load_sample("dirty.csv"))

def sample_name(run):
    if (run.get("contract") or {}).get("contracts"): return "contract.csv"
    return "dirty.csv"

def shared(run,page_title=None,page=None):
    return dict(title=TITLE,short=SHORT,modules=MODULES,run=run,history=store.history(),labels=LABELS,
                sample_name=sample_name(run),page=page,page_title=page_title,
                contract_keys=[c["contract_key"] for c in (run.get("contract") or {}).get("contracts",[])])

def register_pages(app):
    def page(index):
        run=current_run();stage=run["stages"][index];rows=stage["rows"]
        return render_template(f"m{index+1}.html",index=index,stage=stage,rows=rows,columns=COLUMNS[index][:12],**shared(run))
    def contracts_page():
        run=current_run();contract=run.get("contract") or {}
        state=store.contract_state()
        active={c["contract_key"]:c.get("active") for c in state.get("contracts",[])}
        return render_template("contracts.html",index=5,contract=contract,state=state,active=active,**shared(run,page_title="合同签认",page="contracts"))
    app.add_url_rule("/",view_func=lambda:page(4))
    for index,(slug,name) in enumerate(MODULES):
        app.add_url_rule("/"+slug,endpoint="page_"+slug,view_func=lambda index=index:page(index))
    app.add_url_rule("/contracts",endpoint="page_contracts",view_func=contracts_page)

    @app.get("/api/contracts/workbench")
    def workbench():
        run=current_run();contract=run.get("contract") or {}
        state=store.contract_state()
        return jsonify({"run_id":run["run_id"],"contract":contract,"state":state})
