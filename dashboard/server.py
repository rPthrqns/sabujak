#!/usr/bin/env python3
"""AI Company Hub - Multi-company Dashboard Server
Enhanced with Goals, Kanban Board, Cost Tracking, Approval Gates, and Task Dependencies."""
import json, os, re, http.server, socketserver, subprocess, threading, time, urllib.request, uuid
from pathlib import Path
from datetime import datetime

# ─── Constants ───
PORT = 3000
BASE = Path("/home/sra/.openclaw/workspace/ai-company")
DATA = BASE / "data"
COMPANIES_FILE = DATA / "companies.json"
SSE_CLIENTS = []
SSE_LOCK = threading.Lock()
PROCESSORS = {}
PROCESSORS_LOCK = threading.Lock()
AGENT_LOCK = threading.Lock()
_running_task_threads = set()

# Default agent templates per role
AGENT_TEMPLATES = {
    "ceo": {"name": "CEO", "role": {"ko":"총괄","en":"Executive","ja":"総責任者","zh":"总负责人"}, "emoji": "👔"},
    "cmo": {"name": "CMO", "role": {"ko":"마케팅","en":"Marketing","ja":"マーケティング","zh":"市场"}, "emoji": "📈"},
    "cto": {"name": "CTO", "role": {"ko":"기술/개발","en":"Tech/Dev","ja":"技術/開発","zh":"技术/开发"}, "emoji": "💻"},
    "coo": {"name": "COO", "role": {"ko":"운영","en":"Operations","ja":"運営","zh":"运营"}, "emoji": "⚙️"},
    "cfo": {"name": "CFO", "role": {"ko":"재무","en":"Finance","ja":"財務","zh":"财务"}, "emoji": "💰"},
    "designer": {"name": "Designer", "role": {"ko":"디자인","en":"Design","ja":"デザイン","zh":"设计"}, "emoji": "🎨"},
    "hr": {"name": "HR", "role": {"ko":"인사","en":"HR","ja":"人事","zh":"人事"}, "emoji": "🤝"},
    "sales": {"name": "Sales", "role": {"ko":"영업","en":"Sales","ja":"営業","zh":"销售"}, "emoji": "📊"},
    "legal": {"name": "Legal", "role": {"ko":"법무","en":"Legal","ja":"法務","zh":"法务"}, "emoji": "⚖️"},
    "support": {"name": "Support", "role": {"ko":"고객지원","en":"Support","ja":"サポート","zh":"客服"}, "emoji": "🎧"},
}

TOPIC_ORGS = {
    "default": ["ceo", "cmo", "cto"],
    "marketing": ["ceo", "cmo", "designer", "cto", "coo"],
    "development": ["ceo", "cto", "designer", "coo"],
    "ecommerce": ["ceo", "cmo", "cto", "sales", "coo", "support"],
    "finance": ["ceo", "cfo", "legal", "coo"],
    "recruitment": ["ceo", "hr", "cmo", "coo"],
    "restaurant": ["ceo", "cmo", "coo", "designer"],
    "education": ["ceo", "cmo", "cto", "support"],
    "healthcare": ["ceo", "cfo", "legal", "cmo", "coo"],
    "realestate": ["ceo", "sales", "cmo", "legal", "cto"],
}

LANG = {"ko":"한국어","en":"English","ja":"日本語","zh":"中文"}

TASK_KEYWORDS = ['모니터링', '감시', '정기', '주기', '매시간', '매일', '자동', '반복', '정기적', '보고', '리포트', '상황공유', '업데이트', '보고해', '보고올려', '보고드려', '매주', '주간', '격주', '월간', '매월', '매년']

TASK_INTERVAL_KEYWORDS = {
    '매시간': 60, '한시간마다': 60, '1시간마다': 60, '시간마다': 60,
    '매일': 1440, '하루에한번': 1440, '매분': 1, '30분마다': 30, '30분': 30,
    '10분마다': 10, '10분': 10, '5분마다': 5, '5분': 5, '15분마다': 15, '15분': 15,
    '2시간마다': 120, '2시간': 120, '3시간마다': 180, '6시간마다': 360, '12시간마다': 720,
    '반나절마다': 720, '주': 10080, '일주일마다': 10080,
    '매주': 10080, '주간': 10080, '매주금요일': 10080, '매주 금요일': 10080,
    '격주': 20160, '월간': 43200, '매월': 43200, '매년': 525600,
}

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
                t['status'] = '진행'
                results.append(f"🚀 '{title}' 시작")
                break
    
    # TASK_BLOCK:작업명:사유
    for m in re.finditer(r'\[TASK_BLOCK:([^:]+):([^\]]+)\]', text):
        title = m.group(1).strip()
        reason = m.group(2).strip()
        for t in board_tasks:
            if t.get('title','') == title:
                t['status'] = '차단'
                results.append(f"🚫 '{title}' 차단 ({reason})")
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
            task = add_board_task(cid, title, agent_id, '진행', [], '')
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
        except json.JSONDecodeError:
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
    with open(path, 'w', encoding='utf-8') as f:
        f.write(test)

def gen_id(prefix="id"):
    """Generate a short unique ID."""
    return f"{prefix}-{datetime.now().strftime('%m%d%H%M%S')}-{uuid.uuid4().hex[:4]}"

# ─── Company Data Access ───

def init_companies():
    if not COMPANIES_FILE.exists():
        save_json(COMPANIES_FILE, [])
    return load_json(COMPANIES_FILE)

def get_company(cid):
    state_file = DATA / f"{cid}.json"
    if state_file.exists():
        try:
            data = load_json(state_file)
            if data: return data
        except: pass
    return None

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
                            target=trigger_processor,
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

COMPLEX_PROMPT = """
## 의사결정 프로토콜 (COMPLEX)
복잡한 작업은 다음 단계를 따르세요:
1. **관찰(Observe)** — 현재 상황, 채팅 맥락, 작업 상태 파악
2. **사고(Think)** — 문제 분석, 위험 요소, 의존관계 확인
3. **계획(Plan)** — 구체적 실행 계획 + 성공 기준
4. **준비(Build)** — 작업 생성(칸반보드), 리소스 확인
5. **실행(Execute)** — 계획 실행, 결과물을 워크스페이스에 파일로 저장
6. **복기(Learn)** — 결과 검증, 교훈을 메모리에 기록

## 결과물 생성 (핵심)
작업 결과는 반드시 `deliverables/` 폴더에 파일로 저장하세요.
- 기획서, 보고서, 계획안 → `.md` 파일
- 코드, 설정 → `.py`, `.js`, `.json` 파일
- 파일을 작성한 후 채팅에서는 간단히 "📄 [파일명] 작성 완료 - 핵심 요약"만 보고하세요.
- 팀원과 공유할 내용은 파일에 적고, @멘션으로 파일명을 알려주세요.
- 파일을 읽고 수정하면서 작업하세요. 대화에 모든 걸 적지 마세요.

간단한 응답이면 바로 답변하세요. 복잡한 작업일 때만 위 단계를 따르세요.

## 작업 관리 명령
칸반보드에 작업을 직접 추가/변경할 수 있습니다. 응답에 다음 형식을 포함하세요:
- `[TASK_ADD:작업명:우선순위(높음/보통/낮음)]` — 새 작업 추가 (담당자는 자동)
- `[TASK_DONE:작업명]` — 작업 완료 처리
- `[TASK_START:작업명]` — 작업 시작
- `[TASK_BLOCK:작업명:사유]` — 작업 차단

예시:
"캠페인 기획안을 작성하겠습니다. [TASK_ADD:캠페인 기획안 작성:높음]"
"기획안이 완성되었습니다. [TASK_DONE:캠페인 기획안 작성]"

## 정기 작업 명령
반복 작업이 필요하면 **반드시 [CRON_ADD:...] 형식을 사용하세요.** 외부 cron 시스템은 없습니다.
- `[CRON_ADD:작업명:주기(분):프롬프트]` — 정기 작업 추가 (시스템이 자동 실행)
- `[CRON_DEL:작업명]` — 정기 작업 삭제

예시:
"[CRON_ADD:5분 상황보고:5:현재 작업 진행 상황을 간단히 보고하세요]"
"[CRON_ADD:일일 품질 점검:30:오늘 완료된 작업과 발견된 문제점을 보고하세요]"""

def setup_agent_workspace(agent_workspace, name, role, company_name, emoji):
    """Initialize agent workspace with required files."""
    agent_workspace.mkdir(parents=True, exist_ok=True)
    (agent_workspace / "AGENTS.md").write_text(
        "# AGENTS.md\n\n당신은 회사 에이전트입니다. SOUL.md를 읽고 역할에 맞게 응답하세요.\n"
        "부트스트랩은 건너뛰세요. 받은 메시지에 항상 응답하세요.\n")
    if not (agent_workspace / "SOUL.md").exists():
        (agent_workspace / "SOUL.md").write_text(
            f"# SOUL.md\n당신은 '{company_name}'의 {name}({role})입니다.\n"
            f"팀원들에게 @멘션으로 지시하고, @CEO에게 보고하세요.\n"
            f"한국어로 소통합니다.\n")
    if not (agent_workspace / "IDENTITY.md").exists():
        (agent_workspace / "IDENTITY.md").write_text(
            f"- **Name:** {name}\n- **Role:** {role}\n- **Emoji:** {emoji}\n")
    bootstrap = agent_workspace / "BOOTSTRAP.md"
    if bootstrap.exists():
        bootstrap.unlink()

def register_agent(agent_id, agent_workspace, name, role, company_name, emoji, wait=False, on_done=None):
    """Register and activate an OpenClaw agent."""
    setup_agent_workspace(agent_workspace, name, role, company_name, emoji)
    if wait:
        _register_and_activate(agent_id, str(agent_workspace), name, role)
        if on_done: on_done()
    else:
        def _task():
            _register_and_activate(agent_id, str(agent_workspace), name, role)
            if on_done: on_done()
        threading.Thread(target=_task, daemon=True).start()

def _register_and_activate(agent_id, workspace, name, role):
    """Background: register agent and activate session."""
    with AGENT_LOCK:
        try:
            subprocess.run(
                ['openclaw', 'agents', 'add', agent_id, '--workspace', workspace, '--non-interactive'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15
            )
        except: pass
    bootstrap = Path(workspace) / "BOOTSTRAP.md"
    if bootstrap.exists():
        bootstrap.unlink()
    try:
        subprocess.run(
            ['openclaw', 'agent', '--agent', agent_id, '--local',
             '-m', f'당신은 {name}({role})입니다. 확인만 하세요.'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30
        )
    except: pass

def auto_create_agents(cid, company, text, time_str):
    """Detect agent creation intent and auto-create agents."""
    keywords = ['만들', '생성', '추가', '고용', '채용', '영입', '합류', '배치', '구성']
    if not any(kw in text for kw in keywords):
        return []

    existing_ids = {a['id'] for a in company.get('agents', [])}
    created_logs = []

    for key, (name, role, emoji) in ROLE_MAP.items():
        if key in existing_ids:
            continue
        if name.lower() in text.lower() or key in text.lower() or role in text:
            aid = key
            agent_id = f"{cid}-{aid}"
            agent_workspace = DATA / cid / "workspaces" / aid
            register_agent(agent_id, agent_workspace, name, role, company.get('name',''), emoji)
            agent = {"id": aid, "agent_id": agent_id, "name": name, "emoji": emoji,
                     "role": role, "status": "active", "tasks": [], "messages": [], "prompt": "",
                     "cost": {"total_tokens": 0, "total_cost": 0.0, "last_run_cost": 0.0}}
            company['agents'].append(agent)
            created_logs.append({"time": time_str, "agent": "시스템", "text": f"🆕 {emoji} {name} ({role}) 합류"})

    if created_logs:
        update_company(cid, {"agents": company['agents'], "activity_log": company.get('activity_log', []) + created_logs})
        ceo_agent = next((a for a in company['agents'] if a['id'] == 'ceo'), None)
        if ceo_agent:
            new_names = ', '.join(log['text'] for log in created_logs)
            ceo_prompt = f"새 팀원이 합류했습니다: {new_names}. 마스터에게 보고하고, 필요하면 다른 팀원들에게 소개해주세요."
            threading.Thread(target=trigger_processor, args=(cid, ceo_prompt, 'CEO'), daemon=True).start()
    return created_logs

# ─── Recurring Task System ───

def detect_task_intent(text, company):
    """Detect if text contains recurring task creation intent."""
    if not any(kw in text for kw in TASK_KEYWORDS):
        return None
    interval = 60
    for kw, mins in sorted(TASK_INTERVAL_KEYWORDS.items(), key=lambda x: -len(x[0])):
        if kw in text:
            interval = mins
            break
    title = text[:50].strip()
    for w in ['해줘', '해주세요', '부탁해', '부탁드려', '시작해', '진행해', '해도 돼', '좀', '부탁']:
        title = title.replace(w, '').strip()
    if not title:
        title = '정기 작업'
    mention = re.search(r'@(\w+)', text)
    target = mention.group(1).lower() if mention else None
    if not target and company:
        agents = company.get('agents', [])
        if agents:
            target = agents[0]['id']
    agent = None
    if target and company:
        agent = next((a for a in company.get('agents', []) if a['id'] == target), None)
    return {
        'title': title, 'prompt': text, 'interval_minutes': interval,
        'agent_id': agent['id'] if agent else (target or 'ceo'),
        'agent_name': agent['name'] if agent else (target.upper() if target else 'CEO'),
        'agent_emoji': agent['emoji'] if agent else '👔',
    }

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
                urllib.request.urlopen(req, timeout=5)
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
                "waiting": "⏳ 에이전트를 준비하고 있습니다. 잠시만 기다려주세요...",
                "ready": "✅ 에이전트가 모두 준비 완료되었습니다! 대화를 시작하시면 됩니다.",
                "log": f"🏢 '{name}' 프로젝트 시작. 주제: {topic}"},
        'en': {"greeting": f"Hello Master! 👋\n\nI'm the CEO of '{name}'.\n\nTopic: {topic}\nTeam: {team}\n\nUse @mention to instruct team members. What should we start with?",
                "waiting": "⏳ Agents are being prepared. Please wait a moment...",
                "ready": "✅ All agents are ready! You can start the conversation now.",
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
                       wait=False, on_done=make_done_callback(company_id, aid, len(org), lang))
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
    state_file = DATA / f"{company_id}.json"
    save_json(state_file, company)
    return company

# ─── Trigger Processor (Agent Execution) ───

def trigger_processor(cid, text, target):
    company = get_company(cid)
    if not company:
        return
    agent = next((a for a in company.get('agents', []) if a['id'] == target.lower()), None)
    if not agent:
        agent = company['agents'][0]

    agent_id = agent.get('agent_id', f"{cid}-{agent['id']}")
    emoji = agent.get('emoji', '👔')
    company_name = company.get('name', '')
    topic = company.get('topic', '')

    agent_workspace = DATA / cid / "workspaces" / agent['id']
    if not agent_workspace.exists():
        register_agent(agent_id, agent_workspace, agent['name'], agent['role'], company_name, emoji)
        time.sleep(3)
    # 결과물 폴더 보장
    (agent_workspace / "deliverables").mkdir(parents=True, exist_ok=True)

    available_agents = ", ".join([f"@{a['id'].upper()}" for a in company.get('agents', []) if a['id'] != agent['id']])
    is_ceo = agent['id'] == 'ceo'

    # ─── 파일 기반 컨텍스트 (Claw-Empire 방식) ───
    # 대신 워크스페이스 파일을 읽어서 컨텍스트 구성
    file_context_parts = []
    
    # 1) deliverables/ 파일 목록 + 내용 (최대 3개)
    del_dir = agent_workspace / "deliverables"
    if del_dir.exists():
        files = sorted(del_dir.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)
        file_list = [f.name for f in files if f.is_file()]
        if file_list:
            file_context_parts.append(f"[결과물 파일: {', '.join(file_list)}]")
            # 최근 파일 3개 내용 읽기 (각 500자)
            for f in files[:3]:
                if f.is_file():
                    try:
                        content = f.read_text(encoding='utf-8')[:500]
                        file_context_parts.append(f"--- {f.name} ---\n{content}")
                    except: pass
    
    # 2) 최근 대화는 짧게만 (최근 3개, 각 100자)
    chat_history = company.get('chat', [])
    recent = [m for m in chat_history[-3:] if m.get('type') != 'system']
    for m in recent:
        sender = m.get('from', '?')
        msg_text = (m.get('text', '') or '')[:100]
        if m.get('type') == 'user': file_context_parts.append(f"[마스터] {msg_text}")
        else: file_context_parts.append(f"[{sender}] {msg_text}")
    
    # 3) 총 2000자 제한
    context_str = '\n'.join(file_context_parts)
    if len(context_str) > 2000:
        context_str = context_str[-2000:]
    
    # 4) 대화 20개 초과 시 백그라운드 자동 요약 (파일 요약도 함께)
    if len(chat_history) > SUMMARY_THRESHOLD:
        threading.Thread(target=auto_summarize, args=(cid,), daemon=True).start()
    
    # 5) 테스크 컨텍스트
    task_context = ''
    board_tasks = company.get('board_tasks', [])
    my_tasks = [t for t in board_tasks if t.get('agent_id') == agent['id']]
    if my_tasks:
        task_lines = []
        for t in my_tasks:
            status = t.get('status', 'todo')
            task_lines.append(f"- [{status}] {t.get('title','')} (우선순위: {t.get('priority','보통')})")
            if t.get('description'):
                task_lines.append(f"  설명: {t['description'][:100]}")
        task_context = f"\n=== 내 작업 목록 ===\n" + '\n'.join(task_lines) + '\n'

    # 메모리 로드
    memory_context = load_agent_memory(cid, agent['id'])

    if is_ceo:
        prompt = f"""당신은 '{company_name}'의 {agent['name']}({agent['role']})입니다. 주제: {topic}

팀원: {available_agents}
워크스페이스: {agent_workspace}
결과물 폴더: {agent_workspace}/deliverables/

{COMPLEX_PROMPT}

{f"=== 에이전트 메모리 ===\n{memory_context}\n" if memory_context else ""}{task_context}{f"=== 현재 상황 (파일+최근 대화) ===\n{context_str}\n" if context_str else ""}
메시지: "{text}"
답변:"""
    else:
        prompt = f"""당신은 '{company_name}'의 {agent['name']}({agent['role']})입니다. 주제: {topic}

팀원: {available_agents}
워크스페이스: {agent_workspace}
결과물 폴더: {agent_workspace}/deliverables/

{COMPLEX_PROMPT}

{f"=== 에이전트 메모리 ===\n{memory_context}\n" if memory_context else ""}{task_context}{f"=== 현재 상황 (파일+최근 대화) ===\n{context_str}\n" if context_str else ""}
메시지: "{text}"
답변:"""

    lock_key = f"{cid}:{agent['id']}"
    with PROCESSORS_LOCK:
        if lock_key in PROCESSORS:
            return
        PROCESSORS[lock_key] = True

    def _update_agent_status(status):
        try:
            c = get_company(cid)
            if c:
                for a in c.get('agents', []):
                    if a['id'] == agent['id']:
                        a['status'] = status
                        break
                update_company(cid, {"agents": c['agents']})
        except: pass

    # 세션 ID: 에이전트별 고유 + 타임스탬프로 항상 새 세션 보장
    session_id = f"{agent_id}-turn-{int(time.time())}"

    _update_agent_status('working')
    # 내 칸반 대기 작업을 진행중으로 변경
    try:
        company = get_company(cid)
        for t in company.get('board_tasks', []):
            if t.get('agent_id') == agent['id'] and t.get('status') == '대기':
                t['status'] = '진행중'
        update_company(cid, {'board_tasks': company['board_tasks']})
    except: pass
    
    try:
        proc = subprocess.Popen(
            ['openclaw', 'agent', '--agent', agent_id, '--session-id', session_id, '--local', '-m', prompt],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        stdout, stderr = proc.communicate(timeout=180)
        reply_raw = stdout.decode().strip()
        print(f"[processor] {agent_id} reply={len(reply_raw)}chars rc={proc.returncode}")
        if proc.returncode != 0:
            print(f"[WARN] processor {agent_id} failed: {stderr.decode()[:200]}")
        else:
            lines = reply_raw.split('\n')
            clean_lines = [l for l in lines if not l.startswith('[') and not l.startswith('(agent') and l.strip()]
            reply = '\n'.join(clean_lines).strip()

            # 메모리에 응답 저장
            save_agent_memory(cid, agent['id'], reply)
            
            # 작업 명령 처리 (칸반 자동 업데이트)
            task_results = process_task_commands(cid, reply, agent['id'])
            
            # 멘션에 대한 응답이면 가장 오래된 대기/진행 작업 자동 완료
            if not task_results:
                company = get_company(cid)
                board_tasks = company.get('board_tasks', [])
                my_pending = [t for t in board_tasks if t.get('agent_id') == agent['id'] and t.get('status') in ('대기', '진행중')]
                if my_pending:
                    my_pending[0]['status'] = '완료'
                    my_pending[0]['updated_at'] = datetime.now().isoformat()
                    save_company(company)
                    update_company(cid, {'board_tasks': board_tasks})
                    task_results = [f"🎉 '{my_pending[0]['title']}' 완료"]
            
            if task_results:
                task_msg = ' '.join(task_results)
                try:
                    payload = json.dumps({"from": "시스템", "emoji": "⚙️", "to": "", "text": task_msg}).encode()
                    req = urllib.request.Request(
                        f'http://localhost:3000/api/agent-msg/{cid}',
                        data=payload, headers={'Content-Type': 'application/json'}
                    )
                    urllib.request.urlopen(req, timeout=5)
                except: pass
            
            # Estimate tokens and cost from response
            est_tokens = max(len(reply) // 4, 100)
            est_cost = round(est_tokens * COST_PER_1K_TOKENS / 1000, 6)
            update_agent_cost(cid, agent['id'], est_tokens, est_cost)

            if reply and len(reply) > 1 and reply not in ('No reply from agent.', ''):
                try:
                    chunks = split_message(reply, max_chars=1500)
                    for chunk in chunks:
                        payload = json.dumps({
                            "from": agent['name'], "emoji": emoji,
                            "to": "마스터", "text": chunk
                        }).encode()
                        req = urllib.request.Request(
                            f'http://localhost:3000/api/agent-msg/{cid}',
                            data=payload, headers={'Content-Type': 'application/json'}
                        )
                        urllib.request.urlopen(req, timeout=5)
                        if len(chunks) > 1:
                            time.sleep(1)
                except Exception as e:
                    print(f"[WARN] post response failed: {e}")
            else:
                print(f"[WARN] empty/no reply from {agent_id}")
    except Exception as e:
        print(f"Processor error: {e}")
    finally:
        with PROCESSORS_LOCK:
            PROCESSORS.pop(lock_key, None)
        _update_agent_status('active')

# ─── HTTP Handler ───

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
            cid = self.path.replace('/api/deliverables/', '')
            agent_id = None
            if '/' in cid:
                cid, agent_id = cid.split('/', 1)
            ws = DATA / cid / 'workspaces'
            files = []
            if ws.exists():
                if agent_id:
                    d = ws / agent_id / 'deliverables'
                else:
                    # 모든 에이전트 deliverables 합치기
                    d = ws
                if d.exists():
                    for f in sorted(d.rglob('*'), key=lambda x: x.stat().st_mtime, reverse=True):
                        if f.is_file() and not f.name.startswith('.') and 'deliverables' in str(f):
                            rel = str(f.relative_to(ws))
                            size = f.stat().st_size
                            mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime('%m-%d %H:%M')
                            files.append({'path': rel, 'size': size, 'modified': mtime})
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
        body = json.loads(self.rfile.read(length)) if length else {}
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

        created_agents = auto_create_agents(cid, company, text, time_str)
        company["chat"].append(msg)
        if created_agents:
            company["activity_log"].extend(created_agents)
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
            # 멘션 내용에서 작업 자동 추출 → 타겟 칸반에 추가
            for target in targets:
                task_title = extract_task_from_instruction(instruction)
                if task_title:
                    add_board_task(cid, task_title, target, '대기', [], '')
                    update_company(cid, {'board_tasks': get_company(cid).get('board_tasks', [])})
                    print(f"[auto-task] {target}: '{task_title}' 대기 추가 (멘션에서)")
                threading.Thread(target=trigger_processor, args=(cid, instruction, target), daemon=True).start()
        elif not is_mention_msg:
            # 일반 채팅 → CEO가 응답 (기존 동작)
            threading.Thread(target=trigger_processor, args=(cid, text, 'CEO'), daemon=True).start()

    # ─── Agent Message Handler ───
    def _handle_agent_msg(self, path, body):
        cid = path.split('/')[-1]
        from_agent = body.get('from', 'CEO')
        to_agent = body.get('to', 'CEO')
        text = body.get('text', '').strip()
        emoji = body.get('emoji', '👔')
        if not text or text in ('No reply from agent.', ''): self._json({"ok": False, "reason": "empty/no_reply"}); return

        text = re.sub(r'@마스터\s*', '', text).strip()
        company = get_company(cid)
        if not company: self._json({"error": "not found"}, 404); return

        now = datetime.now(); time_str = now.strftime('%H:%M')
        
        # 에이전트 응답에서 멘션 부분과 일반 부분 분리
        has_mentions = bool(re.search(r'@(\w+)', text))
        normal_text, mention_text = text, ''
        if has_mentions:
            normal_parts, mention_parts = [], []
            block_re = re.compile(r'@(\w+)\s*```([\s\S]*?)```', re.MULTILINE)
            remaining = text
            # 블록 멘션 추출
            for bm in block_re.finditer(text):
                before = text[:bm.start()].strip()
                after = text[bm.end():].strip()
                mention_parts.append(f"@{bm.group(1)} {bm.group(2).strip()}")
                if before: normal_parts.append(before)
                if after and not re.match(r'@\w+', after): normal_parts.append(after)
            if not mention_parts:
                # 블록 없으면 한줄 멘션
                for line in text.split('\n'):
                    lm = re.match(r'@(\w+)\s+(.+)', line.strip())
                    if lm and lm.group(1).upper() != from_agent.upper():
                        mention_parts.append(line.strip())
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

        created_agents = auto_create_agents(cid, company, text, time_str)
        if created_agents:
            company["activity_log"].extend(created_agents)
            update_company(cid, {"agents": company["agents"], "activity_log": company["activity_log"]})
            self._json({"ok": True, "msg": msg, "created": [c["text"] for c in created_agents]})
            return

        update_company(cid, {"chat": company["chat"], "activity_log": company["activity_log"]})
        self._json({"ok": True, "msg": msg})

        # Chain mentions (에이전트→에이전트)
        existing_ids = {a['id'].lower() for a in company.get('agents', [])}
        # 멘션 추출: @AGENT 한줄 or @AGENT ```멀티라인```
        block_re = re.compile(r'@(\w+)\s*```([\s\S]*?)```', re.MULTILINE)
        line_re = re.compile(r'@(\w+)\s+(.+)')
        seen = set()
        # 1) 블록 멘션 처리
        for bm in block_re.finditer(mention_text or text):
            m_name = bm.group(1)
            instruction = bm.group(2).strip()
            upper = m_name.upper()
            if upper != from_agent.upper() and m_name.lower() in existing_ids and upper not in seen:
                seen.add(upper)
                lock_key = f"{cid}:{upper}"
                with PROCESSORS_LOCK:
                    is_running = lock_key in PROCESSORS
                if not is_running:
                    threading.Thread(target=trigger_processor, args=(cid, instruction, upper), daemon=True).start()
        # 2) 한줄 멘션 처리
        for line in (mention_text or text).split('\n'):
            lm = line_re.match(line.strip())
            if lm:
                m_name = lm.group(1)
                instruction = lm.group(2).strip()
                # 따옴표 제거
                if instruction.startswith('"') and instruction.endswith('"'):
                    instruction = instruction[1:-1].strip()
                upper = m_name.upper()
                if upper != from_agent.upper() and m_name.lower() in existing_ids and upper not in seen:
                    seen.add(upper)
                    lock_key = f"{cid}:{upper}"
                    with PROCESSORS_LOCK:
                        is_running = lock_key in PROCESSORS
                    if not is_running:
                        threading.Thread(target=trigger_processor, args=(cid, instruction, upper), daemon=True).start()

    # ─── Company Delete Handler ───
    def _handle_company_delete(self, body):
        cid = body.get('id')
        company = get_company(cid)
        if company:
            for agent in company.get('agents', []):
                agent_id = agent.get('agent_id', '')
                if agent_id:
                    try:
                        subprocess.run(['openclaw', 'agents', 'delete', agent_id, '--force'],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
                    except Exception as e:
                        print(f"[WARN] agent delete failed {agent_id}: {e}")
            import shutil
            company_dir = DATA / cid
            if company_dir.exists():
                shutil.rmtree(company_dir, ignore_errors=True)
        companies = load_json(COMPANIES_FILE)
        companies = [c for c in companies if c["id"] != cid]
        save_json(COMPANIES_FILE, companies)
        state_file = DATA / f"{cid}.json"
        if state_file.exists(): state_file.unlink()
        queue_file = DATA / f"{cid}-queue.json"
        if queue_file.exists(): queue_file.unlink()
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
        register_agent(agent_id, agent_workspace, name, role, company.get('name',''), emoji, wait=True)
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
            # If agent addition was approved, actually add the agent
            if resolution == 'approved' and approval.get('type') == 'agent_add':
                company = get_company(cid)
                detail = approval.get('agent', '')
                # Parse from detail (stored as agent name in 'agent' field)
                # We'll need to re-trigger the add from UI
                pass
            self._json({"ok": True, "approval": approval})
        else:
            self._json({"error": "not found"}, 404)

# ─── Server Setup ───

class ReusableTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

def ensure_agents_registered():
    """On startup, re-register all agents from companies data."""
    companies = load_json(COMPANIES_FILE, [])
    for company in companies:
        cid = company['id']
        for agent in company.get('agents', []):
            agent_id = agent.get('agent_id', '')
            if not agent_id:
                continue
            result = subprocess.run(['openclaw', 'agents', 'list'], capture_output=True, text=True, timeout=10)
            if agent_id not in result.stdout:
                print(f"[INIT] Re-registering {agent_id}...")
                ws = DATA / cid / "workspaces" / agent['id']
                bootstrap = ws / "BOOTSTRAP.md"
                if bootstrap.exists():
                    bootstrap.unlink()
                register_agent(agent_id, ws, agent['name'], agent['role'],
                               company.get('name', ''), agent.get('emoji', '🤖'), wait=True)
                agent['status'] = 'active'

init_companies()
ensure_agents_registered()
restore_running_tasks()
print(f"🚀 AI Company Hub: http://localhost:{PORT}")

with ReusableTCPServer(("", PORT), Handler) as httpd:
    httpd.serve_forever()
