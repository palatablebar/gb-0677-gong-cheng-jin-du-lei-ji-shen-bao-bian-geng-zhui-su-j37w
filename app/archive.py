import csv,io,json
def build_archive(run):
    stream=io.StringIO(newline="");writer=csv.writer(stream)
    writer.writerow(["run_id","input_sha256","rule_version","kind","source_lines","reason","evidence"])
    meta=run["manifest"]
    for row in run["stages"][-1]["rows"]:
        writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],"accepted",json.dumps(row.get("_sources",[])),row.get("status",""),json.dumps(row,ensure_ascii=False)])
    for row in run["stages"][-1].get("cumulative_quarantine",[]):
        writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],"quarantine",json.dumps(row.get("_sources",[])),row.get("reason",""),json.dumps(row,ensure_ascii=False)])
    contract=run.get("contract") or {}
    for summary in contract.get("contracts",[]):
        writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],"contract_control",json.dumps(sorted({line for s in summary.get("segments",[]) for line in s.get("source_lines",[])})),summary.get("status",""),json.dumps(summary,ensure_ascii=False)])
    for segment in contract.get("segments",[]):
        writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],"contract_segment",json.dumps(segment.get("source_lines",[])),segment.get("status",""),json.dumps(segment,ensure_ascii=False)])
    for row in contract.get("quarantine",[]):
        writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],"contract_quarantine",json.dumps(row.get("_sources",[])),row.get("reason",""),json.dumps(row,ensure_ascii=False)])
    return "﻿"+stream.getvalue()
