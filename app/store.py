import sqlite3,json
from datetime import datetime,timezone
from pathlib import Path
from app.config import DATA_DIR
from app.errors import DomainError

RUN_COLUMNS={"basis_id":"TEXT","signed_qty":"TEXT","unsigned_qty":"TEXT","created_at":"TEXT"}

def connect():
    DATA_DIR.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(DATA_DIR/"records.db",timeout=20)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=20000")
    db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY,payload TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,decision TEXT NOT NULL DEFAULT '待复核')")
    existing={row[1] for row in db.execute("PRAGMA table_info(runs)")}
    for name,kind in RUN_COLUMNS.items():
        if name not in existing: db.execute(f"ALTER TABLE runs ADD COLUMN {name} {kind}")
    db.execute("""CREATE TABLE IF NOT EXISTS contract_signatures (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contract_key TEXT NOT NULL,
        basis_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        revision INTEGER NOT NULL,
        status TEXT NOT NULL,
        signed_qty TEXT NOT NULL,
        unsigned_qty TEXT NOT NULL,
        remaining_after TEXT NOT NULL,
        decision TEXT NOT NULL DEFAULT '已签认',
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL,
        superseded_at TEXT
    )""")
    # 每个合同控制只有一个现行版本；历史版本保留但不得同时占用余量。
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_active_contract ON contract_signatures(contract_key) WHERE status='active'")
    db.execute("CREATE INDEX IF NOT EXISTS contract_history ON contract_signatures(contract_key,revision DESC)")
    return db

def _now(): return datetime.now(timezone.utc).isoformat()

def save(run_id,payload,basis=None,contract_signed=None,contract_unsigned=None):
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO runs(id,payload,version,decision,basis_id,signed_qty,unsigned_qty,created_at) VALUES (?,?,1,'待复核',?,?,?,?)",
                   (run_id,json.dumps(payload,ensure_ascii=False),basis,contract_signed,contract_unsigned,_now()))
    return get(run_id)

def get(run_id):
    with connect() as db: row=db.execute("SELECT payload,version,decision FROM runs WHERE id=?",(run_id,)).fetchone()
    if not row: raise DomainError("NOT_FOUND","批次不存在",404)
    return {**json.loads(row[0]),"version":row[1],"decision":row[2]}

def latest():
    with connect() as db: row=db.execute("SELECT id FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
    return get(row[0]) if row else None

def history():
    with connect() as db: return [{"run_id":i,"version":v,"decision":d} for i,v,d in db.execute("SELECT id,version,decision FROM runs ORDER BY rowid DESC")]

def review(run_id,version,decision):
    if decision not in {"已复核","退回复核"}: raise DomainError("BAD_DECISION","复核状态无效")
    with connect() as db:
        cursor=db.execute("UPDATE runs SET version=version+1,decision=? WHERE id=? AND version=?",(decision,run_id,version))
        if cursor.rowcount!=1:
            if not db.execute("SELECT 1 FROM runs WHERE id=?",(run_id,)).fetchone(): raise DomainError("NOT_FOUND","批次不存在",404)
            raise DomainError("STALE_VERSION","版本已更新，请刷新",409)
    return get(run_id)

def _summaries(payload):
    contract=payload.get("contract") or {}
    return [s for s in contract.get("contracts",[]) if isinstance(s,dict) and s.get("contract_key")]

def _active(db,key):
    row=db.execute("SELECT id,contract_key,basis_id,run_id,revision,status,signed_qty,unsigned_qty,remaining_after,decision,payload,created_at,superseded_at FROM contract_signatures WHERE contract_key=? AND status='active'",(key,)).fetchone()
    return _signature(row) if row else None

def _signature(row):
    if row is None: return None
    return {"id":row[0],"contract_key":row[1],"basis_id":row[2],"run_id":row[3],"revision":row[4],"status":row[5],
            "signed_qty":row[6],"unsigned_qty":row[7],"remaining_after":row[8],"decision":row[9],
            "contract":json.loads(row[10]),"created_at":row[11],"superseded_at":row[12]}

def contract_state(contract_key=None):
    """返回现行签认版本与完整历史；历史用于恢复上一份旧版结果。"""
    with connect() as db:
        keys=[r[0] for r in db.execute("SELECT DISTINCT contract_key FROM contract_signatures ORDER BY contract_key")]
        if contract_key is not None:
            keys=[key for key in keys if key==contract_key]
            if not keys: return {"contracts":[]}
        result=[]
        for key in keys:
            active=_active(db,key)
            revisions=[_signature(row) for row in db.execute(
                "SELECT id,contract_key,basis_id,run_id,revision,status,signed_qty,unsigned_qty,remaining_after,decision,payload,created_at,superseded_at FROM contract_signatures WHERE contract_key=? ORDER BY revision DESC",(key,))]
            result.append({"contract_key":key,"active":active,"revisions":revisions})
        return {"contracts":result}

def sign_contract(run_id,version=None,contract_key=None):
    """把批次中的合同控制结果签认为现行版本。

    - 同一审批依据重跑：返回现有版本，不新增修订、不重复占用余量；
    - 审批依据变化：新版本替换旧版本占用，而不是叠加；
    - 失败：事务回滚，旧版本仍是现行版本。
    """
    try:
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT payload,version FROM runs WHERE id=?",(run_id,)).fetchone()
            if not row: raise DomainError("NOT_FOUND","批次不存在",404)
            if version is not None and version!=row[1]: raise DomainError("STALE_VERSION","版本已更新，请刷新",409)
            payload=json.loads(row[0]);basis=payload.get("basis_id") or run_id
            summaries=_summaries(payload)
            if not summaries: raise DomainError("NO_CONTRACT","该批次没有合同控制结果")
            if contract_key is not None: summaries=[s for s in summaries if s["contract_key"]==contract_key]
            if not summaries: raise DomainError("NO_CONTRACT","该批次不包含此合同控制",404)
            contract=payload.get("contract") or {}
            if contract.get("metrics",{}).get("balanced") is False: raise DomainError("CONTRACT_UNBALANCED","合同控制未对平，不能签认")
            chosen=[]
            for summary in summaries:
                key=summary["contract_key"];active=_active(db,key)
                if active and active["basis_id"]==basis:
                    # 依据未变的重跑：视为同一次占用，保持旧版本。
                    chosen.append(active);continue
                if summary.get("balanced") is False or summary.get("overdrawn"): raise DomainError("CONTRACT_UNBALANCED","合同控制未对平或已超控制量，不能切换")
                # 同一审批依据只能有一条占用记录；依据变回旧版时恢复旧版，
                # 而不是再插一条同依据的新版本，避免重复占用余量。
                same=db.execute("SELECT id,revision FROM contract_signatures WHERE contract_key=? AND basis_id=? ORDER BY revision DESC LIMIT 1",(key,basis)).fetchone()
                if same:
                    if active:
                        db.execute("UPDATE contract_signatures SET status='superseded',superseded_at=? WHERE id=?",(_now(),active["id"]))
                    db.execute("UPDATE contract_signatures SET status='active',superseded_at=NULL,decision='已恢复' WHERE id=?",(same[0],))
                    chosen.append(_active(db,key));continue
                revision=(db.execute("SELECT COALESCE(MAX(revision),0)+1 FROM contract_signatures WHERE contract_key=?",(key,)).fetchone()[0])
                if active:
                    db.execute("UPDATE contract_signatures SET status='superseded',superseded_at=? WHERE id=?",(_now(),active["id"]))
                db.execute("""INSERT INTO contract_signatures
                    (contract_key,basis_id,run_id,revision,status,signed_qty,unsigned_qty,remaining_after,decision,payload,created_at)
                    VALUES (?,?,?,?,'active',?,?,?,?,?,?)""",
                    (key,basis,run_id,revision,str(summary.get("signed_qty","0")),str(summary.get("unsigned_qty","0")),
                     str(summary.get("remaining_after","0")),"已签认",json.dumps(summary,ensure_ascii=False),_now()))
                chosen.append(_active(db,key))
    except sqlite3.IntegrityError:
        raise DomainError("CONTRACT_CONFLICT","合同控制正在被另一签认占用，请重试",409)
    return {"signed":chosen,"state":contract_state(contract_key)}

def restore_contract(contract_key,revision=None):
    """恢复上一份（或指定修订）旧版结果；现行版本转入历史，失败不留半成品。"""
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        active=_active(db,contract_key)
        if not active: raise DomainError("NOT_FOUND","合同控制尚无签认版本",404)
        if revision is None:
            row=db.execute("SELECT revision FROM contract_signatures WHERE contract_key=? AND status<>'active' ORDER BY revision DESC LIMIT 1",(contract_key,)).fetchone()
        else:
            row=db.execute("SELECT revision,status FROM contract_signatures WHERE contract_key=? AND revision=?",(contract_key,revision)).fetchone()
            if row and row[1]=="active":
                return {"restored":active,"state":contract_state(contract_key)}
        if not row: raise DomainError("NO_PREVIOUS","没有可恢复的旧版结果",404)
        target=row[0]
        db.execute("UPDATE contract_signatures SET status='superseded',superseded_at=? WHERE id=?",(_now(),active["id"]))
        db.execute("UPDATE contract_signatures SET status='active',superseded_at=NULL,decision='已恢复' WHERE contract_key=? AND revision=?",(contract_key,target))
    return {"restored":contract_state(contract_key)["contracts"][0]["active"],"state":contract_state(contract_key)}
