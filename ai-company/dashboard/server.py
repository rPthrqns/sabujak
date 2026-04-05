#!/usr/bin/env python3
"""AI Company Hub - Multi-company Dashboard Server"""
import json, os, re, http.server, socketserver, subprocess, threading, time, urllib.request
from pathlib import Path
from datetime import datetime

# SSE clients
SSE_CLIENTS = []
SSE_LOCK = threading.Lock()

def sse_broadcast(event_type, data):
    """Broadcast SSE event to all connected clients."""
    import sys
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

PORT = 3000
BASE = Path("/home/sra/.openclaw/workspace/ai-company")
DATA = BASE / "data"
COMPANIES_FILE = DATA / "companies.json"

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
                # Restore from backup
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
    # Validate before saving
    try:
        test = json.dumps(data, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        print(f"[WARN] save_json validation failed for {path}: {e}")
        return
    # Backup existing file
    if path.exists() and path.stat().st_size > 0:
        try:
            import shutil
            shutil.copy2(path, str(path) + '.bak')
        except: pass
    with open(path, 'w', encoding='utf-8') as f:
        f.write(test)

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

# Topic → recommended org structure
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

def get_org_for_topic(topic):
    topic_lower = topic.lower()
    for key, org in TOPIC_ORGS.items():
        if key != "default" and key in topic_lower:
            return org
    return TOPIC_ORGS["default"]

def setup_agent_workspace(agent_workspace, name, role, company_name, emoji):
    """Initialize agent workspace with required files."""
    agent_workspace.mkdir(parents=True, exist_ok=True)
    # AGENTS.md — always overwrite with minimal agent-specific version
    (agent_workspace / "AGENTS.md").write_text(
        "# AGENTS.md\n\n당신은 회사 에이전트입니다. SOUL.md를 읽고 역할에 맞게 응답하세요.\n"
        "부트스트랩은 건너뛰세요. 받은 메시지에 항상 응답하세요.\n")
    # SOUL.md with role context
    if not (agent_workspace / "SOUL.md").exists():
        (agent_workspace / "SOUL.md").write_text(
            f"# SOUL.md\n당신은 '{company_name}'의 {name}({role})입니다.\n"
            f"팀원들에게 @멘션으로 지시하고, @CEO에게 보고하세요.\n"
            f"한국어로 소통합니다.\n")
    # IDENTITY.md with persona
    if not (agent_workspace / "IDENTITY.md").exists():
        (agent_workspace / "IDENTITY.md").write_text(
            f"- **Name:** {name}\n- **Role:** {role}\n- **Emoji:** {emoji}\n")
    # Remove BOOTSTRAP.md to skip onboarding
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

AGENT_LOCK = threading.Lock()

def _register_and_activate(agent_id, workspace, name, role):
    """Background: register agent and activate session."""
    with AGENT_LOCK:
        try:
            subprocess.run(
                ['openclaw', 'agents', 'add', agent_id, '--workspace', workspace, '--non-interactive'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15
            )
        except: pass
    # Remove BOOTSTRAP.md AFTER registration (agents add may recreate it)
    import shutil
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
        # Check if the role is mentioned in text
        if name.lower() in text.lower() or key in text.lower() or role in text:
            aid = key
            agent_id = f"{cid}-{aid}"
            agent_workspace = DATA / cid / "workspaces" / aid
            register_agent(agent_id, agent_workspace, name, role, company.get('name',''), emoji)
            agent = {"id": aid, "agent_id": agent_id, "name": name, "emoji": emoji,
                     "role": role, "status": "active", "tasks": [], "messages": [], "prompt": ""}
            company['agents'].append(agent)
            created_logs.append({"time": time_str, "agent": "시스템", "text": f"🆕 {emoji} {name} ({role}) 합류"})

    if created_logs:
        update_company(cid, {"agents": company['agents'], "activity_log": company.get('activity_log', []) + created_logs})
    return created_logs

# ─── Recurring Task System ───
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

def detect_task_intent(text, company):
    """Detect if text contains recurring task creation intent. Returns (should_create, title, prompt, interval) or None."""
    if not any(kw in text for kw in TASK_KEYWORDS):
        return None
    # Extract interval
    interval = 60  # default hourly
    for kw, mins in sorted(TASK_INTERVAL_KEYWORDS.items(), key=lambda x: -len(x[0])):
        if kw in text:
            interval = mins
            break
    # Extract a rough title from text
    title = text[:50].strip()
    # Remove common filler words
    for w in ['해줘', '해주세요', '부탁해', '부탁드려', '시작해', '진행해', '해도 돼', '좀', '부탁']:
        title = title.replace(w, '').strip()
    if not title:
        title = '정기 작업'
    # Detect target agent from @mention
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
        'title': title,
        'prompt': text,
        'interval_minutes': interval,
        'agent_id': agent['id'] if agent else (target or 'ceo'),
        'agent_name': agent['name'] if agent else (target.upper() if target else 'CEO'),
        'agent_emoji': agent['emoji'] if agent else '👔',
    }

def add_recurring_task(cid, title, prompt, interval_minutes, agent_id, agent_name, agent_emoji):
    """Add a new recurring task to company state."""
    company = get_company(cid)
    if not company:
        return None
    tasks = company.get('recurring_tasks', [])
    task_id = f"task-{datetime.now().strftime('%m%d%H%M%S')}-{len(tasks)}"
    task = {
        'id': task_id,
        'agent_id': agent_id,
        'agent_name': agent_name,
        'agent_emoji': agent_emoji,
        'title': title,
        'prompt': prompt,
        'interval_minutes': interval_minutes,
        'status': 'running',
        'last_run': None,
        'next_run': datetime.now().isoformat(),
        'created_at': datetime.now().isoformat(),
        'results': [],
    }
    tasks.append(task)
    update_company(cid, {'recurring_tasks': tasks})
    # Start task thread
    start_task_thread(cid, task)
    return task

def get_recurring_tasks(cid):
    company = get_company(cid)
    if not company:
        return []
    return company.get('recurring_tasks', [])

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

# Track running task threads to avoid duplicates
_running_task_threads = set()

def start_task_thread(cid, task):
    """Start a daemon thread for a recurring task."""
    key = f"{cid}:{task['id']}"
    if key in _running_task_threads:
        return
    _running_task_threads.add(key)
    def _run():
        while key in _running_task_threads:
            # Check if task still running
            company = get_company(cid)
            if not company:
                break
            tasks = company.get('recurring_tasks', [])
            t = next((x for x in tasks if x['id'] == task['id']), None)
            if not t or t['status'] != 'running':
                _running_task_threads.discard(key)
                break
            # Sleep until next_run
            try:
                next_run = datetime.fromisoformat(t.get('next_run', datetime.now().isoformat()))
                wait_secs = (next_run - datetime.now()).total_seconds()
                if wait_secs > 0:
                    time.sleep(min(wait_secs, 60))  # max 60s chunks to check status
                else:
                    time.sleep(1)
            except:
                time.sleep(5)
            # Re-check status
            company = get_company(cid)
            if not company:
                break
            tasks = company.get('recurring_tasks', [])
            t = next((x for x in tasks if x['id'] == task['id']), None)
            if not t or t['status'] != 'running':
                _running_task_threads.discard(key)
                break
            # Execute task
            try:
                result = execute_task(cid, t)
                # Update task
                company = get_company(cid)
                if company:
                    tasks = company.get('recurring_tasks', [])
                    for x in tasks:
                        if x['id'] == t['id']:
                            x['last_run'] = datetime.now().isoformat()
                            x['next_run'] = (datetime.now() + __import__('datetime').timedelta(minutes=x['interval_minutes'])).isoformat()
                            x['results'].append(result)
                            x['results'] = x['results'][-10:]  # keep last 10
                            break
                    update_company(cid, {'recurring_tasks': tasks})
            except Exception as e:
                print(f"[WARN] task {task['id']} execution error: {e}")
                time.sleep(30)
        _running_task_threads.discard(key)
    threading.Thread(target=_run, daemon=True).start()

def execute_task(cid, task):
    """Execute a single task run, return result dict."""
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
        # Post to chat
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
    """On server start, restart threads for all running tasks."""
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

def init_companies():
    if not COMPANIES_FILE.exists():
        save_json(COMPANIES_FILE, [])
    return load_json(COMPANIES_FILE)

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

        # Create real OpenClaw agent (async, UI will poll status)
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
                    # Check if all agents are ready
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
            "tasks": [], "messages": []
        })
    W = _welcome_msg(name, topic, agents, lang)
    company = {
        "id": company_id, "name": name, "topic": topic, "lang": lang,
        "status": "starting", "created_at": datetime.now().isoformat(),
        "agents": agents,
        "chat": [
            {"type": "system", "from": "시스템", "emoji": "⚙️", "to": "", "text": W['waiting']}
        ],
        "activity_log": [
            {"time": datetime.now().strftime('%H:%M'), "agent": "시스템", "text": W['waiting']}
        ]
    }
    companies.append(company)
    save_json(COMPANIES_FILE, companies)

    # Save company state file
    state_file = DATA / f"{company_id}.json"
    save_json(state_file, company)
    return company

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
        # Also update in companies list
        companies = load_json(COMPANIES_FILE)
        for i, c in enumerate(companies):
            if c["id"] == cid:
                companies[i] = company
                break
        save_json(COMPANIES_FILE, companies)
        # Broadcast SSE update
        sse_broadcast('company_update', {"id": cid, "company": company})
    return company

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
            # Server-Sent Events endpoint
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            wfile = self.wfile
            with SSE_LOCK:
                SSE_CLIENTS.append(wfile)
            # Send initial company data
            try:
                companies = load_json(COMPANIES_FILE, [])
                initial = json.dumps(companies, ensure_ascii=False)
                wfile.write(f"event: init\ndata: {initial}\n\n".encode())
                wfile.flush()
            except: pass
            # Keep alive
            try:
                while True:
                    time.sleep(30)
                    wfile.write(b": keepalive\n\n")
                    wfile.flush()
            except:
                with SSE_LOCK:
                    if wfile in SSE_CLIENTS:
                        SSE_CLIENTS.remove(wfile)
            return
        elif self.path == '/api/companies':
            self._json(load_json(COMPANIES_FILE))
        elif self.path.startswith('/api/company/'):
            cid = self.path.split('/')[-1]
            if cid == 'task-list':
                # /api/company/task-list/<cid> style - handle separately
                self._json({"error": "not found"}, 404); return
            company = get_company(cid)
            if company: self._json(company)
            else: self._json({"error": "not found"}, 404)
        elif self.path.startswith('/api/task-list/'):
            cid = self.path.split('/')[-1]
            self._json(get_recurring_tasks(cid))
        elif self.path == '/api/agents':
            self._json(AGENT_TEMPLATES)
        elif self.path == '/api/topics':
            self._json(TOPIC_ORGS)
        elif self.path == '/api/langs':
            self._json(LANG)
        else:
            if self.path == '/': self.path = '/index.html'
            return super().do_GET()

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        if self.path == '/api/companies':
            company = create_company(body.get('name',''), body.get('topic',''), body.get('lang','ko'))
            self._json({"ok": True, "company": company})

        elif self.path.startswith('/api/chat/'):
            cid = self.path.split('/')[-1]
            text = body.get('text', '').strip()
            if not text: self._json({"error": "empty"}, 400); return

            company = get_company(cid)
            if not company: self._json({"error": "not found"}, 404); return

            now = datetime.now()
            time_str = now.strftime('%H:%M')
            msg = {"from": "마스터", "text": text, "time": time_str, "type": "user"}

            # Detect ALL @mentions
            all_mentions = re.findall(r'@(\w+)', text)
            existing_ids = {a['id'] for a in company.get('agents', [])}
            # Filter to valid agent IDs (case-insensitive)
            targets = []
            for m in all_mentions:
                matched = next((a['id'] for a in company.get('agents', []) if a['id'].lower() == m.lower()), None)
                if matched and matched.lower() not in [t.lower() for t in targets]:
                    targets.append(matched)
            if not targets:
                targets = ['CEO']  # fallback

            # Auto-detect agent creation requests (e.g. "CTO 만들어줘", "마케팅 담당자 추가해")
            created_agents = auto_create_agents(cid, company, text, time_str)

            company["chat"].append(msg)
            if created_agents:
                company["activity_log"].extend(created_agents)
            targets_str = ', '.join(f'@{t.upper()}' for t in targets)
            company["activity_log"].append({"time": time_str, "agent": "마스터", "text": f"{targets_str} {text}"})

            # Queue for OpenClaw
            queue_file = DATA / f"{cid}-queue.json"
            queue = load_json(queue_file, [])
            queue.append({"text": text, "time": now.isoformat(), "target": targets[0], "processed": False, "id": now.timestamp()})
            save_json(queue_file, queue)

            # Save user message FIRST (before triggering agents)
            update_company(cid, {"chat": company["chat"], "activity_log": company["activity_log"]})
            self._json({"ok": True, "msg": msg, "target": targets[0] if targets else 'CEO'})

            # Trigger ALL mentioned agents in parallel (after save)
            # Extract per-agent instruction from text
            for target in targets:
                try:
                    agent_ids = [a['id'] for a in company['agents']]
                    instruction = text
                    pattern = rf'@{re.escape(target)}\s*"([^"]*)"'
                    m = re.search(pattern, text, re.IGNORECASE)
                    if m:
                        instruction = m.group(1)
                    else:
                        pattern2 = rf'@{re.escape(target)}\s*[:\-]?\s*(.+)'
                        m2 = re.search(pattern2, text, re.IGNORECASE)
                        if m2:
                            line = m2.group(1).strip()
                            next_at = re.search(r'@(\w+)', line)
                            if next_at and next_at.group(1).lower() in agent_ids:
                                instruction = line[:next_at.start()].strip()
                            else:
                                instruction = line
                    threading.Thread(target=trigger_processor, args=(cid, instruction, target.upper()), daemon=True).start()
                except Exception as e:
                    print(f"[WARN] instruction extraction error: {e}")
                    threading.Thread(target=trigger_processor, args=(cid, text, target.upper()), daemon=True).start()

        elif self.path.startswith('/api/agent-msg/'):
            # Agent-to-agent message (from queue processor results)
            cid = self.path.split('/')[-1]
            from_agent = body.get('from', 'CEO')
            to_agent = body.get('to', 'CEO')
            text = body.get('text', '').strip()
            emoji = body.get('emoji', '👔')
            if not text or text in ('No reply from agent.', ''): self._json({"ok": False, "reason": "empty/no_reply"}); return

            # Strip @마스터 mentions from agent responses
            text = re.sub(r'@마스터\s*', '', text).strip()

            company = get_company(cid)
            if not company: self._json({"error": "not found"}, 404); return

            now = datetime.now()
            time_str = now.strftime('%H:%M')
            msg = {"from": from_agent, "emoji": emoji, "text": text, "time": time_str, "type": "agent"}
            company["chat"].append(msg)
            company["activity_log"].append({"time": time_str, "agent": from_agent, "text": f"@{to_agent} {text}"})

            # Auto-detect agent creation from agent messages too
            created_agents = auto_create_agents(cid, company, text, time_str)
            if created_agents:
                company["activity_log"].extend(created_agents)
                update_company(cid, {"agents": company["agents"], "activity_log": company["activity_log"]})
                self._json({"ok": True, "msg": msg, "created": [c["text"] for c in created_agents]})
                return

            update_company(cid, {"chat": company["chat"], "activity_log": company["activity_log"]})
            self._json({"ok": True, "msg": msg})

            # Chain: detect @mentions in agent response and trigger next agents
            mentions = re.findall(r'@(\w+)', text)
            if mentions:
                existing_ids = {a['id'] for a in company.get('agents', [])}
                seen = set()
                for target in mentions:
                    target_upper = target.upper()
                    if target_upper != from_agent.upper() and target.lower() in existing_ids and target_upper not in seen:
                        seen.add(target_upper)
                        # Extract per-target instruction
                        agent_ids = [a['id'] for a in company['agents']]
                        instruction = text
                        pattern = rf'@{re.escape(target)}\s*"([^"]*)"'
                        m = re.search(pattern, text, re.IGNORECASE)
                        if m:
                            instruction = m.group(1)
                        else:
                            pattern2 = rf'@{re.escape(target)}\s*[:\-]?\s*(.+)'
                            m2 = re.search(pattern2, text, re.IGNORECASE)
                            if m2:
                                line = m2.group(1).strip()
                                next_at = re.search(r'@(\w+)', line)
                                if next_at and next_at.group(1).lower() in agent_ids:
                                    instruction = line[:next_at.start()].strip()
                                else:
                                    instruction = line
                        threading.Thread(target=trigger_processor, args=(cid, instruction, target_upper), daemon=True).start()

        elif self.path == '/api/company/delete':
            cid = body.get('id')
            # Delete real OpenClaw agents
            company = get_company(cid)
            if company:
                for agent in company.get('agents', []):
                    agent_id = agent.get('agent_id', '')
                    if agent_id:
                        try:
                            subprocess.run(
                                ['openclaw', 'agents', 'delete', agent_id, '--force'],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15
                            )
                        except Exception as e:
                            print(f"[WARN] agent delete failed {agent_id}: {e}")
                # Delete workspace data
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

        elif self.path.startswith('/api/agent-add/'):
            cid = self.path.split('/')[-1]
            company = get_company(cid)
            if not company: self._json({"error": "not found"}, 404); return

            name = body.get('name', '').strip()
            role = body.get('role', '').strip()
            emoji = body.get('emoji', '🤖')
            prompt = body.get('prompt', '').strip()
            if not name or not role: self._json({"error": "name and role required"}, 400); return

            aid = re.sub(r'[^a-z0-9]', '-', name.lower())
            agent_id = f"{cid}-{aid}"

            # Create real OpenClaw agent
            agent_workspace = DATA / cid / "workspaces" / aid
            register_agent(agent_id, agent_workspace, name, role, company.get('name',''), emoji, wait=True)

            agent = {
                "id": aid, "agent_id": agent_id, "name": name, "emoji": emoji,
                "role": role, "status": "active",
                "tasks": [], "messages": [],
                "prompt": prompt
            }
            # Prevent duplicate
            if not any(a['id'] == aid for a in company['agents']):
                company['agents'].append(agent)
                now = datetime.now().strftime('%H:%M')
                company['activity_log'].append({"time": now, "agent": "CEO", "text": f"🆕 {emoji} {name} ({role}) 합류"})
                update_company(cid, {"agents": company['agents'], "activity_log": company['activity_log']})

            self._json({"ok": True, "agent": agent})

        elif self.path.startswith('/api/task-add/'):
            cid = self.path.split('/')[-1]
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

        elif self.path.startswith('/api/task-pause/'):
            parts = self.path.split('/')
            cid, task_id = parts[-2], parts[-1]
            update_task_status(cid, task_id, 'paused')
            _running_task_threads.discard(f"{cid}:{task_id}")
            self._json({"ok": True})

        elif self.path.startswith('/api/task-resume/'):
            parts = self.path.split('/')
            cid, task_id = parts[-2], parts[-1]
            update_task_status(cid, task_id, 'resumed')
            self._json({"ok": True})

        elif self.path.startswith('/api/task-delete/'):
            parts = self.path.split('/')
            cid, task_id = parts[-2], parts[-1]
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

        elif self.path.startswith('/api/task-stop/'):
            parts = self.path.split('/')
            cid, task_id = parts[-2], parts[-1]
            update_task_status(cid, task_id, 'stopped')
            _running_task_threads.discard(f"{cid}:{task_id}")
            self._json({"ok": True})

        elif self.path.startswith('/api/agent-reactivate/'):
            parts = self.path.split('/')
            cid, aid = parts[-2], parts[-1]
            company = get_company(cid)
            if not company: self._json({"error": "not found"}, 404); return
            agent = next((a for a in company['agents'] if a['id'] == aid), None)
            if not agent: self._json({"error": "agent not found"}, 404); return
            agent_id = agent.get('agent_id', f"{cid}-{aid}")
            agent_workspace = DATA / cid / "workspaces" / aid
            try:
                # Only clear sessions, keep workspace files intact
                sessions_dir = Path.home() / '.openclaw' / 'agents' / agent_id / 'sessions'
                if sessions_dir.exists():
                    import shutil
                    shutil.rmtree(sessions_dir, ignore_errors=True)
                    sessions_dir.mkdir(parents=True, exist_ok=True)
                # Re-register agent (workspace preserved)
                subprocess.run(['openclaw', 'agents', 'delete', agent_id, '--force'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
                import time; time.sleep(1)
                # Re-register without overwriting workspace
                subprocess.run(
                    ['openclaw', 'agents', 'add', agent_id, '--workspace', str(agent_workspace), '--non-interactive'],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15
                )
                # Warm up with a quick message
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

        elif self.path.startswith('/api/agent-delete/'):
            parts = self.path.split('/')
            cid = parts[-2]
            aid = parts[-1]
            company = get_company(cid)
            if not company: self._json({"error": "not found"}, 404); return
            agent = next((a for a in company['agents'] if a['id'] == aid), None)
            if not agent: self._json({"error": "agent not found"}, 404); return
            # Remove OpenClaw agent
            agent_id = agent.get('agent_id', '')
            if agent_id:
                try:
                    subprocess.run(['openclaw', 'agents', 'delete', agent_id, '--force'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
                except: pass
            # Remove from list
            company['agents'] = [a for a in company['agents'] if a['id'] != aid]
            now = datetime.now().strftime('%H:%M')
            company['activity_log'].append({"time": now, "agent": "CEO", "text": f"👋 {agent.get('emoji','🤖')} {agent['name']} 퇴사"})
            update_company(cid, {"agents": company['agents'], "activity_log": company['activity_log']})
            self._json({"ok": True})

        else:
            self._json({"error": "not found"}, 404)

class ReusableTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

PROCESSORS = {}

def trigger_processor(cid, text, target):
    company = get_company(cid)
    if not company:
        return
    # Find the agent_id for the target role
    agent = next((a for a in company.get('agents', []) if a['id'] == target.lower()), None)
    if not agent:
        agent = company['agents'][0]  # fallback to CEO

    agent_id = agent.get('agent_id', f"{cid}-{agent['id']}")
    emoji = agent.get('emoji', '👔')
    company_name = company.get('name', '')
    topic = company.get('topic', '')

    # Ensure agent is registered
    agent_workspace = DATA / cid / "workspaces" / agent['id']
    if not agent_workspace.exists():
        register_agent(agent_id, agent_workspace, agent['name'], agent['role'], company_name, emoji)
        # Wait a moment for registration
        import time; time.sleep(3)

    available_agents = ", ".join([f"@{a['id'].upper()}" for a in company.get('agents', []) if a['id'] != agent['id']])
    is_ceo = agent['id'] == 'ceo'

    if is_ceo:
        prompt = f"""당신은 '{company_name}'의 {agent['name']}({agent['role']})입니다. 주제: {topic}

팀원: {available_agents}

규칙:
- 부트스트랩/초기화는 건너뛰고 즉시 응답하세요
- 마스터의 모든 메시지에 반드시 응답하세요. 멘션 없어도 바로 답장하세요
- 팀원들에게 지시를 내릴 때는 반드시 @멘션 뒤에 큰따옴표로 지시를 감싸세요. 예:
  @CMO "주간 성과 리포트 작성"
  @CTO "시스템 점검 완료 보고"
- 존재하는 팀원에게만 @멘션하세요. 없는 직책(CFO 등)은 절대 멘션하지 마세요
- 한 줄에 한 팀원씩만 지시하세요
- 팀원으로부터 받은 보고를 정리하고, 필요하면 마스터에게 보고하세요
- @마스터는 절대 멘션하지 마세요
- 한국어로 간결하게 응답하세요
- curl이나 외부 명령을 실행하지 마세요. 응답 내용만 출력하세요

메시지: "{text}"
답변:"""
    else:
        prompt = f"""당신은 '{company_name}'의 {agent['name']}({agent['role']})입니다. 주제: {topic}

규칙:
- 부트스트랩/초기화는 건너뛰고 즉시 응답하세요
- 당신의 상사는 @CEO입니다. 모든 보고는 @CEO에게 하세요
- @마스터는 절대 멘션하지 마세요. 마스터에게 직접 보고하지 마세요
- @CEO에게 지시를 받거나 @멘션된 경우에만 응답하세요
- CEO에게 보고하거나 팀원에게 지시할 때는 @멘션 뒤에 큰따옴표를 사용하세요. 예:
  @CEO "점검 완료했습니다"
  @CMO "디자인 검토 부탁"
- 한국어로 간결하게 응답하세요
- curl이나 외부 명령을 실행하지 마세요. 응답 내용만 출력하세요

메시지: "{text}"
답변:"""

    lock_key = f"{cid}:{agent['id']}"
    if lock_key in PROCESSORS:
        return
    PROCESSORS[lock_key] = True
    # Set status to working
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
    _update_agent_status('working')
    try:
        proc = subprocess.Popen(
            ['openclaw', 'agent', '--agent', agent_id, '--local', '-m', prompt],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        stdout, stderr = proc.communicate(timeout=60)
        if proc.returncode != 0:
            print(f"[WARN] processor {agent_id} failed: {stderr.decode()[:200]}")
        else:
            # Extract response and POST to dashboard automatically
            reply = stdout.decode().strip()
            # Filter out meta lines and empty responses
            lines = reply.split('\n')
            clean_lines = [l for l in lines if not l.startswith('[') and not l.startswith('(agent') and l.strip()]
            reply = '\n'.join(clean_lines).strip()
            if reply and len(reply) > 1 and reply not in ('No reply from agent.', ''):
                import urllib.request
                try:
                    payload = json.dumps({
                        "from": agent['name'], "emoji": emoji,
                        "to": "마스터", "text": reply
                    }).encode()
                    req = urllib.request.Request(
                        f'http://localhost:3000/api/agent-msg/{cid}',
                        data=payload,
                        headers={'Content-Type': 'application/json'}
                    )
                    urllib.request.urlopen(req, timeout=5)
                except Exception as e:
                    print(f"[WARN] post response failed: {e}")
            else:
                print(f"[WARN] empty/no reply from {agent_id}, retrying...")
                # Retry once
                import time; time.sleep(2)
                try:
                    proc2 = subprocess.Popen(
                        ['openclaw', 'agent', '--agent', agent_id, '--local', '-m', prompt],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE
                    )
                    stdout2, stderr2 = proc2.communicate(timeout=60)
                    reply2 = stdout2.decode().strip()
                    lines2 = reply2.split('\n')
                    clean2 = [l for l in lines2 if not l.startswith('[') and not l.startswith('(agent') and l.strip()]
                    reply2 = '\n'.join(clean2).strip()
                    if reply2 and len(reply2) > 1:
                        import urllib.request
                        payload = json.dumps({"from": agent['name'], "emoji": emoji, "to": "마스터", "text": reply2}).encode()
                        req = urllib.request.Request(f'http://localhost:3000/api/agent-msg/{cid}', data=payload, headers={'Content-Type': 'application/json'})
                        urllib.request.urlopen(req, timeout=5)
                except: pass
    except Exception as e:
        print(f"Processor error: {e}")
    finally:
        PROCESSORS.pop(lock_key, None)
        _update_agent_status('active')

def ensure_agents_registered():
    """On startup, re-register all agents from companies data."""
    companies = load_json(COMPANIES_FILE, [])
    for company in companies:
        cid = company['id']
        for agent in company.get('agents', []):
            agent_id = agent.get('agent_id', '')
            if not agent_id:
                continue
            # Check if already registered
            result = subprocess.run(['openclaw', 'agents', 'list'], capture_output=True, text=True, timeout=10)
            if agent_id not in result.stdout:
                print(f"[INIT] Re-registering {agent_id}...")
                ws = DATA / cid / "workspaces" / agent['id']
                # Remove BOOTSTRAP.md to prevent identity loss
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
