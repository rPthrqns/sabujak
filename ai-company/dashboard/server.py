#!/usr/bin/env python3
"""AI Company Hub - Multi-company Dashboard Server"""
import json, os, re, http.server, socketserver, subprocess, threading
from pathlib import Path
from datetime import datetime

PORT = 3000
BASE = Path("/home/sra/.openclaw/workspace/ai-company")
DATA = BASE / "data"
COMPANIES_FILE = DATA / "companies.json"

def load_json(path, default=None):
    if path.exists():
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return default if default is not None else []

def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

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
            agent_workspace.mkdir(parents=True, exist_ok=True)
            if not (agent_workspace / "AGENTS.md").exists():
                (agent_workspace / "AGENTS.md").write_text("# AGENTS.md\n")
            if not (agent_workspace / "SOUL.md").exists():
                (agent_workspace / "SOUL.md").write_text(f"# SOUL.md\n당신은 '{name}'({role})입니다.\n")
            try:
                subprocess.run(
                    ['openclaw', 'agents', 'add', agent_id,
                     '--workspace', str(agent_workspace), '--non-interactive'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10
                )
            except: pass
            agent = {"id": aid, "agent_id": agent_id, "name": name, "emoji": emoji,
                     "role": role, "status": "active", "tasks": [], "messages": [], "prompt": ""}
            company['agents'].append(agent)
            created_logs.append({"time": time_str, "agent": "시스템", "text": f"🆕 {emoji} {name} ({role}) 합류"})

    if created_logs:
        update_company(cid, {"agents": company['agents'], "activity_log": company.get('activity_log', []) + created_logs})
    return created_logs

def init_companies():
    if not COMPANIES_FILE.exists():
        save_json(COMPANIES_FILE, [])
    return load_json(COMPANIES_FILE)

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

        # Create real OpenClaw agent
        agent_workspace = DATA / company_id / "workspaces" / aid
        agent_workspace.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ['openclaw', 'agents', 'add', agent_id,
             '--workspace', str(agent_workspace), '--non-interactive'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10
        )

        agents.append({
            "id": aid, "agent_id": agent_id, "name": agent_name, "emoji": agent_emoji,
            "role": agent_role, "status": "active" if aid == "ceo" else "idle",
            "tasks": [], "messages": []
        })
    company = {
        "id": company_id, "name": name, "topic": topic, "lang": lang,
        "status": "starting", "created_at": datetime.now().isoformat(),
        "agents": agents,
        "chat": [
            {"type": "agent", "from": "CEO", "emoji": "👔", "to": "마스터", "text": f"안녕하세요 마스터! 👋\n\n저는 '{name}'의 CEO입니다.\n\n주제: {topic}\n팀원: {', '.join(a['name'] for a in agents[1:])}\n\n@멘션으로 팀원들에게 지시하실 수 있습니다. 무엇부터 시작할까요?"}
        ],
        "activity_log": [
            {"time": datetime.now().strftime('%H:%M'), "agent": "CEO", "text": f"🏢 '{name}' 프로젝트 시작. 주제: {topic}"}
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
        return load_json(state_file)
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
    return company

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(BASE / "dashboard"), **kw)

    def log_message(self, fmt, *args): pass

    def _json(self, data, code=200):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self._cors()
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def do_OPTIONS(self):
        self.send_response(200); self._cors(); self.end_headers()

    def do_GET(self):
        if self.path == '/api/companies':
            self._json(load_json(COMPANIES_FILE))
        elif self.path.startswith('/api/company/'):
            cid = self.path.split('/')[-1]
            company = get_company(cid)
            if company: self._json(company)
            else: self._json({"error": "not found"}, 404)
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

            # Detect @mention
            mention = re.search(r'@(\w+)', text)
            target = mention.group(1).upper() if mention else 'CEO'

            # Auto-detect agent creation requests (e.g. "CTO 만들어줘", "마케팅 담당자 추가해")
            created_agents = auto_create_agents(cid, company, text, time_str)

            company["chat"].append(msg)
            if created_agents:
                company["activity_log"].extend(created_agents)
            company["activity_log"].append({"time": time_str, "agent": "마스터", "text": f"@{target} {text}"})

            # Queue for OpenClaw
            queue_file = DATA / f"{cid}-queue.json"
            queue = load_json(queue_file, [])
            queue.append({"text": text, "time": now.isoformat(), "target": target, "processed": False, "id": now.timestamp()})
            # Save queue
            save_json(queue_file, queue)

            # Immediately trigger processing via dedicated agent
            threading.Thread(target=trigger_processor, args=(cid, text, target), daemon=True).start()

            update_company(cid, {"chat": company["chat"], "activity_log": company["activity_log"]})
            self._json({"ok": True, "msg": msg, "target": target})

        elif self.path.startswith('/api/agent-msg/'):
            # Agent-to-agent message (from queue processor results)
            cid = self.path.split('/')[-1]
            from_agent = body.get('from', 'CEO')
            to_agent = body.get('to', 'CEO')
            text = body.get('text', '').strip()
            emoji = body.get('emoji', '👔')
            if not text: self._json({"error": "empty"}, 400); return

            company = get_company(cid)
            if not company: self._json({"error": "not found"}, 404); return

            now = datetime.now()
            time_str = now.strftime('%H:%M')
            msg = {"from": from_agent, "emoji": emoji, "text": f"@{to_agent} {text}", "time": time_str, "type": "agent"}
            company["chat"].append(msg)
            company["activity_log"].append({"time": time_str, "agent": from_agent, "text": f"@{to_agent} {text}"})
            update_company(cid, {"chat": company["chat"], "activity_log": company["activity_log"]})
            self._json({"ok": True, "msg": msg})

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
            agent_workspace.mkdir(parents=True, exist_ok=True)
            # Ensure required workspace files exist
            if not (agent_workspace / "AGENTS.md").exists():
                (agent_workspace / "AGENTS.md").write_text("# AGENTS.md\n")
            if not (agent_workspace / "SOUL.md").exists():
                (agent_workspace / "SOUL.md").write_text(f"# SOUL.md\n당신은 '{name}'({role})입니다.\n")
            try:
                subprocess.run(
                    ['openclaw', 'agents', 'add', agent_id,
                     '--workspace', str(agent_workspace), '--non-interactive'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10
                )
            except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
                pass  # agent creation is optional

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

class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True

PROCESSORS = {}

def trigger_processor(cid, text, target):
    if cid in PROCESSORS:
        return
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

    prompt = f"""당신은 '{company_name}'의 {agent['name']}({agent['role']})입니다. 주제: {topic}

메시지: "{text}"

처리 후 대시보드에 응답 POST:
curl -s -X POST http://localhost:3000/api/agent-msg/{cid} -H 'Content-Type: application/json' -d '{{"from":"{agent['name']}","emoji":"{emoji}","to":"마스터","text":"응답내용"}}'

큐 마킹: python3 -c "import json; f='/home/sra/.openclaw/workspace/ai-company/data/{cid}-queue.json'; q=json.load(open(f)); [m.__setitem__('processed',True) for m in q if not m.get('processed')]; json.dump(q,open(f,'w'),ensure_ascii=False)"

한국어로 응답."""

    PROCESSORS[cid] = True
    try:
        proc = subprocess.Popen(
            ['openclaw', 'agent', '--agent', agent_id, '--local', '-m', prompt],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        stdout, stderr = proc.communicate(timeout=60)
        if proc.returncode != 0:
            print(f"[WARN] processor {agent_id} failed: {stderr.decode()[:200]}")
    except Exception as e:
        print(f"Processor error: {e}")
    finally:
        PROCESSORS.pop(cid, None)

init_companies()
print(f"🚀 AI Company Hub: http://localhost:{PORT}")

with ReusableTCPServer(("", PORT), Handler) as httpd:
    httpd.serve_forever()
