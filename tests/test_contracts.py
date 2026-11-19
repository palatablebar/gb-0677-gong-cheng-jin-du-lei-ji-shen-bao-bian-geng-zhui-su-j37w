import json
from decimal import Decimal
import pytest
from app import store
from app.contracts import evaluate,parse_contracts
from app.errors import DomainError
from app.pipeline import calculate,load_sample,sample_parameters

PARAMS={"contracts":[{"item":"001","period":"2026-11","control_qty":"500","prior_signed_qty":"480",
                      "precision":"0.001","segments":["一工区","二工区"]}]}

def claim(id="A",segment="一工区",increment="35",reversal="none",price="120.00"):
    return {"claim_id":id,"item":"001","period":"2026-11","segment":segment,
            "increment_qty":increment,"reversal":reversal,"unit_price":price,
            "gross_amount":str(Decimal(increment)*Decimal(price)),"_line":1,"_sources":[1]}

def sample_run():
    name="contract.csv";return calculate(load_sample(name),sample_parameters(name))

def test_contract_sample_reconciles_with_reversal():
    run=sample_run();c=run["contract"]["contracts"][0]
    assert Decimal(c["remaining_before"])==20 and c["requested_qty"]=="25.000"
    assert c["reversal_qty"]=="10.000" and c["signed_qty"]=="20.000" and c["remaining_after"]=="0.000"
    assert c["balanced"] is True and c["status"]=="部分签认"

def test_unsigned_stays_in_original_segment():
    c=sample_run()["contract"]["contracts"][0]
    by={s["segment"]:s for s in c["segments"]}
    assert by["一工区"]["unsigned_qty"]=="5.000" and by["一工区"]["status"]=="部分签认"
    assert by["二工区"]["reversals"]==1 and Decimal(by["二工区"]["unsigned_qty"])==0

def test_reversal_settles_before_positive_increment():
    """冲回先签认，正增量才能用到退回的余量；否则只能签 20 而不是 30。"""
    rows=[claim("A",increment="35"),claim("B","二工区",increment="-10",reversal="approved")]
    c=evaluate(rows,PARAMS)["contracts"][0]
    assert c["signed_positive_qty"]=="30.000" and c["remaining_after"]=="0.000"

def test_naive_order_would_overrun_control():
    """若先签正增量再签冲回，净签认会超过控制余量；本实现校验并拒绝这种结果。"""
    rows=[claim("A",increment="35"),claim("B","二工区",increment="-10",reversal="approved")]
    c=evaluate(rows,PARAMS)["contracts"][0]
    assert Decimal(c["signed_qty"])<=Decimal(c["remaining_before"])
    assert c["checks"]["cap_respected"] is True

def test_never_shift_excess_between_segments():
    rows=[claim("A","一工区",increment="15"),claim("B","二工区",increment="15")]
    contract=evaluate(rows,PARAMS)
    first,second=contract["contracts"][0]["segments"]
    assert (first["signed_qty"],second["signed_qty"])==("15.000","5.000")
    assert second["unsigned_qty"]=="10.000" and second["segment"]=="二工区"

def test_unsegmented_claim_quarantined_not_pooled():
    rows=[{"claim_id":"A","item":"001","period":"2026-11","increment_qty":"5","unit_price":"120","gross_amount":"600","_line":9,"_sources":[9]}]
    contract=evaluate(rows,PARAMS)
    assert Decimal(contract["contracts"][0]["signed_qty"])==0 and contract["quarantine"][0]["reason"]=="UNSEGMENTED_CLAIM"

def test_negative_without_approval_not_signed_or_moved():
    rows=[{"claim_id":"A","item":"001","period":"2026-11","segment":"一工区","increment_qty":"-5","reversal":"none","unit_price":"120","gross_amount":"-600","_line":2,"_sources":[2]}]
    contract=evaluate(rows,PARAMS)
    assert contract["quarantine"][0]["reason"]=="REVERSAL_REQUIRED" and Decimal(contract["contracts"][0]["signed_qty"])==0

def test_lineage_preserved_per_row():
    run=sample_run()
    assert {tuple(r["_sources"]) for r in run["contract"]["rows"]}=={(2,),(3,)}
    assert run["contract"]["metrics"]["records"]==2

def test_rerun_same_basis_does_not_occupy_again(client):
    """审批依据变化后重跑不能重复占用余量：同依据同输入只产生一个现行版本。"""
    run=client.post("/api/runs",json={"sample":"contract.csv"}).get_json()
    first=client.post(f"/api/runs/{run['run_id']}/contract/sign",json={"version":1})
    again=client.post(f"/api/runs/{run['run_id']}/contract/sign",json={"version":1})
    assert first.status_code==200 and again.status_code==200
    assert again.get_json()["signed"][0]["revision"]==1
    assert len(client.get("/api/contracts/state").get_json()["contracts"][0]["revisions"])==1

def test_changed_basis_supersedes_not_adds(client):
    run=client.post("/api/runs",json={"sample":"contract.csv"}).get_json()
    client.post(f"/api/runs/{run['run_id']}/contract/sign",json={"version":1})
    raw=load_sample("contract.csv").decode();params=sample_parameters("contract.csv")
    params["contracts"][0]["control_qty"]="495"
    run2=client.post("/api/runs",json={"csv":raw,"parameters":params}).get_json()
    assert run2["basis_id"]!=run["basis_id"]
    client.post(f"/api/runs/{run2['run_id']}/contract/sign",json={"version":1})
    revisions=client.get("/api/contracts/state").get_json()["contracts"][0]["revisions"]
    assert [r["status"] for r in revisions]==["active","superseded"]
    assert revisions[0]["remaining_after"]=="0.000" and revisions[1]["signed_qty"]=="20.000"

def test_failed_switch_keeps_previous_active(client):
    run=client.post("/api/runs",json={"sample":"contract.csv"}).get_json()
    client.post(f"/api/runs/{run['run_id']}/contract/sign",json={"version":1})
    stale=client.post(f"/api/runs/{run['run_id']}/contract/sign",json={"version":99})
    assert stale.status_code==409 and stale.get_json()["code"]=="STALE_VERSION"
    active=client.get("/api/contracts/state").get_json()["contracts"][0]["active"]
    assert active["revision"]==1 and active["status"]=="active"

def test_restore_previous_revision(client):
    run=client.post("/api/runs",json={"sample":"contract.csv"}).get_json()
    client.post(f"/api/runs/{run['run_id']}/contract/sign",json={"version":1})
    raw=load_sample("contract.csv").decode();params=sample_parameters("contract.csv")
    params["contracts"][0]["control_qty"]="495"
    run2=client.post("/api/runs",json={"csv":raw,"parameters":params}).get_json()
    client.post(f"/api/runs/{run2['run_id']}/contract/sign",json={"version":1})
    restored=client.post("/api/contracts/001%7C2026-11/restore",json={})
    assert restored.status_code==200
    active=client.get("/api/contracts/state").get_json()["contracts"][0]["active"]
    assert active["revision"]==1 and active["decision"]=="已恢复"

def test_restore_without_history_404(client):
    run=client.post("/api/runs",json={"sample":"contract.csv"}).get_json()
    client.post(f"/api/runs/{run['run_id']}/contract/sign",json={"version":1})
    assert client.post("/api/contracts/001%7C2026-11/restore",json={}).status_code==404

def test_contract_sample_page_and_api(client):
    client.post("/api/runs",json={"sample":"contract.csv"})
    assert client.get("/contracts").status_code==200
    assert client.get("/api/contracts").status_code==200
    body=client.get("/api/contracts/workbench").get_json()
    assert body["contract"]["metrics"]["signed_qty"]=="20.000"

def test_contract_archive_keeps_segment_evidence(client):
    run=client.post("/api/runs",json={"sample":"contract.csv"}).get_json()
    import csv,io
    rows=list(csv.reader(io.StringIO(client.get(f"/api/runs/{run['run_id']}/archive").data.decode("utf-8-sig"))))
    kinds={r[3] for r in rows[1:]}
    assert {"contract_control","contract_segment"}<=kinds

def test_invalid_contract_definition_rejected():
    with pytest.raises(DomainError):parse_contracts({"contracts":[{"item":"001","period":"2026-11","control_qty":"-1"}]})
    with pytest.raises(DomainError):parse_contracts({"contracts":[{"item":"001","period":"2026-11","control_qty":"1"},{"item":"001","period":"2026-11","control_qty":"2"}]})

def test_missing_segment_column_still_accepted():
    raw=load_sample("clean.csv")
    assert calculate(raw,{})["contract"] is not None

def test_reverting_basis_restores_old_revision_not_new(client):
    raw=load_sample("contract.csv").decode()
    base=sample_parameters("contract.csv");changed=sample_parameters("contract.csv")
    changed["contracts"][0]["control_qty"]="495"
    a=client.post("/api/runs",json={"csv":raw,"parameters":base}).get_json()
    client.post(f"/api/runs/{a['run_id']}/contract/sign",json={"version":1})
    b=client.post("/api/runs",json={"csv":raw,"parameters":changed}).get_json()
    client.post(f"/api/runs/{b['run_id']}/contract/sign",json={"version":1})
    client.post(f"/api/runs/{a['run_id']}/contract/sign",json={"version":1})
    revisions=client.get("/api/contracts/state").get_json()["contracts"][0]["revisions"]
    active=client.get("/api/contracts/state").get_json()["contracts"][0]["active"]
    assert len(revisions)==2 and active["basis_id"]==a["basis_id"] and active["status"]=="active"

def test_only_one_active_revision_per_contract(client):
    run=client.post("/api/runs",json={"sample":"contract.csv"}).get_json()
    client.post(f"/api/runs/{run['run_id']}/contract/sign",json={"version":1})
    revisions=client.get("/api/contracts/state").get_json()["contracts"][0]["revisions"]
    assert sum(r["status"]=="active" for r in revisions)==1

def test_contract_metrics_do_not_rewrite_increments():
    """合同控制只裁签认量，不改写已还原的本期工程量。"""
    run=sample_run()
    increments={r["claim_id"]:r["increment_qty"] for r in run["stages"][3]["rows"]}
    assert increments=={"10001":"35","10002":"-10"}
    assert all(r["increment_qty"]==increments[r["claim_id"]] for r in run["contract"]["rows"])
