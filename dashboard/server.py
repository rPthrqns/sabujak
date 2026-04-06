#!/usr/bin/env python3
"""AI Company Hub - Multi-company Dashboard Server
Enhanced with Goals, Kanban Board, Cost Tracking, Approval Gates, and Task Dependencies."""
import json, os, re, http.server, socketserver, subprocess, threading, time, urllib.request, uuid
from pathlib import Path
from datetime import datetime

# ─── Constants ───
PORT = 3000
BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
COMPANIES_FILE = DATA / "companies.json"
SSE_CLIENTS = []
SSE_LOCK = threading.Lock()
AGENT_LOCK = threading.Lock()
_running_task_threads = set()

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
    """Broadcast SSE event to all connected clients."""
    msg = f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    with SSE_LOCK:
        dead = []
        for wfile in SSE_CLIENTS:
            try:
                wfile.write(msg.encode())
                wfile.flush()
            except:
                dead.append(wfile)
        for w in dead:
            SSE_CLIENTS.remove(w)

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
    company = get_company(cid)
    if not company: return []
    
    results = []
    board_tasks = company.get('board_tasks', [])
    
    # TASK_ADD:작업명:우선순위
    for m in re.finditer(r'\[TASK_ADD:([^:]+):([^\]]+)\]', text):
        title = m.group(1).strip()
        priority = m.group(2).strip()
        # 중복 체크
        if any(t.get('title','') == title for t in board_tasks):
            results.append(f"⚠️ '{title}' 이미 존재")
            continue
        task = add_board_task(cid, title, agent_id, '대기', [], '')
        if task:
            task['priority'] = priority
            # 저장
            company = get_company(cid)
            company['board_tasks'] = board_tasks
            save_company(company)
            update_company(cid, {'board_tasks': company['board_tasks']})
            results.append(f"✅ '{title}' 칸반에 추가됨 ({priority})")
    
    # TASK_DONE:작업명
    for m in re.finditer(r'\[TASK_DONE:([^\]]+)\]', text):
        title = m.group(1).strip()
        for t in board_tasks:
            if t.get('title','') == title and t.get('status') != '완료':
                t['status'] = '완료'
                results.append(f"🎉 '{title}' 완료 처리")
                break
    
    # TASK_START:작업명
    for m in re.finditer(r'\[TASK_START:([^\]]+)\]', text):
        title = m.group(1).strip()
        for t in board_tasks:
            if t.get('title','') == title and t.get('status') == '대기':
                t['status'] = '진행중'
                results.append(f"🚀 '{title}' 시작")
                break
    
    # TASK_BLOCK:작업명:사유
    for m in re.finditer(r'\[TASK_BLOCK:([^:]+):([^\]]+)\]', text):
        title = m.group(1).strip()
        reason = m.group(2).strip()
        for t in board_tasks:
            if t.get('title','') == title:
                t['status'] = '검토'
                results.append(f"🚫 '{title}' 검토 필요 ({reason})")
                break
    
    if results:
        company = get_company(cid)
        company['board_tasks'] = board_tasks
        save_company(company)
        update_company(cid, {'board_tasks': company['board_tasks']})
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
        tasks = company.get('recurring_tasks', [])
        company['recurring_tasks'] = [t for t in tasks if t.get('title','') != title]
        save_company(company)
        update_company(cid, {'recurring_tasks': company['recurring_tasks']})
        results.append(f"🗑️ '{title}' 정기 작업 삭제됨")
    
    if any('CRON' in r for r in results):
        print(f"[cron_cmds] {agent_id}: {'; '.join(results)}")
    
    # 자동 작업 감지: [TASK_XXX] 명령이 없을 때 패턴 기반 자동 추가
    has_task_cmd = bool(re.search(r'\[TASK_', text))
    if not has_task_cmd:
        company = get_company(cid)
        board_tasks = company.get('board_tasks', [])
        agent = next((a for a in company.get('agents', []) if a['id'] == agent_id), None)
        
        # "~작성하겠습니다", "~수립하겠습니다", "~구축하겠습니다" → 자동 칸반 추가
        start_patterns = re.findall(r'(?:작성|수립|구축|개발|제작|준비|기획|설계|구현|검토|분석|실행|진행|도입|설정)하?겠습니다[:\s]*([^\n.]{2,30})', text)
        for title in start_patterns:
            title = title.strip().rstrip('.,;')
            if len(title) < 2: continue
            if any(t.get('title','') == title for t in board_tasks): continue
            task = add_board_task(cid, title, agent_id, '진행중', [], '')
            if task:
                board_tasks.append(task)
                results.append(f"📋 '{title}' 자동 추가됨 (진행)")
        
        # "~완료", "~완성", "~작성 완료" → 자동 완료 처리
        done_patterns = re.findall(r'(?:완료|완성|마무리|제출|완료했습니다|완성했습니다)\s*(?:하였습니다|했습니다)?[:\s]*([^\n.]{2,30})', text)
        for title in done_patterns:
            title = title.strip().rstrip('.,;')
            for t in board_tasks:
                if title in t.get('title','') and t.get('status') != '완료':
                    t['status'] = '완료'
                    results.append(f"🎉 '{t['title']}' 완료 처리")
                    break
        
        if results:
            company = get_company(cid)
            company['board_tasks'] = board_tasks
            save_company(company)
            update_company(cid, {'board_tasks': company['board_tasks']})
    
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
            except:
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
        except: pass
    # Atomic write via temp file
    import tempfile, os
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(test)
        os.replace(tmp, str(path))
    except:
        try: os.unlink(tmp)
        except: pass

def gen_id(prefix="id"):
    """Generate a short unique ID."""
    return f"{prefix}-{datetime.now().strftime('%m%d%H%M%S')}-{uuid.uuid4().hex[:4]}"

# ─── Company Data Access ───

def init_companies():
    if not COMPANIES_FILE.exists():
        save_json(COMPANIES_FILE, [])

    recovered = []
    seen_ids = set()

    def _consider_company_data(data, source_name=None):
        if not data or 'id' not in data:
            return
        cid = data.get('id')
        if not cid or cid in seen_ids:
            return
        changed = False
        for a in data.get('agents', []):
            if a.get('status') == 'working':
                a['status'] = 'active'
                changed = True
        state_file = DATA / f"{cid}.json"
        if changed or not state_file.exists():
            save_json(state_file, data)
            if changed:
                print(f"[reset] {cid}: stuck agents reset")
            elif source_name:
                print(f"[init] restored {cid} from {source_name}")
        recovered.append(data)
        seen_ids.add(cid)

    # 1) 정상 json state 파일 복구
    for f in sorted(DATA.glob('*.json')):
        if f.name == 'companies.json' or '-queue' in f.stem:
            continue
        try:
            _consider_company_data(load_json(f), f.name)
        except:
            pass

    # 2) .bak state 파일 복구
    for f in sorted(DATA.glob('*.json.bak')):
        if f.name == 'companies.json.bak' or '-queue' in f.stem:
            continue
        try:
            data = json.loads(f.read_text(encoding='utf-8'))
            _consider_company_data(data, f.name)
        except:
            pass

    # 3) companies.json.bak 에서 누락 회사 복구
    companies_bak = DATA / 'companies.json.bak'
    if companies_bak.exists():
        try:
            bak_list = json.loads(companies_bak.read_text(encoding='utf-8'))
            for item in bak_list if isinstance(bak_list, list) else []:
                _consider_company_data(item, companies_bak.name)
        except:
            pass

    current = load_json(COMPANIES_FILE, [])
    current_ids = {c.get('id') for c in current}
    merged = list(current)
    for company in recovered:
        if company.get('id') not in current_ids:
            merged.append(company)
    if merged != current:
        save_json(COMPANIES_FILE, merged)
        print(f"[init] recovered {len(merged) - len(current)} companies into companies.json")
    return load_json(COMPANIES_FILE, [])

def get_company(cid):
    state_file = DATA / f"{cid}.json"
    if state_file.exists():
        try:
            data = load_json(state_file)
            if data:
                return data
        except:
            pass

    # Fallback: recover from companies.json if state file is missing or stale
    companies = load_json(COMPANIES_FILE, [])
    for company in companies:
        if company.get('id') == cid:
            try:
                save_json(state_file, company)
            except:
                pass
            return company
    return None

def save_company(company):
    if not company or 'id' not in company:
        return None
    cid = company['id']
    state_file = DATA / f"{cid}.json"
    save_json(state_file, company)
    companies = load_json(COMPANIES_FILE, [])
    replaced = False
    for i, c in enumerate(companies):
        if c.get("id") == cid:
            companies[i] = company
            replaced = True
            break
    if not replaced:
        companies.append(company)
    save_json(COMPANIES_FILE, companies)
    sse_broadcast('company_update', {"id": cid, "company": company})
    return company

def update_company(cid, updates):
    state_file = DATA / f"{cid}.json"
    company = get_company(cid)
    if company:
        company.update(updates)
        save_json(state_file, company)
        companies = load_json(COMPANIES_FILE)
        for i, c in enumerate(companies):
            if c["id"] == cid:
                companies[i] = company
                break
        save_json(COMPANIES_FILE, companies)
        sse_broadcast('company_update', {"id": cid, "company": company})
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
    tasks = company.get('board_tasks', [])
    # Prevent queue overflow: auto-complete old done tasks when total exceeds 50
    MAX_TASKS = 50
    if len(tasks) >= MAX_TASKS:
        done = [t for t in tasks if t.get('status') == '완료']
        if done:
            # Remove oldest completed tasks
            remove_ids = {t['id'] for t in done[:len(tasks) - MAX_TASKS + 5]}
            tasks = [t for t in tasks if t['id'] not in remove_ids]
    task = {
        'id': gen_id('bt'),
        'title': title,
        'agent_id': agent_id or '',
        'status': status,
        'depends_on': depends_on or [],
        'deadline': deadline or '',
        'created_at': datetime.now().isoformat(),
    }
    tasks.append(task)
    update_company(cid, {'board_tasks': tasks})
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
    update_company(cid, {'board_tasks': tasks})

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
        update_company(cid, {'board_tasks': tasks})
        # Log
        now_str = datetime.now().strftime('%H:%M')
        log_entries = [
            {"time": now_str, "agent": "시스템",
             "text": f"🔗 자동 시작: \"{t['title']}\" (의존성 해결)"}
            for t in unlocked
        ]
        company = get_company(cid)
        company['activity_log'] = company.get('activity_log', []) + log_entries
        update_company(cid, {'activity_log': company['activity_log']})

    return unlocked

def delete_board_task(cid, task_id):
    company = get_company(cid)
    if not company:
        return
    tasks = company.get('board_tasks', [])
    tasks = [t for t in tasks if t['id'] != task_id]
    # Also remove from goals
    goals = company.get('goals', [])
    for g in goals:
        g['task_ids'] = [tid for tid in g.get('task_ids', []) if tid != task_id]
    update_company(cid, {'board_tasks': tasks, 'goals': goals})

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
    update_company(cid, {'approvals': approvals})
    # Log
    now_str = datetime.now().strftime('%H:%M')
    company = get_company(cid)
    company['activity_log'] = company.get('activity_log', []) + [
        {"time": now_str, "agent": "시스템", "text": f"⚠️ 승인 요청: {detail}"}
    ]
    update_company(cid, {'activity_log': company['activity_log']})
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
        company = get_company(cid)
        company['activity_log'] = company.get('activity_log', []) + [
            {"time": now_str, "agent": "시스템",
             "text": f"{emoji} 승인 {('승인' if resolution == 'approved' else '거부')}: {approval.get('detail', '')}"}
        ]
        update_company(cid, {'activity_log': company['activity_log']})
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
        try:
            subprocess.run(
                ['openclaw', 'agents', 'add', agent_id, '--workspace', str(ws_path), '--non-interactive'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15
            )
        except: pass
    
    # 첫 메시지로 세션 활성화
    try:
        subprocess.run(
            ['openclaw', 'agent', '--agent', agent_id, '--local',
             '-m', f'당신은 {name}({role})입니다. "확인"이라고만 답하세요.'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30
        )
        print(f"[register] {agent_id} ({name}) activated")
    except Exception as e:
        print(f"[register] {agent_id} activate failed: {e}")

# ─── Recurring Task System ───

def add_recurring_task(cid, title, prompt, interval_minutes, agent_id, agent_name, agent_emoji):
    company = get_company(cid)
    if not company:
        return None
    tasks = company.get('recurring_tasks', [])
    task_id = f"task-{datetime.now().strftime('%m%d%H%M%S')}-{len(tasks)}"
    task = {
        'id': task_id, 'agent_id': agent_id, 'agent_name': agent_name,
        'agent_emoji': agent_emoji, 'title': title, 'prompt': prompt,
        'interval_minutes': interval_minutes, 'status': 'running',
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
    companies = load_json(COMPANIES_FILE)
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
_AGENT_BUSY = set()   # {"cid:agent_id"} currently processing

def generate_newspaper(cid):
    """Generate a concise daily brief from current state."""
    company = get_company(cid)
    if not company:
        return ''
    lang = company.get('lang', 'ko')
    name = company.get('name', '?')
    now = datetime.now().strftime('%m/%d %H:%M')
    brief_label = '브리프' if lang == 'ko' else 'Briefing'
    lines = [f"📰 {name} {brief_label} ({now})"]

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

    shared_dir = DATA / cid / "_shared" / "deliverables"
    if shared_dir.exists():
        files = sorted(shared_dir.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)[:5]
        if files:
            lines.append(f"\n{_s('news.recent_deliverables', lang)}")
            for f in files:
                mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime('%H:%M')
                lines.append(f"- {f.name} ({mtime})")

    wb_path = DATA / cid / "_shared" / "whiteboard.md"
    if wb_path.exists():
        try:
            wb = wb_path.read_text(encoding='utf-8').strip()
            if wb and len(wb) > 30:
                lines.append(f"\n{_s('news.whiteboard', lang)}")
                lines.append(wb[:300])
        except: pass

    brief = '\n'.join(lines)

    # Save to file
    brief_dir = DATA / cid / "_shared"
    brief_dir.mkdir(parents=True, exist_ok=True)
    (brief_dir / "newspaper.md").write_text(brief, encoding='utf-8')

    return brief


def read_agent_standup(cid, agent_id):
    """Read agent's standup file if it exists."""
    path = DATA / cid / "workspaces" / agent_id / "standup.md"
    if path.exists():
        try: return path.read_text(encoding='utf-8')[:500]
        except: pass
    return None


def add_to_inbox(cid, agent_id, from_name, instruction, lang="ko"):
    """Write a message to agent's inbox. Server-managed, agent only reads."""
    inbox_dir = DATA / cid / "workspaces" / agent_id / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    filename = now.strftime("%H%M%S") + f"-{from_name}.md"
    content = f"{_s('inbox.from', lang)}: {from_name}\n{_s('inbox.time', lang)}: {now.strftime('%m/%d %H:%M')}\n\n{instruction}"
    (inbox_dir / filename).write_text(content, encoding='utf-8')
    return filename


def read_agent_inbox(cid, agent_id, limit=5):
    """Read latest inbox messages."""
    inbox_dir = DATA / cid / "workspaces" / agent_id / "inbox"
    if not inbox_dir.exists(): return None
    files = sorted(inbox_dir.iterdir(), key=lambda f: f.name)[-limit:]
    if not files: return None
    parts = []
    for f in files:
        try: parts.append(f.read_text(encoding='utf-8')[:200])
        except: pass
    return '\n---\n'.join(parts)


def archive_inbox(cid, agent_id):
    """Move processed inbox items to archive."""
    inbox_dir = DATA / cid / "workspaces" / agent_id / "inbox"
    archive_dir = DATA / cid / "workspaces" / agent_id / "inbox-done"
    if not inbox_dir.exists(): return
    archive_dir.mkdir(parents=True, exist_ok=True)
    for f in inbox_dir.iterdir():
        try: f.rename(archive_dir / f.name)
        except: pass


def nudge_agent(cid, text, target):
    """Queue-based nudge: FIFO order, dedup recent, context-aware, no locks."""
    print(f"[nudge] called: cid={cid} target={target} text={text[:50]}")
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
        if key in _AGENT_BUSY:
            return
        _AGENT_BUSY.add(key)
        # Rebuild context for this message
        company = get_company(cid)
        newspaper = generate_newspaper(cid)
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
            c = get_company(cid)
            for t in c.get('board_tasks', []):
                if t.get('agent_id') == aid and t.get('status') == '대기':
                    t['status'] = '진행중'
                    save_company(c)
                    update_company(cid, {'board_tasks': c['board_tasks']})
                    break

            proc = subprocess.Popen(
                ['openclaw', 'agent', '--agent', agent_id,
                 '--session-id', session_id, '--local', '-m', prompt],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            stdout, stderr = proc.communicate(timeout=120)
            elapsed = time.time() - nudge_start
            reply_raw = stdout.decode().strip()
            print(f"[nudge] {agent_id} reply={len(reply_raw)}chars rc={proc.returncode} time={elapsed:.1f}s raw={reply_raw[:100]}")

            retry_ok = True  # Assume OK unless retry needed
            if not reply_raw or 'No reply from agent' in reply_raw or proc.returncode != 0:
                print(f"[nudge] {agent_id} no reply, retrying...")
                time.sleep(2)
                proc2 = subprocess.Popen(
                    ['openclaw', 'agent', '--agent', agent_id,
                     '--session-id', f"{session_id}-retry", '--local', '-m', prompt],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )
                stdout2, stderr2 = proc2.communicate(timeout=120)
                reply_raw = stdout2.decode().strip()
                retry_ok = proc2.returncode == 0
                print(f"[nudge] {agent_id} retry reply={len(reply_raw)}chars rc={proc2.returncode}")

            # 3rd attempt: session reset
            if not reply_raw or 'No reply from agent' in reply_raw or not retry_ok:
                print(f"[nudge] {agent_id} 2nd attempt failed, resetting session...")
                time.sleep(5)
                new_session = f"{agent_id}-fresh-{int(time.time())}"
                proc3 = subprocess.Popen(
                    ['openclaw', 'agent', '--agent', agent_id,
                     '--session-id', new_session, '--local', '-m', prompt],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )
                stdout3, stderr3 = proc3.communicate(timeout=120)
                reply_raw = stdout3.decode().strip()
                print(f"[nudge] {agent_id} 3rd attempt reply={len(reply_raw)}chars")
                if reply_raw and 'No reply from agent' not in reply_raw and proc3.returncode == 0:
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

                    # CEO acknowledgment detection: if no @mention and no action, nudge again
                    if aid == 'ceo' and '@' not in clean and len(clean) < 200:
                        print(f"[nudge] CEO acknowledged without delegation, prompting for plan...")
                        time.sleep(2)
                        followup = f"{agent_name}, 당신은 방금 '{text[:50]}'에 대해 계획만 언급하고 팀원에게 지시하지 않았습니다. 지금 바로 구체적인 계획을 세우고 @CMO @CTO에 각자 해야 할 작업을 @멘션으로 지시하세요. COMPLEX 프로토콜을 따르세요."
                        proc_f = subprocess.Popen(
                            ['openclaw', 'agent', '--agent', agent_id,
                             '--session-id', session_id, '--local', '-m', followup],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                        stdout_f, stderr_f = proc_f.communicate(timeout=120)
                        reply_f = stdout_f.decode().strip()
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
            _AGENT_BUSY.discard(key)
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
                next_text = _AGENT_QUEUES[key].popleft()
                if not _AGENT_QUEUES[key]:
                    del _AGENT_QUEUES[key]
                threading.Thread(target=_process, args=(next_text,), daemon=True).start()

    if key in _AGENT_BUSY:
        if key not in _AGENT_QUEUES:
            from collections import deque
            _AGENT_QUEUES[key] = deque(maxlen=3)
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
            self._json(load_json(COMPANIES_FILE))
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
            if len(parts) >= 1:
                cid = parts[0]
                file_rel = parts[1] if len(parts) > 1 else ''
                file_path = DATA / cid / file_rel
                if file_path.exists() and file_path.is_file():
                    try:
                        content = file_path.read_text(encoding='utf-8')
                        self.send_response(200)
                        self.send_header('Content-Type', 'text/plain; charset=utf-8')
                        self.end_headers()
                        self.wfile.write(content.encode('utf-8'))
                        return
                    except:
                        pass
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
        elif self.path == '/api/agents':
            self._json(AGENT_TEMPLATES)
        elif self.path == '/api/topics':
            self._json(TOPIC_ORGS)
        elif self.path == '/api/langs':
            self._json(LANG)
        else:
            if self.path == '/': self.path = '/index.html'
            return super().do_GET()

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
            companies = load_json(COMPANIES_FILE, [])
            initial = json.dumps(companies, ensure_ascii=False)
            wfile.write(f"event: init\ndata: {initial}\n\n".encode())
            wfile.flush()
        except: pass
        try:
            while True:
                time.sleep(30)
                wfile.write(b": keepalive\n\n")
                wfile.flush()
        except:
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
            company = create_company(body.get('name',''), body.get('topic',''), body.get('lang','ko'))
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

        else:
            self._json({"error": "not found"}, 404)

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

        msg = {"from": "마스터", "text": text, "time": time_str, "type": "user", "mention": is_mention_msg}

        company["chat"].append(msg)
        targets_str = ', '.join(f'@{t}' for t in targets)
        company["activity_log"].append({"time": time_str, "agent": "마스터", "text": f"{targets_str} {instruction}" if is_mention_msg else text})

        queue_file = DATA / f"{cid}-queue.json"
        queue = load_json(queue_file, [])
        queue.append({"text": text, "time": now.isoformat(), "target": targets[0], "processed": False, "id": now.timestamp()})
        save_json(queue_file, queue)

        update_company(cid, {"chat": company["chat"], "activity_log": company["activity_log"]})
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
                company["chat"].append(chat_msg)
            company["activity_log"].append({"time": time_str, "agent": from_agent, "text": f"@마스터 {master_request[:50]}{'...' if len(master_request)>50 else ''}"})
            # 결재 탭에 전체 내용 저장
            approval_item = {
                'id': str(uuid.uuid4())[:8],
                'from_agent': from_agent,
                'from_emoji': emoji,
                'type': '보고서' if is_long else '요청',
                'detail': master_request,  # full content, no truncation
                'status': 'pending',
                'time': time_str,
                'created_at': datetime.now().isoformat()
            }
            company['approvals'] = company.get('approvals', [])
            company['approvals'].append(approval_item)
            update_company(cid, {"chat": company["chat"], "activity_log": company["activity_log"], "approvals": company['approvals']})
            if not is_long:
                _sse_send(json.dumps({'type':'chat','msg':chat_msg}))
            _sse_send(json.dumps({'type':'approval','approval':approval_item}))
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
        
        # 일반 텍스트가 있으면 채팅에 저장
        if normal_text:
            msg = {"from": from_agent, "emoji": emoji, "text": normal_text, "time": time_str, "type": "agent"}
            company["chat"].append(msg)
            company["activity_log"].append({"time": time_str, "agent": from_agent, "text": normal_text})
        
        # 멘션 텍스트가 있으면 별도 채팅에 저장 (from = 멘션 발신자, mention = true)
        if mention_text:
            # 각 멘션 라인별로 저장
            for ml in mention_text.split('\n'):
                ml = ml.strip()
                if not ml: continue
                msg = {"from": from_agent, "emoji": emoji, "text": ml, "time": time_str, "type": "agent", "mention": True}
                company["chat"].append(msg)
            company["activity_log"].append({"time": time_str, "agent": from_agent, "text": mention_text})

        update_company(cid, {"chat": company["chat"], "activity_log": company["activity_log"]})
        self._json({"ok": True, "msg": msg})

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
    def _handle_company_delete(self, body):
        cid = body.get('id')
        company = get_company(cid)
        if not company: self._json({"ok": True}); return
        for agent in company.get('agents', []):
            agent_id = agent.get('agent_id', '')
            if agent_id:
                try:
                    subprocess.run(['openclaw', 'agents', 'delete', agent_id, '--force'],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
                    print(f"[delete] agent {agent_id} removed")
                except Exception as e:
                    print(f"[WARN] agent delete failed {agent_id}: {e}")
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
            try:
                subprocess.run(['openclaw', 'agents', 'delete', agent_id, '--force'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            except: pass
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
            subprocess.run(['openclaw', 'agents', 'delete', agent_id, '--force'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
            time.sleep(1)
            subprocess.run(
                ['openclaw', 'agents', 'add', agent_id, '--workspace', str(agent_workspace), '--non-interactive'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15
            )
            subprocess.run(
                ['openclaw', 'agent', '--agent', agent_id, '--local',
                 '-m', f'당신은 {agent["name"]}({agent["role"]})입니다. 확인만 하세요.'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30
            )
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
        if not title or not prompt: self._json({"error": "title and prompt required"}, 400); return
        task = add_recurring_task(cid, title, prompt, interval, agent['id'], agent['name'], agent['emoji'])
        if task:
            now_str = datetime.now().strftime('%H:%M')
            company = get_company(cid)
            company["chat"].append({"type": "system", "from": "시스템", "emoji": "🔄", "to": "",
                "text": f"🔄 정기 작업 생성: \"{title}\" ({interval}분마다)"})
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

# ─── Server Setup ───

class ReusableTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

def ensure_agents_registered():
    """On startup, re-register all agents from companies data. Runs non-blocking."""
    companies = load_json(COMPANIES_FILE, [])
    try:
        result = subprocess.run(['openclaw', 'agents', 'list'], capture_output=True, text=True, timeout=20)
        registered_output = result.stdout or ''
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

init_companies()
threading.Thread(target=ensure_agents_registered, daemon=True).start()
restore_running_tasks()
print(f"🚀 AI Company Hub: http://localhost:{PORT}", flush=True)

def _watchdog():
    """10초마다 에이전트 상태 체크, working이 오래 지속되면 active로 복원"""
    import time as _t
    working_since = {}
    last_refresh = 0
    while True:
        _t.sleep(10)
        try:
            companies = load_json(COMPANIES_FILE, [])
            for c in companies:
                cid = c['id']
                for a in c.get('agents', []):
                    aid = a['id']
                    st = a.get('status', 'active')
                    key = f"{cid}:{aid}"
                    if st == 'working':
                        if key not in working_since:
                            working_since[key] = _t.time()
                        elif _t.time() - working_since[key] > 60:
                            print(f"[watchdog] {aid} stuck {int(_t.time()-working_since[key])}s → active")
                            comp = get_company(cid)
                            if comp:
                                for ag in comp.get('agents', []):
                                    if ag['id'] == aid:
                                        ag['status'] = 'active'
                                save_company(comp)
                                update_company(cid, {'agents': comp['agents']})
                            working_since.pop(key, None)
                    else:
                        working_since.pop(key, None)
            # Periodically keep agent sessions alive (every 5 min)
            if _t.time() - last_refresh > 300:
                last_refresh = _t.time()
                for c in load_json(COMPANIES_FILE, []):
                    for a in c.get('agents', []):
                        agent_id = a.get('agent_id', '')
                        if agent_id and a.get('status') == 'active':
                            try:
                                subprocess.run(
                                    ['openclaw', 'agent', '--agent', agent_id, '--local',
                                     '-m', 'ping'],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10
                                )
                            except: pass
        except Exception as e:
            print(f"[watchdog] error: {e}")
threading.Thread(target=_watchdog, daemon=True).start()

with ReusableTCPServer(("", PORT), Handler) as httpd:
    httpd.serve_forever()
