#!/usr/bin/env python3
"""AI Company Hub - Multi-company Dashboard Server
Enhanced with Goals, Kanban Board, Cost Tracking, Approval Gates, and Task Dependencies."""
import asyncio, hmac, json, os, re, http.server, socketserver, subprocess, threading, time, urllib.request, urllib.parse, uuid
from pathlib import Path
from datetime import datetime
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime import get_runtime

# FastAPI / async server stack
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from db import (init_db, migrate_from_json, db_get_company, db_save_company, db_update_company,
               db_get_all_companies, db_delete_company, db_add_chat, db_add_chats, db_add_activity, db_add_activities,
               db_add_approval, db_get_approvals, db_update_approval, db_get_tasks, db_add_task, db_update_task, db_delete_task,
               db_search_chat, db_get_webhook_routes, db_add_webhook_route, db_delete_webhook_route,
               db_create_snapshot, db_get_snapshots, db_get_snapshot, db_delete_snapshot,
               db_get_doc, db_save_doc, db_clear_doc_cache,
               db_get_plan_tasks, db_add_plan_task, db_update_plan_task, db_delete_plan_task)

# ─── Constants ───
PORT = 3000
BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
COMPANIES_FILE = DATA / "companies.json"
SSE_CLIENTS = []          # kept for backward compat reference; not used by FastAPI SSE
SSE_LOCK = threading.Lock()
SSE_QUEUES: list = []     # asyncio.Queue per SSE client
SSE_QUEUES_LOCK = threading.Lock()
_EVENT_LOOP: asyncio.AbstractEventLoop | None = None
AGENT_LOCK = threading.Lock()
_COMPANY_LOCKS = {}
_COMPANY_LOCKS_MUTEX = threading.Lock()

def _get_company_lock(cid):
    with _COMPANY_LOCKS_MUTEX:
        if cid not in _COMPANY_LOCKS:
            _COMPANY_LOCKS[cid] = threading.Lock()
        return _COMPANY_LOCKS[cid]
_running_task_threads = set()

# ─── Runtime Abstraction ───
RUNTIME = get_runtime('openclaw')

def _reset_stuck_agents():
    """서버 시작 시 working 상태인 에이전트를 active로 리셋"""
    try:
        for f in sorted(DATA.glob('company-*.json')):
            if '-queue' in f.stem:
                continue
            cid = f.stem
            if cid.startswith('company-'):
                c = get_company(cid)
                if c:
                    changed = False
                    for a in c.get('agents', []):
                        if a.get('status') == 'working':
                            a['status'] = 'active'
                            changed = True
                    if changed:
                        save_company(c)
                        print(f"[reset] {cid}: stuck agents reset to active")
    except Exception as e:
        print(f"[reset] error: {e}")

# Default agent templates per role
AGENT_TEMPLATES = {
    "ceo": {"name": "CEO", "role": {"ko":"총괄","en":"Executive","ja":"総責任者","zh":"总负责人"}, "emoji": "👔"},
    "cmo": {"name": "CMO", "role": {"ko":"마케팅","en":"Marketing","ja":"マーケティング","zh":"市场"}, "emoji": "📈"},
    "cto": {"name": "CTO", "role": {"ko":"기술/개발","en":"Tech/Dev","ja":"技術/開発","zh":"技术/开发"}, "emoji": "💻"},
    "cfo": {"name": "CFO", "role": {"ko":"재무","en":"Finance","ja":"財務","zh":"财务"}, "emoji": "💰"},
    "designer": {"name": "Designer", "role": {"ko":"디자인","en":"Design","ja":"デザイン","zh":"设计"}, "emoji": "🎨"},
    "hr": {"name": "HR", "role": {"ko":"인사","en":"HR","ja":"人事","zh":"人事"}, "emoji": "🤝"},
    "sales": {"name": "Sales", "role": {"ko":"영업","en":"Sales","ja":"営業","zh":"销售"}, "emoji": "📊"},
    "legal": {"name": "Legal", "role": {"ko":"법무","en":"Legal","ja":"法務","zh":"法务"}, "emoji": "⚖️"},
    "support": {"name": "Support", "role": {"ko":"고객지원","en":"Support","ja":"サポート","zh":"客服"}, "emoji": "🎧"},
}

TOPIC_ORGS = {
    "default": ["ceo", "cmo", "cto"],
    "marketing": ["ceo", "cmo", "designer", "cto"],
    "development": ["ceo", "cto", "designer"],
    "ecommerce": ["ceo", "cmo", "cto", "sales", "support"],
    "finance": ["ceo", "cfo", "legal"],
    "recruitment": ["ceo", "hr", "cmo"],
    "restaurant": ["ceo", "cmo", "designer"],
    "education": ["ceo", "cmo", "cto", "support"],
    "healthcare": ["ceo", "cfo", "legal", "cmo"],
    "realestate": ["ceo", "sales", "cmo", "legal", "cto"],
}

LANG = {"ko":"한국어","en":"English","ja":"日本語","zh":"中文"}

ROLE_MAP = {
    'ceo': ('CEO', '최고경영자', '👔'), 'cto': ('CTO', '기술총괄', '💻'),
    'cfo': ('CFO', '재무총괄', '💰'), 'coo': ('COO', '운영총괄', '⚙️'),
    'cmo': ('CMO', '마케팅총괄', '📢'), 'cpo': ('CPO', '제품총괄', '📦'),
    'chro': ('CHRO', '인사총괄', '👥'), 'cso': ('CSO', '영업총괄', '🤝'),
    'designer': ('디자이너', 'UI/UX 디자인', '🎨'), 'developer': ('개발자', '프론트엔드/백엔드', '👨‍💻'),
    'sales': ('영업팀', '영업 및 고객관계', '📊'), 'support': ('고객지원', '고객 서비스', '🎧'),
    'marketing': ('마케팅팀', '디지털 마케팅', '📣'), 'hr': ('인사팀', '인사 및 채용', '📋'),
    'legal': ('법무팀', '법무 및 컴플라이언스', '⚖️'), 'data': ('데이터팀', '데이터 분석', '📈'),
    'pr': ('홍보팀', '홍보 및 PR', '🎤'), 'planner': ('기획자', '서비스 기획', '📝'),
}

# ─── Task Status Constants ───
TASK_STATUSES = ["대기", "진행중", "완료", "검토"]
VALID_TASK_TRANSITIONS = {
    "대기": ["진행중"],
    "진행중": ["완료", "대기"],
    "완료": ["검토", "진행중"],
    "검토": ["완료", "진행중"],
}

# ─── Cost / Budget Defaults ───
DEFAULT_BUDGET = 10.0  # USD monthly budget default
COST_PER_1K_TOKENS = 0.003  # rough estimate for cost calculation

# ─── Utility Functions ───

def sse_broadcast(event_type, data):
    """Broadcast SSE event to all connected clients (thread-safe)."""
    msg = f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    with SSE_QUEUES_LOCK:
        dead = []
        for q in SSE_QUEUES:
            try:
                if _EVENT_LOOP is not None and _EVENT_LOOP.is_running():
                    _EVENT_LOOP.call_soon_threadsafe(q.put_nowait, msg)
                else:
                    q.put_nowait(msg)
            except Exception:
                dead.append(q)
        for q in dead:
            SSE_QUEUES.remove(q)

import re

def extract_task_from_instruction(text):
    """멘션 내용에서 작업명을 추출"""
    text = text.strip().strip('"').strip()
    # 대시/불필요한 접두사 제거
    text = re.sub(r'^[\s\-—–·•◆▶"",\']+\s*', '', text)
    if not text: return None
    # 짧은 지시는 그대로
    if len(text) <= 30 and len(text) >= 2:
        return text
    # 명령형 패턴: "~해주세요", "~부탁드립니다" → 앞부분 추출
    m = re.match(r'(.{2,25}?)(?:를|을|해|부탁|작성|수립|준비|보고|제출|검토|확인|수정|업데이트)', text)
    if m:
        title = m.group(1).strip()
        if len(title) >= 2:
            return title
    # 첫 문장 추출
    first = text.split('.')[0].split('\n')[0].strip()
    if len(first) >= 2 and len(first) <= 30:
        return first
    return None

def process_task_commands(cid, text, agent_id):
    """에이전트 응답에서 [TASK_XXX:...] 명령을 파싱해서 칸반에 반영"""
    results = []
    board_tasks = db_get_tasks(cid)
    
    # TASK_ADD:작업명:우선순위
    for m in re.finditer(r'\[TASK_ADD:([^:]+):([^\]]+)\]', text):
        title = m.group(1).strip()
        priority = m.group(2).strip()
        if any(t.get('title','') == title for t in board_tasks):
            results.append(f"⚠️ '{title}' 이미 존재")
            continue
        task = add_board_task(cid, title, agent_id, '대기', [], '')
        if task:
            db_update_task(cid, task['id'], {'title': f"{title} ({priority})"})
            results.append(f"✅ '{title}' 칸반에 추가됨 ({priority})")
    
    # TASK_DONE:작업명
    for m in re.finditer(r'\[TASK_DONE:([^\]]+)\]', text):
        title = m.group(1).strip()
        for t in board_tasks:
            if t.get('title','') == title and t.get('status') != '완료':
                db_update_task(cid, t['id'], {'status': '완료', 'updated_at': datetime.now().isoformat()})
                results.append(f"🎉 '{title}' 완료 처리")
                break
    
    # TASK_START:작업명
    for m in re.finditer(r'\[TASK_START:([^\]]+)\]', text):
        title = m.group(1).strip()
        for t in board_tasks:
            if t.get('title','') == title and t.get('status') == '대기':
                db_update_task(cid, t['id'], {'status': '진행중', 'updated_at': datetime.now().isoformat()})
                results.append(f"🚀 '{title}' 시작")
                break
    
    # TASK_BLOCK:작업명:사유
    for m in re.finditer(r'\[TASK_BLOCK:([^:]+):([^\]]+)\]', text):
        title = m.group(1).strip()
        reason = m.group(2).strip()
        for t in board_tasks:
            if t.get('title','') == title:
                db_update_task(cid, t['id'], {'status': '검토', 'updated_at': datetime.now().isoformat()})
                results.append(f"🚫 '{title}' 검토 필요 ({reason})")
                break
    
    if results:
        print(f"[task_cmds] {agent_id}: {'; '.join(results)}")
    
    # CRON 명령 처리
    company = get_company(cid)
    agent = next((a for a in company.get('agents', []) if a['id'] == agent_id), None)
    
    for m in re.finditer(r'\[CRON_ADD:([^:]+):(\d+):([^\]]+)\]', text):
        title = m.group(1).strip()
        interval = int(m.group(2))
        prompt_text = m.group(3).strip()
        if agent:
            task = add_recurring_task(cid, title, prompt_text, interval, agent['id'], agent['name'], agent['emoji'])
            if task:
                results.append(f"⏰ '{title}' 정기 작업 추가됨 ({interval}분마다)")
    
    for m in re.finditer(r'\[CRON_DEL:([^\]]+)\]', text):
        title = m.group(1).strip()
        company = get_company(cid)
        if company:
            company['recurring_tasks'] = [t for t in company.get('recurring_tasks', []) if t.get('title','') != title]
            update_company(cid, {'recurring_tasks': company['recurring_tasks']})
            results.append(f"🗑️ '{title}' 정기 작업 삭제됨")
    
    if any('CRON' in r for r in results):
        print(f"[cron_cmds] {agent_id}: {'; '.join(results)}")
    
    # 자동 작업 감지: [TASK_XXX] 명령이 없을 때 패턴 기반 자동 추가
    has_task_cmd = bool(re.search(r'\[TASK_', text))
    if not has_task_cmd:
        company = get_company(cid)
        if not company: return results
        board_tasks = db_get_tasks(cid)
        agent = next((a for a in company.get('agents', []) if a['id'] == agent_id), None)
        
        start_patterns = re.findall(r'(?:작성|수립|구축|개발|제작|준비|기획|설계|구현|검토|분석|실행|진행|도입|설정)하?겠습니다[:\s]*([^\n.]{2,30})', text)
        for title in start_patterns:
            title = title.strip().rstrip('.,;')
            if len(title) < 2: continue
            if any(t.get('title','') == title for t in board_tasks): continue
            task = add_board_task(cid, title, agent_id, '진행중', [], '')
            if task:
                results.append(f"📋 '{title}' 자동 추가됨 (진행)")
        
        done_patterns = re.findall(r'(?:완료|완성|마무리|제출|완료했습니다|완성했습니다)\s*(?:하였습니다|했습니다)?[:\s]*([^\n.]{2,30})', text)
        for title in done_patterns:
            title = title.strip().rstrip('.,;')
            for t in board_tasks:
                if title in t.get('title','') and t.get('status') != '완료':
                    db_update_task(cid, t['id'], {'status': '완료', 'updated_at': datetime.now().isoformat()})
                    results.append(f"🎉 '{t['title']}' 완료 처리")
                    break
    
    return results

def _post_local(url, data, retries=3):
    """POST to localhost with retry on connection failure."""
    import time as _t
    payload = json.dumps(data).encode()
    req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
    for attempt in range(retries):
        try:
            urllib.request.urlopen(req, timeout=10)
            return True
        except Exception as e:
            if attempt < retries - 1:
                _t.sleep(1)
            else:
                print(f"[post] failed after {retries} attempts: {e}")
                return False

def split_message(text, max_chars=1500):
    """Split long messages at natural boundaries."""
    if len(text) <= max_chars:
        return [text]
    chunks = []
    paragraphs = text.split('\n\n')
    current = ''
    for para in paragraphs:
        if len(current) + len(para) + 2 > max_chars and current:
            chunks.append(current.strip())
            current = ''
        current += para + '\n\n'
        if len(current) > max_chars:
            lines = current.strip().split('\n')
            current = ''
            line_chunk = ''
            for line in lines:
                if len(line_chunk) + len(line) + 1 > max_chars and line_chunk:
                    chunks.append(line_chunk.strip())
                    line_chunk = ''
                line_chunk += line + '\n'
            current = line_chunk
    if current.strip():
        chunks.append(current.strip())
    return chunks if chunks else [text]

def load_json(path, default=None):
    if path.exists() and path.stat().st_size > 0:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError):
            print(f"[WARN] corrupted JSON: {path}, trying backup...")
            try:
                with open(str(path) + '.bak', 'r', encoding='utf-8') as f:
                    data = json.load(f)
                import shutil
                shutil.copy2(str(path) + '.bak', path)
                return data
            except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                print(f"[WARN] backup also failed, resetting {path}")
                if default is not None:
                    save_json(path, default)
                return default
    return default if default is not None else []

def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        test = json.dumps(data, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        print(f"[WARN] save_json validation failed for {path}: {e}")
        return
    if path.exists() and path.stat().st_size > 0:
        try:
            import shutil
            shutil.copy2(path, str(path) + '.bak')
        except OSError as e:
            print(f"[WARN] backup failed for {path}: {e}")
    # Atomic write via temp file
    import tempfile, os
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(test)
        os.replace(tmp, str(path))
    except OSError as e:
        print(f"[WARN] save_json write failed for {path}: {e}")
        try:
            os.unlink(tmp)
        except OSError:
            pass

def gen_id(prefix="id"):
    """Generate a short unique ID."""
    return f"{prefix}-{datetime.now().strftime('%m%d%H%M%S')}-{uuid.uuid4().hex[:4]}"

# ─── Company Data Access ───

def init_companies():
    """Reset stuck agents on startup, using DB."""
    companies = db_get_all_companies()
    reset_count = 0
    for comp in companies:
        changed = False
        for a in comp.get('agents', []):
            if a.get('status') == 'working':
                a['status'] = 'active'
                changed = True
                reset_count += 1
        if changed:
            db_save_company(comp)
            print(f"[reset] {comp['id']}: {reset_count} stuck agents reset")
    # Also recover from old JSON state files if DB is empty
    if not companies:
        for f in sorted(DATA.glob('company-*.json')):
            if '-queue' in f.stem: continue
            try:
                data = load_json(f)
                if data and 'id' in data:
                    for a in data.get('agents', []):
                        if a.get('status') == 'working':
                            a['status'] = 'active'
                    db_save_company(data)
                    print(f"[init] recovered {data['id']} from {f.name}")
            except Exception as e:
                print(f"[init] failed to recover {f.name}: {e}")
    print(f"[init] {len(db_get_all_companies())} companies ready")
    return db_get_all_companies()

def get_company(cid):
    return db_get_company(cid)

def save_company(company):
    if not company or 'id' not in company:
        return None
    cid = company['id']
    db_save_company(company)
    sse_broadcast('company_update', {"id": cid, "company": company})
    return company

def update_company(cid, updates):
    company = db_update_company(cid, updates)
    if company:
        sse_broadcast('company_update', {"id": cid, "company": company})
    return company


def append_chat(cid, msg, broadcast=True):
    db_add_chat(cid, msg)
    company = get_company(cid)
    if company:
        sse_broadcast('company_update', {"id": cid, "company": company})
    if broadcast:
        sse_broadcast('chat', {'msg': msg})
    return company


def append_chats(cid, messages, broadcast=True):
    db_add_chats(cid, messages)
    company = get_company(cid)
    if company:
        sse_broadcast('company_update', {"id": cid, "company": company})
    if broadcast:
        for msg in messages:
            sse_broadcast('chat', {'msg': msg})
    return company


def append_activity(cid, entry):
    db_add_activity(cid, entry)
    company = get_company(cid)
    if company:
        sse_broadcast('company_update', {"id": cid, "company": company})
    return company


def append_activities(cid, entries):
    db_add_activities(cid, entries)
    company = get_company(cid)
    if company:
        sse_broadcast('company_update', {"id": cid, "company": company})
    return company


def append_approval(cid, approval):
    db_add_approval(cid, approval)
    company = get_company(cid)
    if company:
        sse_broadcast('company_update', {"id": cid, "company": company})
        sse_broadcast('approval', {'approval': approval})
    return company

# ─── Goal System ───

def get_goals(cid):
    company = get_company(cid)
    return company.get('goals', []) if company else []

def compute_goal_progress(cid, goal):
    """Compute goal progress from linked board_tasks completion rate."""
    company = get_company(cid)
    if not company:
        return 0
    board_tasks = company.get('board_tasks', [])
    task_ids = goal.get('task_ids', [])
    if not task_ids:
        return 0
    linked = [t for t in board_tasks if t['id'] in task_ids]
    if not linked:
        return 0
    completed = sum(1 for t in linked if t.get('status') == '완료')
    return round(completed / len(linked) * 100)

def add_goal(cid, title, task_ids=None):
    company = get_company(cid)
    if not company:
        return None
    goals = company.get('goals', [])
    goal = {
        'id': gen_id('goal'),
        'title': title,
        'status': 'active',
        'task_ids': task_ids or [],
    }
    goals.append(goal)
    update_company(cid, {'goals': goals})
    return goal

def update_goal(cid, goal_id, **kwargs):
    company = get_company(cid)
    if not company:
        return None
    goals = company.get('goals', [])
    for g in goals:
        if g['id'] == goal_id:
            for k, v in kwargs.items():
                if k in ('title', 'status', 'task_ids'):
                    g[k] = v
            break
    update_company(cid, {'goals': goals})
    return goals

def delete_goal(cid, goal_id):
    company = get_company(cid)
    if not company:
        return
    goals = company.get('goals', [])
    goals = [g for g in goals if g['id'] != goal_id]
    update_company(cid, {'goals': goals})

# ─── Board Tasks (Kanban) ───

def get_board_tasks(cid):
    company = get_company(cid)
    return company.get('board_tasks', []) if company else []

def add_board_task(cid, title, agent_id=None, status="대기", depends_on=None, deadline=None):
    company = get_company(cid)
    if not company:
        return None
    task = {
        'id': gen_id('bt'),
        'title': title,
        'agent_id': agent_id or '',
        'status': status,
        'depends_on': depends_on or [],
        'deadline': deadline or '',
        'created_at': datetime.now().isoformat(),
        'updated_at': datetime.now().isoformat(),
    }
    db_add_task(cid, task)
    refreshed = get_company(cid)
    if refreshed:
        sse_broadcast('company_update', {"id": cid, "company": refreshed})
    return task

def update_board_task_status(cid, task_id, new_status):
    """Update a board task's status. Returns (task, unlocked_tasks) or (None, [])."""
    if new_status not in TASK_STATUSES:
        return None, []

    company = get_company(cid)
    if not company:
        return None, []

    tasks = company.get('board_tasks', [])
    task = None
    for t in tasks:
        if t['id'] == task_id:
            task = t
            break
    if not task:
        return None, []

    # Validate transition
    current = task.get('status', '대기')
    allowed = VALID_TASK_TRANSITIONS.get(current, TASK_STATUSES)
    if new_status not in allowed:
        # Allow force set for initial states
        pass

    task['status'] = new_status
    task['updated_at'] = datetime.now().isoformat()
    db_update_task(cid, task_id, {'status': new_status, 'updated_at': task['updated_at']})
    refreshed = get_company(cid)
    if refreshed:
        sse_broadcast('company_update', {"id": cid, "company": refreshed})

    # Check dependency chain: if completed, unlock dependents
    unlocked = []
    if new_status == '완료':
        unlocked = check_and_unlock_dependencies(cid, task_id)

    return task, unlocked

def check_and_unlock_dependencies(cid, completed_task_id):
    """When a task completes, find tasks depending on it and auto-start them."""
    company = get_company(cid)
    if not company:
        return []
    tasks = company.get('board_tasks', [])
    unlocked = []

    for t in tasks:
        if completed_task_id in t.get('depends_on', []) and t.get('status') == '대기':
            # Check if ALL dependencies are completed
            all_deps_done = all(
                any(d['id'] == dep_id and d.get('status') == '완료'
                    for d in tasks)
                for dep_id in t.get('depends_on', [])
            )
            if all_deps_done:
                t['status'] = '진행중'
                unlocked.append(t)
                # Notify the assigned agent
                if t.get('agent_id'):
                    agent = next((a for a in company.get('agents', [])
                                  if a['id'] == t['agent_id']), None)
                    if agent:
                        notify_text = (
                            f"의존성 작업이 완료되어 자동으로 시작됩니다: \"{t['title']}\". "
                            f"작업을 진행해주세요."
                        )
                        threading.Thread(
                            target=nudge_agent,
                            args=(cid, notify_text, t['agent_id']),
                            daemon=True
                        ).start()

    if unlocked:
        for t in unlocked:
            t['updated_at'] = datetime.now().isoformat()
            db_update_task(cid, t['id'], {'status': '진행중', 'updated_at': t['updated_at']})
        refreshed = get_company(cid)
        if refreshed:
            sse_broadcast('company_update', {"id": cid, "company": refreshed})
        # Log
        now_str = datetime.now().strftime('%H:%M')
        log_entries = [
            {"time": now_str, "agent": "시스템",
             "text": f"🔗 자동 시작: \"{t['title']}\" (의존성 해결)"}
            for t in unlocked
        ]
        append_activities(cid, log_entries)

    return unlocked

def delete_board_task(cid, task_id):
    company = get_company(cid)
    if not company:
        return
    db_delete_task(cid, task_id)
    # Also remove from goals
    goals = company.get('goals', [])
    for g in goals:
        g['task_ids'] = [tid for tid in g.get('task_ids', []) if tid != task_id]
    update_company(cid, {'goals': goals})
    refreshed = get_company(cid)
    if refreshed:
        sse_broadcast('company_update', {"id": cid, "company": refreshed})

# ─── Cost Tracking ───

def get_agent_cost(company, agent_id):
    """Get cost data for a specific agent, with defaults."""
    agent = next((a for a in company.get('agents', []) if a['id'] == agent_id), None)
    if not agent:
        return None
    cost = agent.get('cost', {})
    return {
        'total_tokens': cost.get('total_tokens', 0),
        'total_cost': cost.get('total_cost', 0.0),
        'last_run_cost': cost.get('last_run_cost', 0.0),
    }

def update_agent_cost(cid, agent_id, tokens_used, estimated_cost):
    """Update cost tracking for an agent after a run."""
    company = get_company(cid)
    if not company:
        return
    for agent in company.get('agents', []):
        if agent['id'] == agent_id:
            cost = agent.get('cost', {})
            cost['total_tokens'] = cost.get('total_tokens', 0) + tokens_used
            cost['total_cost'] = cost.get('total_cost', 0.0) + estimated_cost
            cost['last_run_cost'] = estimated_cost
            agent['cost'] = cost
            break
    update_company(cid, {'agents': company['agents']})

    # Check budget and create approval if needed
    budget = company.get('budget', DEFAULT_BUDGET)
    total_spent = sum(
        a.get('cost', {}).get('total_cost', 0.0)
        for a in company.get('agents', [])
    )
    if total_spent > budget and not has_pending_approval(cid, 'budget_exceeded'):
        create_approval(cid, 'budget_exceeded', '시스템',
                        f"예산 초과: ${total_spent:.2f} / ${budget:.2f}")

def get_company_costs(cid):
    """Get cost summary for entire company."""
    company = get_company(cid)
    if not company:
        return None
    total_cost = 0.0
    total_tokens = 0
    agent_costs = []
    for agent in company.get('agents', []):
        cost = agent.get('cost', {})
        ac = {
            'agent_id': agent['id'],
            'name': agent.get('name', ''),
            'emoji': agent.get('emoji', ''),
            'total_tokens': cost.get('total_tokens', 0),
            'total_cost': cost.get('total_cost', 0.0),
            'last_run_cost': cost.get('last_run_cost', 0.0),
        }
        agent_costs.append(ac)
        total_cost += ac['total_cost']
        total_tokens += ac['total_tokens']
    return {
        'total_cost': round(total_cost, 4),
        'total_tokens': total_tokens,
        'budget': company.get('budget', DEFAULT_BUDGET),
        'agents': agent_costs,
    }

# ─── Approval Gate System ───

def get_approvals(cid, status=None):
    company = get_company(cid)
    if not company:
        return []
    approvals = company.get('approvals', [])
    if status:
        approvals = [a for a in approvals if a.get('status') == status]
    return approvals

def create_approval(cid, approval_type, agent, detail):
    company = get_company(cid)
    if not company:
        return None
    approvals = company.get('approvals', [])
    approval = {
        'id': gen_id('apr'),
        'type': approval_type,
        'agent': agent,
        'detail': detail,
        'status': 'pending',
        'time': datetime.now().isoformat(),
    }
    approvals.append(approval)
    append_approval(cid, approval)
    # Log
    now_str = datetime.now().strftime('%H:%M')
    append_activity(cid, {"time": now_str, "agent": "시스템", "text": f"⚠️ 승인 요청: {detail}"})
    return approval

def has_pending_approval(cid, approval_type):
    """Check if there's already a pending approval of this type."""
    return any(a.get('status') == 'pending' and a.get('type') == approval_type
               for a in get_approvals(cid))

def resolve_approval(cid, approval_id, resolution):
    """Approve or reject an approval. resolution='approved' or 'rejected'."""
    company = get_company(cid)
    if not company:
        return None
    approvals = company.get('approvals', [])
    approval = None
    for a in approvals:
        if a['id'] == approval_id:
            a['status'] = resolution
            a['resolved_at'] = datetime.now().isoformat()
            approval = a
            break
    update_company(cid, {'approvals': approvals})
    # Log
    if approval:
        now_str = datetime.now().strftime('%H:%M')
        emoji = "✅" if resolution == "approved" else "❌"
        append_activity(cid, {
            "time": now_str,
            "agent": "시스템",
            "text": f"{emoji} 승인 {('승인' if resolution == 'approved' else '거부')}: {approval.get('detail', '')}"
        })
        # If budget exceeded was approved, increase budget by 50%
        if resolution == 'approved' and approval.get('type') == 'budget_exceeded':
            old_budget = company.get('budget', DEFAULT_BUDGET)
            company = get_company(cid)
            company['budget'] = round(old_budget * 1.5, 2)
            update_company(cid, {'budget': company['budget']})
    return approval

# ─── Agent Registration & Workspace ───

def get_org_for_topic(topic):
    topic_lower = topic.lower()
    for key, org in TOPIC_ORGS.items():
        if key != "default" and key in topic_lower:
            return org
    return TOPIC_ORGS["default"]

# ── 장기 메모리 시스템 ──
def load_agent_memory(cid, agent_id):
    """에이전트 메모리 로드: 최근 5개 일일파일 + MEMORY.md 상위 100줄"""
    memory_dir = DATA / cid / "workspaces" / agent_id / "memory"
    if not memory_dir.exists():
        return ""
    parts = []
    # MEMORY.md (핵심 장기 메모리)
    mem_file = memory_dir / "MEMORY.md"
    if mem_file.exists():
        mem_lines = mem_file.read_text(encoding='utf-8', errors='ignore').splitlines()
        if mem_lines:
            parts.append("=== 장기 메모리 ===")
            parts.extend(mem_lines[:100])
    # 최근 5개 일일 로그
    daily_files = sorted(memory_dir.glob("*.md"), reverse=True)
    daily_files = [f for f in daily_files if f.name != "MEMORY.md"][:5]
    if daily_files:
        parts.append("=== 최근 활동 ===")
        for f in reversed(daily_files):
            content = f.read_text(encoding='utf-8', errors='ignore').strip()
            if content:
                parts.append(f"--- {f.name} ---")
                parts.append(content[:500])  # 파일당 최대 500자
    return '\n'.join(parts)

def save_agent_memory(cid, agent_id, text):
    """에이전트 응답을 일일 로그에 저장 + 중요 내용 추출"""
    if not text or len(text) < 10:
        return
    memory_dir = DATA / cid / "workspaces" / agent_id / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime('%Y-%m-%d')
    daily_file = memory_dir / f"{today}.md"
    time_str = datetime.now().strftime('%H:%M')
    # 일일 로그에 추가
    entry = f"\n[{time_str}] {text[:500]}\n"
    with open(daily_file, 'a', encoding='utf-8') as f:
        f.write(entry)

SUMMARY_THRESHOLD = 20  # 대화 20개 초과 시 자동 요약
SUMMARY_KEEP_RECENT = 5  # 최근 5개는 전문 유지

SUMMARY_PROMPT = """다음 대화 기록을 읽고 핵심 내용을 10줄 이내로 요약해주세요.
형식:
- [발화자] 핵심 내용
중요한 결정, 진행 상황, 다음 단계를 중심으로 요약하세요.

대화 기록:
"""

def auto_summarize(cid):
    """대화가 임계치 초과 시 오래된 대화를 요약해서 메모리에 저장"""
    company = get_company(cid)
    if not company:
        return
    chat = company.get('chat', [])
    if len(chat) <= SUMMARY_THRESHOLD:
        return
    
    # 요약할 범위: 0 ~ (전체 - SUMMARY_KEEP_RECENT)
    to_summarize = chat[:-SUMMARY_KEEP_RECENT]
    if len(to_summarize) < 5:
        return
    
    # CEO 에이전트로 요약 생성
    summary_chat = '\n'.join(
        f"[{m.get('from','?')}] {(m.get('text','') or '')[:200]}"
        for m in to_summarize if m.get('type') != 'system'
    )
    
    if len(summary_chat) < 100:
        return
    
    prompt = f"{SUMMARY_PROMPT}{summary_chat}\n\n요약:"
    try:
        proc = subprocess.Popen(
            ['openclaw', 'agent', '--local', '-m', prompt],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        stdout, stderr = proc.communicate(timeout=60)
        summary = stdout.decode().strip()
        # 클린업
        lines = summary.split('\n')
        clean = [l for l in lines if not l.startswith('[') and not l.startswith('(agent') and l.strip()]
        summary = '\n'.join(clean).strip()
        
        if summary and len(summary) > 20:
            # 요약을 회사 레벨 메모리에 저장
            summary_dir = DATA / cid / "workspaces" / "_shared" / "memory"
            summary_dir.mkdir(parents=True, exist_ok=True)
            today = datetime.now().strftime('%Y-%m-%d')
            summary_file = summary_dir / f"conversation-summary-{today}.md"
            with open(summary_file, 'w', encoding='utf-8') as f:
                f.write(f"# 대화 요약 ({today})\n\n{summary}\n")
            
            # 오래된 대화를 시스템 메시지로 대체 (채팅에서는 제거하지 않음, 프롬프트에서만 요약 사용)
            # 요약 파일 경로를 company 메타에 기록
            meta = company.get('_meta', {})
            meta['last_summary'] = f"{today}: {len(to_summarize)}개 대화 요약 완료"
            company['_meta'] = meta
            save_company(company)
            print(f"[auto_summarize] {cid}: {len(to_summarize)}개 대화 요약 완료 ({len(summary)}자)")
    except Exception as e:
        print(f"[auto_summarize] error: {e}")

def load_conversation_summary(cid):
    """저장된 대화 요약을 로드"""
    summary_dir = DATA / cid / "workspaces" / "_shared" / "memory"
    if not summary_dir.exists():
        return None
    # 가장 최근 요약 파일
    files = sorted(summary_dir.glob("conversation-summary-*.md"), reverse=True)
    if not files:
        return None
    try:
        return files[0].read_text(encoding='utf-8').strip()
    except:
        return None

# ─── i18n ───
LANG_MAP = {"ko": "한국어", "en": "English"}

def _s(key, lang, **kwargs):
    """Simple i18n lookup with format kwargs."""
    T = {
        "role.intro": {"ko": "당신은 '{company}'의 {name}({role})입니다.", "en": "You are {name}({role}) of '{company}'."},
        "role.report": {"ko": "팀원들에게 @멘션으로 지시하고, @CEO에게 보고하세요. 마스터의 확인/승인/정보 제공이 필요하면 @마스터 멘션으로 요청하세요.", "en": "Instruct team members with @mention and report to @CEO. When you need master's confirmation/approval/info, use @마스터 mention."},
        "speak.lang": {"ko": "한국어로 소통합니다.", "en": "Communicate in English."},
        "complex.title": {"ko": "## 의사결정 프로토콜 (COMPLEX)", "en": "## Decision Protocol (COMPLEX)"},
        "complex.intro": {"ko": "복잡한 작업은 다음 단계를 따르세요:", "en": "For complex tasks, follow these steps:"},
        "complex.1": {"ko": "1. 관찰(Observe) — 현재 상황, 작업 상태 파악", "en": "1. Observe — Assess current situation and task status"},
        "complex.2": {"ko": "2. 사고(Think) — 문제 분석, 위험 요소, 의존관계 확인", "en": "2. Think — Analyze problems, risks, dependencies"},
        "complex.3": {"ko": "3. 계획(Plan) — 구체적 실행 계획 + 성공 기준", "en": "3. Plan — Concrete execution plan + success criteria"},
        "complex.4": {"ko": "4. 준비(Build) — 작업 생성, 리소스 확인", "en": "4. Build — Create tasks, verify resources"},
        "complex.5": {"ko": "5. 실행(Execute) — 계획 실행, 결과물을 파일로 저장", "en": "5. Execute — Run the plan, save deliverables as files"},
        "complex.6": {"ko": "6. 복기(Learn) — 결과 검증, 교훈을 메모리에 기록", "en": "6. Learn — Verify results, record lessons in memory"},
        "complex.simple": {"ko": "간단한 응답이면 바로 답변하세요. 복잡한 작업일 때만 위 단계를 따르세요.", "en": "For simple queries, respond directly. Follow these steps only for complex tasks."},
        "brief.title": {"ko": "## 브리프", "en": "## Briefing"},
        "brief.desc": {"ko": "- _shared/newspaper.md를 읽고 현재 상황 파악", "en": "- Read _shared/newspaper.md for current status"},
        "inbox.title": {"ko": "## Inbox", "en": "## Inbox"},
        "inbox.desc1": {"ko": "- inbox/ 폴더에 받은 지시가 있습니다", "en": "- You have instructions in the inbox/ folder"},
        "inbox.desc2": {"ko": "- 확인 후 작업 수행, 응답하면 자동 보관됩니다", "en": "- Process them after reading; they auto-archive after you respond"},
        "standup.title": {"ko": "## Standup", "en": "## Standup"},
        "standup.desc1": {"ko": "- 작업 완료 후 standup.md 업데이트", "en": "- Update standup.md after completing work"},
        "standup.desc2": {"ko": "- 양식: 어제 한 것 / 지금 하는 것 / 필요한 것", "en": "- Format: What I did / What I'm doing / What I need"},
        "whiteboard.title": {"ko": "## 화이트보드", "en": "## Whiteboard"},
        "whiteboard.desc": {"ko": "- _shared/whiteboard.md에 아이디어, 의견, 질문을 자유롭게 적으세요", "en": "- Freely post ideas, opinions, questions on _shared/whiteboard.md"},
        "work.title": {"ko": "## 작업 방식", "en": "## Work Style"},
        "work.deliverables": {"ko": "- 긴 결과물은 _shared/deliverables/에 저장, 파일명만 채팅에", "en": "- Save long deliverables to _shared/deliverables/, share filename only in chat"},
        "work.files": {"ko": "- 기획서/보고서는 .md, 코드는 .py/.js/.json으로 저장", "en": "- Plans/reports as .md, code as .py/.js/.json"},
        "work.commands": {"ko": "- [TASK_ADD:작업명:우선순위] 작업추가 / [TASK_DONE:작업명] 완료\n- [TASK_START:작업명] 시작 / [TASK_BLOCK:작업명:사유] 차단\n- [CRON_ADD:작업명:분:프롬프트] 정기작업 등록", "en": "- [TASK_ADD:name:priority] Add task / [TASK_DONE:name] Complete\n- [TASK_START:name] Start / [TASK_BLOCK:name:reason] Block\n- [CRON_ADD:name:mins:prompt] Schedule recurring task"},
        "agents.intro": {"ko": "당신은 회사 에이전트입니다. SOUL.md를 읽고 역할에 맞게 응답하세요.", "en": "You are a company agent. Read SOUL.md and respond according to your role."},
        "agents.skip": {"ko": "부트스트랩은 건너뛰세요. 받은 메시지에 항상 응답하세요.", "en": "Skip bootstrap. Always respond to messages."},
        "user.name": {"ko": "마스터", "en": "Master"},
        "user.role": {"ko": "회사 운영자", "en": "Company Operator"},
        "heartbeat": {"ko": "현재 할 일이 없으면 NO_REPLY로 응답하세요.", "en": "If you have nothing to do, respond with NO_REPLY."},
        # Server messages
        "msg.no_reply": {"ko": "{emoji} {name}이(가) 응답하지 않았습니다. 다시 시도하거나 에이전트를 재활성화해주세요.", "en": "{emoji} {name} did not respond. Please try again or reactivate the agent."},
        "status.waiting": {"ko": "⏳ 에이전트를 준비하고 있습니다. 잠시만 기다려주세요...", "en": "⏳ Agents are being prepared. Please wait..."},
        "status.ready": {"ko": "✅ 에이전트가 모두 준비 완료되었습니다! 대화를 시작하시면 됩니다.", "en": "✅ All agents are ready! You can start the conversation now."},
        "intervention.title": {"ko": "👤 사용자 개입 필요 ({agent}): {snippet}", "en": "👤 User intervention needed ({agent}): {snippet}"},
        # Newspaper
        "news.team_status": {"ko": "## 팀원 상태", "en": "## Team Status"},
        "news.tasks": {"ko": "## 작업 현황", "en": "## Task Status"},
        "news.waiting": {"ko": "⏸️ 대기", "en": "⏸️ Waiting"},
        "news.doing": {"ko": "⏳ 진행중", "en": "⏳ In Progress"},
        "news.done_today": {"ko": "✅ 오늘 완료", "en": "✅ Done Today"},
        "news.approvals": {"ko": "## 승인 대기", "en": "## Pending Approvals"},
        "news.recent_orders": {"ko": "## 마스터의 최근 지시", "en": "## Master's Recent Orders"},
        "news.recent_deliverables": {"ko": "## 최근 결과물", "en": "## Recent Deliverables"},
        "news.whiteboard": {"ko": "## 화이트보드", "en": "## Whiteboard"},
        # Kanban statuses
        "status.대기": {"ko": "⏸️ 대기", "en": "⏸️ Waiting"},
        "status.진행중": {"ko": "⏳ 진행 중", "en": "⏳ In Progress"},
        "status.완료": {"ko": "✅ 완료", "en": "✅ Done"},
        # Inbox
        "inbox.from": {"ko": "보낸 사람", "en": "From"},
        "inbox.time": {"ko": "시간", "en": "Time"},
        # Intervention keywords (same for all langs)
        "kw.intervention": ['비밀번호','패스워드','password','계정','아이디','로그인','API 키','API key','AWS','GCP','Stripe','결제','신용카드','도메인','SSL','인증서','외부 서비스','가입','회원가입','인증','OTP','2FA','MFA'],
    }
    if key == "kw.intervention": return T[key]
    entry = T.get(key, {})
    text = entry.get(lang, entry.get("ko", key))
    if kwargs: text = text.format(**kwargs)
    return text


def setup_agent_workspace(agent_workspace, name, role, company_name, emoji, lang="ko", cid=None):
    """Initialize agent workspace with required files."""
    agent_workspace.mkdir(parents=True, exist_ok=True)
    (agent_workspace / "AGENTS.md").write_text(
        f"# AGENTS.md\n\n{_s('agents.intro', lang)}\n{_s('agents.skip', lang)}\n")
    # SOUL.md에 절대경로 안내 추가
    if cid:
        whiteboard_path = DATA / cid / "_shared" / "whiteboard.md"
        deliverables_path = DATA / cid / "_shared" / "deliverables"
        shared_path = DATA / cid / "_shared"
    if not (agent_workspace / "SOUL.md").exists():
        (agent_workspace / "SOUL.md").write_text(
            f"# SOUL.md\n{_s('role.intro', lang, company=company_name, name=name, role=role)}\n"
            f"{_s('role.report', lang)}\n{_s('speak.lang', lang)}\n"
            f"\n{_s('complex.title', lang)}\n{_s('complex.intro', lang)}\n"
            f"{_s('complex.1', lang)}\n{_s('complex.2', lang)}\n{_s('complex.3', lang)}\n{_s('complex.4', lang)}\n{_s('complex.5', lang)}\n{_s('complex.6', lang)}\n"
            f"{_s('complex.simple', lang)}\n"
            f"\n{_s('brief.title', lang)}\n{_s('brief.desc', lang)}\n"
            f"\n{_s('inbox.title', lang)}\n{_s('inbox.desc1', lang)}\n{_s('inbox.desc2', lang)}\n"
            f"\n{_s('standup.title', lang)}\n{_s('standup.desc1', lang)}\n{_s('standup.desc2', lang)}\n"
            f"\n{_s('whiteboard.title', lang)}\n- Path: {whiteboard_path}\n"
            f"\n{_s('work.title', lang)}\n{_s('work.deliverables', lang)}\n{_s('work.files', lang)}\n{_s('work.commands', lang)}\n"
            f"\n## Paths\n"
            f"- Shared: {shared_path}\n"
            f"- Deliverables: {deliverables_path}\n"
            f"- Whiteboard: {whiteboard_path}\n"
            if cid else ""
        )
    if not (agent_workspace / "IDENTITY.md").exists():
        (agent_workspace / "IDENTITY.md").write_text(
            f"- **Name:** {name}\n- **Role:** {role}\n- **Emoji:** {emoji}\n")
    if not (agent_workspace / "USER.md").exists():
        (agent_workspace / "USER.md").write_text(
            f"# USER.md\n\n- **Name:** {_s('user.name', lang)}\n- **Role:** {_s('user.role', lang)}\n")
    if not (agent_workspace / "TOOLS.md").exists():
        (agent_workspace / "TOOLS.md").write_text(
            "# TOOLS.md\n\n## Commands\n"
            "- `@mention` to instruct team members\n"
            "- `[TASK_ADD:name:priority]` Add task\n"
            "- `[TASK_DONE:name]` Complete task\n"
            "- `[TASK_START:name]` Start task\n"
            "- `[CRON_ADD:name:mins:prompt]` Schedule recurring task\n"
            "- `[CRON_DEL:name]` Delete recurring task\n"
        )
    if not (agent_workspace / "HEARTBEAT.md").exists():
        (agent_workspace / "HEARTBEAT.md").write_text(
            f"# HEARTBEAT.md\n\n{_s('heartbeat', lang)}\n")
        (agent_workspace / "HEARTBEAT.md").write_text(
            f"# HEARTBEAT.md\n\n{_s('heartbeat', lang)}\n")
    mem_dir = agent_workspace / "memory"
    if not mem_dir.exists():
        mem_dir.mkdir(parents=True, exist_ok=True)
    bootstrap = agent_workspace / "BOOTSTRAP.md"
    if bootstrap.exists():
        bootstrap.unlink()

def register_agent(agent_id, agent_workspace, name, role, company_name, emoji, lang='ko', wait=False, on_done=None, company_id=None):
    """Register and activate an OpenClaw agent."""
    setup_agent_workspace(agent_workspace, name, role, company_name, emoji, lang, cid=company_id)
    if wait:
        _register_and_activate(agent_id, str(agent_workspace), name, role)
        if on_done: on_done()
    else:
        def _task():
            try:
                _register_and_activate(agent_id, str(agent_workspace), name, role)
            except Exception as e:
                print(f"[register] {agent_id} failed: {e}")
            if on_done:
                try:
                    on_done()
                except Exception as e:
                    print(f"[register] {agent_id} on_done callback error: {e}")
        threading.Thread(target=_task, daemon=True).start()

def _register_and_activate(agent_id, workspace, name, role):
    """Background: register agent and activate session."""
    import shutil
    ws_path = Path(workspace)
    agent_dir = Path.home() / '.openclaw' / 'agents' / agent_id / 'agent'
    
    # 에이전트 디렉토리에 워크스페이스 파일 복사
    agent_dir.mkdir(parents=True, exist_ok=True)
    for fname in ['SOUL.md', 'AGENTS.md', 'USER.md', 'IDENTITY.md', 'TOOLS.md', 'HEARTBEAT.md']:
        src = ws_path / fname
        if src.exists():
            shutil.copy2(src, agent_dir / fname)
    
    # BOOTSTRAP.md 삭제 (바로 응답하도록)
    bootstrap = ws_path / "BOOTSTRAP.md"
    if bootstrap.exists():
        bootstrap.unlink()
    bs2 = agent_dir / "BOOTSTRAP.md"
    if bs2.exists():
        bs2.unlink()
    
    with AGENT_LOCK:
        RUNTIME.register(agent_id, str(ws_path))

    # 첫 메시지로 세션 활성화
    try:
        RUNTIME.run(agent_id, f"{agent_id}-init",
                    f'당신은 {name}({role})입니다. "확인"이라고만 답하세요.', timeout=30)
        print(f"[register] {agent_id} ({name}) activated")
    except Exception as e:
        print(f"[register] {agent_id} activate failed: {e}")

# ─── Recurring Task System ───

def add_recurring_task(cid, title, prompt, interval_minutes, agent_id, agent_name, agent_emoji, cron_expression=''):
    company = get_company(cid)
    if not company:
        return None
    tasks = company.get('recurring_tasks', [])
    task_id = f"task-{datetime.now().strftime('%m%d%H%M%S')}-{len(tasks)}"
    task = {
        'id': task_id, 'agent_id': agent_id, 'agent_name': agent_name,
        'agent_emoji': agent_emoji, 'title': title, 'prompt': prompt,
        'interval_minutes': interval_minutes, 'status': 'running',
        'cron_expression': cron_expression,
        'last_run': None, 'next_run': datetime.now().isoformat(),
        'created_at': datetime.now().isoformat(), 'results': [],
    }
    tasks.append(task)
    update_company(cid, {'recurring_tasks': tasks})
    start_task_thread(cid, task)
    return task

def get_recurring_tasks(cid):
    company = get_company(cid)
    return company.get('recurring_tasks', []) if company else []

def run_meeting(cid, topic, agent_ids):
    """Start a meeting: nudge each agent with the meeting topic simultaneously.
    Each agent is prompted to share their perspective and collaborate via @mentions."""
    company = get_company(cid)
    if not company:
        return
    agents = [a for a in company.get('agents', []) if a['id'] in [x.lower() for x in agent_ids]]
    if not agents:
        agents = company.get('agents', [])[:3]  # Default: first 3 agents

    now_str = datetime.now().strftime('%H:%M')
    meeting_id = f"meeting-{int(time.time())}"
    # Announce meeting in chat
    announce = f"🏛️ 회의 시작: {topic}\n참석자: {', '.join(a['emoji']+' '+a['name'] for a in agents)}"
    append_chat(cid, {"from": "시스템", "emoji": "🏛️", "text": announce,
                       "time": datetime.now().isoformat(), "type": "system"}, broadcast=True)
    append_activity(cid, {"time": now_str, "agent": "시스템",
                           "text": f"🏛️ 회의 시작: {topic} (참석자 {len(agents)}명)"})

    meeting_prompt_base = (
        f"📋 [회의] 주제: {topic}\n\n"
        f"이 회의에 참석 중인 다른 팀원: {', '.join('@'+a['name'] for a in agents)}\n\n"
        "당신의 전문 분야 관점에서 이 주제에 대한 의견, 계획, 우려사항을 공유하세요. "
        "필요하면 @멘션으로 다른 팀원과 협력하세요."
    )
    # Nudge all participants (the queue system handles concurrency)
    for a in agents:
        threading.Thread(
            target=nudge_agent,
            args=(cid, meeting_prompt_base, a['id'].upper()),
            daemon=True
        ).start()
        time.sleep(0.5)  # Small stagger to avoid race on status updates

def update_task_status(cid, task_id, new_status):
    company = get_company(cid)
    if not company:
        return
    tasks = company.get('recurring_tasks', [])
    for t in tasks:
        if t['id'] == task_id:
            t['status'] = new_status
            if new_status == 'resumed':
                t['status'] = 'running'
                t['next_run'] = datetime.now().isoformat()
                start_task_thread(cid, t)
            break
    update_company(cid, {'recurring_tasks': tasks})

def start_task_thread(cid, task):
    """Start a daemon thread for a recurring task."""
    key = f"{cid}:{task['id']}"
    if key in _running_task_threads:
        return
    _running_task_threads.add(key)
    def _run():
        while key in _running_task_threads:
            company = get_company(cid)
            if not company:
                break
            tasks = company.get('recurring_tasks', [])
            t = next((x for x in tasks if x['id'] == task['id']), None)
            if not t or t['status'] != 'running':
                _running_task_threads.discard(key)
                break
            try:
                next_run = datetime.fromisoformat(t.get('next_run', datetime.now().isoformat()))
                wait_secs = (next_run - datetime.now()).total_seconds()
                if wait_secs > 0:
                    time.sleep(min(wait_secs, 60))
                else:
                    time.sleep(1)
            except:
                time.sleep(5)
            company = get_company(cid)
            if not company:
                break
            tasks = company.get('recurring_tasks', [])
            t = next((x for x in tasks if x['id'] == task['id']), None)
            if not t or t['status'] != 'running':
                _running_task_threads.discard(key)
                break
            try:
                result = execute_task(cid, t)
                company = get_company(cid)
                if company:
                    tasks = company.get('recurring_tasks', [])
                    for x in tasks:
                        if x['id'] == t['id']:
                            x['last_run'] = datetime.now().isoformat()
                            x['next_run'] = (datetime.now() + __import__('datetime').timedelta(minutes=x['interval_minutes'])).isoformat()
                            x['results'].append(result)
                            x['results'] = x['results'][-10:]
                            break
                    update_company(cid, {'recurring_tasks': tasks})
            except Exception as e:
                print(f"[WARN] task {task['id']} execution error: {e}")
                time.sleep(30)
        _running_task_threads.discard(key)
    threading.Thread(target=_run, daemon=True).start()

def execute_task(cid, task):
    """Execute a single recurring task run."""
    agent_id_full = f"{cid}-{task['agent_id']}"
    prompt = f"""당신은 '{task['agent_name']}'입니다. 다음 정기 작업을 수행하세요:

{task['prompt']}

간결하게 결과만 @CEO에게 보고하세요. (2-3줄 이내) @마스터는 절대 멘션하지 마세요."""
    start = time.time()
    try:
        proc = subprocess.Popen(
            ['openclaw', 'agent', '--agent', agent_id_full, '--local', '-m', prompt],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        stdout, stderr = proc.communicate(timeout=90)
        reply = stdout.decode().strip()
        lines = reply.split('\n')
        clean = [l for l in lines if not l.startswith('[') and not l.startswith('(agent') and l.strip()]
        reply = '\n'.join(clean).strip()
        elapsed = round(time.time() - start, 1)

        # Estimate cost from response length
        tokens = max(len(reply) // 4, 100)  # rough token estimate
        estimated_cost = round(tokens * COST_PER_1K_TOKENS / 1000, 6)
        update_agent_cost(cid, task['agent_id'], tokens, estimated_cost)

        if reply and len(reply) > 1:
            try:
                payload = json.dumps({
                    "from": task['agent_name'], "emoji": task['agent_emoji'],
                    "to": "마스터", "text": f"🔄 [{task['title']}]\n{reply}"
                }).encode()
                req = urllib.request.Request(
                    f'http://localhost:3000/api/agent-msg/{cid}',
                    data=payload, headers={'Content-Type': 'application/json'}
                )
                _post_local(req.full_url, json.loads(payload))
            except: pass
        return {"time": datetime.now().strftime('%H:%M'), "text": reply[:200] if reply else "(빈 응답)", "elapsed": elapsed}
    except Exception as e:
        return {"time": datetime.now().strftime('%H:%M'), "text": f"오류: {str(e)[:100]}", "elapsed": round(time.time() - start, 1)}

def restore_running_tasks():
    companies = load_json(COMPANIES_FILE) or []
    for c in companies:
        cid = c.get('id')
        if not cid:
            continue
        state = get_company(cid)
        if not state:
            continue
        for task in state.get('recurring_tasks', []):
            if task.get('status') == 'running':
                start_task_thread(cid, task)

# ─── Company Creation ───

def _welcome_msg(name, topic, agents, lang):
    team = ', '.join(a['name'] for a in agents[1:])
    msgs = {
        'ko': {"greeting": f"안녕하세요 마스터! 👋\n\n저는 '{name}'의 CEO입니다.\n\n주제: {topic}\n팀원: {team}\n\n@멘션으로 팀원들에게 지시하실 수 있습니다. 무엇부터 시작할까요?",
                "waiting": _s('status.waiting','ko'),
                "ready": _s('status.ready','ko'),
                "log": f"🏢 '{name}' 프로젝트 시작. 주제: {topic}"},
        'en': {"greeting": f"Hello Master! 👋\n\nI'm the CEO of '{name}'.\n\nTopic: {topic}\nTeam: {team}\n\nUse @mention to instruct team members. What should we start with?",
                "waiting": _s('status.waiting','en'),
                "ready": _s('status.ready','en'),
                "log": f"🏢 '{name}' project started. Topic: {topic}"},
        'ja': {"greeting": f"こんにちはマスター！👋\n\n私は '{name}' のCEOです。\n\nテーマ: {topic}\nチーム: {team}\n\n@メンションでチームメンバーに指示できます。何から始めましょうか？",
                "waiting": "⏳ エージェントを準備しています。しばらくお待ちください...",
                "ready": "✅ 全エージェントの準備が完了しました！会話を開始できます。",
                "log": f"🏢 '{name}' プロジェクト開始。テーマ: {topic}"},
        'zh': {"greeting": f"你好管理员！👋\n\n我是 '{name}' 的CEO。\n\n主题: {topic}\n团队: {team}\n\n使用@提及来指示团队成员。我们从什么开始？",
                "waiting": "⏳ 正在准备代理，请稍等...",
                "ready": "✅ 所有代理已准备就绪！您可以开始对话了。",
                "log": f"🏢 '{name}' 项目启动。主题: {topic}"},
    }
    return msgs.get(lang, msgs['ko'])

def create_company(name, topic, lang="ko"):
    companies = db_get_all_companies()
    slug = re.sub(r'[^a-z0-9]', '-', name.lower()).strip('-')
    if not slug: slug = 'company'
    company_id = slug + "-" + datetime.now().strftime('%m%d%H%M')
    org = get_org_for_topic(topic)
    agents = []
    for aid in org:
        t = AGENT_TEMPLATES[aid]
        agent_id = f"{company_id}-{aid}"
        agent_name = t["name"]
        agent_emoji = t["emoji"]
        agent_role = t["role"].get(lang, t["role"]["en"])
        agent_workspace = DATA / company_id / "workspaces" / aid
        def make_done_callback(cid, aid_val, total_agents, lang):
            def _done():
                c = get_company(cid)
                if c:
                    all_ready = True
                    for a in c.get('agents', []):
                        if a['id'] == aid_val:
                            a['status'] = 'active'
                        if a.get('status') != 'active':
                            all_ready = False
                    update_company(cid, {"agents": c['agents']})
                    if all_ready:
                        w = _welcome_msg(c['name'], c['topic'], c['agents'], lang)
                        c['chat'].append({"type": "system", "from": "시스템", "emoji": "✅", "to": "", "text": w['ready']})
                        c['chat'].append({"type": "agent", "from": "CEO", "emoji": "👔", "to": "마스터", "text": w['greeting']})
                        c['activity_log'].append({"time": datetime.now().strftime('%H:%M'), "agent": "시스템", "text": w['ready']})
                        c['activity_log'].append({"time": datetime.now().strftime('%H:%M'), "agent": "CEO", "text": w['log']})
                        update_company(cid, {"chat": c['chat'], "activity_log": c['activity_log']})
            return _done
        register_agent(agent_id, agent_workspace, agent_name, agent_role, name, agent_emoji,
                       lang=lang, wait=False, on_done=make_done_callback(company_id, aid, len(org), lang))
        agents.append({
            "id": aid, "agent_id": agent_id, "name": agent_name, "emoji": agent_emoji,
            "role": agent_role, "status": "registering",
            "tasks": [], "messages": [],
            "cost": {"total_tokens": 0, "total_cost": 0.0, "last_run_cost": 0.0},
        })
    W = _welcome_msg(name, topic, agents, lang)
    company = {
        "id": company_id, "name": name, "topic": topic, "lang": lang,
        "status": "starting", "created_at": datetime.now().isoformat(),
        "budget": DEFAULT_BUDGET,
        "agents": agents,
        "goals": [],
        "board_tasks": [],
        "approvals": [],
        "chat": [
            {"type": "system", "from": "시스템", "emoji": "⚙️", "to": "", "text": W['waiting']}
        ],
        "activity_log": [
            {"time": datetime.now().strftime('%H:%M'), "agent": "시스템", "text": W['waiting']}
        ]
    }
    companies.append(company)
    db_save_company(company)
    save_json(COMPANIES_FILE, companies)
    # Init shared folders
    shared = DATA / company_id / "_shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "whiteboard.md").write_text("# Whiteboard\nPost ideas, opinions, questions freely.\n", encoding='utf-8')
    (shared / "deliverables").mkdir(exist_ok=True)
    state_file = DATA / f"{company_id}.json"
    save_json(state_file, company)
    return company

# ─── Lightweight Agent Nudge (Empire-style) ───

# Per-agent queue for ordered processing + dedup
_AGENT_QUEUES = {}  # {"cid:agent_id": deque of texts}
_AGENT_BUSY = set()
_AGENT_STATE_LOCK = threading.Lock()
from collections import deque
_MENTION_COUNTS = {}  # {cid: {chain_key: {'count': int, 'ts': float}}}
_MENTION_LIMIT = 5    # max mentions per chain
_MENTION_TTL = 1800   # seconds
_MAX_CONCURRENT = 2  # max agents thinking at once
_ACTIVE_SEMAPHORE = threading.Semaphore(_MAX_CONCURRENT)

_newspaper_cache = {}  # {cid: (brief, timestamp)}
_AB_RESULTS = {}  # {test_id: {status, agents: [{agent_id, response, elapsed}]}}


def _clean_mention_counts(now_ts=None):
    now_ts = now_ts or time.time()
    dead_cids = []
    for cid, chains in list(_MENTION_COUNTS.items()):
        dead = [k for k, v in chains.items() if now_ts - v.get('ts', 0) > _MENTION_TTL]
        for k in dead:
            chains.pop(k, None)
        if not chains:
            dead_cids.append(cid)
    for cid in dead_cids:
        _MENTION_COUNTS.pop(cid, None)


def _bump_mention_chain(cid, chain):
    now_ts = time.time()
    _clean_mention_counts(now_ts)
    bucket = _MENTION_COUNTS.setdefault(cid, {})
    state = bucket.get(chain, {'count': 0, 'ts': now_ts})
    state['count'] += 1
    state['ts'] = now_ts
    bucket[chain] = state
    return state['count']

def generate_newspaper(cid):
    """Generate a concise daily brief from current state. Cached for 60s."""
    cache_now = time.time()
    cached = _newspaper_cache.get(cid)
    if cached and cache_now - cached[1] < 60:
        return cached[0]
    company = get_company(cid)
    if not company:
        return ''
    lang = company.get('lang', 'ko')
    name = company.get('name', '?')
    display_now = datetime.now().strftime('%m/%d %H:%M')
    brief_label = '브리프' if lang == 'ko' else 'Briefing'
    lines = [f"📰 {name} {brief_label} ({display_now})"]

    agents = company.get('agents', [])
    if agents:
        parts = []
        for a in agents:
            status = a.get('status', 'active')
            emoji = a.get('emoji', '👤')
            label = '⏳' if status == 'working' else '✅'
            parts.append(f"  {label} {emoji} {a.get('name','?')}: {status}")
        lines.append(f"\n{_s('news.team_status', lang)}")
        lines.extend(parts)

    tasks = company.get('board_tasks', [])
    if tasks:
        waiting = [t for t in tasks if t.get('status') == '대기']
        doing = [t for t in tasks if t.get('status') == '진행중']
        done_today = [t for t in tasks if t.get('status') == '완료' and t.get('updated_at', '').startswith(datetime.now().strftime('%Y-%m-%d'))]
        lines.append(f"\n{_s('news.tasks', lang)}")
        if waiting:
            wl = _s('news.waiting', lang)
            lines.append(f"{wl} ({len(waiting)}): {', '.join(t.get('title','')[:20] for t in waiting[:5])}")
        if doing:
            dl = _s('news.doing', lang)
            lines.append(f"{dl} ({len(doing)}): {', '.join(t.get('title','')[:20] for t in doing[:5])}")
        if done_today:
            dtl = _s('news.done_today', lang)
            lines.append(f"{dtl} ({len(done_today)}): {', '.join(t.get('title','')[:20] for t in done_today[:5])}")

    approvals = [a for a in company.get('approvals', []) if a.get('status') == 'pending']
    if approvals:
        lines.append(f"\n{_s('news.approvals', lang)}")
        for a in approvals[:3]:
            lines.append(f"⚠️ {a.get('type','?')}: {a.get('detail','')[:60]}")

    chat = company.get('chat', [])
    recent_user = [m for m in chat[-20:] if m.get('type') == 'user']
    if recent_user:
        lines.append(f"\n{_s('news.recent_orders', lang)}")
        for m in recent_user[-3:]:
            txt = (m.get('text', '') or '')[:100]
            lines.append(f"- {txt}")

    deliv = db_get_doc(cid, 'deliverables', '')
    if deliv:
        lines.append(f"\n{_s('news.recent_deliverables', lang)}")
        for line in deliv.split('\n')[:5]:
            if line.strip():
                lines.append(f"- {line.strip()}")

    wb = db_get_doc(cid, 'whiteboard', '')
    if wb and len(wb) > 30:
        lines.append(f"\n{_s('news.whiteboard', lang)}")
        lines.append(wb[:300])

    brief = '\n'.join(lines)

    # Save to file
    brief_dir = DATA / cid / "_shared"
    brief_dir.mkdir(parents=True, exist_ok=True)
    db_save_doc(cid, 'newspaper', '', brief)
    _newspaper_cache[cid] = (brief, cache_now)
    return brief


def read_agent_standup(cid, agent_id):
    """Read agent's standup from DB cache."""
    content = db_get_doc(cid, 'standup', agent_id)
    return content[:500] if content else None


def add_to_inbox(cid, agent_id, from_name, instruction, lang="ko"):
    """Write a message to agent's inbox in DB."""
    now = datetime.now()
    content = f"{_s('inbox.from', lang)}: {from_name}\n{_s('inbox.time', lang)}: {now.strftime('%m/%d %H:%M')}\n\n{instruction}"
    # Append to existing inbox
    existing = db_get_doc(cid, 'inbox', agent_id)
    db_save_doc(cid, 'inbox', agent_id, f"{existing}\n---\n{content}" if existing else content)
    return True


def read_agent_inbox(cid, agent_id, limit=5):
    """Read inbox from DB cache."""
    content = db_get_doc(cid, 'inbox', agent_id)
    if not content: return None
    # Return last N blocks
    blocks = content.split('\n---\n')
    return '\n---\n'.join(blocks[-limit:])


def archive_inbox(cid, agent_id):
    """Clear agent's inbox after processing."""
    db_save_doc(cid, 'inbox', agent_id, '')


def _clean_stale_locks():
    """Remove lock files whose owner PID is dead."""
    import glob
    for lock in glob.glob(str(Path.home() / '.openclaw' / 'agents' / '**' / '*.lock'), recursive=True):
        try:
            content = Path(lock).read_text()
            pid = int(re.search(r'pid=(\d+)', content).group(1)) if re.search(r'pid=(\d+)', content) else 0
            if pid and not _pid_alive(pid):
                Path(lock).unlink()
                print(f"[lock] cleaned stale: {lock}")
        except: pass

def _pid_alive(pid):
    import os
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False

def nudge_agent(cid, text, target):
    """Queue-based nudge: FIFO order, dedup recent, context-aware, no locks."""
    print(f"[nudge] called: cid={cid} target={target} text={text[:50]}")
    _clean_stale_locks()
    company = get_company(cid)
    if not company:
        print(f"[nudge] company not found: {cid}")
        return
    agent = next((a for a in company.get('agents', [])
                  if a['id'] == target.lower()), None)
    if not agent:
        agent = company['agents'][0]
    agent_id = agent.get('agent_id', f"{cid}-{agent['id']}")
    agent_name = agent.get('name', target)
    emoji = agent.get('emoji', '👔')
    aid = agent['id']

    # Build context: newspaper + standup + inbox
    newspaper = generate_newspaper(cid)
    standup = read_agent_standup(cid, aid)
    inbox = read_agent_inbox(cid, aid)
    my_tasks = [t for t in company.get('board_tasks', [])
                if t.get('agent_id') == aid and t.get('status') in ('대기', '진행중')]

    context_parts = []
    if newspaper:
        context_parts.append(f"=== 브리프 ===\n{newspaper}")
    if inbox:
        context_parts.append(f"=== 받은 메시지 (inbox) ===\n{inbox}")
    if standup:
        context_parts.append(f"=== 내 스탠드업 ===\n{standup}")
    if my_tasks:
        task_lines = '\n'.join(f"- [{t.get('status','')}] {t.get('title','')}" for t in my_tasks[:5])
        context_parts.append(f"=== 내 작업 ===\n{task_lines}")
    context = '\n\n'.join(context_parts)
    # Use persistent session for memory continuity
    session_id = f"{agent_id}-main"

    # Queue-based processing
    key = f"{cid}:{aid}"

    def _process(msg):
        nonlocal session_id
        with _AGENT_STATE_LOCK:
            if key in _AGENT_BUSY:
                return
            _AGENT_BUSY.add(key)
        # Global concurrency limit
        acquired = _ACTIVE_SEMAPHORE.acquire(blocking=True, timeout=60)
        if not acquired:
            print(f"[nudge] {aid} dropped: concurrency limit ({_MAX_CONCURRENT})")
            _AGENT_BUSY.discard(key)
            return
        standup = read_agent_standup(cid, aid)
        inbox = read_agent_inbox(cid, aid)
        ctx_parts = []
        if newspaper: ctx_parts.append(f"=== 브리프 ===\n{newspaper}")
        if inbox: ctx_parts.append(f"=== 받은 메시지 (inbox) ===\n{inbox}")
        if standup: ctx_parts.append(f"=== 내 스탠드업 ===\n{standup}")
        # 칸반보드 전체 작업 (누가 뭘 하는지 파악)
        all_tasks = company.get('board_tasks', [])
        active_tasks = [t for t in all_tasks if t.get('status') in ('대기', '진행중')]
        if active_tasks:
            ctx_parts.append("=== 팀 작업 현황 ===\n" + '\n'.join(
                f"- [{t.get('agent_id','')}] [{t.get('status','')}] {t.get('title','')}"
                for t in active_tasks[:10]))
        # 대기 중인 결재
        pending_apr = company.get('approvals', [])
        pending_apr = [a for a in pending_apr if a.get('status') == 'pending']
        if pending_apr:
            ctx_parts.append("=== 대기 중인 결재 ===\n" + '\n'.join(
                f"- {a.get('from_agent','')} {a.get('from_emoji','')}: {a.get('detail','')[:80]}"
                for a in pending_apr[-5:]))
        # 최근 결과물
        import glob as _glob
        del_dir = DATA / cid / '_shared' / 'deliverables'
        if del_dir.exists():
            del_files = sorted(del_dir.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)[:5]
            if del_files:
                ctx_parts.append("=== 최근 결과물 ===\n" + '\n'.join(f"- {f.name}" for f in del_files))
        # 정기작업
        recurring = company.get('recurring_tasks', [])
        if recurring:
            ctx_parts.append("=== 정기 작업 ===\n" + '\n'.join(
                f"- [{t.get('agent_id','')}] {t.get('title','')} ({t.get('interval','')}분마다)"
                for t in recurring[:5]))
        ctx = '\n\n'.join(ctx_parts)
        report_note = '\n\n⚠️ 작업 완료 후 반드시 결과를 @CEO에게 보고하세요. @CEO 멘션을 포함하세요.' if aid != 'ceo' else '\n\n⚠️ 팀원 결과를 취합한 후 @마스터에게 최종 보고하세요. @마스터 멘션을 포함하세요.'
        prompt = f"{ctx}\n\n메시지: {msg}{report_note}" if ctx else msg

        nudge_start = time.time()
        try:
            c = get_company(cid)
            if c:
                for a in c.get('agents', []):
                    if a['id'] == aid:
                        a['status'] = 'working'
                        break
                update_company(cid, {"agents": c['agents']})
            sse_broadcast('agent_thinking', {
                'agent_id': aid, 'cid': cid,
                'prompt_preview': prompt[:80],
                'started_at': time.time()
            })
            c = get_company(cid)
            for t in c.get('board_tasks', []):
                if t.get('agent_id') == aid and t.get('status') == '대기':
                    t['status'] = '진행중'
                    save_company(c)
                    update_company(cid, {'board_tasks': c['board_tasks']})
                    break

            try:
                reply_raw = RUNTIME.run(agent_id, session_id, prompt, timeout=120)
            except subprocess.TimeoutExpired:
                print(f"[nudge] {agent_id} main timeout")
                raise
            elapsed = time.time() - nudge_start
            print(f"[nudge] {agent_id} reply={len(reply_raw)}chars time={elapsed:.1f}s raw={reply_raw[:100]}")

            retry_ok = bool(reply_raw)
            if not reply_raw or 'No reply from agent' in reply_raw:
                print(f"[nudge] {agent_id} no reply, retrying...")
                time.sleep(2)
                try:
                    reply_raw = RUNTIME.run(agent_id, f"{session_id}-retry", prompt, timeout=120)
                    retry_ok = bool(reply_raw)
                except subprocess.TimeoutExpired:
                    retry_ok = False
                print(f"[nudge] {agent_id} retry reply={len(reply_raw)}chars")

            # 3rd attempt: session reset
            if not reply_raw or 'No reply from agent' in reply_raw or not retry_ok:
                print(f"[nudge] {agent_id} 2nd attempt failed, resetting session...")
                time.sleep(5)
                new_session = f"{agent_id}-fresh-{int(time.time())}"
                try:
                    reply_raw = RUNTIME.run(agent_id, new_session, prompt, timeout=120)
                except subprocess.TimeoutExpired:
                    reply_raw = ''
                print(f"[nudge] {agent_id} 3rd attempt reply={len(reply_raw)}chars")
                if reply_raw and 'No reply from agent' not in reply_raw:
                    session_id = new_session  # Use new session going forward

            # All attempts failed — notify user
            if not reply_raw or 'No reply from agent' in reply_raw:
                print(f"[nudge] {agent_id} ALL FAILED, notifying user")
                try:
                    lang = get_company(cid).get('lang', 'ko') if get_company(cid) else 'ko'
                    msg = _s('msg.no_reply', lang, emoji=emoji, name=agent_name)
                    payload = json.dumps({"from": "시스템", "emoji": "⚠️", "text": msg}).encode()
                    req = urllib.request.Request(
                        f'http://localhost:3000/api/agent-msg/{cid}',
                        data=payload, headers={'Content-Type': 'application/json'})
                    _post_local(req.full_url, json.loads(payload))
                except Exception as e:
                    print(f"[nudge] notify failed: {e}")
                reply_raw = ''  # Skip processing below

            if reply_raw and 'No reply from agent' not in reply_raw:
                lines = reply_raw.split('\n')
                clean = '\n'.join(l for l in lines
                                  if not l.startswith('[') and not l.startswith('(agent') and l.strip()).strip()
                if clean:
                    save_agent_memory(cid, aid, clean)
                    process_task_commands(cid, clean, aid)
                    archive_inbox(cid, aid)
                    _check_user_intervention(cid, clean, agent_name)
                    est_tokens = max(len(clean) // 4, 100)
                    est_cost = round(est_tokens * COST_PER_1K_TOKENS / 1000, 6)
                    update_agent_cost(cid, aid, est_tokens, est_cost)
                    c = get_company(cid)
                    if c:
                        for t in c.get('board_tasks', []):
                            if t.get('agent_id') == aid and t.get('status') == '진행중':
                                t['status'] = '완료'
                                t['updated_at'] = datetime.now().isoformat()
                                save_company(c)
                                update_company(cid, {'board_tasks': c['board_tasks']})
                                break
                    chunks = split_message(clean, max_chars=1500)
                    for chunk in chunks:
                        try:
                            payload = json.dumps({"from": agent_name, "emoji": emoji, "text": chunk}).encode()
                            req = urllib.request.Request(
                                f'http://localhost:3000/api/agent-msg/{cid}',
                                data=payload, headers={'Content-Type': 'application/json'})
                            _post_local(req.full_url, json.loads(payload))
                            if len(chunks) > 1: time.sleep(1)
                        except Exception as e:
                            print(f"[nudge] post failed: {e}")

                    # CEO acknowledgment detection: if no @agent mention (only @마스터 doesn't count), nudge again
                    has_agent_mention = bool(re.search(r'@(CMO|CTO|CEO|CFO|COO)', clean, re.IGNORECASE))
                    # Rate limit mentions to prevent ping-pong loops
                    if has_agent_mention:
                        mentions = ','.join(re.findall(r'@(\w+)', clean, re.IGNORECASE))
                        chain = f"{aid}->{mentions}"
                        mention_count = _bump_mention_chain(cid, chain)
                        if mention_count > _MENTION_LIMIT:
                            print(f"[nudge] mention rate limit hit: {chain} ({mention_count})")
                            has_agent_mention = False
                    if aid == 'ceo' and not has_agent_mention and len(clean) < 300:
                        print(f"[nudge] CEO acknowledged without delegation, prompting for plan...")
                        time.sleep(2)
                        followup = f"{agent_name}, 당신은 방금 '{text[:50]}'에 대해 계획만 언급하고 팀원에게 지시하지 않았습니다. 지금 바로 구체적인 계획을 세우고 @CMO @CTO에 각자 해야 할 작업을 @멘션으로 지시하세요. COMPLEX 프로토콜을 따르세요."
                        try:
                            reply_f = RUNTIME.run(agent_id, session_id, followup, timeout=120)
                        except subprocess.TimeoutExpired:
                            reply_f = ''
                        print(f"[nudge] CEO followup reply={len(reply_f)}chars")
                        if reply_f:
                            clean_f = '\n'.join(l for l in reply_f.split('\n')
                                                if not l.startswith('[') and not l.startswith('(agent') and l.strip()).strip()
                            if clean_f:
                                for chunk in split_message(clean_f, max_chars=1500):
                                    try:
                                        payload = json.dumps({"from": agent_name, "emoji": emoji, "text": chunk}).encode()
                                        req = urllib.request.Request(
                                            f'http://localhost:3000/api/agent-msg/{cid}',
                                            data=payload, headers={'Content-Type': 'application/json'})
                                        _post_local(req.full_url, json.loads(payload))
                                    except Exception as e:
                                        print(f"[nudge] followup post failed: {e}")

        except subprocess.TimeoutExpired:
            print(f"[nudge] {agent_id} timeout after {time.time()-nudge_start:.0f}s")
        except Exception as e:
            print(f"[nudge] {agent_id} error: {e}")
        finally:
            sse_broadcast('agent_done', {'agent_id': aid, 'cid': cid})
            _AGENT_BUSY.discard(key)
            _ACTIVE_SEMAPHORE.release()
            try:
                c = get_company(cid)
                if c:
                    for a in c.get('agents', []):
                        if a['id'] == aid:
                            a['status'] = 'active'
                            break
                    update_company(cid, {"agents": c['agents']})
            except: pass
            if key in _AGENT_QUEUES and _AGENT_QUEUES[key]:
                next_text = _AGENT_QUEUES[key].pop(0)
                if not _AGENT_QUEUES[key]:
                    del _AGENT_QUEUES[key]
                threading.Thread(target=_process, args=(next_text,), daemon=True).start()

    if key in _AGENT_BUSY:
        if key not in _AGENT_QUEUES:
            _AGENT_QUEUES[key] = []
        if len(_AGENT_QUEUES[key]) >= 3:
            dropped = _AGENT_QUEUES[key].pop(0)
            print(f"[nudge] {agent_id} queue full, dropped oldest: {dropped[:60]}")
            try:
                append_chat(cid, warn_msg, broadcast=True)
                append_activity(cid, {
                    "time": datetime.now().strftime('%H:%M'),
                    "agent": "시스템",
                    "text": f"{agent_name} queue overflow: oldest request dropped"
                })
            except Exception as e:
                print(f"[nudge] queue overflow notify failed: {e}")
        _AGENT_QUEUES[key].append(text)
        print(f"[nudge] {agent_id} busy, queued (len={len(_AGENT_QUEUES[key])})")
    else:
        threading.Thread(target=_process, args=(text,), daemon=True).start()

def _check_user_intervention(cid, text, from_agent):
    """Detect if agent response contains requests needing user action (credentials, accounts, etc)."""
    keywords = ['비밀번호', '패스워드', 'password', '계정', '아이디', '로그인', 'API 키', 'API key',
                'AWS', 'GCP', 'Stripe', '결제', '신용카드', '도메인', 'SSL', '인증서',
                '외부 서비스', '가입', '회원가입', '인증', 'OTP', '2FA', 'MFA']
    lower = text.lower()
    if not any(kw.lower() in lower for kw in keywords):
        return
    # Avoid duplicate
    if has_pending_approval(cid, 'user_intervention'):
        return
    # Extract relevant snippet
    lines = text.split('\n')
    snippet_lines = [l for l in lines if any(kw.lower() in l.lower() for kw in keywords)][:3]
    snippet = ' '.join(snippet_lines)[:200]
    if not snippet:
        return
    create_approval(cid, 'user_intervention', from_agent,
                      _s('intervention.title', get_company(cid).get('lang','ko') if get_company(cid) else 'ko', agent=from_agent, snippet=snippet))
    print(f"[intervention] {cid}: user action needed from {from_agent}")

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(BASE / "dashboard"), **kw)

    def log_message(self, fmt, *args):
        import sys; sys.stderr.write(f"[HTTP] {fmt % args}\n")

    def _json(self, data, code=200):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self._cors()
        self.end_headers()
        try:
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode())
        except BrokenPipeError:
            pass

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def do_OPTIONS(self):
        self.send_response(200); self._cors(); self.end_headers()

    def do_GET(self):
        if self.path == '/api/sse':
            self._handle_sse()
        elif self.path == '/api/companies':
            self._json(db_get_all_companies())
        elif self.path.startswith('/api/company/'):
            cid = self.path.split('/')[-1]
            company = get_company(cid)
            if company: self._json(company)
            else: self._json({"error": "not found"}, 404)
        elif self.path.startswith('/api/newspaper/'):
            cid = self.path.split('/')[-1]
            brief = generate_newspaper(cid)
            self._json({"newspaper": brief})
        elif self.path.startswith('/api/inbox/'):
            parts = self.path.split('/')
            cid = parts[-2]
            agent_id = parts[-1]
            inbox = read_agent_inbox(cid, agent_id)
            self._json({"inbox": inbox})
        elif self.path.startswith('/api/task-list/'):
            cid = self.path.split('/')[-1]
            self._json(get_recurring_tasks(cid))
        elif self.path.startswith('/api/file/'):
            # 결과물 파일 열기
            rel_path = self.path.replace('/api/file/', '')
            from urllib.parse import unquote
            rel_path = unquote(rel_path)
            # 형식: {cid}/workspaces/{agent}/deliverables/{file}
            parts = rel_path.split('/', 1)
            if len(parts) >= 2:
                cid = parts[0]
                file_rel = parts[1]
                try:
                    # 경로 탐색 공격 방지: 실제 경로를 resolve하고 허용 디렉토리 내인지 확인
                    allowed_base = (DATA / cid).resolve()
                    file_path = (DATA / cid / file_rel).resolve()
                    if not str(file_path).startswith(str(allowed_base) + os.sep) and str(file_path) != str(allowed_base):
                        self._json({'error': 'forbidden'}, 403)
                        return
                    if file_path.exists() and file_path.is_file():
                        content = file_path.read_text(encoding='utf-8')
                        self.send_response(200)
                        self.send_header('Content-Type', 'text/plain; charset=utf-8')
                        self.end_headers()
                        self.wfile.write(content.encode('utf-8'))
                        return
                except (OSError, ValueError) as e:
                    print(f"[WARN] file read error: {e}")
            self._json({'error': 'file not found'}, 404)
        elif self.path.startswith('/api/costs/'):
            cid = self.path.split('/')[-1]
            costs = get_company_costs(cid)
            if costs: self._json(costs)
            else: self._json({"error": "not found"}, 404)
        elif self.path.startswith('/api/goals/'):
            cid = self.path.split('/')[-1]
            self._json(get_goals(cid))
        elif self.path.startswith('/api/board-tasks/'):
            cid = self.path.split('/')[-1]
            self._json(get_board_tasks(cid))
        elif self.path.startswith('/api/deliverables/'):
            cid = self.path.replace('/api/deliverables/', '').split('/')[0]
            shared_dir = DATA / cid / '_shared' / 'deliverables'
            files = []
            if shared_dir.exists():
                for f in sorted(shared_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
                    if f.is_file() and not f.name.startswith('.'):
                        size = f.stat().st_size
                        mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime('%m-%d %H:%M')
                        files.append({'path': f'_shared/deliverables/{f.name}', 'size': size, 'modified': mtime})
            self._json(files[:50])
        elif self.path.startswith('/api/approvals/'):
            cid = self.path.split('/')[-1]
            status_filter = None
            if '?' in self.path:
                qs = self.path.split('?', 1)[1]
                if 'status=pending' in qs:
                    status_filter = 'pending'
            self._json(get_approvals(cid, status_filter))
        elif self.path.startswith('/api/search'):
            self._handle_search()
        elif self.path.startswith('/api/ab-result/'):
            test_id = self.path.split('/')[-1]
            self._json(_AB_RESULTS.get(test_id, {"status": "not_found"}))
        elif self.path.startswith('/api/webhook-routes/'):
            cid = self.path.split('/')[-1]
            self._json(db_get_webhook_routes(cid))
        elif self.path.startswith('/api/snapshots/'):
            cid = self.path.split('/')[-1]
            self._json(db_get_snapshots(cid))
        elif self.path == '/api/agents':
            self._json(AGENT_TEMPLATES)
        elif self.path == '/api/topics':
            self._json(TOPIC_ORGS)
        elif self.path == '/api/langs':
            self._json(LANG)
        else:
            if self.path == '/': self.path = '/index.html'
            return super().do_GET()

    def _handle_search(self):
        """GET /api/search?q=query&cid=... — full-text search across chat messages."""
        qs = self.path.split('?', 1)[1] if '?' in self.path else ''
        params = {}
        for part in qs.split('&'):
            if '=' in part:
                k, v = part.split('=', 1)
                params[k] = urllib.parse.unquote_plus(v)
        query = params.get('q', '').strip()
        cid_filter = params.get('cid', '').strip()
        if not query:
            self._json({"error": "q required"}, 400); return
        company_ids = [cid_filter] if cid_filter else None
        results = db_search_chat(query, company_ids=company_ids, limit=50)
        # Attach company name for display
        company_map = {c['id']: c.get('name', c['id']) for c in db_get_all_companies()}
        for r in results:
            r['company_name'] = company_map.get(r['company_id'], r['company_id'])
        self._json(results)

    def _handle_sse(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'keep-alive')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        wfile = self.wfile
        with SSE_LOCK:
            SSE_CLIENTS.append(wfile)
        try:
            companies = db_get_all_companies()
            initial = json.dumps(companies, ensure_ascii=False)
            wfile.write(f"event: init\ndata: {initial}\n\n".encode())
            wfile.flush()
        except (BrokenPipeError, OSError, ConnectionResetError):
            pass
        try:
            while True:
                time.sleep(30)
                try:
                    wfile.write(b": keepalive\n\n")
                    wfile.flush()
                except (BrokenPipeError, OSError, ConnectionResetError):
                    break
        finally:
            with SSE_LOCK:
                if wfile in SSE_CLIENTS:
                    SSE_CLIENTS.remove(wfile)

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        try:
            body = json.loads(self.rfile.read(length)) if length else {}
        except json.JSONDecodeError:
            self._json({"error": "invalid json"}, 400)
            return
        path = self.path

        # ─── Existing Endpoints ───
        if path == '/api/companies':
            name = body.get('name', '').strip()
            if not name or len(name) > 100:
                self._json({"error": "name must be 1-100 characters"}, 400); return
            lang = body.get('lang', 'ko')
            if lang not in LANG:
                lang = 'ko'
            company = create_company(name, body.get('topic',''), lang)
            self._json({"ok": True, "company": company})

        elif path.startswith('/api/chat/'):
            self._handle_chat(path, body)

        elif path.startswith('/api/agent-msg/'):
            self._handle_agent_msg(path, body)

        elif path == '/api/company/delete':
            self._handle_company_delete(body)

        elif path.startswith('/api/agent-add/'):
            self._handle_agent_add(path, body)

        elif path.startswith('/api/agent-delete/'):
            self._handle_agent_delete(path)

        elif path.startswith('/api/agent-reactivate/'):
            self._handle_agent_reactivate(path)

        elif path.startswith('/api/task-add/'):
            self._handle_task_add(path, body)

        elif path.startswith('/api/task-pause/'):
            parts = path.split('/'); cid, task_id = parts[-2], parts[-1]
            update_task_status(cid, task_id, 'paused')
            _running_task_threads.discard(f"{cid}:{task_id}")
            self._json({"ok": True})

        elif path.startswith('/api/task-resume/'):
            parts = path.split('/'); cid, task_id = parts[-2], parts[-1]
            update_task_status(cid, task_id, 'resumed')
            self._json({"ok": True})

        elif path.startswith('/api/task-delete/'):
            self._handle_task_delete(path)

        elif path.startswith('/api/task-stop/'):
            parts = path.split('/'); cid, task_id = parts[-2], parts[-1]
            update_task_status(cid, task_id, 'stopped')
            _running_task_threads.discard(f"{cid}:{task_id}")
            self._json({"ok": True})

        # ─── New Goal APIs ───
        elif path.startswith('/api/goal-add/'):
            self._handle_goal_add(path, body)

        elif path.startswith('/api/goal-update/'):
            self._handle_goal_update(path, body)

        elif path.startswith('/api/goal-delete/'):
            self._handle_goal_delete(path, body)

        # ─── New Board Task Status API ───
        elif path.startswith('/api/board-task-add/'):
            self._handle_board_task_add(path, body)

        elif path.startswith('/api/task-status/'):
            self._handle_task_status(path, body)

        elif path.startswith('/api/board-task-delete/'):
            self._handle_board_task_delete(path, body)

        # ─── New Approval APIs ───
        elif path.startswith('/api/approval-approve/'):
            self._handle_approval_resolve(path, body, 'approved')

        elif path.startswith('/api/approval-reject/'):
            self._handle_approval_resolve(path, body, 'rejected')

        elif path.startswith('/api/webhook/'):
            self._handle_webhook(path, body)

        elif path.startswith('/api/cross-nudge'):
            self._handle_cross_nudge(path, body)

        elif path.startswith('/api/ab-test/'):
            self._handle_ab_test(path, body)

        elif path.startswith('/api/webhook-route-add/'):
            self._handle_webhook_route_add(path, body)

        elif path.startswith('/api/webhook-route-delete/'):
            self._handle_webhook_route_delete(path, body)

        elif path.startswith('/api/snapshot/'):
            self._handle_snapshot(path, body)

        elif path.startswith('/api/fork/'):
            self._handle_fork(path, body)

        elif path.startswith('/api/restore/'):
            self._handle_restore(path, body)

        elif path.startswith('/api/meeting/'):
            self._handle_meeting(path, body)

        elif path.startswith('/api/daily-report/'):
            self._handle_daily_report(path, body)

        else:
            self._json({"error": "not found"}, 404)


    def _handle_webhook(self, path, body):
        """Receive external webhook and route via webhook_routes table, fallback CEO."""
        cid = path.split('/')[-1]
        company = get_company(cid)
        if not company: self._json({"error": "not found"}, 404); return
        wh_secret = company.get('webhook_secret', '')
        req_secret = self.headers.get('X-Webhook-Secret', '')
        if wh_secret and not hmac.compare_digest(req_secret, wh_secret):
            self._json({"error": "unauthorized"}, 401); return
        payload_str = json.dumps(body, ensure_ascii=False)
        text = body.get('text', body.get('message', payload_str))
        if not text: self._json({"error": "empty"}, 400); return
        now = datetime.now(); time_str = now.strftime('%H:%M')
        msg = {"from": "webhook", "emoji": "🔗", "text": text[:500], "time": time_str, "type": "user"}
        append_chat(cid, msg, broadcast=True)

        # Check webhook routes
        routes = db_get_webhook_routes(cid)
        routed = False
        for route in routes:
            filter_expr = route.get('filter_expr', '').strip()
            # Simple filter: check if filter keyword is present in payload
            if filter_expr and filter_expr.lower() not in payload_str.lower():
                continue
            tmpl = route.get('prompt_template', '') or f"[웹훅 수신 - {route.get('source','custom')}] {text[:300]}"
            # Simple {{field}} substitution from body
            for k, v in body.items():
                tmpl = tmpl.replace('{{'+k+'}}', str(v))
            target = route.get('target_agent', 'CEO')
            threading.Thread(target=nudge_agent, args=(cid, tmpl, target.upper()), daemon=True).start()
            routed = True
        if not routed:
            threading.Thread(target=nudge_agent, args=(cid, f"[웹훅 수신] {text[:200]}", 'CEO'), daemon=True).start()
        self._json({"ok": True, "routed": routed})

    # ─── Snapshot / Fork / Restore ───
    def _handle_snapshot(self, path, body):
        cid = path.split('/')[-1]
        company = get_company(cid)
        if not company: self._json({"error": "not found"}, 404); return
        label = body.get('label', datetime.now().strftime('%Y-%m-%d %H:%M')).strip()[:100]
        snap_id = db_create_snapshot(cid, label, company)
        self._json({"ok": True, "snapshot_id": snap_id, "label": label})

    def _handle_fork(self, path, body):
        """Create a new company from a snapshot."""
        parts = path.split('/')
        snap_id = parts[-1]
        snap = db_get_snapshot(snap_id)
        if not snap: self._json({"error": "snapshot not found"}, 404); return
        data = snap['data']
        new_name = body.get('name', f"{data.get('name','회사')} (포크)").strip()[:100]
        new_cid = f"fork-{uuid.uuid4().hex[:8]}"
        fork_company = dict(data)
        fork_company['id'] = new_cid
        fork_company['name'] = new_name
        fork_company['status'] = 'starting'
        fork_company['created_at'] = datetime.now().isoformat()
        fork_company['activity_log'] = (data.get('activity_log') or [])[-20:]  # Keep recent history
        save_company(fork_company)
        init_companies()
        sse_broadcast('company_update', {"id": new_cid, "company": get_company(new_cid)})
        self._json({"ok": True, "new_cid": new_cid, "name": new_name})

    def _handle_restore(self, path, body):
        """Restore a company to a snapshot state."""
        parts = path.split('/')
        snap_id = parts[-1]
        cid = parts[-2] if len(parts) > 2 else None
        snap = db_get_snapshot(snap_id)
        if not snap: self._json({"error": "snapshot not found"}, 404); return
        if not cid or not get_company(cid): self._json({"error": "company not found"}, 404); return
        data = dict(snap['data'])
        data['id'] = cid  # Keep original ID
        save_company(data)
        refreshed = get_company(cid)
        sse_broadcast('company_update', {"id": cid, "company": refreshed})
        self._json({"ok": True, "restored_to": snap.get('label','')})

    # ─── Webhook Route Management ───
    def _handle_webhook_route_add(self, path, body):
        cid = path.split('/')[-1]
        if not get_company(cid): self._json({"error": "not found"}, 404); return
        source = body.get('source', 'custom').strip()[:50]
        filter_expr = body.get('filter_expr', '').strip()[:200]
        target_agent = body.get('target_agent', 'CEO').strip()[:50]
        prompt_template = body.get('prompt_template', '').strip()[:500]
        route_id = db_add_webhook_route(cid, source, filter_expr, target_agent, prompt_template)
        self._json({"ok": True, "route_id": route_id})

    def _handle_webhook_route_delete(self, path, body):
        cid = path.split('/')[-1]
        route_id = body.get('route_id', '').strip()
        if not route_id: self._json({"error": "route_id required"}, 400); return
        db_delete_webhook_route(cid, route_id)
        self._json({"ok": True})

    # ─── A/B Agent Test Handler ───
    def _handle_ab_test(self, path, body):
        """POST /api/ab-test/{cid} — send same prompt to 2+ agents simultaneously and compare."""
        cid = path.split('/')[-1]
        company = get_company(cid)
        if not company: self._json({"error": "not found"}, 404); return
        text = body.get('text', '').strip()
        agent_ids = body.get('agents', [])
        if not text: self._json({"error": "text required"}, 400); return
        if len(agent_ids) < 2: self._json({"error": "need at least 2 agents"}, 400); return

        test_id = f"ab-{uuid.uuid4().hex[:8]}"
        agents = company.get('agents', [])
        participants = [a for a in agents if a['id'] in agent_ids]
        if len(participants) < 2:
            self._json({"error": "agents not found in company"}, 400); return

        _AB_RESULTS[test_id] = {
            'status': 'running', 'text': text,
            'agents': [{'agent_id': a['id'], 'name': a.get('name',''), 'emoji': a.get('emoji','🤖'),
                        'response': None, 'elapsed': None, 'status': 'waiting'} for a in participants]
        }

        def _run_agent(idx, agent):
            start = time.time()
            _AB_RESULTS[test_id]['agents'][idx]['status'] = 'running'
            ab_prompt = f"[A/B 테스트] {text}\n\n이 지시에 대해 당신의 관점에서 최선의 응답을 작성하세요."
            # Direct subprocess call (bypassing queue for clean comparison)
            agent_id_full = agent.get('agent_id', f"{cid}-{agent['id']}")
            session_id = f"{agent_id_full}-ab-{test_id}"
            try:
                proc = subprocess.Popen(
                    ['openclaw', 'agent', '--agent', agent_id_full,
                     '--session-id', session_id, '--local', '-m', ab_prompt],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                stdout, _ = proc.communicate(timeout=120)
                reply = stdout.decode().strip()
                lines = reply.split('\n')
                clean = '\n'.join(l for l in lines if not l.startswith('[') and not l.startswith('(agent') and l.strip()).strip()
                elapsed = round(time.time() - start, 1)
                _AB_RESULTS[test_id]['agents'][idx]['response'] = clean or '(응답 없음)'
                _AB_RESULTS[test_id]['agents'][idx]['elapsed'] = elapsed
                _AB_RESULTS[test_id]['agents'][idx]['status'] = 'done'
                _AB_RESULTS[test_id]['agents'][idx]['tokens'] = max(len(clean) // 4, 1)
            except Exception as e:
                _AB_RESULTS[test_id]['agents'][idx]['response'] = f'오류: {e}'
                _AB_RESULTS[test_id]['agents'][idx]['status'] = 'error'
            # Check if all done
            if all(r['status'] in ('done','error') for r in _AB_RESULTS[test_id]['agents']):
                _AB_RESULTS[test_id]['status'] = 'done'
                sse_broadcast('ab_done', {'test_id': test_id})

        for i, a in enumerate(participants):
            threading.Thread(target=_run_agent, args=(i, a), daemon=True).start()
        self._json({"ok": True, "test_id": test_id})

    # ─── Cross-Company Outsourcing Handler ───
    def _handle_cross_nudge(self, path, body):
        """POST /api/cross-nudge — delegate work from one company's agent to another."""
        from_cid = body.get('from_cid', '').strip()
        to_cid = body.get('to_cid', '').strip()
        from_agent = body.get('from_agent', '').strip()
        to_agent = body.get('to_agent', '').strip()
        text = body.get('text', '').strip()
        if not all([from_cid, to_cid, text]):
            self._json({"error": "from_cid, to_cid, text required"}, 400); return
        from_company = get_company(from_cid)
        to_company = get_company(to_cid)
        if not from_company: self._json({"error": f"company not found: {from_cid}"}, 404); return
        if not to_company: self._json({"error": f"company not found: {to_cid}"}, 404); return

        from_name = from_company.get('name', from_cid)
        to_name = to_company.get('name', to_cid)
        if not to_agent:
            agents = to_company.get('agents', [])
            to_agent = agents[0]['id'] if agents else 'ceo'
        outsource_text = (
            f"🔗 [아웃소싱 의뢰] {from_name} → {to_name}\n"
            f"의뢰 내용: {text}\n\n"
            "외부 파트너사 요청입니다. 전문적으로 검토하고 결과를 응답하세요."
        )

        def _run():
            # Send task to target company
            nudge_agent(to_cid, outsource_text, to_agent.upper())
            # Log in source company's chat
            now_str = datetime.now().isoformat()
            src_msg = {
                'from': f'🔗 아웃소싱→{to_name}',
                'emoji': '🔗', 'text': f"[외주 의뢰] {to_name} {to_agent.upper()}에게 전달:\n{text}",
                'time': now_str, 'type': 'system'
            }
            append_chat(from_cid, src_msg, broadcast=True)
        threading.Thread(target=_run, daemon=True).start()
        self._json({"ok": True, "from": from_name, "to": to_name, "to_agent": to_agent})

    # ─── Meeting Handler ───
    def _handle_meeting(self, path, body):
        cid = path.split('/')[-1]
        company = get_company(cid)
        if not company: self._json({"error": "not found"}, 404); return
        topic = body.get('topic', '').strip()
        if not topic: self._json({"error": "topic required"}, 400); return
        agent_ids = body.get('agents', [a['id'] for a in company.get('agents', [])[:4]])
        threading.Thread(target=run_meeting, args=(cid, topic, agent_ids), daemon=True).start()
        self._json({"ok": True, "topic": topic, "participants": len(agent_ids)})

    # ─── Daily Report Handler ───
    def _handle_daily_report(self, path, body):
        cid = path.split('/')[-1]
        company = get_company(cid)
        if not company: self._json({"error": "not found"}, 404); return
        agents = company.get('agents', [])
        ceo = next((a for a in agents if a['id'] == 'ceo'), agents[0] if agents else None)
        if not ceo: self._json({"error": "no agents"}, 400); return
        task = add_recurring_task(
            cid,
            title='📊 데일리 리포트',
            prompt=(
                "오늘 하루 팀 전체의 업무 성과를 정리하여 데일리 리포트를 작성하세요.\n"
                "포함 사항: ✅ 완료된 작업, ⏳ 진행 중인 작업, ⚠️ 이슈/블로커, 내일 계획.\n"
                "결과를 @마스터에게 보고하세요."
            ),
            interval_minutes=1440,  # 1 day
            agent_id=ceo['id'],
            agent_name=ceo['name'],
            agent_emoji=ceo.get('emoji', '👔')
        )
        self._json({"ok": True, "task": task})

    # ─── Chat Handler ───
    def _handle_chat(self, path, body):
        cid = path.split('/')[-1]
        text = body.get('text', '').strip()
        if not text: self._json({"error": "empty"}, 400); return
        company = get_company(cid)
        if not company: self._json({"error": "not found"}, 404); return
        lang = company.get('lang', 'ko')

        now = datetime.now(); time_str = now.strftime('%H:%M')
        # ── 멘션 분리: @로 시작하면 멘션 메시지, 아니면 일반 채팅 ──
        is_mention_msg = text.lstrip().startswith('@')
        targets = []
        instruction = text

        if is_mention_msg:
            # ```블록 멘션: @AGENT ```내용```
            block_match = re.match(r'@(\w+)\s*```([\s\S]*?)```', text)
            if block_match:
                m_name = block_match.group(1)
                instruction = block_match.group(2).strip()
                if m_name.lower() in {a['id'].lower() for a in company.get('agents', [])}:
                    targets.append(m_name.upper())
            else:
                # 한줄 멘션: @AGENT1 @AGENT2 나머지
                mention_matches = re.findall(r'@(\w+)', text)
                agent_ids_lower = {a['id'].lower() for a in company.get('agents', [])}
                for m in mention_matches:
                    if m.lower() in agent_ids_lower:
                        upper = m.upper()
                        if upper not in targets:
                            targets.append(upper)
                instruction = re.sub(r'@\w+\s*', '', text).strip()

        # 일반 채팅이면 CEO에게만 전달
        if not targets:
            targets = ['CEO']

        parent_id = body.get('parent_id')
        msg = {"from": "마스터", "text": text, "time": time_str, "type": "user", "mention": is_mention_msg,
               "parent_id": parent_id}

        append_chat(cid, msg, broadcast=False)
        targets_str = ', '.join(f'@{t}' for t in targets)
        append_activity(cid, {"time": time_str, "agent": "마스터", "text": f"{targets_str} {instruction}" if is_mention_msg else text})

        queue_file = DATA / f"{cid}-queue.json"
        queue = load_json(queue_file, [])
        queue.append({"text": text, "time": now.isoformat(), "target": targets[0], "processed": False, "id": now.timestamp()})
        save_json(queue_file, queue)

        self._json({"ok": True, "msg": msg, "target": targets[0] if targets else 'CEO'})

        # 멘션 메시지면 에이전트들에게 instruction 전달
        if is_mention_msg and instruction:
            # 멘션 내용에서 작업 자동 추출 → 타겟 칸반에 추가 + inbox에 기록
            for target in targets:
                task_title = extract_task_from_instruction(instruction) or instruction[:30]
                add_board_task(cid, task_title, target.lower(), '대기', [], '')
                add_to_inbox(cid, target.lower(), '마스터' if lang=='ko' else 'Master', instruction, lang)
                refreshed_company = get_company(cid)
                if refreshed_company:
                    update_company(cid, {'board_tasks': refreshed_company.get('board_tasks', [])})
                else:
                    print(f"[WARN] board_tasks update skipped, company missing: {cid}")
                threading.Thread(target=nudge_agent, args=(cid, instruction, target), daemon=True).start()
        elif not is_mention_msg:
            # 일반 채팅 → CEO가 응답
            print(f"[chat] dispatching to CEO: {text[:50]}")
            threading.Thread(target=nudge_agent, args=(cid, text, 'CEO'), daemon=True).start()

    # ─── Agent Message Handler ───
    def _handle_agent_msg(self, path, body):
        cid = path.split('/')[-1]
        from_agent = body.get('from', 'CEO')
        to_agent = body.get('to', 'CEO')
        text = body.get('text', '').strip()
        emoji = body.get('emoji', '👔')
        if not text or text in ('No reply from agent.', ''): self._json({"ok": False, "reason": "empty/no_reply"}); return

        company = get_company(cid)
        if not company: self._json({"error": "not found"}, 404); return
        now = datetime.now(); time_str = now.strftime('%H:%M')

        # @마스터 멘션 감지 → 채팅 표시 + 승인 탭에도 추가
        master_mention = re.search(r'@\uB9C8\uC2A4\uD130 ?(.*)', text, re.DOTALL)
        master_request = ''
        if master_mention:
            master_request = master_mention.group(1).strip()
            text = re.sub(r'@\uB9C8\uC2A4\uD130 ?', '', text).strip()
            is_long = len(master_request) > 100 or master_request.count('\n') >= 2
            # 짧은 건 채팅에도 표시, 긴 건 결재만
            if not is_long:
                chat_msg = {"from": from_agent, "emoji": emoji, "text": f"@마스터 {master_request}", "time": time_str, "type": "agent", "mention": True}
                append_chat(cid, chat_msg, broadcast=True)
            append_activity(cid, {"time": time_str, "agent": from_agent, "text": f"@마스터 {master_request[:50]}{'...' if len(master_request)>50 else ''}"})
            # 결재 탭에 전체 내용 저장
            approval_item = {
                'id': str(uuid.uuid4())[:8],
                'from_agent': from_agent,
                'from_emoji': emoji,
                'type': '보고서' if is_long else '요청',
                'detail': master_request,
                'status': 'pending',
                'time': time_str,
                'created_at': datetime.now().isoformat()
            }
            append_approval(cid, approval_item)
            print(f"[master] {from_agent} → @마스터: {master_request[:60]}")
        
        # 에이전트 응답에서 멘션 부분과 일반 부분 분리
        has_mentions = bool(re.search(r'@([A-Za-z0-9]+)', text))
        normal_text, mention_text = text, ''
        if has_mentions:
            normal_parts, mention_parts = [], []
            block_re = re.compile(r'@([A-Za-z0-9]+)\s*```([\s\S]*?)```', re.MULTILINE)
            existing_ids = {a['id'].lower() for a in company.get('agents', [])}
            # 블록 멘션 추출
            for bm in block_re.finditer(text):
                m_name = bm.group(1)
                before = text[:bm.start()].strip()
                after = text[bm.end():].strip()
                if m_name.lower() in existing_ids:
                    mention_parts.append(f"@{bm.group(1)} {bm.group(2).strip()}")
                else:
                    # Not a known agent - treat as normal text
                    mention_parts.append(text[bm.start():bm.end()].strip())
                if before: normal_parts.append(before)
                if after and not re.match(r'@', after): normal_parts.append(after)
            if not mention_parts:
                # 블록 없으면 한줄 멘션 (with or without content)
                for line in text.split('\n'):
                    stripped = line.strip()
                    lm = re.match(r'@([A-Za-z0-9]+)(?:\s+(.+))?', stripped)
                    if lm and lm.group(1).upper() != from_agent.upper() and lm.group(1).lower() in existing_ids:
                        mention_parts.append(stripped)
                    else:
                        normal_parts.append(line)
            normal_text = '\n'.join(normal_parts).strip()
            mention_text = '\n'.join(mention_parts).strip()
        
        response_msg = None

        # 일반 텍스트가 있으면 채팅에 저장
        if normal_text:
            response_msg = {"from": from_agent, "emoji": emoji, "text": normal_text, "time": time_str, "type": "agent"}
        
        # 멘션 텍스트가 있으면 별도 채팅에 저장 (from = 멘션 발신자, mention = true)
        if mention_text:
            # 각 멘션 라인별로 저장
            for ml in mention_text.split('\n'):
                ml = ml.strip()
                if not ml: continue
                if response_msg is None:
                    response_msg = {"from": from_agent, "emoji": emoji, "text": ml, "time": time_str, "type": "agent", "mention": True}

        if response_msg:
            chat_messages = []
            if normal_text:
                chat_messages.append({"from": from_agent, "emoji": emoji, "text": normal_text, "time": time_str, "type": "agent"})
            if mention_text:
                for ml in mention_text.split('\n'):
                    ml = ml.strip()
                    if ml:
                        chat_messages.append({"from": from_agent, "emoji": emoji, "text": ml, "time": time_str, "type": "agent", "mention": True})
            append_chats(cid, chat_messages, broadcast=False)
        if normal_text:
            append_activity(cid, {"time": time_str, "agent": from_agent, "text": normal_text})
        if mention_text:
            append_activity(cid, {"time": time_str, "agent": from_agent, "text": mention_text})
        self._json({"ok": True, "msg": response_msg})

        # Detect user intervention needed (credentials, external accounts, etc)
        _check_user_intervention(cid, text, from_agent)

        # Agent-to-agent mentions: nudge mentioned agents
        if mention_text:
            existing_ids = {a['id'].lower() for a in company.get('agents', [])}
            for ml in mention_text.split('\n'):
                ml = ml.strip()
                if not ml: continue
                for m in re.findall(r'@([A-Za-z0-9]+)', ml):
                    if m.lower() in existing_ids and m.upper() != from_agent.upper():
                        target = m.upper()
                        instruction = re.sub(r'@[A-Za-z0-9]+\s*', '', ml).strip()
                        if not instruction:
                            # No block content, use full text as context
                            instruction = text
                        add_to_inbox(cid, target.lower(), from_agent, instruction, company.get('lang','ko'))
                        task_title = extract_task_from_instruction(instruction) or instruction[:30]
                        add_board_task(cid, task_title, target.lower(), '대기', [], '')
                        refreshed = get_company(cid)
                        if refreshed:
                            update_company(cid, {'board_tasks': refreshed.get('board_tasks', [])})
                        print(f"[agent-mention] {from_agent} → @{target}: {instruction[:60]}")
                        threading.Thread(target=nudge_agent, args=(cid, instruction, target), daemon=True).start()
                        break  # one nudge per line

        company_after_update = get_company(cid)
        if not company_after_update:
            print(f"[WARN] company missing after update: {cid}")
            return

        # Chain mentions (에이전트→에이전트)
        existing_ids = {a['id'].lower() for a in company.get('agents', [])}
        block_re = re.compile(r'@(\w+)\s*```([\s\S]*?)```', re.MULTILINE)
        line_re = re.compile(r'@(\w+)\s+(.+)')
        seen = set()
        # 먼저 모든 멘션을 수집
        pending_mentions = []
        for bm in block_re.finditer(mention_text or text):
            m_name = bm.group(1)
            instruction = bm.group(2).strip()
            upper = m_name.upper()
            if upper != from_agent.upper() and m_name.lower() in existing_ids and upper not in seen:
                seen.add(upper)
                pending_mentions.append((upper, instruction))
        for line in (mention_text or text).split('\n'):
            lm = line_re.match(line.strip())
            if lm:
                m_name = lm.group(1)
                instruction = lm.group(2).strip()
                upper = m_name.upper()
                if upper != from_agent.upper() and m_name.lower() in existing_ids and upper not in seen:
                    seen.add(upper)
                    pending_mentions.append((upper, instruction))
        # 한 번에 대기열 추가 + 에이전트 실행
        for upper, instruction in pending_mentions:
            task_title = extract_task_from_instruction(instruction) or instruction[:30]
            add_board_task(cid, task_title, upper.lower(), '대기', [], '')
        if pending_mentions:
            refreshed_company = get_company(cid)
            if refreshed_company:
                update_company(cid, {'board_tasks': refreshed_company.get('board_tasks', [])})
            else:
                print(f"[WARN] board_tasks update skipped, company missing: {cid}")
        for upper, instruction in pending_mentions:
            lock_key = f"{cid}:{upper}"
            print(f"[auto-task] {upper}: task queued (chain mention)")
            threading.Thread(target=nudge_agent, args=(cid, instruction, upper), daemon=True).start()

    # ─── Company Delete Handler ───
    def _handle_company_delete(self, path, body):
        cid = body.get('id')
        company = get_company(cid)
        if not company: self._json({"ok": True}); return
        for agent in company.get('agents', []):
            agent_id = agent.get('agent_id', '')
            if agent_id:
                ok = RUNTIME.delete(agent_id)
                print(f"[delete] agent {agent_id} {'removed' if ok else 'delete failed'}")
        import shutil
        company_dir = DATA / cid
        if company_dir.exists():
            shutil.rmtree(company_dir, ignore_errors=True)
        # Remove state files
        for suffix in ['.json', '.json.bak', '-queue.json', '-queue.json.bak']:
            f = DATA / f"{cid}{suffix}"
            if f.exists(): f.unlink()
        companies = load_json(COMPANIES_FILE)
        companies = [c for c in companies if c["id"] != cid]
        save_json(COMPANIES_FILE, companies)
        sse_broadcast('company_update', {"id": cid, "deleted": True})
        self._json({"ok": True})

    # ─── Agent Add Handler ───
    def _handle_agent_add(self, path, body):
        cid = path.split('/')[-1]
        company = get_company(cid)
        if not company: self._json({"error": "not found"}, 404); return

        name = body.get('name', '').strip()
        role = body.get('role', '').strip()
        emoji = body.get('emoji', '🤖')
        prompt = body.get('prompt', '').strip()
        if not name or not role: self._json({"error": "name and role required"}, 400); return

        # Check if approval needed for agent addition (if already >= 6 agents)
        if len(company.get('agents', [])) >= 6:
            approval = create_approval(cid, 'agent_add', name,
                                       f"에이전트 추가 요청: {emoji} {name} ({role})")
            self._json({"ok": True, "pending_approval": True, "approval_id": approval['id'],
                        "message": "에이전트가 6명 이상이므로 승인이 필요합니다."})
            return

        self._do_add_agent(cid, company, name, role, emoji, prompt)

    def _do_add_agent(self, cid, company, name, role, emoji, prompt):
        aid = re.sub(r'[^a-z0-9]', '-', name.lower())
        agent_id = f"{cid}-{aid}"
        agent_workspace = DATA / cid / "workspaces" / aid
        register_agent(agent_id, agent_workspace, name, role, company.get('name',''), emoji, lang=company.get('lang','ko'), wait=True)
        agent = {
            "id": aid, "agent_id": agent_id, "name": name, "emoji": emoji,
            "role": role, "status": "active",
            "tasks": [], "messages": [], "prompt": prompt,
            "cost": {"total_tokens": 0, "total_cost": 0.0, "last_run_cost": 0.0},
        }
        if not any(a['id'] == aid for a in company['agents']):
            company['agents'].append(agent)
            now = datetime.now().strftime('%H:%M')
            company['activity_log'].append({"time": now, "agent": "CEO", "text": f"🆕 {emoji} {name} ({role}) 합류"})
            update_company(cid, {"agents": company['agents'], "activity_log": company['activity_log']})
        self._json({"ok": True, "agent": agent})

    # ─── Agent Delete Handler ───
    def _handle_agent_delete(self, path):
        parts = path.split('/'); cid = parts[-2]; aid = parts[-1]
        company = get_company(cid)
        if not company: self._json({"error": "not found"}, 404); return
        agent = next((a for a in company['agents'] if a['id'] == aid), None)
        if not agent: self._json({"error": "agent not found"}, 404); return
        agent_id = agent.get('agent_id', '')
        if agent_id:
            RUNTIME.delete(agent_id)
        company['agents'] = [a for a in company['agents'] if a['id'] != aid]
        now = datetime.now().strftime('%H:%M')
        company['activity_log'].append({"time": now, "agent": "CEO", "text": f"👋 {agent.get('emoji','🤖')} {agent['name']} 퇴사"})
        update_company(cid, {"agents": company['agents'], "activity_log": company['activity_log']})
        self._json({"ok": True})

    # ─── Agent Reactivate Handler ───
    def _handle_agent_reactivate(self, path):
        parts = path.split('/'); cid, aid = parts[-2], parts[-1]
        company = get_company(cid)
        if not company: self._json({"error": "not found"}, 404); return
        agent = next((a for a in company['agents'] if a['id'] == aid), None)
        if not agent: self._json({"error": "agent not found"}, 404); return
        agent_id = agent.get('agent_id', f"{cid}-{aid}")
        agent_workspace = DATA / cid / "workspaces" / aid
        try:
            sessions_dir = Path.home() / '.openclaw' / 'agents' / agent_id / 'sessions'
            if sessions_dir.exists():
                import shutil
                shutil.rmtree(sessions_dir, ignore_errors=True)
                sessions_dir.mkdir(parents=True, exist_ok=True)
            RUNTIME.delete(agent_id)
            time.sleep(1)
            RUNTIME.register(agent_id, str(agent_workspace))
            try:
                RUNTIME.run(agent_id, f"{agent_id}-reactivate",
                            f'당신은 {agent["name"]}({agent["role"]})입니다. 확인만 하세요.',
                            timeout=30)
            except Exception:
                pass
            for a2 in company['agents']:
                if a2['id'] == aid: a2['status'] = 'active'; break
            update_company(cid, {"agents": company["agents"]})
            now = datetime.now().strftime('%H:%M')
            company = get_company(cid)
            company['activity_log'].append({"time": now, "agent": "시스템",
                "text": f"🔄 {agent.get('emoji','')} {agent['name']} 재활성화 완료 (대화 내용 유지)"})
            update_company(cid, {"activity_log": company["activity_log"]})
            self._json({"ok": True})
        except Exception as e:
            self._json({"error": str(e)}, 500)

    # ─── Task Add Handler ───
    def _handle_task_add(self, path, body):
        cid = path.split('/')[-1]
        company = get_company(cid)
        if not company: self._json({"error": "not found"}, 404); return
        title = body.get('title', '').strip()
        prompt = body.get('prompt', '').strip()
        interval = body.get('interval_minutes', 60)
        agent_id = body.get('agent_id', 'ceo')
        agent = next((a for a in company.get('agents', []) if a['id'] == agent_id), None)
        if not agent: agent = company['agents'][0] if company.get('agents') else None
        if not agent: self._json({"error": "no agents"}, 400); return
        cron_expression = body.get('cron_expression', '').strip()
        if not title or not prompt: self._json({"error": "title and prompt required"}, 400); return
        task = add_recurring_task(cid, title, prompt, interval, agent['id'], agent['name'], agent['emoji'], cron_expression=cron_expression)
        if task:
            now_str = datetime.now().strftime('%H:%M')
            company = get_company(cid)
            schedule_desc = cron_expression if cron_expression else f"{interval}분마다"
            company["chat"].append({"type": "system", "from": "시스템", "emoji": "🔄", "to": "",
                "text": f"🔄 정기 작업 생성: \"{title}\" ({schedule_desc})"})
            company["activity_log"].append({"time": now_str, "agent": "시스템", "text": f"🔄 정기 작업: {title}"})
            update_company(cid, {"chat": company["chat"], "activity_log": company["activity_log"]})
            self._json({"ok": True, "task": task})
        else:
            self._json({"error": "failed to create task"}, 500)

    # ─── Task Delete Handler ───
    def _handle_task_delete(self, path):
        parts = path.split('/'); cid, task_id = parts[-2], parts[-1]
        company = get_company(cid)
        if not company: self._json({"error": "not found"}, 404); return
        tasks = company.get('recurring_tasks', [])
        task = next((t for t in tasks if t['id'] == task_id), None)
        if not task: self._json({"error": "task not found"}, 404); return
        company['recurring_tasks'] = [t for t in tasks if t['id'] != task_id]
        _running_task_threads.discard(f"{cid}:{task_id}")
        now = datetime.now().strftime('%H:%M')
        company['activity_log'].append({"time": now, "agent": "시스템", "text": f"🗑️ 작업 삭제: \"{task['title']}\""})
        update_company(cid, {"recurring_tasks": company['recurring_tasks'], "activity_log": company['activity_log']})
        self._json({"ok": True})

    # ─── Goal Add Handler ───
    def _handle_goal_add(self, path, body):
        cid = path.split('/')[-1]
        company = get_company(cid)
        if not company: self._json({"error": "not found"}, 404); return
        title = body.get('title', '').strip()
        task_ids = body.get('task_ids', [])
        if not title: self._json({"error": "title required"}, 400); return
        goal = add_goal(cid, title, task_ids)
        if goal:
            now_str = datetime.now().strftime('%H:%M')
            company = get_company(cid)
            company['activity_log'].append(
                {"time": now_str, "agent": "시스템", "text": f"🎯 목표 추가: \"{title}\""})
            update_company(cid, {'activity_log': company['activity_log']})
            self._json({"ok": True, "goal": goal})
        else:
            self._json({"error": "failed"}, 500)

    # ─── Goal Update Handler ───
    def _handle_goal_update(self, path, body):
        cid = path.split('/')[-1]
        goal_id = body.get('goal_id', '')
        if not goal_id: self._json({"error": "goal_id required"}, 400); return
        kwargs = {}
        for k in ('title', 'status', 'task_ids'):
            if k in body:
                kwargs[k] = body[k]
        goals = update_goal(cid, goal_id, **kwargs)
        if goals is not None:
            self._json({"ok": True, "goals": goals})
        else:
            self._json({"error": "not found"}, 404)

    # ─── Goal Delete Handler ───
    def _handle_goal_delete(self, path, body):
        cid = path.split('/')[-1]
        goal_id = body.get('goal_id', '')
        if not goal_id: self._json({"error": "goal_id required"}, 400); return
        delete_goal(cid, goal_id)
        self._json({"ok": True})

    # ─── Board Task Add Handler ───
    def _handle_board_task_add(self, path, body):
        cid = path.split('/')[-1]
        company = get_company(cid)
        if not company: self._json({"error": "not found"}, 404); return
        title = body.get('title', '').strip()
        if not title: self._json({"error": "title required"}, 400); return
        agent_id = body.get('agent_id', '')
        depends_on = body.get('depends_on', [])
        deadline = body.get('deadline', '')
        task = add_board_task(cid, title, agent_id, '대기', depends_on, deadline)
        if task:
            now_str = datetime.now().strftime('%H:%M')
            company = get_company(cid)
            company['activity_log'].append(
                {"time": now_str, "agent": "시스템", "text": f"📌 작업 추가: \"{title}\""})
            update_company(cid, {'activity_log': company['activity_log']})
            self._json({"ok": True, "task": task})
        else:
            self._json({"error": "failed"}, 500)

    # ─── Task Status Update Handler ───
    def _handle_task_status(self, path, body):
        cid = path.split('/')[-1]
        task_id = body.get('task_id', '')
        new_status = body.get('status', '')
        if not task_id or not new_status: self._json({"error": "task_id and status required"}, 400); return
        task, unlocked = update_board_task_status(cid, task_id, new_status)
        if task:
            self._json({"ok": True, "task": task, "unlocked": unlocked})
        else:
            self._json({"error": "task not found"}, 404)

    # ─── Board Task Delete Handler ───
    def _handle_board_task_delete(self, path, body):
        cid = path.split('/')[-1]
        task_id = body.get('task_id', '')
        if not task_id: self._json({"error": "task_id required"}, 400); return
        delete_board_task(cid, task_id)
        self._json({"ok": True})

    # ─── Approval Resolve Handler ───
    def _handle_approval_resolve(self, path, body, resolution):
        cid = path.split('/')[-1]
        approval_id = body.get('approval_id', '')
        if not approval_id: self._json({"error": "approval_id required"}, 400); return
        approval = resolve_approval(cid, approval_id, resolution)
        if approval:
            # Pass user response back to the requesting agent
            response_text = body.get('response', '').strip()
            from_agent = approval.get('from_agent', '')
            if from_agent and response_text:
                agent_id = f"{cid}-{from_agent.lower()}"
                nudge_msg = f"@{from_agent} 마스터의 결재 응답입니다:\n{response_text}"
                print(f"[approval] sending response to {from_agent}: {response_text[:60]}")
                threading.Thread(target=nudge_agent, args=(cid, nudge_msg, from_agent.upper()), daemon=True).start()
            elif from_agent and resolution == 'approved' and not response_text:
                agent_id = f"{cid}-{from_agent.lower()}"
                nudge_msg = f"@{from_agent} 마스터가 결재를 승인했습니다."
                print(f"[approval] sending approval to {from_agent}")
                threading.Thread(target=nudge_agent, args=(cid, nudge_msg, from_agent.upper()), daemon=True).start()
            elif from_agent and resolution == 'rejected':
                agent_id = f"{cid}-{from_agent.lower()}"
                nudge_msg = f"@{from_agent} 마스터가 결재를 반려했습니다."
                print(f"[approval] sending rejection to {from_agent}")
                threading.Thread(target=nudge_agent, args=(cid, nudge_msg, from_agent.upper()), daemon=True).start()

            # If agent addition was approved, actually add the agent
            if resolution == 'approved' and approval.get('type') == 'agent_add':
                company = get_company(cid)
                if company:
                    detail = approval.get('detail', '')
                    # Extract name/role from detail text like "에이전트 추가 요청: 🤖 이름 (역할)"
                    import re as _re
                    m = _re.search(r'(\S+)\s*\(([^)]+)\)', detail)
                    if m:
                        add_name = m.group(1)
                        add_role = m.group(2)
                        # Find emoji from detail
                        em = _re.search(r'([\U0001F300-\U0001F9FF])', detail)
                        add_emoji = em.group(1) if em else '🤖'
                        self._do_add_agent(cid, company, add_name, add_role, add_emoji, '')
                    else:
                        print(f"[approval] could not parse agent detail: {detail}")
            self._json({"ok": True, "approval": approval})
        else:
            self._json({"error": "not found"}, 404)

# ─── FastAPI App ───────────────────────────────────────────────────────────
#
# _CallContext lets us reuse the existing Handler._handle_* methods without
# rewriting them. FastAPI routes create a ctx, bind Handler's unbound methods
# to it, call them, and return ctx._result.
# ───────────────────────────────────────────────────────────────────────────

class _CallContext:
    """Minimal fake 'self' that captures _json() calls from Handler methods."""
    _result = None
    _status = 200

    def __init__(self, path: str = '', headers: dict | None = None):
        self.path = path
        self.headers = headers or {}

    def _json(self, data, code: int = 200):
        self._result = data
        self._status = code

    def _cors(self):
        pass


def _call(method_fn, path: str, *args, headers: dict | None = None):
    """Call an unbound Handler method via _CallContext and return (data, status)."""
    ctx = _CallContext(path, headers)
    method_fn(ctx, path, *args)
    return ctx._result or {"ok": True}, ctx._status


app = FastAPI(title="AI Company Hub")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# ── Startup: capture the event loop for thread-safe SSE broadcasts ──

@app.on_event("startup")
async def _on_startup():
    global _EVENT_LOOP
    _EVENT_LOOP = asyncio.get_running_loop()

# ── SSE ────────────────────────────────────────────────────────────────────

@app.get("/api/sse")
async def api_sse():
    q: asyncio.Queue = asyncio.Queue()
    with SSE_QUEUES_LOCK:
        SSE_QUEUES.append(q)

    async def _generate():
        companies = await asyncio.to_thread(db_get_all_companies)
        yield f"event: init\ndata: {json.dumps(companies, ensure_ascii=False)}\n\n"
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=30)
                    yield msg
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            with SSE_QUEUES_LOCK:
                if q in SSE_QUEUES:
                    SSE_QUEUES.remove(q)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},
    )

# ── GET routes ─────────────────────────────────────────────────────────────

@app.get("/api/companies")
def api_get_companies():
    return db_get_all_companies()

@app.get("/api/company/{cid}")
def api_get_company(cid: str):
    company = get_company(cid)
    if company:
        return company
    raise HTTPException(status_code=404, detail="not found")

@app.get("/api/newspaper/{cid}")
def api_get_newspaper(cid: str):
    return {"newspaper": generate_newspaper(cid)}

@app.get("/api/inbox/{cid}/{agent_id}")
def api_get_inbox(cid: str, agent_id: str):
    return {"inbox": read_agent_inbox(cid, agent_id)}

@app.get("/api/task-list/{cid}")
def api_get_task_list(cid: str):
    return get_recurring_tasks(cid)

@app.get("/api/file/{file_path:path}")
def api_get_file(file_path: str):
    from urllib.parse import unquote
    file_path = unquote(file_path)
    parts = file_path.split('/', 1)
    if len(parts) < 2:
        raise HTTPException(status_code=404, detail="file not found")
    cid, file_rel = parts[0], parts[1]
    try:
        allowed_base = (DATA / cid).resolve()
        fp = (DATA / cid / file_rel).resolve()
        if not str(fp).startswith(str(allowed_base) + os.sep):
            raise HTTPException(status_code=403, detail="forbidden")
        if fp.exists() and fp.is_file():
            return PlainTextResponse(fp.read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        print(f"[WARN] file read error: {e}")
    raise HTTPException(status_code=404, detail="file not found")

@app.get("/api/costs/{cid}")
def api_get_costs(cid: str):
    costs = get_company_costs(cid)
    if costs:
        return costs
    raise HTTPException(status_code=404, detail="not found")

@app.get("/api/goals/{cid}")
def api_get_goals(cid: str):
    return get_goals(cid)

@app.get("/api/board-tasks/{cid}")
def api_get_board_tasks(cid: str):
    return get_board_tasks(cid)

@app.get("/api/deliverables/{cid}")
def api_get_deliverables(cid: str):
    shared_dir = DATA / cid / '_shared' / 'deliverables'
    files = []
    if shared_dir.exists():
        for f in sorted(shared_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if f.is_file() and not f.name.startswith('.'):
                size = f.stat().st_size
                mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime('%m-%d %H:%M')
                files.append({'path': f'_shared/deliverables/{f.name}', 'size': size, 'modified': mtime})
    return files[:50]

@app.get("/api/approvals/{cid}")
def api_get_approvals(cid: str, status: str | None = None):
    return get_approvals(cid, status if status else None)

@app.get("/api/search")
def api_search(q: str | None = None, cid: str | None = None):
    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="q required")
    company_ids = [cid] if cid else None
    results = db_search_chat(q, company_ids=company_ids, limit=50)
    company_map = {c['id']: c.get('name', c['id']) for c in db_get_all_companies()}
    for r in results:
        r['company_name'] = company_map.get(r['company_id'], r['company_id'])
    return results

@app.get("/api/ab-result/{test_id}")
def api_ab_result(test_id: str):
    return _AB_RESULTS.get(test_id, {"status": "not_found"})

@app.get("/api/webhook-routes/{cid}")
def api_get_webhook_routes(cid: str):
    return db_get_webhook_routes(cid)

@app.get("/api/snapshots/{cid}")
def api_get_snapshots(cid: str):
    return db_get_snapshots(cid)

@app.get("/api/agents")
def api_get_agents():
    return AGENT_TEMPLATES

@app.get("/api/topics")
def api_get_topics():
    return TOPIC_ORGS

@app.get("/api/langs")
def api_get_langs():
    return LANG

# ── POST routes ────────────────────────────────────────────────────────────

@app.post("/api/companies")
async def api_create_company(request: Request):
    body = await request.json()
    name = body.get('name', '').strip()
    if not name or len(name) > 100:
        raise HTTPException(status_code=400, detail="name must be 1-100 characters")
    lang = body.get('lang', 'ko')
    if lang not in LANG:
        lang = 'ko'
    company = create_company(name, body.get('topic', ''), lang)
    return {"ok": True, "company": company}

@app.post("/api/company/delete")
async def api_delete_company(request: Request):
    body = await request.json()
    data, code = _call(Handler._handle_company_delete, '/api/company/delete', body)
    return JSONResponse(data, status_code=code)

@app.post("/api/chat/{cid}")
async def api_chat(cid: str, request: Request):
    body = await request.json()
    path = f"/api/chat/{cid}"
    data, code = _call(Handler._handle_chat, path, body)
    return JSONResponse(data, status_code=code)

@app.post("/api/agent-msg/{cid}")
async def api_agent_msg(cid: str, request: Request):
    body = await request.json()
    path = f"/api/agent-msg/{cid}"
    data, code = _call(Handler._handle_agent_msg, path, body)
    return JSONResponse(data, status_code=code)

@app.post("/api/agent-add/{cid}")
async def api_agent_add(cid: str, request: Request):
    body = await request.json()
    data, code = _call(Handler._handle_agent_add, f"/api/agent-add/{cid}", body)
    return JSONResponse(data, status_code=code)

@app.post("/api/agent-delete/{cid}/{aid}")
async def api_agent_delete(cid: str, aid: str):
    data, code = _call(Handler._handle_agent_delete, f"/api/agent-delete/{cid}/{aid}")
    return JSONResponse(data, status_code=code)

@app.post("/api/agent-reactivate/{cid}/{aid}")
async def api_agent_reactivate(cid: str, aid: str):
    data, code = _call(Handler._handle_agent_reactivate, f"/api/agent-reactivate/{cid}/{aid}")
    return JSONResponse(data, status_code=code)

@app.post("/api/task-add/{cid}")
async def api_task_add(cid: str, request: Request):
    body = await request.json()
    data, code = _call(Handler._handle_task_add, f"/api/task-add/{cid}", body)
    return JSONResponse(data, status_code=code)

@app.post("/api/task-pause/{cid}/{task_id}")
def api_task_pause(cid: str, task_id: str):
    update_task_status(cid, task_id, 'paused')
    _running_task_threads.discard(f"{cid}:{task_id}")
    return {"ok": True}

@app.post("/api/task-resume/{cid}/{task_id}")
def api_task_resume(cid: str, task_id: str):
    update_task_status(cid, task_id, 'resumed')
    return {"ok": True}

@app.post("/api/task-stop/{cid}/{task_id}")
def api_task_stop(cid: str, task_id: str):
    update_task_status(cid, task_id, 'stopped')
    _running_task_threads.discard(f"{cid}:{task_id}")
    return {"ok": True}

@app.post("/api/task-delete/{cid}/{task_id}")
async def api_task_delete(cid: str, task_id: str):
    data, code = _call(Handler._handle_task_delete, f"/api/task-delete/{cid}/{task_id}")
    return JSONResponse(data, status_code=code)

@app.post("/api/goal-add/{cid}")
async def api_goal_add(cid: str, request: Request):
    body = await request.json()
    data, code = _call(Handler._handle_goal_add, f"/api/goal-add/{cid}", body)
    return JSONResponse(data, status_code=code)

@app.post("/api/goal-update/{cid}")
async def api_goal_update(cid: str, request: Request):
    body = await request.json()
    data, code = _call(Handler._handle_goal_update, f"/api/goal-update/{cid}", body)
    return JSONResponse(data, status_code=code)

@app.post("/api/goal-delete/{cid}")
async def api_goal_delete(cid: str, request: Request):
    body = await request.json()
    data, code = _call(Handler._handle_goal_delete, f"/api/goal-delete/{cid}", body)
    return JSONResponse(data, status_code=code)

@app.post("/api/board-task-add/{cid}")
async def api_board_task_add(cid: str, request: Request):
    body = await request.json()
    data, code = _call(Handler._handle_board_task_add, f"/api/board-task-add/{cid}", body)
    return JSONResponse(data, status_code=code)

@app.post("/api/task-status/{cid}/{task_id}")
async def api_task_status(cid: str, task_id: str, request: Request):
    body = await request.json()
    data, code = _call(Handler._handle_task_status, f"/api/task-status/{cid}/{task_id}", body)
    return JSONResponse(data, status_code=code)

@app.post("/api/board-task-delete/{cid}")
async def api_board_task_delete(cid: str, request: Request):
    body = await request.json()
    data, code = _call(Handler._handle_board_task_delete, f"/api/board-task-delete/{cid}", body)
    return JSONResponse(data, status_code=code)

@app.post("/api/approval-approve/{cid}")
async def api_approval_approve(cid: str, request: Request):
    body = await request.json()
    data, code = _call(Handler._handle_approval_resolve, f"/api/approval-approve/{cid}", body, 'approved')
    return JSONResponse(data, status_code=code)

@app.post("/api/approval-reject/{cid}")
async def api_approval_reject(cid: str, request: Request):
    body = await request.json()
    data, code = _call(Handler._handle_approval_resolve, f"/api/approval-reject/{cid}", body, 'rejected')
    return JSONResponse(data, status_code=code)

@app.post("/api/webhook/{cid}")
async def api_webhook(cid: str, request: Request):
    body = await request.json()
    raw_headers = dict(request.headers)
    data, code = _call(Handler._handle_webhook, f"/api/webhook/{cid}", body, headers=raw_headers)
    return JSONResponse(data, status_code=code)

@app.post("/api/cross-nudge")
async def api_cross_nudge(request: Request):
    body = await request.json()
    data, code = _call(Handler._handle_cross_nudge, "/api/cross-nudge", body)
    return JSONResponse(data, status_code=code)

@app.post("/api/ab-test/{cid}")
async def api_ab_test(cid: str, request: Request):
    body = await request.json()
    data, code = _call(Handler._handle_ab_test, f"/api/ab-test/{cid}", body)
    return JSONResponse(data, status_code=code)

@app.post("/api/webhook-route-add/{cid}")
async def api_webhook_route_add(cid: str, request: Request):
    body = await request.json()
    data, code = _call(Handler._handle_webhook_route_add, f"/api/webhook-route-add/{cid}", body)
    return JSONResponse(data, status_code=code)

@app.post("/api/webhook-route-delete/{cid}")
async def api_webhook_route_delete(cid: str, request: Request):
    body = await request.json()
    data, code = _call(Handler._handle_webhook_route_delete, f"/api/webhook-route-delete/{cid}", body)
    return JSONResponse(data, status_code=code)

@app.post("/api/snapshot/{cid}")
async def api_snapshot(cid: str, request: Request):
    body = await request.json()
    data, code = _call(Handler._handle_snapshot, f"/api/snapshot/{cid}", body)
    return JSONResponse(data, status_code=code)

@app.post("/api/fork/{snap_id}")
async def api_fork(snap_id: str, request: Request):
    body = await request.json()
    data, code = _call(Handler._handle_fork, f"/api/fork/{snap_id}", body)
    return JSONResponse(data, status_code=code)

@app.post("/api/restore/{cid}/{snap_id}")
async def api_restore(cid: str, snap_id: str, request: Request):
    body = await request.json()
    data, code = _call(Handler._handle_restore, f"/api/restore/{cid}/{snap_id}", body)
    return JSONResponse(data, status_code=code)

@app.post("/api/meeting/{cid}")
async def api_meeting(cid: str, request: Request):
    body = await request.json()
    data, code = _call(Handler._handle_meeting, f"/api/meeting/{cid}", body)
    return JSONResponse(data, status_code=code)

@app.post("/api/daily-report/{cid}")
async def api_daily_report(cid: str, request: Request):
    body = await request.json()
    data, code = _call(Handler._handle_daily_report, f"/api/daily-report/{cid}", body)
    return JSONResponse(data, status_code=code)

# ─── Plan Tasks API ───────────────────────────────────────────────────────────

@app.get("/api/plan-tasks/{cid}")
async def api_get_plan_tasks(cid: str):
    tasks = db_get_plan_tasks(cid)
    return JSONResponse(tasks)

@app.post("/api/plan-task-add/{cid}")
async def api_add_plan_task(cid: str, request: Request):
    body = await request.json()
    task = db_add_plan_task(cid, body)
    return JSONResponse({"ok": True, "task": task})

@app.post("/api/plan-task-update/{cid}")
async def api_update_plan_task(cid: str, request: Request):
    body = await request.json()
    task_id = body.get("id")
    if not task_id:
        return JSONResponse({"ok": False, "error": "id required"}, status_code=400)
    db_update_plan_task(cid, task_id, body)
    return JSONResponse({"ok": True})

@app.post("/api/plan-task-delete/{cid}")
async def api_delete_plan_task(cid: str, request: Request):
    body = await request.json()
    task_id = body.get("id")
    if not task_id:
        return JSONResponse({"ok": False, "error": "id required"}, status_code=400)
    db_delete_plan_task(cid, task_id)
    return JSONResponse({"ok": True})

# ── Static files (must be last — catches everything else) ──────────────────

app.mount("/", StaticFiles(directory=str(BASE / "dashboard"), html=True), name="static")

# ─── Server Setup ───

class ReusableTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

def ensure_agents_registered():
    """On startup, re-register all agents from companies data. Runs non-blocking."""
    companies = load_json(COMPANIES_FILE, [])
    try:
        from runtime.openclaw import OpenClawRuntime
        registered_output = OpenClawRuntime().list_registered()
    except Exception as e:
        print(f"[INIT] agents list failed: {e}")
        registered_output = ''
    for company in companies:
        cid = company['id']
        state_file = DATA / f"{cid}.json"
        if not state_file.exists():
            save_json(state_file, company)
        for agent in company.get('agents', []):
            agent_id = agent.get('agent_id', '')
            if not agent_id:
                continue
            if agent_id not in registered_output:
                print(f"[INIT] Re-registering {agent_id}...")
                ws = DATA / cid / "workspaces" / agent['id']
                def make_done(cid_val, aid_val):
                    def _done():
                        c = get_company(cid_val)
                        if c:
                            for a in c.get('agents', []):
                                if a['id'] == aid_val:
                                    a['status'] = 'active'
                                    break
                            save_company(c)
                            print(f"[INIT] {aid_val} registered and active")
                    return _done
                register_agent(agent_id, ws, agent['name'], agent['role'],
                               company.get('name', ''), agent.get('emoji', '🤖'),
                               lang=company.get('lang','ko'),
                               wait=False, on_done=make_done(cid, agent['id']), company_id=cid)
            elif agent.get('status') in ('registering', 'working'):
                agent['status'] = 'active'
                save_company(company)

init_db()
migrate_from_json()
init_companies()
threading.Thread(target=ensure_agents_registered, daemon=True).start()
restore_running_tasks()
print(f"🚀 AI Company Hub: http://localhost:{PORT}", flush=True)

def _watchdog():
    """30초마다 에이전트 상태 체크, working이 오래 지속되면 active로 복원"""
    import time as _t
    working_since = {}
    while True:
        _t.sleep(30)
        try:
            companies = db_get_all_companies()
            for comp in companies:
                cid = comp['id']
                for a in comp.get('agents', []):
                    aid = a['id']
                    st = a.get('status', 'active')
                    key = f"{cid}:{aid}"
                    if st == 'working':
                        if key not in working_since:
                            working_since[key] = _t.time()
                        elif _t.time() - working_since[key] > 90:
                            print(f"[watchdog] {aid} stuck {int(_t.time()-working_since[key])}s → active")
                            for ag in comp.get('agents', []):
                                if ag['id'] == aid:
                                    ag['status'] = 'active'
                            update_company(cid, {'agents': comp['agents']})
                            working_since.pop(key, None)
                    else:
                        working_since.pop(key, None)
            # Lightweight session keepalive via HTTP (no subprocess)
            # Sessions stay alive as long as openclaw gateway is running
        except Exception as e:
            print(f"[watchdog] error: {e}")
threading.Thread(target=_watchdog, daemon=True).start()

# ─── Simple Cron Expression Matcher ───
def _cron_matches_now(cron_expr):
    """Check if a 5-field cron expression matches the current minute.
    Fields: minute hour day-of-month month day-of-week (standard cron).
    Supports: * ranges (1-5) lists (1,3,5) step (*/15)."""
    import calendar
    now = datetime.now()
    fields = cron_expr.strip().split()
    if len(fields) != 5:
        return False
    def _match(field, val, lo, hi):
        if field == '*': return True
        if '/' in field:
            base, step = field.split('/', 1)
            start = 0 if base == '*' else int(base)
            return (val - start) % int(step) == 0 and val >= start
        if '-' in field:
            a, b = field.split('-', 1)
            return int(a) <= val <= int(b)
        if ',' in field:
            return val in [int(x) for x in field.split(',')]
        return val == int(field)
    try:
        return (
            _match(fields[0], now.minute, 0, 59) and
            _match(fields[1], now.hour, 0, 23) and
            _match(fields[2], now.day, 1, 31) and
            _match(fields[3], now.month, 1, 12) and
            _match(fields[4], now.weekday(), 0, 6)  # 0=Monday
        )
    except Exception:
        return False

def _cron_scheduler():
    """Check all recurring tasks with cron_expression field every minute."""
    while True:
        try:
            time.sleep(60 - datetime.now().second)  # Align to minute boundary
            for cid_entry in db_get_all_companies():
                cid = cid_entry['id']
                company = get_company(cid)
                if not company: continue
                for task in company.get('recurring_tasks', []):
                    cron_expr = task.get('cron_expression', '')
                    if not cron_expr or task.get('status') != 'running': continue
                    if _cron_matches_now(cron_expr):
                        print(f"[cron] {cid}/{task.get('title','')} triggered by {cron_expr}")
                        prompt = task.get('prompt', task.get('title', ''))
                        agent_id = task.get('agent_id', 'ceo')
                        threading.Thread(target=nudge_agent, args=(cid, prompt, agent_id.upper()), daemon=True).start()
        except Exception as e:
            print(f"[cron] scheduler error: {e}")

threading.Thread(target=_cron_scheduler, daemon=True).start()

uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
