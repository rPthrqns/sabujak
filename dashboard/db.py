#!/usr/bin/env python3
"""SQLite database layer for AI Company Hub."""
import sqlite3, json, os, threading
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "hub.db"
_lock = threading.RLock()

def _conn():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn

def init_db():
    with _lock:
        conn = _conn()
        conn.executescript("""
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
                sort_order INTEGER DEFAULT 0
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
                detail TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                time TEXT DEFAULT '',
                created_at TEXT DEFAULT ''
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
        """)
        conn.commit()
        conn.close()

# ─── Company CRUD ───

def db_get_company(cid):
    with _lock:
        conn = _conn()
        row = conn.execute("SELECT * FROM companies WHERE id=?", (cid,)).fetchone()
        if not row:
            conn.close()
            return None
        company = dict(row)
        company['chat'] = db_get_chat(cid)
        company['board_tasks'] = db_get_tasks(cid)
        company['approvals'] = db_get_approvals(cid)
        company['activity_log'] = db_get_activity(cid)
        conn.close()
        # Merge with stored data for fields not in schema
        extra = json.loads(company.pop('data', '{}'))
        company.update({k: v for k, v in extra.items() if k not in company})
        return company

def db_save_company(company):
    if not company or 'id' not in company:
        return None
    cid = company['id']
    with _lock:
        conn = _conn()
        # Extract DB-managed fields
        db_fields = {'id', 'name', 'topic', 'lang', 'status', 'created_at', 'budget', 'data'}
        data = {k: v for k, v in company.items() if k not in db_fields and k not in ('chat', 'board_tasks', 'approvals', 'activity_log')}
        conn.execute("""INSERT OR REPLACE INTO companies (id, name, topic, lang, status, created_at, budget, data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (cid, company.get('name',''), company.get('topic',''), company.get('lang','ko'),
             company.get('status','starting'), company.get('created_at',''),
             company.get('budget', 10.0), json.dumps(data, ensure_ascii=False)))
        # Save chat
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
    with _lock:
        company = db_get_company(cid)
        if not company:
            return None
        company.update(updates)
        db_save_company(company)
        return company

def db_get_all_companies():
    with _lock:
        conn = _conn()
        company_rows = conn.execute("SELECT * FROM companies ORDER BY created_at DESC").fetchall()
        if not company_rows:
            conn.close()
            return []
        ids = [row['id'] for row in company_rows]
        placeholders = ','.join('?' for _ in ids)

        chat_rows = conn.execute(
            f"SELECT * FROM chat_messages WHERE company_id IN ({placeholders}) ORDER BY company_id, sort_order, id",
            ids,
        ).fetchall()
        task_rows = conn.execute(
            f"SELECT * FROM board_tasks WHERE company_id IN ({placeholders}) ORDER BY company_id, sort_order, id",
            ids,
        ).fetchall()
        approval_rows = conn.execute(
            f"SELECT * FROM approvals WHERE company_id IN ({placeholders}) ORDER BY company_id, created_at DESC, id DESC",
            ids,
        ).fetchall()
        activity_rows = conn.execute(
            f"SELECT * FROM activity_log WHERE company_id IN ({placeholders}) ORDER BY company_id, id DESC",
            ids,
        ).fetchall()
        conn.close()

    chats = {}
    for r in chat_rows:
        chats.setdefault(r['company_id'], []).append({
            'from': r['from_field'], 'emoji': r['emoji'], 'text': r['text'],
            'time': r['time'], 'type': r['msg_type'], 'mention': bool(r['mention']),
            'to': r['to_field']
        })

    tasks = {}
    for r in task_rows:
        tasks.setdefault(r['company_id'], []).append({
            'id': r['id'], 'title': r['title'], 'agent_id': r['agent_id'],
            'status': r['status'], 'depends_on': json.loads(r['depends_on']),
            'deadline': r['deadline'], 'created_at': r['created_at'],
            'updated_at': r['updated_at']
        })

    approvals = {}
    for r in approval_rows:
        approvals.setdefault(r['company_id'], []).append({
            'id': r['id'], 'from_agent': r['from_agent'], 'from_emoji': r['from_emoji'],
            'type': r['approval_type'], 'detail': r['detail'], 'status': r['status'],
            'time': r['time'], 'created_at': r['created_at']
        })

    activities = {}
    for r in activity_rows:
        bucket = activities.setdefault(r['company_id'], [])
        if len(bucket) < 50:
            bucket.append({'time': r['time'], 'agent': r['agent'], 'text': r['text']})

    companies = []
    for row in company_rows:
        company = dict(row)
        extra = json.loads(company.pop('data', '{}'))
        company.update({k: v for k, v in extra.items() if k not in company})
        cid = company['id']
        company['chat'] = chats.get(cid, [])
        company['board_tasks'] = tasks.get(cid, [])
        company['approvals'] = approvals.get(cid, [])
        company['activity_log'] = activities.get(cid, [])
        companies.append(company)
    return companies

def db_delete_company(cid):
    with _lock:
        conn = _conn()
        conn.execute("DELETE FROM companies WHERE id=?", (cid,))
        conn.execute("DELETE FROM chat_messages WHERE company_id=?", (cid,))
        conn.execute("DELETE FROM board_tasks WHERE company_id=?", (cid,))
        conn.execute("DELETE FROM approvals WHERE company_id=?", (cid,))
        conn.execute("DELETE FROM activity_log WHERE company_id=?", (cid,))
        conn.commit()
        conn.close()

# ─── Chat ───

def db_get_chat(cid):
    with _lock:
        conn = _conn()
        # Keep last 200 messages, delete older ones
        count = conn.execute("SELECT COUNT(*) FROM chat_messages WHERE company_id=?", (cid,)).fetchone()[0]
        if count > 200:
            conn.execute("DELETE FROM chat_messages WHERE company_id=? AND id NOT IN (SELECT id FROM chat_messages WHERE company_id=? ORDER BY id DESC LIMIT 200)", (cid, cid))
        rows = conn.execute("SELECT * FROM chat_messages WHERE company_id=? ORDER BY sort_order, id", (cid,)).fetchall()
        conn.close()
    return [{'from': r['from_field'], 'emoji': r['emoji'], 'text': r['text'],
             'time': r['time'], 'type': r['msg_type'], 'mention': bool(r['mention']),
             'to': r['to_field']} for r in rows]

def db_add_chat(cid, msg):
    with _lock:
        conn = _conn()
        count = conn.execute("SELECT COUNT(*) FROM chat_messages WHERE company_id=?", (cid,)).fetchone()[0]
        conn.execute("""INSERT INTO chat_messages 
            (company_id, from_field, emoji, text, time, msg_type, mention, to_field, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (cid, msg.get('from',''), msg.get('emoji',''), msg.get('text',''),
             msg.get('time',''), msg.get('type','user'),
             1 if msg.get('mention') else 0, msg.get('to',''), count))
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

# ─── Board Tasks ───

def db_get_tasks(cid):
    with _lock:
        conn = _conn()
        rows = conn.execute("SELECT * FROM board_tasks WHERE company_id=? ORDER BY sort_order, id", (cid,)).fetchall()
        conn.close()
    return [{'id': r['id'], 'title': r['title'], 'agent_id': r['agent_id'],
             'status': r['status'], 'depends_on': json.loads(r['depends_on']),
             'deadline': r['deadline'], 'created_at': r.get('created_at',''),
             'updated_at': r.get('updated_at','')} for r in rows]

def db_add_task(cid, task):
    with _lock:
        conn = _conn()
        count = conn.execute("SELECT COUNT(*) FROM board_tasks WHERE company_id=?", (cid,)).fetchone()[0]
        conn.execute("""INSERT OR REPLACE INTO board_tasks 
            (id, company_id, title, agent_id, status, depends_on, deadline, created_at, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (task.get('id',''), cid, task.get('title',''), task.get('agent_id',''),
             task.get('status','대기'), json.dumps(task.get('depends_on',[])),
             task.get('deadline',''), task.get('created_at',''), count))
        conn.commit()
        conn.close()

def _save_tasks(conn, cid, tasks):
    conn.execute("DELETE FROM board_tasks WHERE company_id=?", (cid,))
    for i, t in enumerate(tasks):
        conn.execute("""INSERT OR REPLACE INTO board_tasks 
            (id, company_id, title, agent_id, status, depends_on, deadline, created_at, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (t.get('id', f"task-{i}"), cid, t.get('title',''), t.get('agent_id',''),
             t.get('status','대기'), json.dumps(t.get('depends_on',[])),
             t.get('deadline',''), t.get('created_at',''), i))

# ─── Approvals ───

def db_get_approvals(cid, status=None):
    with _lock:
        conn = _conn()
        if status:
            rows = conn.execute("SELECT * FROM approvals WHERE company_id=? AND status=? ORDER BY created_at DESC", (cid, status)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM approvals WHERE company_id=? ORDER BY created_at DESC", (cid,)).fetchall()
        conn.close()
    return [{'id': r['id'], 'from_agent': r['from_agent'], 'from_emoji': r['from_emoji'],
             'type': r['approval_type'], 'detail': r['detail'], 'status': r['status'],
             'time': r['time'], 'created_at': r['created_at']} for r in rows]

def db_update_approval(cid, aid, updates):
    with _lock:
        conn = _conn()
        sets = ', '.join(f"{k}=?" for k in updates)
        vals = list(updates.values()) + [aid, cid]
        conn.execute(f"UPDATE approvals SET {sets} WHERE id=? AND company_id=?", vals)
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
    with _lock:
        conn = _conn()
        rows = conn.execute("SELECT * FROM activity_log WHERE company_id=? ORDER BY id DESC LIMIT 50", (cid,)).fetchall()
        conn.close()
    return [{'time': r['time'], 'agent': r['agent'], 'text': r['text']} for r in rows]

def db_add_activity(cid, entry):
    with _lock:
        conn = _conn()
        conn.execute("INSERT INTO activity_log (company_id, agent, text, time) VALUES (?, ?, ?, ?)",
            (cid, entry.get('agent',''), entry.get('text',''), entry.get('time','')))
        conn.commit()
        conn.close()

def _save_activity(conn, cid, entries):
    conn.execute("DELETE FROM activity_log WHERE company_id=?", (cid,))
    for e in entries[-50:]:
        conn.execute("INSERT INTO activity_log (company_id, agent, text, time) VALUES (?, ?, ?, ?)",
            (cid, e.get('agent',''), e.get('text',''), e.get('time','')))

# ─── Migration ───


# ─── Document Cache ───
_doc_cache = {}  # {(cid, doc_type, agent_id): content}

def db_get_doc(cid, doc_type, agent_id=''):
    key = (cid, doc_type, agent_id)
    if key in _doc_cache:
        return _doc_cache[key]
    with _lock:
        conn = _conn()
        row = conn.execute("SELECT content FROM documents WHERE company_id=? AND doc_type=? AND agent_id=?",
                          (cid, doc_type, agent_id)).fetchone()
        conn.close()
    content = row['content'] if row else ''
    _doc_cache[key] = content
    return content

def db_save_doc(cid, doc_type, agent_id, content):
    key = (cid, doc_type, agent_id)
    _doc_cache[key] = content
    now = datetime.now().isoformat()
    with _lock:
        conn = _conn()
        conn.execute("INSERT OR REPLACE INTO documents (id, company_id, doc_type, agent_id, content, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (f"{cid}-{doc_type}-{agent_id}", cid, doc_type, agent_id, content, now))
        conn.commit()
        conn.close()

def db_clear_doc_cache():
    _doc_cache.clear()

def migrate_from_json():
    """One-time migration from JSON files to SQLite."""
    if DB_PATH.exists() and DB_PATH.stat().st_size > 0:
        print("[db] SQLite DB exists, skipping migration")
        return
    init_db()
    from pathlib import Path
    DATA = Path(__file__).parent.parent / "data"
    companies_file = DATA / "companies.json"
    if not companies_file.exists():
        return
    try:
        companies = json.loads(companies_file.read_text(encoding='utf-8') or '[]')
    except:
        return
    migrated = 0
    for comp in companies:
        cid = comp.get('id', '')
        if not cid: continue
        db_save_company(comp)
        migrated += 1
    print(f"[db] Migrated {migrated} companies to SQLite")
