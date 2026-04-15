#!/usr/bin/env python3
"""SQLite database layer for Sabujak.

Sharding layout:
  hub.db              — companies, snapshots, webhook_routes  (meta)
  {cid}/company.db    — chat_messages, board_tasks, approvals,
                        activity_log, documents, chat_fts      (per-company)
"""
import sqlite3, json, os, threading, shutil
from datetime import datetime
from pathlib import Path

DATA = Path(__file__).parent.parent / "data"
DB_PATH = DATA / "hub.db"          # meta DB (kept for backwards-compat)

# ─── Per-company DB helpers ───

def _company_db_path(cid: str) -> Path:
    return DATA / cid / "company.db"

_LOCKS: dict = {}
_LOCKS_MUTEX = threading.Lock()

def _get_lock(cid=None):
    key = cid or '__meta__'
    with _LOCKS_MUTEX:
        if key not in _LOCKS:
            _LOCKS[key] = threading.RLock()
        return _LOCKS[key]

# Legacy alias used in many functions
_lock = _get_lock()   # meta lock (None → hub.db)

def _conn(cid=None):
    """Return a connection to hub.db (cid=None) or {cid}/company.db."""
    if cid:
        path = _company_db_path(cid)
    else:
        path = DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn

# ─── Schema helpers ───

_META_SCHEMA = """
    CREATE TABLE IF NOT EXISTS companies (
        id TEXT PRIMARY KEY,
        name TEXT DEFAULT '',
        topic TEXT DEFAULT '',
        lang TEXT DEFAULT 'ko',
        status TEXT DEFAULT 'starting',
        created_at TEXT DEFAULT '',
        budget REAL DEFAULT 10.0,
        data TEXT NOT NULL DEFAULT '{}'
    );
    CREATE TABLE IF NOT EXISTS snapshots (
        id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL,
        label TEXT DEFAULT '',
        data TEXT DEFAULT '{}',
        created_at TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_snapshots_company ON snapshots(company_id);
    CREATE TABLE IF NOT EXISTS webhook_routes (
        id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL,
        source TEXT DEFAULT 'custom',
        filter_expr TEXT DEFAULT '',
        target_agent TEXT DEFAULT 'CEO',
        prompt_template TEXT DEFAULT '',
        created_at TEXT DEFAULT '',
        enabled INTEGER DEFAULT 1
    );
    CREATE INDEX IF NOT EXISTS idx_routes_company ON webhook_routes(company_id);
"""

_COMPANY_SCHEMA = """
    CREATE TABLE IF NOT EXISTS chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id TEXT NOT NULL,
        from_field TEXT DEFAULT '',
        emoji TEXT DEFAULT '',
        text TEXT DEFAULT '',
        time TEXT DEFAULT '',
        msg_type TEXT DEFAULT 'user',
        mention INTEGER DEFAULT 0,
        to_field TEXT DEFAULT '',
        sort_order INTEGER DEFAULT 0,
        parent_id INTEGER DEFAULT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_chat_company ON chat_messages(company_id);
    CREATE TABLE IF NOT EXISTS board_tasks (
        id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL,
        title TEXT DEFAULT '',
        agent_id TEXT DEFAULT '',
        status TEXT DEFAULT '대기',
        depends_on TEXT DEFAULT '[]',
        deadline TEXT DEFAULT '',
        created_at TEXT DEFAULT '',
        updated_at TEXT DEFAULT '',
        sort_order INTEGER DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_tasks_company ON board_tasks(company_id);
    CREATE TABLE IF NOT EXISTS approvals (
        id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL,
        from_agent TEXT DEFAULT '',
        from_emoji TEXT DEFAULT '',
        approval_type TEXT DEFAULT '요청',
        category TEXT DEFAULT 'general',
        title TEXT DEFAULT '',
        detail TEXT DEFAULT '',
        amount TEXT DEFAULT '',
        approval_line TEXT DEFAULT '[]',
        current_step INTEGER DEFAULT 0,
        status TEXT DEFAULT 'draft',
        comments TEXT DEFAULT '[]',
        time TEXT DEFAULT '',
        created_at TEXT DEFAULT '',
        updated_at TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_approvals_company ON approvals(company_id);
    CREATE TABLE IF NOT EXISTS activity_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id TEXT NOT NULL,
        agent TEXT DEFAULT '',
        text TEXT DEFAULT '',
        time TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_log_company ON activity_log(company_id);
    CREATE TABLE IF NOT EXISTS documents (
        id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL,
        doc_type TEXT DEFAULT 'standup',
        agent_id TEXT DEFAULT '',
        content TEXT DEFAULT '',
        updated_at TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_docs_company ON documents(company_id, doc_type);
    CREATE VIRTUAL TABLE IF NOT EXISTS chat_fts
        USING fts5(text, company_id UNINDEXED, msg_id UNINDEXED, from_field UNINDEXED, time UNINDEXED,
                   content='', contentless_delete=1);
    CREATE TABLE IF NOT EXISTS plan_tasks (
        id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL,
        parent_id TEXT DEFAULT NULL,
        title TEXT DEFAULT '',
        description TEXT DEFAULT '',
        status TEXT DEFAULT 'todo',
        agent_id TEXT DEFAULT '',
        sort_order INTEGER DEFAULT 0,
        created_at TEXT DEFAULT '',
        updated_at TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_plan_tasks_company ON plan_tasks(company_id);
    CREATE INDEX IF NOT EXISTS idx_plan_tasks_parent ON plan_tasks(parent_id);
    CREATE TABLE IF NOT EXISTS sprints (
        id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL,
        title TEXT DEFAULT '',
        goal TEXT DEFAULT '',
        start_date TEXT DEFAULT '',
        end_date TEXT DEFAULT '',
        status TEXT DEFAULT 'planning',
        created_at TEXT DEFAULT '',
        retro TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_sprints_company ON sprints(company_id);
    CREATE TABLE IF NOT EXISTS sprint_tasks (
        sprint_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        PRIMARY KEY (sprint_id, task_id)
    );
    CREATE TABLE IF NOT EXISTS wiki_pages (
        id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL,
        title TEXT DEFAULT '',
        content TEXT DEFAULT '',
        author TEXT DEFAULT '',
        category TEXT DEFAULT 'general',
        created_at TEXT DEFAULT '',
        updated_at TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_wiki_company ON wiki_pages(company_id);
    CREATE TABLE IF NOT EXISTS meeting_notes (
        id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL,
        topic TEXT DEFAULT '',
        participants TEXT DEFAULT '[]',
        decisions TEXT DEFAULT '',
        action_items TEXT DEFAULT '[]',
        summary TEXT DEFAULT '',
        created_at TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_meetings_company ON meeting_notes(company_id);
    CREATE TABLE IF NOT EXISTS milestones (
        id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL,
        title TEXT DEFAULT '',
        description TEXT DEFAULT '',
        deadline TEXT DEFAULT '',
        status TEXT DEFAULT 'pending',
        linked_tasks TEXT DEFAULT '[]',
        created_at TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_milestones_company ON milestones(company_id);
    CREATE TABLE IF NOT EXISTS risks (
        id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL,
        title TEXT DEFAULT '',
        description TEXT DEFAULT '',
        severity TEXT DEFAULT 'medium',
        status TEXT DEFAULT 'open',
        owner TEXT DEFAULT '',
        mitigation TEXT DEFAULT '',
        created_at TEXT DEFAULT '',
        updated_at TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_risks_company ON risks(company_id);
    CREATE TABLE IF NOT EXISTS announcements (
        id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL,
        title TEXT DEFAULT '',
        content TEXT DEFAULT '',
        author TEXT DEFAULT '',
        pinned INTEGER DEFAULT 0,
        created_at TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_ann_company ON announcements(company_id);
    CREATE TABLE IF NOT EXISTS work_journals (
        id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL,
        agent_id TEXT DEFAULT '',
        date TEXT DEFAULT '',
        tasks_done TEXT DEFAULT '',
        tasks_next TEXT DEFAULT '',
        issues TEXT DEFAULT '',
        notes TEXT DEFAULT '',
        created_at TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_wj_company ON work_journals(company_id, date);
    CREATE TABLE IF NOT EXISTS policies (
        id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL,
        title TEXT DEFAULT '',
        content TEXT DEFAULT '',
        category TEXT DEFAULT 'general',
        effective_date TEXT DEFAULT '',
        created_at TEXT DEFAULT '',
        updated_at TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_pol_company ON policies(company_id);
    CREATE TABLE IF NOT EXISTS budgets (
        id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL,
        agent_id TEXT DEFAULT '',
        department TEXT DEFAULT '',
        allocated REAL DEFAULT 0,
        spent REAL DEFAULT 0,
        period TEXT DEFAULT '',
        created_at TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_bud_company ON budgets(company_id);
    CREATE TABLE IF NOT EXISTS votes (
        id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL,
        title TEXT DEFAULT '',
        description TEXT DEFAULT '',
        options TEXT DEFAULT '[]',
        results TEXT DEFAULT '{}',
        voters TEXT DEFAULT '[]',
        status TEXT DEFAULT 'open',
        deadline TEXT DEFAULT '',
        created_at TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_votes_company ON votes(company_id);
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id TEXT NOT NULL,
        action TEXT DEFAULT '',
        actor TEXT DEFAULT '',
        target TEXT DEFAULT '',
        detail TEXT DEFAULT '',
        created_at TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_audit_company ON audit_log(company_id);
    CREATE TABLE IF NOT EXISTS memory_stream (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id TEXT NOT NULL,
        agent_id TEXT DEFAULT '',
        content TEXT DEFAULT '',
        importance INTEGER DEFAULT 5,
        mem_type TEXT DEFAULT 'observation',
        created_at TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_mem_agent ON memory_stream(company_id, agent_id);
    CREATE TABLE IF NOT EXISTS crm_contacts (
        id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL,
        name TEXT DEFAULT '',
        email TEXT DEFAULT '',
        phone TEXT DEFAULT '',
        company_name TEXT DEFAULT '',
        role TEXT DEFAULT '',
        status TEXT DEFAULT 'lead',
        notes TEXT DEFAULT '',
        tags TEXT DEFAULT '',
        owner TEXT DEFAULT '',
        created_at TEXT DEFAULT '',
        updated_at TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_crm_company ON crm_contacts(company_id);
    CREATE TABLE IF NOT EXISTS agent_priorities (
        company_id TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        category TEXT NOT NULL,
        priority INTEGER DEFAULT 3,
        PRIMARY KEY (company_id, agent_id, category)
    );
"""

def _ensure_company_db(cid: str):
    """Create per-company DB schema if not yet initialised."""
    conn = _conn(cid)
    conn.executescript(_COMPANY_SCHEMA)
    # Safe migrations
    for stmt in [
        "ALTER TABLE chat_messages ADD COLUMN parent_id INTEGER DEFAULT NULL",
        "ALTER TABLE plan_tasks ADD COLUMN description TEXT DEFAULT ''",
        "ALTER TABLE approvals ADD COLUMN category TEXT DEFAULT 'general'",
        "ALTER TABLE approvals ADD COLUMN title TEXT DEFAULT ''",
        "ALTER TABLE approvals ADD COLUMN amount TEXT DEFAULT ''",
        "ALTER TABLE approvals ADD COLUMN approval_line TEXT DEFAULT '[]'",
        "ALTER TABLE approvals ADD COLUMN current_step INTEGER DEFAULT 0",
        "ALTER TABLE approvals ADD COLUMN comments TEXT DEFAULT '[]'",
        "ALTER TABLE approvals ADD COLUMN updated_at TEXT DEFAULT ''",
        "ALTER TABLE approvals ADD COLUMN options TEXT DEFAULT ''",
    ]:
        try:
            conn.execute(stmt)
            conn.commit()
        except Exception:
            pass
    conn.close()

def init_db():
    with _get_lock():
        conn = _conn()
        conn.executescript(_META_SCHEMA)
        conn.commit()
        # Migrate old single-file schema tables if hub.db still has them
        _migrate_hub_tables_to_meta(conn)
        conn.close()
    # Initialise per-company DBs for all known companies
    with _get_lock():
        conn = _conn()
        cids = [r[0] for r in conn.execute("SELECT id FROM companies").fetchall()]
        conn.close()
    for cid in cids:
        with _get_lock(cid):
            _ensure_company_db(cid)

def _migrate_hub_tables_to_meta(meta_conn):
    """One-time: move per-company tables out of hub.db into company DBs."""
    # Check if old tables still exist in hub.db
    tables = {r[0] for r in meta_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if 'chat_messages' not in tables:
        return  # Already migrated
    print("[db] Migrating per-company tables from hub.db to sharded DBs...")
    company_ids = [r[0] for r in meta_conn.execute("SELECT id FROM companies").fetchall()]
    for cid in company_ids:
        _ensure_company_db(cid)
        company_conn = _conn(cid)
        # chat_messages
        rows = meta_conn.execute(
            "SELECT * FROM chat_messages WHERE company_id=? ORDER BY sort_order, id", (cid,)).fetchall()
        for r in rows:
            try:
                company_conn.execute("""INSERT OR IGNORE INTO chat_messages
                    (id,company_id,from_field,emoji,text,time,msg_type,mention,to_field,sort_order,parent_id)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (r['id'],cid,r['from_field'],r['emoji'],r['text'],r['time'],
                     r['msg_type'],r['mention'],r['to_field'],r['sort_order'],r['parent_id']))
            except Exception:
                pass
        # board_tasks
        rows = meta_conn.execute("SELECT * FROM board_tasks WHERE company_id=?", (cid,)).fetchall()
        for r in rows:
            try:
                company_conn.execute("""INSERT OR IGNORE INTO board_tasks
                    (id,company_id,title,agent_id,status,depends_on,deadline,created_at,updated_at,sort_order)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (r['id'],cid,r['title'],r['agent_id'],r['status'],r['depends_on'],
                     r['deadline'],r['created_at'],r['updated_at'],r['sort_order']))
            except Exception:
                pass
        # approvals
        rows = meta_conn.execute("SELECT * FROM approvals WHERE company_id=?", (cid,)).fetchall()
        for r in rows:
            try:
                company_conn.execute("""INSERT OR IGNORE INTO approvals
                    (id,company_id,from_agent,from_emoji,approval_type,detail,status,time,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (r['id'],cid,r['from_agent'],r['from_emoji'],r['approval_type'],
                     r['detail'],r['status'],r['time'],r['created_at']))
            except Exception:
                pass
        # activity_log
        rows = meta_conn.execute("SELECT * FROM activity_log WHERE company_id=?", (cid,)).fetchall()
        for r in rows:
            try:
                company_conn.execute("""INSERT OR IGNORE INTO activity_log
                    (company_id,agent,text,time) VALUES (?,?,?,?)""",
                    (cid,r['agent'],r['text'],r['time']))
            except Exception:
                pass
        # documents
        rows = meta_conn.execute("SELECT * FROM documents WHERE company_id=?", (cid,)).fetchall()
        for r in rows:
            try:
                company_conn.execute("""INSERT OR IGNORE INTO documents
                    (id,company_id,doc_type,agent_id,content,updated_at) VALUES (?,?,?,?,?,?)""",
                    (r['id'],cid,r['doc_type'],r['agent_id'],r['content'],r['updated_at']))
            except Exception:
                pass
        company_conn.commit()
        company_conn.close()
    # Also migrate snapshots/webhook_routes if they were in hub.db
    if 'snapshots' not in {r[0] for r in meta_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}:
        meta_conn.executescript(_META_SCHEMA)
        meta_conn.commit()
    print(f"[db] Sharding migration complete for {len(company_ids)} companies")

# ─── Company CRUD ───

def db_get_company(cid):
    with _get_lock():
        conn = _conn()
        row = conn.execute("SELECT * FROM companies WHERE id=?", (cid,)).fetchone()
        conn.close()
        if not row:
            return None
        company = dict(row)
    with _get_lock(cid):
        _ensure_company_db(cid)
        company['chat'] = db_get_chat(cid)
        company['board_tasks'] = db_get_tasks(cid)
        company['approvals'] = db_get_approvals(cid)
        company['activity_log'] = db_get_activity(cid)
    try:
        extra = json.loads(company.pop('data', '{}'))
    except (json.JSONDecodeError, ValueError):
        print(f"[WARN] corrupted JSON data for company {cid}, using empty data")
        extra = {}
        company.pop('data', None)
    company.update({k: v for k, v in extra.items() if k not in company})
    return company

def db_save_company(company):
    if not company or 'id' not in company:
        return None
    cid = company['id']
    # Save metadata to hub.db
    with _get_lock():
        conn = _conn()
        db_fields = {'id', 'name', 'topic', 'lang', 'status', 'created_at', 'budget', 'data'}
        data = {k: v for k, v in company.items()
                if k not in db_fields and k not in ('chat', 'board_tasks', 'approvals', 'activity_log')}
        conn.execute("""INSERT OR REPLACE INTO companies (id, name, topic, lang, status, created_at, budget, data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (cid, company.get('name',''), company.get('topic',''), company.get('lang','ko'),
             company.get('status','starting'), company.get('created_at',''),
             company.get('budget', 10.0), json.dumps(data, ensure_ascii=False)))
        conn.commit()
        conn.close()
    # Save per-company data
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        _save_chat(conn, cid, company.get('chat', []))
        _save_tasks(conn, cid, company.get('board_tasks', []))
        _save_approvals(conn, cid, company.get('approvals', []))
        _save_activity(conn, cid, company.get('activity_log', []))
        conn.commit()
        conn.close()
    return company

def db_update_company(cid, updates):
    if not updates:
        return db_get_company(cid)
    with _get_lock():
        company = db_get_company(cid)
        if not company:
            return None
        company.update(updates)
        db_save_company(company)
        return company

def db_get_all_companies():
    # Get metadata from hub.db
    with _get_lock():
        conn = _conn()
        company_rows = conn.execute("SELECT * FROM companies ORDER BY created_at DESC").fetchall()
        conn.close()
    if not company_rows:
        return []

    companies = []
    for row in company_rows:
        company = dict(row)
        cid = company['id']
        try:
            extra = json.loads(company.pop('data', '{}'))
        except (json.JSONDecodeError, ValueError):
            print(f"[WARN] corrupted JSON data for company {cid}, using empty data")
            extra = {}
            company.pop('data', None)
        company.update({k: v for k, v in extra.items() if k not in company})
        # Per-company data
        with _get_lock(cid):
            _ensure_company_db(cid)
            company['chat'] = db_get_chat(cid)
            company['board_tasks'] = db_get_tasks(cid)
            company['approvals'] = db_get_approvals(cid)
            company['activity_log'] = db_get_activity(cid)
        companies.append(company)
    return companies

def db_delete_company(cid):
    with _get_lock():
        conn = _conn()
        conn.execute("DELETE FROM companies WHERE id=?", (cid,))
        conn.execute("DELETE FROM snapshots WHERE company_id=?", (cid,))
        conn.execute("DELETE FROM webhook_routes WHERE company_id=?", (cid,))
        conn.commit()
        conn.close()
    # Remove per-company DB file
    company_dir = DATA / cid
    if company_dir.exists():
        shutil.rmtree(company_dir, ignore_errors=True)
    with _LOCKS_MUTEX:
        _LOCKS.pop(cid, None)

# ─── Chat ───

def db_get_chat(cid):
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        count = conn.execute("SELECT COUNT(*) FROM chat_messages WHERE company_id=?", (cid,)).fetchone()[0]
        if count > 200:
            conn.execute(
                "DELETE FROM chat_messages WHERE company_id=? AND id NOT IN "
                "(SELECT id FROM chat_messages WHERE company_id=? ORDER BY id DESC LIMIT 200)",
                (cid, cid))
            conn.commit()
        rows = conn.execute(
            "SELECT * FROM chat_messages WHERE company_id=? ORDER BY sort_order, id", (cid,)).fetchall()
        conn.close()
    return [{'id': r['id'], 'from': r['from_field'], 'emoji': r['emoji'], 'text': r['text'],
             'time': r['time'], 'type': r['msg_type'], 'mention': bool(r['mention']),
             'to': r['to_field'], 'parent_id': r['parent_id']} for r in rows]

def db_add_chat(cid, msg):
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        count = conn.execute("SELECT COUNT(*) FROM chat_messages WHERE company_id=?", (cid,)).fetchone()[0]
        cursor = conn.execute("""INSERT INTO chat_messages
            (company_id, from_field, emoji, text, time, msg_type, mention, to_field, sort_order, parent_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (cid, msg.get('from',''), msg.get('emoji',''), msg.get('text',''),
             msg.get('time',''), msg.get('type','user'),
             1 if msg.get('mention') else 0, msg.get('to',''), count, msg.get('parent_id')))
        msg_id = cursor.lastrowid
        text = msg.get('text','')
        if text:
            try:
                conn.execute(
                    "INSERT INTO chat_fts(text, company_id, msg_id, from_field, time) VALUES (?,?,?,?,?)",
                    (text, cid, msg_id, msg.get('from',''), msg.get('time','')))
            except Exception:
                pass
        if count >= 200:
            conn.execute(
                "DELETE FROM chat_messages WHERE company_id=? AND id NOT IN "
                "(SELECT id FROM chat_messages WHERE company_id=? ORDER BY id DESC LIMIT 200)",
                (cid, cid))
        conn.commit()
        conn.close()

def db_add_chats(cid, messages):
    if not messages:
        return
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        count = conn.execute("SELECT COUNT(*) FROM chat_messages WHERE company_id=?", (cid,)).fetchone()[0]
        for i, msg in enumerate(messages):
            cursor = conn.execute("""INSERT INTO chat_messages
                (company_id, from_field, emoji, text, time, msg_type, mention, to_field, sort_order)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (cid, msg.get('from',''), msg.get('emoji',''), msg.get('text',''),
                 msg.get('time',''), msg.get('type','user'),
                 1 if msg.get('mention') else 0, msg.get('to',''), count + i))
            text = msg.get('text','')
            if text:
                try:
                    conn.execute(
                        "INSERT INTO chat_fts(text, company_id, msg_id, from_field, time) VALUES (?,?,?,?,?)",
                        (text, cid, cursor.lastrowid, msg.get('from',''), msg.get('time','')))
                except Exception:
                    pass
        conn.execute(
            "DELETE FROM chat_messages WHERE company_id=? AND id NOT IN "
            "(SELECT id FROM chat_messages WHERE company_id=? ORDER BY id DESC LIMIT 200)",
            (cid, cid))
        conn.commit()
        conn.close()

def _save_chat(conn, cid, messages):
    conn.execute("DELETE FROM chat_messages WHERE company_id=?", (cid,))
    for i, m in enumerate(messages):
        conn.execute("""INSERT INTO chat_messages
            (company_id, from_field, emoji, text, time, msg_type, mention, to_field, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (cid, m.get('from',''), m.get('emoji',''), m.get('text',''),
             m.get('time',''), m.get('type','user'),
             1 if m.get('mention') else 0, m.get('to',''), i))

# ─── Snapshots (stored in hub.db for cross-company lookup) ───

def db_create_snapshot(cid, label, data_dict):
    import uuid as _uuid, datetime as _dt
    snap_id = f"snap-{_uuid.uuid4().hex[:8]}"
    with _get_lock():
        conn = _conn()
        conn.execute(
            "INSERT INTO snapshots (id, company_id, label, data, created_at) VALUES (?,?,?,?,?)",
            (snap_id, cid, label, json.dumps(data_dict, ensure_ascii=False),
             _dt.datetime.now().isoformat()))
        conn.commit()
        conn.close()
    return snap_id

def db_get_snapshots(cid):
    with _get_lock():
        conn = _conn()
        rows = conn.execute(
            "SELECT id, company_id, label, created_at FROM snapshots "
            "WHERE company_id=? ORDER BY created_at DESC LIMIT 20", (cid,)).fetchall()
        conn.close()
    return [dict(r) for r in rows]

def db_get_snapshot(snap_id):
    with _get_lock():
        conn = _conn()
        row = conn.execute("SELECT * FROM snapshots WHERE id=?", (snap_id,)).fetchone()
        conn.close()
    if not row:
        return None
    r = dict(row)
    try:
        r['data'] = json.loads(r['data'])
    except Exception:
        r['data'] = {}
    return r

def db_delete_snapshot(snap_id):
    with _get_lock():
        conn = _conn()
        conn.execute("DELETE FROM snapshots WHERE id=?", (snap_id,))
        conn.commit()
        conn.close()

# ─── Webhook Routes (stored in hub.db) ───

def db_get_webhook_routes(cid):
    with _get_lock():
        conn = _conn()
        rows = conn.execute(
            "SELECT * FROM webhook_routes WHERE company_id=? AND enabled=1", (cid,)).fetchall()
        conn.close()
    return [dict(r) for r in rows]

def db_add_webhook_route(cid, source, filter_expr, target_agent, prompt_template):
    import uuid as _uuid
    import datetime as _dt
    route_id = f"route-{_uuid.uuid4().hex[:8]}"
    with _get_lock():
        conn = _conn()
        conn.execute(
            """INSERT INTO webhook_routes
               (id, company_id, source, filter_expr, target_agent, prompt_template, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (route_id, cid, source, filter_expr, target_agent, prompt_template,
             _dt.datetime.now().isoformat()))
        conn.commit()
        conn.close()
    return route_id

def db_delete_webhook_route(cid, route_id):
    with _get_lock():
        conn = _conn()
        conn.execute("DELETE FROM webhook_routes WHERE id=? AND company_id=?", (route_id, cid))
        conn.commit()
        conn.close()

# ─── Full-Text Search ───

def db_search_chat(query, company_ids=None, limit=50):
    """Search chat messages using FTS5 across company DBs."""
    if not query or not query.strip():
        return []
    safe_query = query.replace('"', '""')

    # Determine which company DBs to search
    if company_ids is None:
        with _get_lock():
            conn = _conn()
            company_ids = [r[0] for r in conn.execute("SELECT id FROM companies").fetchall()]
            conn.close()

    results = []
    for cid in company_ids:
        db_path = _company_db_path(cid)
        if not db_path.exists():
            continue
        with _get_lock(cid):
            conn = _conn(cid)
            try:
                rows = conn.execute(
                    "SELECT company_id, id as msg_id, from_field, time, emoji, text "
                    "FROM chat_messages WHERE text LIKE ? ORDER BY id DESC LIMIT ?",
                    (f'%{query}%', limit)
                ).fetchall()
                results.extend([{
                    'company_id': r['company_id'], 'msg_id': r['msg_id'],
                    'from': r['from_field'], 'emoji': r['emoji'],
                    'text': r['text'], 'time': r['time']
                } for r in rows])
            except Exception as e:
                print(f"[search] error for {cid}: {e}")
            finally:
                conn.close()
        if len(results) >= limit:
            break
    return results[:limit]

# ─── Board Tasks ───

def db_get_tasks(cid):
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        rows = conn.execute(
            "SELECT * FROM board_tasks WHERE company_id=? ORDER BY sort_order, id", (cid,)).fetchall()
        conn.close()
    result = []
    for r in rows:
        try:
            depends_on = json.loads(r['depends_on'])
        except (json.JSONDecodeError, ValueError):
            depends_on = []
        result.append({'id': r['id'], 'title': r['title'], 'agent_id': r['agent_id'],
                       'status': r['status'], 'depends_on': depends_on,
                       'deadline': r['deadline'], 'created_at': r['created_at'] or '',
                       'updated_at': r['updated_at'] or ''})
    return result

def db_add_task(cid, task):
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        count = conn.execute(
            "SELECT COUNT(*) FROM board_tasks WHERE company_id=?", (cid,)).fetchone()[0]
        conn.execute("""INSERT OR REPLACE INTO board_tasks
            (id, company_id, title, agent_id, status, depends_on, deadline, created_at, updated_at, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (task.get('id',''), cid, task.get('title',''), task.get('agent_id',''),
             task.get('status','대기'), json.dumps(task.get('depends_on',[])),
             task.get('deadline',''), task.get('created_at',''), task.get('updated_at',''), count))
        conn.execute(
            "DELETE FROM board_tasks WHERE company_id=? AND id IN "
            "(SELECT id FROM board_tasks WHERE company_id=? AND status='완료' ORDER BY sort_order, id "
            "LIMIT CASE WHEN (SELECT COUNT(*) FROM board_tasks WHERE company_id=?) > 50 "
            "THEN (SELECT COUNT(*) FROM board_tasks WHERE company_id=?) - 45 ELSE 0 END)",
            (cid, cid, cid, cid))
        conn.commit()
        conn.close()

def db_update_task(cid, task_id, updates):
    if not updates:
        return
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        sets = []
        vals = []
        field_map = {
            'title': 'title', 'agent_id': 'agent_id', 'status': 'status',
            'deadline': 'deadline', 'created_at': 'created_at',
            'updated_at': 'updated_at', 'sort_order': 'sort_order',
        }
        for key, col in field_map.items():
            if key in updates:
                sets.append(f"{col}=?")
                vals.append(updates[key])
        if 'depends_on' in updates:
            sets.append("depends_on=?")
            vals.append(json.dumps(updates['depends_on']))
        if not sets:
            conn.close()
            return
        vals.extend([task_id, cid])
        conn.execute(f"UPDATE board_tasks SET {', '.join(sets)} WHERE id=? AND company_id=?", vals)
        conn.commit()
        conn.close()

def db_delete_task(cid, task_id):
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        conn.execute("DELETE FROM board_tasks WHERE id=? AND company_id=?", (task_id, cid))
        conn.commit()
        conn.close()

def _save_tasks(conn, cid, tasks):
    conn.execute("DELETE FROM board_tasks WHERE company_id=?", (cid,))
    for i, t in enumerate(tasks):
        conn.execute("""INSERT OR REPLACE INTO board_tasks
            (id, company_id, title, agent_id, status, depends_on, deadline, created_at, updated_at, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (t.get('id', f"task-{i}"), cid, t.get('title',''), t.get('agent_id',''),
             t.get('status','대기'), json.dumps(t.get('depends_on',[])),
             t.get('deadline',''), t.get('created_at',''), t.get('updated_at',''), i))

# ─── Approvals ───

def db_get_approvals(cid, status=None):
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        if status:
            rows = conn.execute(
                "SELECT * FROM approvals WHERE company_id=? AND status=? ORDER BY created_at DESC",
                (cid, status)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM approvals WHERE company_id=? ORDER BY created_at DESC",
                (cid,)).fetchall()
        conn.close()
    return [{'id': r['id'], 'from_agent': r['from_agent'], 'from_emoji': r['from_emoji'],
             'type': r['approval_type'], 'approval_type': r['approval_type'],
             'category': r['category'], 'title': r['title'],
             'detail': r['detail'], 'status': r['status'],
             'time': r['time'], 'created_at': r['created_at']} for r in rows]

_APPROVAL_ALLOWED_FIELDS = {'status', 'detail', 'time'}

def db_update_approval(cid, aid, updates):
    allowed = {k: v for k, v in updates.items() if k in _APPROVAL_ALLOWED_FIELDS}
    if not allowed:
        return
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        sets = ', '.join(f"{k}=?" for k in allowed)
        vals = list(allowed.values()) + [aid, cid]
        conn.execute(f"UPDATE approvals SET {sets} WHERE id=? AND company_id=?", vals)
        conn.commit()
        conn.close()

def db_add_approval(cid, approval):
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        conn.execute("""INSERT OR REPLACE INTO approvals
            (id, company_id, from_agent, from_emoji, approval_type, category, title, detail, status, time, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (approval.get('id',''), cid, approval.get('from_agent', approval.get('agent','')),
             approval.get('from_emoji',''), approval.get('type', approval.get('approval_type','요청')),
             approval.get('category','general'), approval.get('title',''),
             approval.get('detail',''), approval.get('status','pending'),
             approval.get('time',''), approval.get('created_at','')))
        conn.commit()
        conn.close()

def _save_approvals(conn, cid, approvals):
    conn.execute("DELETE FROM approvals WHERE company_id=?", (cid,))
    for a in approvals:
        conn.execute("""INSERT OR REPLACE INTO approvals
            (id, company_id, from_agent, from_emoji, approval_type, detail, status, time, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (a.get('id',''), cid, a.get('from_agent',''), a.get('from_emoji',''),
             a.get('type','요청'), a.get('detail',''), a.get('status','pending'),
             a.get('time',''), a.get('created_at','')))

# ─── Activity Log ───

def db_get_activity(cid):
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        rows = conn.execute(
            "SELECT * FROM activity_log WHERE company_id=? ORDER BY id DESC LIMIT 50",
            (cid,)).fetchall()
        conn.close()
    return [{'time': r['time'], 'agent': r['agent'], 'text': r['text']} for r in rows]

def db_add_activity(cid, entry):
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        conn.execute(
            "INSERT INTO activity_log (company_id, agent, text, time) VALUES (?, ?, ?, ?)",
            (cid, entry.get('agent',''), entry.get('text',''), entry.get('time','')))
        conn.execute(
            "DELETE FROM activity_log WHERE company_id=? AND id NOT IN "
            "(SELECT id FROM activity_log WHERE company_id=? ORDER BY id DESC LIMIT 50)",
            (cid, cid))
        conn.commit()
        conn.close()

def db_add_activities(cid, entries):
    if not entries:
        return
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        for entry in entries:
            conn.execute(
                "INSERT INTO activity_log (company_id, agent, text, time) VALUES (?, ?, ?, ?)",
                (cid, entry.get('agent',''), entry.get('text',''), entry.get('time','')))
        conn.execute(
            "DELETE FROM activity_log WHERE company_id=? AND id NOT IN "
            "(SELECT id FROM activity_log WHERE company_id=? ORDER BY id DESC LIMIT 50)",
            (cid, cid))
        conn.commit()
        conn.close()

def _save_activity(conn, cid, entries):
    conn.execute("DELETE FROM activity_log WHERE company_id=?", (cid,))
    for e in entries[-50:]:
        conn.execute(
            "INSERT INTO activity_log (company_id, agent, text, time) VALUES (?, ?, ?, ?)",
            (cid, e.get('agent',''), e.get('text',''), e.get('time','')))

# ─── Document Cache ───

_doc_cache: dict = {}  # {(cid, doc_type, agent_id): content}

def db_get_doc(cid, doc_type, agent_id=''):
    key = (cid, doc_type, agent_id)
    if key in _doc_cache:
        return _doc_cache[key]
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        row = conn.execute(
            "SELECT content FROM documents WHERE company_id=? AND doc_type=? AND agent_id=?",
            (cid, doc_type, agent_id)).fetchone()
        conn.close()
    content = row['content'] if row else ''
    _doc_cache[key] = content
    return content

def db_save_doc(cid, doc_type, agent_id, content):
    key = (cid, doc_type, agent_id)
    _doc_cache[key] = content
    now = datetime.now().isoformat()
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        conn.execute(
            "INSERT OR REPLACE INTO documents (id, company_id, doc_type, agent_id, content, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (f"{cid}-{doc_type}-{agent_id}", cid, doc_type, agent_id, content, now))
        conn.commit()
        conn.close()

def db_clear_doc_cache():
    _doc_cache.clear()

# ─── Plan Tasks ───

def db_get_plan_tasks(cid: str) -> list:
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        rows = conn.execute(
            "SELECT * FROM plan_tasks WHERE company_id=? ORDER BY sort_order, created_at",
            (cid,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

def db_add_plan_task(cid: str, task: dict) -> dict:
    import uuid
    tid = task.get('id') or str(uuid.uuid4())[:8]
    now = datetime.utcnow().isoformat()
    with _get_lock(cid):
        conn = _conn(cid)
        conn.execute(
            "INSERT INTO plan_tasks (id,company_id,parent_id,title,description,status,agent_id,sort_order,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (tid, cid, task.get('parent_id'), task.get('title',''),
             task.get('description',''), task.get('status','todo'),
             task.get('agent_id',''), task.get('sort_order',0), now, now)
        )
        conn.commit()
        conn.close()
    return {**task, 'id': tid, 'company_id': cid, 'created_at': now, 'updated_at': now}

def db_update_plan_task(cid: str, task_id: str, updates: dict):
    now = datetime.utcnow().isoformat()
    allowed = {'title','description','status','agent_id','sort_order','parent_id'}
    fields = {k: v for k, v in updates.items() if k in allowed}
    if not fields:
        return
    fields['updated_at'] = now
    set_clause = ', '.join(f"{k}=?" for k in fields)
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        conn.execute(
            f"UPDATE plan_tasks SET {set_clause} WHERE id=? AND company_id=?",
            (*fields.values(), task_id, cid)
        )
        conn.commit()
        conn.close()

def db_delete_plan_task(cid: str, task_id: str):
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        # Delete children first
        conn.execute("DELETE FROM plan_tasks WHERE parent_id=? AND company_id=?", (task_id, cid))
        conn.execute("DELETE FROM plan_tasks WHERE id=? AND company_id=?", (task_id, cid))
        conn.commit()
        conn.close()

# ─── Sprints ───

def db_get_sprints(cid):
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        rows = conn.execute("SELECT * FROM sprints WHERE company_id=? ORDER BY created_at DESC", (cid,)).fetchall()
        conn.close()
    return [dict(r) for r in rows]

def db_add_sprint(cid, sprint):
    import uuid
    sid = sprint.get('id') or str(uuid.uuid4())[:8]
    now = datetime.utcnow().isoformat()
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        conn.execute(
            "INSERT INTO sprints (id,company_id,title,goal,start_date,end_date,status,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (sid, cid, sprint.get('title',''), sprint.get('goal',''),
             sprint.get('start_date',''), sprint.get('end_date',''), 'active', now))
        for tid in sprint.get('task_ids', []):
            conn.execute("INSERT OR IGNORE INTO sprint_tasks (sprint_id,task_id) VALUES (?,?)", (sid, tid))
        conn.commit()
        conn.close()
    return {**sprint, 'id': sid, 'status': 'active', 'created_at': now}

def db_update_sprint(cid, sprint_id, updates):
    allowed = {'title','goal','start_date','end_date','status','retro'}
    fields = {k: v for k, v in updates.items() if k in allowed}
    if not fields:
        return
    set_clause = ', '.join(f"{k}=?" for k in fields)
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        conn.execute(f"UPDATE sprints SET {set_clause} WHERE id=? AND company_id=?",
                     (*fields.values(), sprint_id, cid))
        if 'task_ids' in updates:
            conn.execute("DELETE FROM sprint_tasks WHERE sprint_id=?", (sprint_id,))
            for tid in updates['task_ids']:
                conn.execute("INSERT OR IGNORE INTO sprint_tasks (sprint_id,task_id) VALUES (?,?)", (sprint_id, tid))
        conn.commit()
        conn.close()

def db_get_sprint_tasks(cid, sprint_id):
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        rows = conn.execute(
            "SELECT bt.* FROM board_tasks bt JOIN sprint_tasks st ON bt.id=st.task_id WHERE st.sprint_id=? AND bt.company_id=?",
            (sprint_id, cid)).fetchall()
        conn.close()
    return [dict(r) for r in rows]

def db_link_task_to_sprint(cid, sprint_id, task_id):
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        conn.execute("INSERT OR IGNORE INTO sprint_tasks (sprint_id,task_id) VALUES (?,?)", (sprint_id, task_id))
        conn.commit()
        conn.close()

# ─── Wiki / Knowledge Base ───

def db_get_wiki_pages(cid, category=None):
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        if category:
            rows = conn.execute("SELECT * FROM wiki_pages WHERE company_id=? AND category=? ORDER BY updated_at DESC", (cid, category)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM wiki_pages WHERE company_id=? ORDER BY updated_at DESC", (cid,)).fetchall()
        conn.close()
    return [dict(r) for r in rows]

def db_get_wiki_page(cid, page_id):
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        row = conn.execute("SELECT * FROM wiki_pages WHERE id=? AND company_id=?", (page_id, cid)).fetchone()
        conn.close()
    return dict(row) if row else None

def db_save_wiki_page(cid, page):
    import uuid
    pid = page.get('id') or str(uuid.uuid4())[:8]
    now = datetime.utcnow().isoformat()
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        conn.execute(
            "INSERT OR REPLACE INTO wiki_pages (id,company_id,title,content,author,category,created_at,updated_at) VALUES (?,?,?,?,?,?,COALESCE((SELECT created_at FROM wiki_pages WHERE id=?),?),?)",
            (pid, cid, page.get('title',''), page.get('content',''), page.get('author',''),
             page.get('category','general'), pid, now, now))
        conn.commit()
        conn.close()
    return {**page, 'id': pid, 'updated_at': now}

def db_delete_wiki_page(cid, page_id):
    with _get_lock(cid):
        conn = _conn(cid)
        conn.execute("DELETE FROM wiki_pages WHERE id=? AND company_id=?", (page_id, cid))
        conn.commit()
        conn.close()

# ─── Meeting Notes ───

def db_get_meetings(cid):
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        rows = conn.execute("SELECT * FROM meeting_notes WHERE company_id=? ORDER BY created_at DESC", (cid,)).fetchall()
        conn.close()
    results = []
    for r in rows:
        d = dict(r)
        for k in ('participants', 'action_items'):
            try: d[k] = json.loads(d[k]) if d[k] else []
            except: d[k] = []
        results.append(d)
    return results

def db_add_meeting(cid, meeting):
    import uuid
    mid = meeting.get('id') or str(uuid.uuid4())[:8]
    now = datetime.utcnow().isoformat()
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        conn.execute(
            "INSERT INTO meeting_notes (id,company_id,topic,participants,decisions,action_items,summary,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (mid, cid, meeting.get('topic',''),
             json.dumps(meeting.get('participants',[]), ensure_ascii=False),
             meeting.get('decisions',''),
             json.dumps(meeting.get('action_items',[]), ensure_ascii=False),
             meeting.get('summary',''), now))
        conn.commit()
        conn.close()
    return {**meeting, 'id': mid, 'created_at': now}

# ─── Milestones ───

def db_get_milestones(cid):
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        rows = conn.execute("SELECT * FROM milestones WHERE company_id=? ORDER BY deadline, created_at", (cid,)).fetchall()
        conn.close()
    results = []
    for r in rows:
        d = dict(r)
        try: d['linked_tasks'] = json.loads(d['linked_tasks']) if d['linked_tasks'] else []
        except: d['linked_tasks'] = []
        results.append(d)
    return results

def db_add_milestone(cid, ms):
    import uuid
    mid = ms.get('id') or str(uuid.uuid4())[:8]
    now = datetime.utcnow().isoformat()
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        conn.execute(
            "INSERT INTO milestones (id,company_id,title,description,deadline,status,linked_tasks,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (mid, cid, ms.get('title',''), ms.get('description',''), ms.get('deadline',''),
             ms.get('status','pending'), json.dumps(ms.get('linked_tasks',[]),ensure_ascii=False), now))
        conn.commit()
        conn.close()
    return {**ms, 'id': mid, 'created_at': now}

def db_update_milestone(cid, mid, updates):
    allowed = {'title','description','deadline','status','linked_tasks'}
    fields = {k: v for k, v in updates.items() if k in allowed}
    if 'linked_tasks' in fields:
        fields['linked_tasks'] = json.dumps(fields['linked_tasks'], ensure_ascii=False)
    if not fields: return
    set_clause = ', '.join(f"{k}=?" for k in fields)
    with _get_lock(cid):
        conn = _conn(cid)
        conn.execute(f"UPDATE milestones SET {set_clause} WHERE id=? AND company_id=?", (*fields.values(), mid, cid))
        conn.commit()
        conn.close()

def db_delete_milestone(cid, mid):
    with _get_lock(cid):
        conn = _conn(cid)
        conn.execute("DELETE FROM milestones WHERE id=? AND company_id=?", (mid, cid))
        conn.commit()
        conn.close()

# ─── Risks ───

def db_get_risks(cid):
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        rows = conn.execute("SELECT * FROM risks WHERE company_id=? ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, created_at DESC", (cid,)).fetchall()
        conn.close()
    return [dict(r) for r in rows]

def db_add_risk(cid, risk):
    import uuid
    rid = risk.get('id') or str(uuid.uuid4())[:8]
    now = datetime.utcnow().isoformat()
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        conn.execute(
            "INSERT INTO risks (id,company_id,title,description,severity,status,owner,mitigation,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rid, cid, risk.get('title',''), risk.get('description',''), risk.get('severity','medium'),
             'open', risk.get('owner',''), risk.get('mitigation',''), now, now))
        conn.commit()
        conn.close()
    return {**risk, 'id': rid, 'status': 'open', 'created_at': now}

def db_update_risk(cid, rid, updates):
    allowed = {'title','description','severity','status','owner','mitigation'}
    fields = {k: v for k, v in updates.items() if k in allowed}
    if not fields: return
    fields['updated_at'] = datetime.utcnow().isoformat()
    set_clause = ', '.join(f"{k}=?" for k in fields)
    with _get_lock(cid):
        conn = _conn(cid)
        conn.execute(f"UPDATE risks SET {set_clause} WHERE id=? AND company_id=?", (*fields.values(), rid, cid))
        conn.commit()
        conn.close()

def db_delete_risk(cid, rid):
    with _get_lock(cid):
        conn = _conn(cid)
        conn.execute("DELETE FROM risks WHERE id=? AND company_id=?", (rid, cid))
        conn.commit()
        conn.close()

# ─── Announcements ───

def db_get_announcements(cid):
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        rows = conn.execute("SELECT * FROM announcements WHERE company_id=? ORDER BY pinned DESC, created_at DESC", (cid,)).fetchall()
        conn.close()
    return [dict(r) for r in rows]

def db_add_announcement(cid, ann):
    import uuid
    aid = ann.get('id') or str(uuid.uuid4())[:8]
    now = datetime.utcnow().isoformat()
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        conn.execute("INSERT INTO announcements (id,company_id,title,content,author,pinned,created_at) VALUES (?,?,?,?,?,?,?)",
            (aid, cid, ann.get('title',''), ann.get('content',''), ann.get('author',''), 1 if ann.get('pinned') else 0, now))
        conn.commit(); conn.close()
    return {**ann, 'id': aid, 'created_at': now}

def db_delete_announcement(cid, aid):
    with _get_lock(cid):
        conn = _conn(cid)
        conn.execute("DELETE FROM announcements WHERE id=? AND company_id=?", (aid, cid))
        conn.commit(); conn.close()

# ─── Work Journals ───

def db_get_journals(cid, date=None, agent_id=None):
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        q = "SELECT * FROM work_journals WHERE company_id=?"
        params = [cid]
        if date: q += " AND date=?"; params.append(date)
        if agent_id: q += " AND agent_id=?"; params.append(agent_id)
        rows = conn.execute(q + " ORDER BY date DESC, agent_id", params).fetchall()
        conn.close()
    return [dict(r) for r in rows]

def db_add_journal(cid, j):
    import uuid
    jid = j.get('id') or str(uuid.uuid4())[:8]
    now = datetime.utcnow().isoformat()
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        conn.execute("INSERT OR REPLACE INTO work_journals (id,company_id,agent_id,date,tasks_done,tasks_next,issues,notes,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (jid, cid, j.get('agent_id',''), j.get('date',''), j.get('tasks_done',''), j.get('tasks_next',''), j.get('issues',''), j.get('notes',''), now))
        conn.commit(); conn.close()
    return {**j, 'id': jid, 'created_at': now}

# ─── Policies ───

def db_get_policies(cid):
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        rows = conn.execute("SELECT * FROM policies WHERE company_id=? ORDER BY category, created_at", (cid,)).fetchall()
        conn.close()
    return [dict(r) for r in rows]

def db_add_policy(cid, p):
    import uuid
    pid = p.get('id') or str(uuid.uuid4())[:8]
    now = datetime.utcnow().isoformat()
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        conn.execute("INSERT OR REPLACE INTO policies (id,company_id,title,content,category,effective_date,created_at,updated_at) VALUES (?,?,?,?,?,?,COALESCE((SELECT created_at FROM policies WHERE id=?),?),?)",
            (pid, cid, p.get('title',''), p.get('content',''), p.get('category','general'), p.get('effective_date',''), pid, now, now))
        conn.commit(); conn.close()
    return {**p, 'id': pid}

def db_delete_policy(cid, pid):
    with _get_lock(cid):
        conn = _conn(cid)
        conn.execute("DELETE FROM policies WHERE id=? AND company_id=?", (pid, cid))
        conn.commit(); conn.close()

# ─── Budgets ───

def db_get_budgets(cid):
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        rows = conn.execute("SELECT * FROM budgets WHERE company_id=? ORDER BY department", (cid,)).fetchall()
        conn.close()
    return [dict(r) for r in rows]

def db_set_budget(cid, b):
    import uuid
    bid = b.get('id') or str(uuid.uuid4())[:8]
    now = datetime.utcnow().isoformat()
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        conn.execute("INSERT OR REPLACE INTO budgets (id,company_id,agent_id,department,allocated,spent,period,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (bid, cid, b.get('agent_id',''), b.get('department',''), b.get('allocated',0), b.get('spent',0), b.get('period',''), now))
        conn.commit(); conn.close()
    return {**b, 'id': bid}

# ─── Votes ───

def db_get_votes(cid):
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        rows = conn.execute("SELECT * FROM votes WHERE company_id=? ORDER BY created_at DESC", (cid,)).fetchall()
        conn.close()
    results = []
    for r in rows:
        d = dict(r)
        for k in ('options','voters'):
            try: d[k] = json.loads(d[k]) if d[k] else []
            except: d[k] = []
        try: d['results'] = json.loads(d['results']) if d['results'] else {}
        except: d['results'] = {}
        results.append(d)
    return results

def db_add_vote(cid, v):
    import uuid
    vid = v.get('id') or str(uuid.uuid4())[:8]
    now = datetime.utcnow().isoformat()
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        conn.execute("INSERT INTO votes (id,company_id,title,description,options,results,voters,status,deadline,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (vid, cid, v.get('title',''), v.get('description',''), json.dumps(v.get('options',[]),ensure_ascii=False),
             '{}', '[]', 'open', v.get('deadline',''), now))
        conn.commit(); conn.close()
    return {**v, 'id': vid, 'status': 'open', 'created_at': now}

def db_cast_vote(cid, vote_id, voter, choice):
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        row = conn.execute("SELECT results,voters FROM votes WHERE id=? AND company_id=?", (vote_id, cid)).fetchone()
        if not row: conn.close(); return False
        results = json.loads(row['results'] or '{}')
        voters = json.loads(row['voters'] or '[]')
        if voter in voters: conn.close(); return False
        results[choice] = results.get(choice, 0) + 1
        voters.append(voter)
        conn.execute("UPDATE votes SET results=?, voters=? WHERE id=? AND company_id=?",
            (json.dumps(results,ensure_ascii=False), json.dumps(voters,ensure_ascii=False), vote_id, cid))
        conn.commit(); conn.close()
    return True

# ─── Audit Log ───

def db_add_audit(cid, action, actor='', target='', detail=''):
    now = datetime.utcnow().isoformat()
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        conn.execute("INSERT INTO audit_log (company_id,action,actor,target,detail,created_at) VALUES (?,?,?,?,?,?)",
            (cid, action, actor, target, detail[:500], now))
        conn.commit(); conn.close()

def db_get_audit(cid, limit=100):
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        rows = conn.execute("SELECT * FROM audit_log WHERE company_id=? ORDER BY id DESC LIMIT ?", (cid, limit)).fetchall()
        conn.close()
    return [dict(r) for r in rows]

# ─── Memory Stream (Stanford GenAgents style) ───

def db_add_memory(cid, agent_id, content, importance=5, mem_type='observation'):
    now = datetime.utcnow().isoformat()
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        conn.execute("INSERT INTO memory_stream (company_id,agent_id,content,importance,mem_type,created_at) VALUES (?,?,?,?,?,?)",
            (cid, agent_id, content[:500], importance, mem_type, now))
        # Keep max 100 memories per agent
        conn.execute("DELETE FROM memory_stream WHERE company_id=? AND agent_id=? AND id NOT IN (SELECT id FROM memory_stream WHERE company_id=? AND agent_id=? ORDER BY id DESC LIMIT 100)", (cid, agent_id, cid, agent_id))
        conn.commit(); conn.close()

def db_get_memories(cid, agent_id, query='', limit=10):
    """Get memories weighted by recency × importance × relevance (keyword matching)."""
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        rows = conn.execute(
            "SELECT content, importance, mem_type, created_at, id FROM memory_stream "
            "WHERE company_id=? AND agent_id=? ORDER BY id DESC LIMIT 50",
            (cid, agent_id)).fetchall()
        conn.close()
    if not rows:
        return []
    import math
    now_ts = datetime.utcnow().timestamp()
    keywords = set(query.lower().split()) if query else set()
    scored = []
    for r in rows:
        d = dict(r)
        # Recency: exponential decay (0.995^hours)
        try:
            created = datetime.fromisoformat(d['created_at']).timestamp()
            hours = max(0, (now_ts - created) / 3600)
        except: hours = 24
        recency = math.pow(0.995, hours)
        # Importance: normalized 0-1
        importance = (d.get('importance', 5) or 5) / 10.0
        # Relevance: keyword overlap ratio
        if keywords:
            content_words = set(d['content'].lower().split())
            overlap = len(keywords & content_words)
            relevance = overlap / max(len(keywords), 1)
        else:
            relevance = 0.5  # neutral when no query
        score = recency * importance * max(relevance, 0.1)
        d['_score'] = round(score, 4)
        scored.append(d)
    scored.sort(key=lambda x: x['_score'], reverse=True)
    return scored[:limit]

# ─── CRM ───

def db_get_contacts(cid, status=None):
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        if status:
            rows = conn.execute("SELECT * FROM crm_contacts WHERE company_id=? AND status=? ORDER BY updated_at DESC", (cid, status)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM crm_contacts WHERE company_id=? ORDER BY updated_at DESC", (cid,)).fetchall()
        conn.close()
    return [dict(r) for r in rows]

def db_add_contact(cid, c):
    import uuid
    cid_contact = c.get('id') or str(uuid.uuid4())[:8]
    now = datetime.utcnow().isoformat()
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        conn.execute("INSERT OR REPLACE INTO crm_contacts (id,company_id,name,email,phone,company_name,role,status,notes,tags,owner,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,COALESCE((SELECT created_at FROM crm_contacts WHERE id=?),?),?)",
            (cid_contact, cid, c.get('name',''), c.get('email',''), c.get('phone',''), c.get('company_name',''),
             c.get('role',''), c.get('status','lead'), c.get('notes',''), c.get('tags',''), c.get('owner',''), cid_contact, now, now))
        conn.commit(); conn.close()
    return {**c, 'id': cid_contact, 'updated_at': now}

def db_delete_contact(cid, contact_id):
    with _get_lock(cid):
        conn = _conn(cid)
        conn.execute("DELETE FROM crm_contacts WHERE id=? AND company_id=?", (contact_id, cid))
        conn.commit(); conn.close()

# ─── Agent Priorities ───

PRIORITY_CATEGORIES = ['마케팅', '개발', '디자인', '분석', '보고', '기획']

def db_get_priorities(cid):
    """Return priority matrix as {agent_id: {category: priority}}."""
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        rows = conn.execute("SELECT agent_id, category, priority FROM agent_priorities WHERE company_id=?", (cid,)).fetchall()
        conn.close()
    result = {}
    for r in rows:
        d = dict(r)
        aid = d['agent_id']
        if aid not in result:
            result[aid] = {}
        result[aid][d['category']] = d['priority']
    return result

def db_set_priority(cid, agent_id, category, priority):
    """Set a single priority cell. priority=3 is default, 0=disabled."""
    priority = max(0, min(5, int(priority)))
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        conn.execute(
            "INSERT OR REPLACE INTO agent_priorities (company_id, agent_id, category, priority) VALUES (?,?,?,?)",
            (cid, agent_id, category, priority))
        conn.commit(); conn.close()
    return {'agent_id': agent_id, 'category': category, 'priority': priority}

def db_init_default_priorities(cid, agent_ids):
    """Initialize default priorities (3) for all agents × categories if not already set."""
    with _get_lock(cid):
        _ensure_company_db(cid)
        conn = _conn(cid)
        for aid in agent_ids:
            for cat in PRIORITY_CATEGORIES:
                conn.execute(
                    "INSERT OR IGNORE INTO agent_priorities (company_id, agent_id, category, priority) VALUES (?,?,?,3)",
                    (cid, aid, cat))
        conn.commit(); conn.close()

# ─── Migration ───

def migrate_from_json():
    """One-time migration from JSON files to SQLite."""
    if DB_PATH.exists() and DB_PATH.stat().st_size > 0:
        print("[db] SQLite DB exists, skipping JSON migration")
        return
    init_db()
    companies_file = DATA / "companies.json"
    if not companies_file.exists():
        return
    try:
        companies = json.loads(companies_file.read_text(encoding='utf-8') or '[]')
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        print(f"[db] Migration JSON read failed: {e}")
        return
    migrated = 0
    for comp in companies:
        cid = comp.get('id', '')
        if not cid:
            continue
        db_save_company(comp)
        migrated += 1
    print(f"[db] Migrated {migrated} companies to sharded SQLite")
