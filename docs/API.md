# 接口

模块GET使用仓内样例，模块POST可传csv或rows与context，直接计算当前规则。/api/runs使用完整流水线并原子保存。每个阶段返回rows、quarantine、metrics；后续阶段还返回累计隔离。/api/upload接收multipart文件file。/api/runs/<id>/archive只导出一份带清单与证据的CSV。

## 合同控制对账

- `GET|POST /api/contracts`：返回当前批次的合同控制结果（按批次保存时则取最近批次）。GET默认合同控制样例；POST可传`sample`、`csv`或`rows`与`parameters`。
- `GET /api/contracts/state[?contract_key=]`：返回现行签认版本与全部历史修订。
- `GET /api/contracts/workbench`：工作台一次性取当前批次结果和签认状态。
- `POST /api/runs/<id>/contract/sign`：JSON `{version, contract_key?}`。version与批次当前版本不一致返回409；同一审批依据重跑返回现有修订；依据变化则新修订取代旧修订，不会叠加占用。
- `POST /api/contracts/<contract_key>/restore`：JSON `{revision?}`。省略revision时恢复上一份历史版本；失败事务回滚，现行版本不变。

合同控制结果的`contracts[]`含`control_qty`、`prior_signed_qty`、`remaining_before`、`requested_qty`、`reversal_qty`、`signed_qty`、`unsigned_qty`、`remaining_after`、`checks`与`segments[]`；每个分段保留`source_lines`和未签认量，每个申报行在`rows[]`中保留`_sources`。

