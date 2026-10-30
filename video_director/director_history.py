"""Durable creation drafts and user-visible Director conversations."""
from __future__ import annotations
import json, sqlite3, threading, time, uuid
from pathlib import Path
from typing import Any
from video_director import config

_LOCK = threading.RLock()
_MAX_JSON_BYTES = 8 * 1024 * 1024

def _db_path() -> Path:
    return config.composer_storage_root() / "director_history.db"

def _connect() -> sqlite3.Connection:
    _db_path().parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_db_path()), timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def _json(value: Any) -> str:
    raw = json.dumps(value if value is not None else {}, ensure_ascii=False, separators=(",", ":"))
    if len(raw.encode("utf-8")) > _MAX_JSON_BYTES:
        raise ValueError("草稿或对话快照超过 8 MB")
    return raw

def _ensure(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS director_drafts (
      draft_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL,
      revision INTEGER NOT NULL DEFAULT 1, updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS director_conversations (
      conversation_id TEXT PRIMARY KEY, project_id TEXT NOT NULL DEFAULT '',
      workflow_run_id TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
      snapshot_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'CREATED',
      created_at REAL NOT NULL, updated_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_director_conversations_project
      ON director_conversations(project_id, updated_at DESC);
    CREATE TABLE IF NOT EXISTS director_messages (
      message_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, role TEXT NOT NULL,
      content TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL,
      FOREIGN KEY(conversation_id) REFERENCES director_conversations(conversation_id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_director_messages_conversation
      ON director_messages(conversation_id, created_at, message_id);
    """)

def _decode(value: str, fallback: Any):
    try: return json.loads(value)
    except (TypeError, json.JSONDecodeError): return fallback

def save_draft(draft_id: str = "active", payload: dict | None = None) -> dict:
    draft_id = str(draft_id or "active").strip()[:128] or "active"
    if not isinstance(payload, dict): raise ValueError("草稿内容必须是 JSON 对象")
    raw, now = _json(payload), time.time()
    with _LOCK, _connect() as conn:
        _ensure(conn)
        row = conn.execute("SELECT revision FROM director_drafts WHERE draft_id=?", (draft_id,)).fetchone()
        revision = int(row["revision"]) + 1 if row else 1
        conn.execute("""INSERT INTO director_drafts(draft_id,payload_json,revision,updated_at)
          VALUES(?,?,?,?) ON CONFLICT(draft_id) DO UPDATE SET
          payload_json=excluded.payload_json,revision=excluded.revision,updated_at=excluded.updated_at""",
          (draft_id, raw, revision, now))
    return {"draft_id": draft_id, "revision": revision, "updated_at": now}

def get_draft(draft_id: str = "active") -> dict | None:
    draft_id = str(draft_id or "active")[:128]
    with _LOCK, _connect() as conn:
        _ensure(conn)
        row = conn.execute("SELECT payload_json,revision,updated_at FROM director_drafts WHERE draft_id=?", (draft_id,)).fetchone()
    if not row: return None
    return {"draft_id": draft_id, "revision": row["revision"], "updated_at": row["updated_at"],
            "data": _decode(row["payload_json"], {})}

def delete_draft(draft_id: str = "active") -> bool:
    with _LOCK, _connect() as conn:
        _ensure(conn)
        cur = conn.execute("DELETE FROM director_drafts WHERE draft_id=?", (str(draft_id or "active")[:128],))
    return cur.rowcount > 0

def create_conversation(*, conversation_id: str = "", project_id: str = "", title: str = "",
                        initial_message: str = "", snapshot: dict | None = None) -> dict:
    cid = str(conversation_id or f"conv-{uuid.uuid4().hex[:16]}").strip()
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
    if not cid or len(cid) > 128 or any(ch not in allowed for ch in cid):
        raise ValueError("conversation_id 格式不合法")
    title, now = str(title or "新建创作对话").strip()[:160], time.time()
    raw = _json(snapshot or {})
    with _LOCK, _connect() as conn:
        _ensure(conn)
        conn.execute("""INSERT INTO director_conversations
          (conversation_id,project_id,title,snapshot_json,status,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?)""", (cid,str(project_id or "")[:128],title,raw,"CREATED",now,now))
        if initial_message.strip():
            conn.execute("""INSERT INTO director_messages
              (message_id,conversation_id,role,content,metadata_json,created_at)
              VALUES(?,?,?,?,?,?)""", (f"msg-{uuid.uuid4().hex[:16]}",cid,"user",
              initial_message.strip()[:30000],"{}",now))
    return {"conversation_id":cid,"title":title,"created_at":now}

def link_workflow(conversation_id: str, *, project_id: str = "", workflow_run_id: str = "") -> bool:
    now=time.time()
    with _LOCK, _connect() as conn:
        _ensure(conn)
        cur=conn.execute("""UPDATE director_conversations
          SET project_id=CASE WHEN ?='' THEN project_id ELSE ? END,
          workflow_run_id=CASE WHEN ?='' THEN workflow_run_id ELSE ? END,
          status='RUNNING',updated_at=? WHERE conversation_id=?""",
          (project_id,project_id,workflow_run_id,workflow_run_id,now,conversation_id))
    return cur.rowcount > 0

def append_message(conversation_id: str, role: str, content: str, metadata: dict | None = None) -> bool:
    role=str(role or "").strip().lower()
    if role not in {"user","assistant","tool","system"}: raise ValueError("不支持的对话消息角色")
    content=str(content or "").strip()
    if not content: return False
    now=time.time()
    with _LOCK, _connect() as conn:
        _ensure(conn)
        exists=conn.execute("SELECT 1 FROM director_conversations WHERE conversation_id=?",(conversation_id,)).fetchone()
        if not exists: return False
        conn.execute("""INSERT INTO director_messages
          (message_id,conversation_id,role,content,metadata_json,created_at)
          VALUES(?,?,?,?,?,?)""",(f"msg-{uuid.uuid4().hex[:16]}",conversation_id,role,content[:30000],_json(metadata or {}),now))
        conn.execute("UPDATE director_conversations SET updated_at=? WHERE conversation_id=?",(now,conversation_id))
    return True

def list_conversations(*, project_id: str = "", limit: int = 50) -> list[dict]:
    limit=max(1,min(int(limit or 50),100))
    with _LOCK, _connect() as conn:
        _ensure(conn)
        sql="""SELECT conversation_id,project_id,workflow_run_id,title,status,created_at,updated_at
          FROM director_conversations"""
        rows=conn.execute(sql + (" WHERE project_id=? ORDER BY updated_at DESC LIMIT ?" if project_id else
          " ORDER BY updated_at DESC LIMIT ?"), (project_id,limit) if project_id else (limit,)).fetchall()
    return [dict(row) for row in rows]

def get_conversation(conversation_id: str) -> dict | None:
    with _LOCK, _connect() as conn:
        _ensure(conn)
        row=conn.execute("SELECT * FROM director_conversations WHERE conversation_id=?",(conversation_id,)).fetchone()
        if not row: return None
        messages=conn.execute("""SELECT message_id,role,content,metadata_json,created_at
          FROM director_messages WHERE conversation_id=? ORDER BY created_at,message_id""",(conversation_id,)).fetchall()
    result=dict(row); result["snapshot"]=_decode(result.pop("snapshot_json"),{})
    result["messages"]=[]
    for msg in messages:
        item=dict(msg); item["metadata"]=_decode(item.pop("metadata_json"),{}); result["messages"].append(item)
    return result

def record_workflow_result(run_id: str, status: str, result: dict | None = None, error: str = "") -> None:
    from video_director import workflow_run
    run=workflow_run.get_run(run_id); cid=str((run or {}).get("conversation_id") or "")
    if not cid: return
    result=result or {}
    if status=="COMPLETED": content="创作任务已完成。"
    elif status=="WAITING_CONFIRMATION": content="创作方案已生成，等待人工确认。"
    elif status=="CANCELLED": content="创作任务已停止；已完成的检查点仍保留。"
    elif status in {"FAILED","INTERRUPTED"}: content="创作任务未完成。" + (f" 原因：{error[:1200]}" if error else "")
    else: content=f"创作任务状态更新：{status}"
    pid=str(result.get("project_id") or (run or {}).get("project_id") or "")
    if pid: content += f" 项目：{pid}。"
    bundle=result.get("director_inputs") or {}
    script_obj=bundle.get("script") or {}
    if isinstance(script_obj,dict):
        script_text=str(script_obj.get("text") or "").strip()
        if script_text and script_obj.get("source") == "director":
            content += "\\n\\nAI 生成文案：\\n" + script_text[:4000]
    warnings=[str(x).strip() for x in (result.get("warnings") or []) if str(x).strip()]
    if warnings:
        content += "\\n\\n执行提示：\\n" + "\\n".join("• "+item[:400] for item in warnings[:8])
    checks=(result.get("director_plan") or {}).get("nodes") or []
    if checks:
        completed=sum(1 for node in checks if isinstance(node,dict) and node.get("status")=="DONE")
        failed=sum(1 for node in checks if isinstance(node,dict) and node.get("status")=="FAILED")
        content += f"\\n\\n导演节点记录：{completed} 个完成，{failed} 个失败，共 {len(checks)} 个节点。"
    append_message(cid,"assistant",content,{"workflow_run_id":run_id,"status":status,"result":result})
    with _LOCK, _connect() as conn:
        _ensure(conn)
        conn.execute("UPDATE director_conversations SET status=?,updated_at=? WHERE conversation_id=?",(status,time.time(),cid))
