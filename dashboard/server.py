#!/usr/bin/env python3
"""AI Company Hub - Multi-company Dashboard Server
Enhanced with Goals, Kanban Board, Cost Tracking, Approval Gates, and Task Dependencies."""
import asyncio, hmac, json, os, re, http.server, socketserver, subprocess, threading, time, urllib.request, urllib.parse, uuid
from collections import deque
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
               db_get_plan_tasks, db_add_plan_task, db_update_plan_task, db_delete_plan_task,
               db_get_sprints, db_add_sprint, db_update_sprint, db_get_sprint_tasks, db_link_task_to_sprint,
               db_get_wiki_pages, db_get_wiki_page, db_save_wiki_page, db_delete_wiki_page,
               db_get_meetings, db_add_meeting,
               db_get_milestones, db_add_milestone, db_update_milestone, db_delete_milestone,
               db_get_risks, db_add_risk, db_update_risk, db_delete_risk,
               db_get_announcements, db_add_announcement, db_delete_announcement,
               db_get_journals, db_add_journal,
               db_get_policies, db_add_policy, db_delete_policy,
               db_get_budgets, db_set_budget,
               db_get_votes, db_add_vote, db_cast_vote,
               db_add_audit, db_get_audit,
               db_get_contacts, db_add_contact, db_delete_contact,
               db_add_memory, db_get_memories,
               db_get_priorities, db_set_priority, db_init_default_priorities, PRIORITY_CATEGORIES)

# ─── Configuration (centralized in config.py) ───
from config import (
    PORT, BASE, DATA, COMPANIES_FILE,
    AGENT_RUN_TIMEOUT, AGENT_RETRY_TIMEOUT, AGENT_INIT_TIMEOUT, AGENT_POLL_INTERVAL,
    MAX_CONCURRENT_AGENTS, AGENT_QUEUE_MAX,
    WATCHDOG_INTERVAL, WATCHDOG_STUCK_THRESHOLD,
    GUARDRAIL_PREP_MAX_LEN, GUARDRAIL_MAX_RETRIES, ESCALATION_MAX_LEVELS,
    MEMORY_MAX_PER_AGENT, MEMORY_TOP_K,
    AGENT_LIMIT_BEFORE_APPROVAL,
    DEFAULT_LANG, DEFAULT_BUDGET,
)
from parsers.commands import (
    parse_task_add as _p_task_add,
    parse_task_done as _p_task_done,
    parse_task_start as _p_task_start,
    parse_task_block as _p_task_block,
    parse_cron_add as _p_cron_add,
    parse_cron_del as _p_cron_del,
    parse_approval as _p_approval,
)
from parsers.guardrails import is_prep_only as _g_is_prep, has_required_action as _g_has_action
from observability import dump_prompt as _dump_prompt
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

# Load agent templates and topic orgs from config.json (editable by users)
_CONFIG_PATH = Path(__file__).parent / "config.json"
def _load_config():
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding='utf-8'))
    except Exception:
        return {}
_config = _load_config()
AGENT_TEMPLATES = _config.get('agent_templates', {})
TOPIC_ORGS = _config.get('topic_orgs', {"default": ["ceo", "cmo", "cto"]})

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

# ─── Task Status (i18n) ───
TASK_STATUS_I18N = {
    'waiting':    {'ko': '대기',   'en': 'Waiting',     'ja': '待機',   'zh': '等待'},
    'in_progress':{'ko': '진행중', 'en': 'In Progress',  'ja': '進行中', 'zh': '进行中'},
    'done':       {'ko': '완료',   'en': 'Done',         'ja': '完了',   'zh': '完成'},
    'review':     {'ko': '검토',   'en': 'Review',       'ja': 'レビュー','zh': '审核'},
}
def _ts(key, lang='ko'):
    """Get localized task status name."""
    return TASK_STATUS_I18N.get(key, {}).get(lang, TASK_STATUS_I18N.get(key, {}).get('en', key))

def _ts_reverse(text, lang='ko'):
    """Reverse lookup: localized status name → internal key."""
    for key, names in TASK_STATUS_I18N.items():
        if names.get(lang, '') == text or names.get('en', '') == text:
            return key
    return text

TASK_STATUSES = [_ts('waiting'), _ts('in_progress'), _ts('done'), _ts('review')]
VALID_TASK_TRANSITIONS = {
    _ts('waiting'): [_ts('in_progress')],
    _ts('in_progress'): [_ts('done'), _ts('waiting')],
    _ts('done'): [_ts('review'), _ts('in_progress')],
    _ts('review'): [_ts('done'), _ts('in_progress')],
}

# ─── Pattern Detection (i18n) ───
DETECT_PATTERNS = {
    'ko': {
        'start': r'(?:작성|수립|구축|개발|제작|준비|기획|설계|구현|검토|분석|실행|진행|도입|설정)하?겠습니다',
        'done': r'(?:완료|완성|마무리|제출|완료했습니다|완성했습니다)\s*(?:하였습니다|했습니다)?',
        'plan_done': r'(?:완료|완성|마무리|제출|처리 완료|작성 완료|검토 완료|설정 완료)(?:했|했습|하였|됨|되었)',
        'plan_progress': r'(?:시작|진행|작업 중|분석 중|준비 중|검토 중|작성 중|설계 중)',
        'task_extract': r'(?:를|을|해|부탁|작성|수립|준비|보고|제출|검토|확인|수정|업데이트)',
        'approval_keywords': ['승인','결재','기안','요청','허가','구매','예산','채용','계약'],
        'skip_plan': ['완료', '확인', '정상', '없음', '있음'],
    },
    'en': {
        'start': r'(?:will|going to|starting|implementing|building|creating|designing|analyzing)',
        'done': r'(?:completed|finished|done|delivered|submitted)',
        'plan_done': r'(?:completed|finished|done|delivered|submitted|resolved)',
        'plan_progress': r'(?:starting|working on|analyzing|preparing|reviewing|building)',
        'task_extract': r'(?:please|create|build|prepare|submit|review|update|analyze)',
        'approval_keywords': ['approval','budget','purchase','hire','contract','authorize'],
        'skip_plan': ['completed', 'confirmed', 'normal', 'none', 'exists'],
    },
    'ja': {
        'start': r'(?:作成|構築|開発|制作|準備|企画|設計|実装|検討|分析|実行|導入|設定)します',
        'done': r'(?:完了|完成|終了|提出|納品)',
        'plan_done': r'(?:完了|完成|終了|提出)(?:しました|した|済)',
        'plan_progress': r'(?:開始|進行|作業中|分析中|準備中|検討中)',
        'task_extract': r'(?:を|で|作成|準備|報告|提出|検討|確認|修正)',
        'approval_keywords': ['承認','決裁','起案','要請','許可','購入','予算','採用','契約'],
        'skip_plan': ['完了', '確認', '正常', 'なし', 'あり'],
    },
    'zh': {
        'start': r'(?:编写|构建|开发|制作|准备|策划|设计|实现|审查|分析|执行|引入|设置)',
        'done': r'(?:完成|完毕|结束|提交|交付)',
        'plan_done': r'(?:完成|完毕|结束|提交|交付)(?:了|完)',
        'plan_progress': r'(?:开始|进行|工作中|分析中|准备中|审查中)',
        'task_extract': r'(?:请|创建|构建|准备|提交|审查|更新|分析)',
        'approval_keywords': ['审批','预算','采购','招聘','合同','授权'],
        'skip_plan': ['完成', '确认', '正常', '没有', '存在'],
    },
}

def _get_patterns(lang='ko'):
    return DETECT_PATTERNS.get(lang, DETECT_PATTERNS['en'])

# ─── Cost / Budget ───
DEFAULT_BUDGET = float(os.environ.get('DEFAULT_BUDGET', 10.0))
COST_PER_1K_TOKENS = float(os.environ.get('COST_PER_1K_TOKENS', 0.003))

# ─── Leader/Agent Helpers ───
def get_leader(company):
    """Get the leader agent (first agent, typically CEO). No hardcoded role."""
    agents = company.get('agents', [])
    return agents[0] if agents else None

def get_leader_id(company):
    """Get leader agent ID."""
    leader = get_leader(company)
    return leader['id'] if leader else 'ceo'

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

# ─── Inter-agent Communication Permissions ───
# Modes: "all" = any agent can talk to any, "ceo_only" = only CEO can delegate, "custom" = explicit allow-list
DEFAULT_COMM_PERMISSIONS = {
    "mode": "all",          # "all" | "ceo_only" | "custom"
    "custom_rules": {}      # {"ceo": ["cmo","cto"], "cmo": ["ceo","designer"], ...}
}

def can_communicate(company, from_id, to_id):
    """Check if from_agent is allowed to directly message to_agent."""
    perms = company.get('comm_permissions', DEFAULT_COMM_PERMISSIONS)
    mode = perms.get('mode', 'all')
    if mode == 'all':
        return True
    if mode == 'ceo_only':
        leader_id = get_leader_id(company)
        return from_id.lower() == leader_id or to_id.lower() == leader_id
    if mode == 'custom':
        rules = perms.get('custom_rules', {})
        allowed = rules.get(from_id.lower(), [])
        return to_id.lower() in [a.lower() for a in allowed]
    return True

def extract_task_from_instruction(text, lang="ko"):
    """Extract task name from instruction text."""
    text = text.strip().strip('"').strip()
    # 대시/불필요한 접두사 제거
    # Remove prefixes
    if not text: return None
    # Short instruction → use as-is
    if len(text) <= 30 and len(text) >= 2:
        return text
    # Command pattern extraction
    patterns = _get_patterns(lang)
    m = re.match(r'(.{2,25}?)' + patterns['task_extract'], text)
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
    """에이전트 응답에서 [TASK_XXX:...] 명령을 파싱해서 칸반에 반영.
    Pure parsing is delegated to parsers.commands; this function only does DB I/O."""
    results = []
    board_tasks = db_get_tasks(cid)

    # ─ TASK_ADD ─
    for spec in _p_task_add(text):
        title, priority = spec['title'], spec['priority']
        if any(t.get('title','') == title for t in board_tasks):
            results.append(f"⚠️ '{title}' 이미 존재")
            continue
        task = add_board_task(cid, title, agent_id, '대기', [], '')
        if task:
            db_update_task(cid, task['id'], {'title': f"{title} ({priority})"})
            results.append(f"✅ '{title}' 칸반에 추가됨 ({priority})")

    # ─ TASK_DONE ─
    for title in _p_task_done(text):
        for t in board_tasks:
            if t.get('title','') == title and t.get('status') != '완료':
                db_update_task(cid, t['id'], {'status': '완료', 'updated_at': datetime.now().isoformat()})
                results.append(f"🎉 '{title}' 완료 처리")
                break

    # ─ TASK_START ─
    for title in _p_task_start(text):
        for t in board_tasks:
            if t.get('title','') == title and t.get('status') == '대기':
                db_update_task(cid, t['id'], {'status': '진행중', 'updated_at': datetime.now().isoformat()})
                results.append(f"🚀 '{title}' 시작")
                break

    # ─ TASK_BLOCK ─
    for spec in _p_task_block(text):
        title, reason = spec['title'], spec['reason']
        for t in board_tasks:
            if t.get('title','') == title:
                db_update_task(cid, t['id'], {'status': '검토', 'updated_at': datetime.now().isoformat()})
                results.append(f"🚫 '{title}' 검토 필요 ({reason})")
                break

    if results:
        print(f"[task_cmds] {agent_id}: {'; '.join(results)}")

    # ─ CRON ─
    company = get_company(cid)
    agent = next((a for a in company.get('agents', []) if a['id'] == agent_id), None)

    for spec in _p_cron_add(text):
        title, interval, prompt_text = spec['title'], spec['interval'], spec['prompt']
        if agent:
            task = add_recurring_task(cid, title, prompt_text, interval, agent['id'], agent['name'], agent['emoji'])
            if task:
                results.append(f"⏰ '{title}' 정기 작업 추가됨 ({interval}분마다)")

    for title in _p_cron_del(text):
        company = get_company(cid)
        if company:
            company['recurring_tasks'] = [t for t in company.get('recurring_tasks', []) if t.get('title','') != title]
            update_company(cid, {'recurring_tasks': company['recurring_tasks']})
            results.append(f"🗑️ '{title}' 정기 작업 삭제됨")

    if any('CRON' in r for r in results):
        print(f"[cron_cmds] {agent_id}: {'; '.join(results)}")

    # ─ APPROVAL ─
    approval_specs = _p_approval(text)
    existing_approvals = db_get_approvals(cid) if approval_specs else []
    for spec in approval_specs:
        cat, title, detail = spec['category'], spec['title'], spec['detail']
        # Dedup: skip if a pending approval with same title already exists
        if any(a.get('title','') == title and a.get('status') == 'pending' for a in existing_approvals):
            print(f"[approval] SKIP duplicate: {cat}/{title} (already pending)")
            continue
        company = get_company(cid)
        agent = next((a for a in company.get('agents',[]) if a['id']==agent_id), None) if company else None
        agent_name = agent.get('name','') if agent else agent_id
        agent_emoji = agent.get('emoji','🤖') if agent else '🤖'
        # Build approval line: agent → leader → master
        leader_id = get_leader_id(company) if company else 'ceo'
        approval_line = [agent_id]
        if agent_id != leader_id:
            approval_line.append(leader_id)
        approval_line.append('master')
        approval = {
            'id': str(uuid.uuid4())[:8], 'from_agent': agent_name, 'from_emoji': agent_emoji,
            'approval_type': '기안', 'category': cat, 'title': title, 'detail': detail,
            'approval_line': json.dumps(approval_line), 'current_step': 0,
            'status': 'pending', 'time': datetime.now().strftime('%H:%M'),
            'created_at': datetime.now().isoformat(), 'updated_at': datetime.now().isoformat(),
            'comments': '[]'
        }
        append_approval(cid, approval)
        existing_approvals.append(approval)  # prevent dups within same batch
        results.append(f"📋 기안 '{title}' 제출됨 (결재라인: {' → '.join(approval_line)})")
        print(f"[approval] {agent_id} submitted: {cat}/{title}")
    
    # 자동 작업 감지: [TASK_XXX] 명령이 없을 때 패턴 기반 자동 추가
    has_task_cmd = bool(re.search(r'\[TASK_', text))
    if not has_task_cmd:
        company = get_company(cid)
        if not company: return results
        board_tasks = db_get_tasks(cid)
        agent = next((a for a in company.get('agents', []) if a['id'] == agent_id), None)
        
        patterns = _get_patterns(company.get('lang','ko') if company else 'ko')
        start_patterns = re.findall(patterns['start'] + r'[:\s]*([^\n.]{2,30})', text)
        for title in start_patterns:
            title = title.strip().rstrip('.,;')
            if len(title) < 2: continue
            if any(t.get('title','') == title for t in board_tasks): continue
            task = add_board_task(cid, title, agent_id, '진행중', [], '')
            if task:
                results.append(f"📋 '{title}' 자동 추가됨 (진행)")
        
        done_patterns = re.findall(patterns['done'] + r'[:\s]*([^\n.]{2,30})', text)
        for title in done_patterns:
            title = title.strip().rstrip('.,;')
            for t in board_tasks:
                if title in t.get('title','') and t.get('status') != '완료':
                    db_update_task(cid, t['id'], {'status': '완료', 'updated_at': datetime.now().isoformat()})
                    results.append(f"🎉 '{t['title']}' 완료 처리")
                    break
    
    # 모든 에이전트 응답에서 계획 트리 자동 업데이트
    _auto_update_plan(cid, agent_id, text)

    return results

def _auto_update_plan(cid, agent_id, text):
    """에이전트 응답을 분석하여 plan_tasks 트리를 자동 생성/업데이트.
    Claude Code의 task system처럼 동작:
    - CEO: 지시/계획 수립 → 하위 작업 생성
    - 팀원: 작업 진행/완료 보고 → 상태 업데이트
    - 모든 기록이 연혁으로 남음
    """
    existing = db_get_plan_tasks(cid)
    existing_titles = {t.get('title','').lower(): t for t in existing}

    # Ensure root node exists
    root_id = None
    for t in existing:
        if not t.get('parent_id'):
            root_id = t['id']
            break
    if not root_id:
        company = get_company(cid)
        topic = company.get('topic', '') if company else ''
        root = db_add_plan_task(cid, {
            'title': topic or '프로젝트 계획',
            'description': 'Auto-managed project plan',
            'status': 'in-progress',
            'agent_id': get_leader_id(company) if company else agent_id,
            'sort_order': 0
        })
        root_id = root['id']

    now_iso = datetime.now().isoformat()
    added = 0

    # 1. @멘션 지시 → 하위 작업 생성
    mentions = re.findall(r'@(\w+)\s+(.+?)(?=@\w+|\n\n|$)', text, re.DOTALL)
    for target, instruction in mentions:
        instruction = instruction.strip()
        if not instruction or len(instruction) < 3:
            continue
        title = instruction.split('\n')[0].strip()[:50]
        if title.lower() not in existing_titles:
            db_add_plan_task(cid, {
                'title': title,
                'description': f'[{agent_id.upper()}→@{target.upper()}] {instruction[:200]}',
                'status': 'in-progress',
                'agent_id': target.lower(),
                'parent_id': root_id,
                'sort_order': len(existing) + added
            })
            added += 1

    # 2. 완료 키워드 감지 → 기존 작업 done 처리
    plan_patterns = _get_patterns('ko')  # TODO: get from company lang
    done_patterns = re.findall(plan_patterns['plan_done'], text)
    if done_patterns:
        # Mark this agent's in-progress tasks as done
        for t in existing:
            if t.get('agent_id') == agent_id and t.get('status') in ('in-progress', 'todo'):
                db_update_plan_task(cid, t['id'], {'status': 'done'})

    # 3. 진행 키워드 감지 → todo를 in-progress로
    progress_patterns = re.findall(plan_patterns['plan_progress'], text)
    if progress_patterns and not done_patterns:
        for t in existing:
            if t.get('agent_id') == agent_id and t.get('status') == 'todo':
                db_update_plan_task(cid, t['id'], {'status': 'in-progress'})
                break  # Only first one

    # 4. Leader creates plan items from numbered lists
    company_check = get_company(cid)
    if company_check and agent_id == get_leader_id(company_check):
        # "1. xxx" or "- xxx" 패턴의 계획 항목 감지
        plan_items = re.findall(r'(?:^|\n)\s*(?:\d+[\.\)]\s*|[-•]\s+)(.{5,60}?)(?:\n|$)', text)
        for item in plan_items[:8]:  # Max 8 per response
            title = item.strip().rstrip('.,;:')
            if len(title) < 5 or title.lower() in existing_titles:
                continue
            # Skip if it looks like a status report rather than a plan
            if any(kw in title for kw in plan_patterns.get('skip_plan', [])):
                continue
            db_add_plan_task(cid, {
                'title': title,
                'description': f'[{agent_id.upper()} plan]',
                'status': 'todo',
                'agent_id': agent_id,
                'parent_id': root_id,
                'sort_order': len(existing) + added
            })
            added += 1

    # 5. Root 상태 업데이트: 모든 하위가 done이면 root도 done
    if existing:
        children = [t for t in db_get_plan_tasks(cid) if t.get('parent_id') == root_id]
        if children and all(t.get('status') == 'done' for t in children):
            db_update_plan_task(cid, root_id, {'status': 'done'})

    if added:
        print(f"[auto-plan] {cid}/{agent_id}: +{added} plan tasks")

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
    soul_file = agent_workspace / "SOUL.md"
    lang_name = LANG.get(lang, 'Korean')
    if not soul_file.exists() or soul_file.stat().st_size == 0:
        soul_content = (
            f"# SOUL.md\n\n"
            f"You are {name} ({role}) at '{company_name}'.\n"
            f"Respond in {lang_name}. Execute tasks immediately.\n\n"
            f"## Protocol\n"
            f"- Receive instruction → Execute → Report results\n"
            f"- Delegate to team via @mentions: @AgentName specific_task\n"
            f"- Report to master via @Master when done\n"
            f"- Need approval? Use [APPROVAL:category:title:detail]\n"
            f"- NO prep talk. Everything ASAP.\n"
            f"- 기한을 정하지 말고 바로 처리하세요. 일정/마감/타임라인 대신 즉시 실행.\n"
            f"  AI는 사람과 다릅니다. 계획이 아닌 결과를 내세요.\n"
            f"- NO_REPLY is forbidden.\n\n"
            f"## Context Sources\n"
            f"- Read _shared/newspaper.md for team status\n"
            f"- Check inbox/ for pending messages\n"
            f"- Update standup after work\n"
            f"- Save deliverables to _shared/deliverables/\n"
        )
        if cid:
            soul_content += (
                f"\n## Paths\n"
                f"- Shared: {shared_path}\n"
                f"- Deliverables: {deliverables_path}\n"
                f"- Whiteboard: {whiteboard_path}\n"
            )
        (agent_workspace / "SOUL.md").write_text(soul_content)
    if not (agent_workspace / "IDENTITY.md").exists() or (agent_workspace / "IDENTITY.md").stat().st_size == 0:
        (agent_workspace / "IDENTITY.md").write_text(
            f"- **Name:** {name}\n- **Role:** {role}\n- **Emoji:** {emoji}\n")
    if not (agent_workspace / "USER.md").exists() or (agent_workspace / "USER.md").stat().st_size == 0:
        (agent_workspace / "USER.md").write_text(
            f"# USER.md\n\n- **Name:** {_s('user.name', lang)}\n- **Role:** {_s('user.role', lang)}\n")
    if not (agent_workspace / "TOOLS.md").exists() or (agent_workspace / "TOOLS.md").stat().st_size == 0:
        (agent_workspace / "TOOLS.md").write_text(
            "# TOOLS.md — System Commands Reference\n\n"
            "## Communication\n"
            "- `@AgentName instruction` — Send task to a team member\n"
            "- `@Master report` — Report results to the master (human)\n"
            "- Multiple mentions in one response: `@CMO task1 @CTO task2`\n\n"
            "## Task Management (Kanban)\n"
            "- `[TASK_ADD:name:high/medium/low]` — Create new task\n"
            "- `[TASK_START:name]` — Mark task as in-progress\n"
            "- `[TASK_DONE:name]` — Mark task as completed\n"
            "- `[TASK_BLOCK:name:reason]` — Flag task as blocked\n\n"
            "## Scheduling\n"
            "- `[CRON_ADD:name:minutes:prompt]` — Create recurring task\n"
            "- `[CRON_DEL:name]` — Delete recurring task\n\n"
            "## Approval System\n"
            "- `[APPROVAL:category:title:detail]` — Submit to master for decision\n"
            "  Categories: budget, purchase, project, hr, policy, general\n\n"
            "### When to submit APPROVAL:\n"
            "- Master\'s judgment needed (strategy, priorities, direction)\n"
            "- Real-world action required (purchase, hire, contract, deploy)\n"
            "- Budget exceeds normal range\n"
            "- External credentials/API keys/access needed\n"
            "- Team conflict resolution\n"
            "- Legal/compliance matters\n"
            "Do NOT submit for routine work.\n\n"
            "## Behavior Rules\n"
            "- Execute tasks immediately. No prep talk.\n"
            "- No deadlines. Everything is ASAP.\n"
            "- Report results, not intentions.\n"
            "- NO_REPLY is forbidden.\n"
        )
    if not (agent_workspace / "HEARTBEAT.md").exists() or (agent_workspace / "HEARTBEAT.md").stat().st_size == 0:
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

    # 첫 메시지로 세션 활성화 (실패해도 첫 실제 요청 시 동작함)
    try:
        RUNTIME.run(agent_id, f"{agent_id}-init",
                    f'당신은 {name}({role})입니다. "확인"이라고만 답하세요.', timeout=30)
        print(f"[register] {agent_id} ({name}) activated")
    except Exception as e:
        print(f"[register] {agent_id} activate failed (will work on first request): {e}")
        # Clean up any lock files left by failed init
        sessions_dir = Path.home() / '.openclaw' / 'agents' / agent_id / 'sessions'
        if sessions_dir.exists():
            for lf in sessions_dir.glob('*.lock'):
                try: lf.unlink()
                except: pass

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

    # Auto-save meeting note after a delay (allow agents to respond)
    def _save_meeting_note():
        time.sleep(180)  # Wait 3 min for responses
        comp = get_company(cid)
        if not comp: return
        recent = (comp.get('chat', []) or [])[-20:]
        meeting_msgs = [m for m in recent if topic.lower() in (m.get('text','') or '').lower() or '회의' in (m.get('text','') or '')]
        summary = '\n'.join(f"[{m.get('from','')}] {(m.get('text',''))[:150]}" for m in meeting_msgs[-10:])
        # Extract action items (patterns like @agent, ~하겠습니다)
        actions = []
        for m in meeting_msgs:
            text = m.get('text', '')
            for match in re.findall(r'@(\w+)\s+(.{5,50})', text):
                actions.append({'agent': match[0], 'action': match[1].strip()})
        db_add_meeting(cid, {
            'topic': topic,
            'participants': [a['id'] for a in agents],
            'decisions': summary[:500],
            'action_items': actions[:10],
            'summary': f"주제: {topic}\n참석자: {len(agents)}명\n요약:\n{summary[:300]}"
        })
        print(f"[meeting] auto-saved meeting note for {cid}: {topic}")
    threading.Thread(target=_save_meeting_note, daemon=True).start()

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
                    # Sleep in 60s chunks to allow status checks
                    while wait_secs > 0 and key in _running_task_threads:
                        time.sleep(min(wait_secs, 60))
                        wait_secs -= 60
                    continue  # Re-check status after waiting
            except:
                time.sleep(60)
                continue
            # Time to execute — update next_run FIRST to prevent re-trigger
            company = get_company(cid)
            if not company:
                break
            tasks = company.get('recurring_tasks', [])
            t = next((x for x in tasks if x['id'] == task['id']), None)
            if not t or t['status'] != 'running':
                _running_task_threads.discard(key)
                break
            interval = max(t.get('interval_minutes', 1440), 1)
            new_next = (datetime.now() + __import__('datetime').timedelta(minutes=interval)).isoformat()
            for x in tasks:
                if x['id'] == t['id']:
                    x['next_run'] = new_next
                    break
            update_company(cid, {'recurring_tasks': tasks})
            try:
                result = execute_task(cid, t)
                company = get_company(cid)
                if company:
                    tasks = company.get('recurring_tasks', [])
                    for x in tasks:
                        if x['id'] == t['id']:
                            x['last_run'] = datetime.now().isoformat()
                            x['results'].append(result)
                            x['results'] = x['results'][-10:]
                            break
                    update_company(cid, {'recurring_tasks': tasks})
            except Exception as e:
                print(f"[WARN] task {task['id']} execution error: {e}")
        _running_task_threads.discard(key)
    threading.Thread(target=_run, daemon=True).start()

def execute_task(cid, task):
    """Execute a single recurring task run."""
    agent_id_full = f"{cid}-{task['agent_id']}"
    company = get_company(cid)
    leader = get_leader_id(company) if company else 'CEO'
    prompt = f"""Execute this recurring task immediately:

{task['prompt']}

Report results concisely to @{leader.upper()}. 기한 없이 바로 처리."""
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
    companies = db_get_all_companies()
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

from prompts.welcome import welcome_msg as _welcome_msg  # extracted to prompts/welcome.py

# ── Runtime-extended role translations (filled by /api/i18n/generate) ──
_ROLES_FILE = BASE / "dashboard" / "i18n" / "roles.json"

def _load_runtime_roles() -> dict:
    try:
        return json.loads(_ROLES_FILE.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}

def _save_runtime_roles(data: dict) -> None:
    try:
        _ROLES_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    except OSError as e:
        print(f"[roles] save failed: {e}")

def get_agent_role(aid: str, lang: str) -> str:
    """Return the localized role label for agent template `aid`.
    Priority: runtime roles.json[lang][aid] > config.json agent_templates[aid].role[lang] > ...en > ''"""
    rt = _load_runtime_roles()
    if lang in rt and aid in rt[lang] and rt[lang][aid]:
        return rt[lang][aid]
    t = AGENT_TEMPLATES.get(aid, {})
    role_map = t.get('role', {})
    if isinstance(role_map, dict):
        return role_map.get(lang) or role_map.get('en') or aid
    return str(role_map) or aid

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
        agent_role = get_agent_role(aid, lang)
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
                       lang=lang, wait=False, on_done=make_done_callback(company_id, aid, len(org), lang), company_id=company_id)
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
        "comm_permissions": {"mode": "all", "custom_rules": {}},
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
_AGENT_QUEUES: dict[str, deque] = {}  # {"cid:agent_id": deque of texts}
_AGENT_BUSY = set()
_AGENT_STATE_LOCK = threading.Lock()
_MENTION_COUNTS = {}  # {cid: {chain_key: {'count': int, 'ts': float}}}
_MENTION_LIMIT = 5    # max mentions per chain
_MENTION_TTL = 1800   # seconds
_MAX_CONCURRENT = MAX_CONCURRENT_AGENTS
_ACTIVE_SEMAPHORE = threading.Semaphore(_MAX_CONCURRENT)

_newspaper_cache = {}  # {cid: (brief, timestamp)}
_AB_RESULTS = {}  # {test_id: {status, agents: [{agent_id, response, elapsed}]}}
_AB_RESULTS_LOCK = threading.Lock()
_ESCALATION_COUNTS = {}  # {"cid:agent_id": count} — prevent infinite escalation loops


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



# ─── Event Narrative (Dwarf-Fortress style storytelling) ───

_narrative_cache = {}  # {cid: (events, timestamp)}

def generate_narrative(cid):
    """Generate template-based narrative events from company data.
    Returns list of {icon, text, type, time} dicts, max 15 items. Cached 30s."""
    import re as _re_narr

    cache_now = time.time()
    cached = _narrative_cache.get(cid)
    if cached and cache_now - cached[1] < 30:
        return cached[0]

    company = get_company(cid)
    if not company:
        return []

    events = []
    today_str = datetime.now().strftime('%Y-%m-%d')

    # 1. Parse recent chat messages for delegations and user orders
    chat = company.get('chat', [])
    recent_chat = chat[-20:] if len(chat) > 20 else chat
    for m in recent_chat:
        mtype = m.get('type', '')
        sender = m.get('from', '')
        text = m.get('text', '') or ''
        mtime = m.get('time', '')

        if mtype == 'user':
            mentions = _re_narr.findall(r'@(\w+)', text)
            if mentions:
                clean_text = _re_narr.sub(r'@\w+\s*', '', text).strip()
                summary = clean_text[:40] + ('...' if len(clean_text) > 40 else '')
                for target in mentions:
                    events.append({
                        'icon': '📋', 'type': 'delegation',
                        'text': f"CEO가 {target}에게 '{summary}'을(를) 지시했다",
                        'time': mtime
                    })
            else:
                summary = text[:40] + ('...' if len(text) > 40 else '')
                events.append({
                    'icon': '🗣️', 'type': 'action',
                    'text': f"CEO가 '{summary}'을(를) 지시했다",
                    'time': mtime
                })

        elif mtype == 'agent' and sender:
            agent_mentions = _re_narr.findall(r'@(\w+)', text)
            if agent_mentions:
                targets = ', '.join(agent_mentions[:2])
                clean = _re_narr.sub(r'@\w+', '', text).strip()
                summary = clean[:30] + ('...' if len(clean) > 30 else '')
                events.append({
                    'icon': '🔗', 'type': 'delegation',
                    'text': f"{sender}가 {targets}에게 '{summary}'을(를) 위임했다",
                    'time': mtime
                })

    # 2. Parse board tasks
    board_tasks = company.get('board_tasks', [])
    if not board_tasks:
        board_tasks = db_get_tasks(cid)
    for t in board_tasks:
        title = (t.get('title', '') or '')[:30]
        agent_id = t.get('agent_id', '')
        agent_name = agent_id
        for a in company.get('agents', []):
            if a.get('id') == agent_id:
                agent_name = a.get('name', agent_id)
                break
        status = t.get('status', '')
        updated = t.get('updated_at', '') or ''
        ttime = updated[11:16] if len(updated) > 15 else ''

        if status == '진행중':
            events.append({
                'icon': '🔨', 'type': 'action',
                'text': f"{agent_name}가 '{title}'에 착수했다",
                'time': ttime
            })
        elif status == '완료' and updated.startswith(today_str):
            events.append({
                'icon': '✅', 'type': 'done',
                'text': f"{agent_name}가 '{title}'을(를) 완료했다",
                'time': ttime
            })

    # 3. Approvals
    pending_approvals = [a for a in company.get('approvals', []) if a.get('status') == 'pending']
    if not pending_approvals:
        pending_approvals = db_get_approvals(cid, status='pending')
    for a in pending_approvals:
        title = a.get('detail', a.get('type', '기안'))[:40]
        from_agent = a.get('from_agent', '에이전트')
        events.append({
            'icon': '⏳', 'type': 'approval',
            'text': f"{from_agent}의 '{title}'이(가) 승인을 기다리고 있다",
            'time': a.get('time', '')
        })

    all_approvals = company.get('approvals', [])
    if not all_approvals:
        all_approvals = db_get_approvals(cid)
    for a in all_approvals:
        if a.get('status') == 'approved':
            title = a.get('detail', a.get('type', '기안'))[:40]
            events.append({
                'icon': '✅', 'type': 'milestone',
                'text': f"마스터가 '{title}'을(를) 승인했다",
                'time': a.get('time', '')
            })

    # 4. Activity log
    activity = company.get('activity_log', [])
    if not activity:
        activity = db_get_activity(cid)
    for entry in activity[-15:]:
        text = entry.get('text', '')
        etime = entry.get('time', '')
        if '합류' in text:
            events.append({'icon': '🆕', 'type': 'milestone', 'text': text, 'time': etime})
        elif '퇴사' in text:
            events.append({'icon': '👋', 'type': 'milestone', 'text': text, 'time': etime})
        elif '정기 작업' in text:
            events.append({'icon': '🔄', 'type': 'action', 'text': text, 'time': etime})

    # 5. Budget warning
    total_spent = sum(
        a.get('cost', {}).get('total_cost', 0.0)
        for a in company.get('agents', [])
    )
    budget = company.get('budget', DEFAULT_BUDGET)
    if budget > 0 and total_spent > budget * 0.8:
        pct = int(total_spent / budget * 100)
        events.append({
            'icon': '⚠️', 'type': 'warning',
            'text': f"예산 초과 경고: 현재 ${total_spent:.2f} / ${budget:.2f} ({pct}% 사용)",
            'time': datetime.now().strftime('%H:%M')
        })

    # 6. Working agents
    for a in company.get('agents', []):
        if a.get('status') == 'working':
            events.append({
                'icon': '💭', 'type': 'action',
                'text': f"{a.get('name', '?')}가 현재 작업 중이다",
                'time': datetime.now().strftime('%H:%M')
            })

    # Deduplicate by text prefix
    seen_texts = set()
    unique_events = []
    for ev in reversed(events):
        key = ev['text'][:30]
        if key not in seen_texts:
            seen_texts.add(key)
            unique_events.append(ev)
    unique_events.reverse()

    result = unique_events[-15:]
    result.reverse()  # 최신순
    _narrative_cache[cid] = (result, cache_now)
    return result


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
        nonlocal session_id, company
        print(f"[nudge] _process start: key={key} msg={msg[:50]}")
        # Re-check company exists (may have been deleted while queued)
        company = get_company(cid)
        if not company:
            print(f"[nudge] _process: {key} company deleted, aborting")
            return
        with _AGENT_STATE_LOCK:
            if key in _AGENT_BUSY:
                print(f"[nudge] _process: {key} already busy, returning")
                return
            _AGENT_BUSY.add(key)
        # Global concurrency limit
        print(f"[nudge] _process: {key} acquiring semaphore (current _AGENT_BUSY={_AGENT_BUSY})")
        acquired = _ACTIVE_SEMAPHORE.acquire(blocking=True, timeout=60)
        if not acquired:
            print(f"[nudge] {aid} dropped: concurrency limit ({_MAX_CONCURRENT})")
            _AGENT_BUSY.discard(key)
            return
        print(f"[nudge] _process: {key} semaphore acquired, building context...")
        standup = read_agent_standup(cid, aid)
        inbox = read_agent_inbox(cid, aid)
        ctx_parts = []
        if newspaper: ctx_parts.append(f"=== 브리프 ===\n{newspaper}")
        if inbox: ctx_parts.append(f"=== 받은 메시지 (inbox) ===\n{inbox}")
        if standup: ctx_parts.append(f"=== 내 스탠드업 ===\n{standup}")
        # 칸반보드 전체 작업 (누가 뭘 하는지 파악) — fetch fresh from DB
        all_tasks = db_get_tasks(cid)
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
        # Memory stream (Stanford GenAgents pattern)
        memories = db_get_memories(cid, aid, query=msg, limit=5)
        if memories:
            mem_text = '\n'.join(f"- [{m.get('mem_type','obs')}] {m['content']}" for m in memories)
            ctx_parts.append(f"=== Memory ===\n{mem_text}")
        # Agent work priorities
        try:
            prio_matrix = db_get_priorities(cid)
            agent_prios = prio_matrix.get(aid, {})
            if agent_prios:
                prio_lines = []
                for cat in PRIORITY_CATEGORIES:
                    p = agent_prios.get(cat, 3)
                    if p == 0:
                        prio_lines.append(f"{cat}: - (비활성)")
                    else:
                        stars = '★' * (6 - p)
                        label = {1: '최우선', 2: '높음', 3: '보통', 4: '낮음', 5: '최저'}.get(p, '보통')
                        prio_lines.append(f"{cat}: {stars} ({label})")
                ctx_parts.append("=== 작업 우선순위 ===\n" + '\n'.join(prio_lines))
        except Exception:
            pass
        ctx = '\n\n'.join(ctx_parts)
        lang_name = LANG.get(company.get('lang','ko'), '한국어') if company else '한국어'
        is_leader = (aid == get_leader_id(company)) if company else (aid == 'ceo')
        if is_leader:
            other_agents = [a['id'].upper() for a in (company.get('agents',[]) if company else []) if a['id'] != aid][:4]
            mention_list = ' '.join(f'@{a}' for a in other_agents) if other_agents else '@team'
            instruction = (
                f"\n\n[TASK] {msg}"
                f"\n\n[OUTPUT FORMAT — YOU MUST FOLLOW THIS STRUCTURE]"
                f"\nRespond in {lang_name} with ALL of these sections:"
                f"\n"
                f"\n## Actions Taken"
                f"\n(What you actually did — files created, analysis done, decisions made)"
                f"\n"
                f"\n## Delegations"
                f"\n(Use @mentions: {mention_list} with specific tasks)"
                f"\n"
                f"\n## Results"
                f"\n(Concrete deliverables, numbered list)"
                f"\n"
                f"\n## Next Steps"
                f"\n(What happens next, or [APPROVAL:...] if master decision needed)"
                f"\n"
                f"\n[RULES] 기한 없이 바로 처리. 계획이 아닌 결과를 내세요. NO_REPLY 금지."
                f"\n\n[SYSTEM COMMANDS — USE THESE IN YOUR RESPONSE]"
                f"\n- [TASK_ADD:작업명:high] — 칸반에 작업 추가"
                f"\n- [TASK_DONE:작업명] — 작업 완료 처리"
                f"\n- [APPROVAL:category:title:detail] — 마스터에게 결재 요청"
                f"\n  Example: [APPROVAL:purchase:서버 구매:AWS EC2 인스턴스 월 $50 필요]"
            )
        else:
            leader_name = get_leader_id(company).upper() if company else 'CEO'
            # Check if this agent has child agents (team lead detection)
            # For leader: treat all other agents as children (even if parent_agent is not set)
            all_agents = company.get('agents', []) if company else []
            child_agents = [a for a in all_agents if a.get('parent_agent') == aid]
            if not child_agents and is_leader:
                child_agents = [a for a in all_agents if a['id'] != aid]
            if child_agents:
                # Team lead: can delegate to children
                child_list = '\n'.join(f"- @{a['id'].upper()} ({a.get('role','')}) — 직접 @멘션으로 지시하세요" for a in child_agents)
                child_mentions = ' '.join(f'@{a["id"].upper()}' for a in child_agents)
                instruction = (
                    f"\n\n=== 당신의 팀원 ==="
                    f"\n{child_list}"
                    f"\n"
                    f"\n당신은 팀 리더입니다. 작업을 팀원에게 위임하고 결과를 취합하세요."
                    f"\n\n[TASK] {msg}"
                    f"\n\n[OUTPUT FORMAT — REQUIRED]"
                    f"\nRespond in {lang_name} with:"
                    f"\n"
                    f"\n## Actions Taken"
                    f"\n(What you actually did or decided)"
                    f"\n"
                    f"\n## Delegations"
                    f"\n(Use @mentions to delegate: {child_mentions} with specific tasks)"
                    f"\n"
                    f"\n## Results"
                    f"\n(Concrete output)"
                    f"\n"
                    f"\n## Report"
                    f"\n@{leader_name} (summary of results)"
                    f"\n"
                    f"\n[RULES] 기한 없이 바로 처리. 팀원에게 구체적으로 위임하세요. NO_REPLY 금지."
                    f"\n\n[COMMANDS]"
                    f"\n- [TASK_ADD:작업명:high] — 칸반에 작업 추가"
                    f"\n- [TASK_DONE:작업명] — 작업 완료"
                    f"\n- [APPROVAL:category:title:detail] — 결재 요청"
                )
            else:
                instruction = (
                    f"\n\n[TASK] {msg}"
                    f"\n\n[OUTPUT FORMAT — REQUIRED]"
                    f"\nRespond in {lang_name} with:"
                    f"\n"
                    f"\n## Actions Taken"
                    f"\n(What you actually did)"
                    f"\n"
                    f"\n## Results"
                    f"\n(Concrete output)"
                    f"\n"
                    f"\n## Report"
                    f"\n@{leader_name} (summary of results)"
                    f"\n"
                    f"\n[RULES] 기한 없이 바로 처리. 결과를 즉시 보고하세요. NO_REPLY 금지."
                    f"\n\n[COMMANDS]"
                    f"\n- [TASK_DONE:작업명] — 작업 완료"
                    f"\n- [APPROVAL:category:title:detail] — 결재 요청"
                )
        prompt = f"{ctx}{instruction}" if ctx else instruction.strip()

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

            print(f"[nudge] calling RUNTIME.run for {agent_id} session={session_id} prompt_len={len(prompt)}")
            try:
                reply_raw = RUNTIME.run(agent_id, session_id, prompt, timeout=AGENT_RUN_TIMEOUT)
                _dump_prompt(agent_id, prompt, reply_raw, kind='nudge')
            except subprocess.TimeoutExpired:
                print(f"[nudge] {agent_id} main timeout")
                reply_raw = ''
            elapsed = time.time() - nudge_start
            print(f"[nudge] {agent_id} reply={len(reply_raw)}chars time={elapsed:.1f}s raw={reply_raw[:100]}")

            # Retry once with fresh session if failed
            if not reply_raw or 'No reply from agent' in reply_raw:
                print(f"[nudge] {agent_id} no reply, retrying with fresh session...")
                # Clean locks before retry
                sessions_dir = Path.home() / '.openclaw' / 'agents' / agent_id / 'sessions'
                if sessions_dir.exists():
                    for lf in sessions_dir.glob('*.lock'):
                        try: lf.unlink()
                        except: pass
                time.sleep(2)
                new_session = f"{agent_id}-fresh-{int(time.time())}"
                try:
                    reply_raw = RUNTIME.run(agent_id, new_session, prompt, timeout=AGENT_RETRY_TIMEOUT)
                except subprocess.TimeoutExpired:
                    reply_raw = ''
                print(f"[nudge] {agent_id} retry reply={len(reply_raw)}chars")
                if reply_raw and 'No reply from agent' not in reply_raw:
                    session_id = new_session

            # All attempts failed — escalation chain (with loop prevention)
            if not reply_raw or 'No reply from agent' in reply_raw:
                esc_key = f"{cid}:{aid}"
                esc_count = _ESCALATION_COUNTS.get(esc_key, 0) + 1
                _ESCALATION_COUNTS[esc_key] = esc_count
                print(f"[nudge] {agent_id} ALL FAILED (escalation #{esc_count})")
                escalated = False
                comp = get_company(cid)
                leader_id = get_leader_id(comp) if comp else 'ceo'
                if esc_count <= 2:
                    if comp and aid != leader_id:
                        parent = next((a for a in comp.get('agents',[]) if a['id']==(agent.get('parent_agent') or leader_id)), None)
                        if parent and parent['id'] != aid:
                            esc_text = f"⚠️ {agent_name} 응답 실패. 원래 지시: {text[:80]}"
                            print(f"[escalation] {aid} → {parent['id']}")
                            append_chat(cid, {"from": "시스템", "emoji": "🔺", "text": f"🔺 {agent_name} → {parent['name']} 에스컬레이션", "time": datetime.now().strftime('%H:%M'), "type": "system"}, broadcast=True)
                            threading.Thread(target=nudge_agent, args=(cid, esc_text, parent['id'].upper()), daemon=True).start()
                            escalated = True
                    elif comp and aid == leader_id:
                        # Only create escalation approval if no duplicate pending
                        existing = db_get_approvals(cid)
                        has_esc = any(a.get('type')=='escalation' and a.get('status')=='pending' for a in existing)
                        if not has_esc:
                            append_approval(cid, {
                                'id': str(uuid.uuid4())[:8], 'from_agent': agent_name, 'from_emoji': emoji,
                                'type': 'escalation', 'title': f'{agent_name} 응답 실패',
                                'detail': f"에이전트가 작업을 처리하지 못했습니다.\n지시: {text[:200]}",
                                'status': 'pending', 'time': datetime.now().strftime('%H:%M'),
                                'created_at': datetime.now().isoformat()
                            })
                        escalated = True
                else:
                    print(f"[escalation] {esc_key} max reached ({esc_count}), stopping")
                if not escalated:
                    try:
                        agent_model = agent.get('model', 'default')
                        msg = f"⚠️ {emoji} {agent_name}이(가) 응답하지 않았습니다.\n💡 같은 메시지를 다시 보내보세요.\n💡 계속 실패하면 조직도에서 🧠 클릭으로 모델({agent_model})을 변경해보세요."
                        payload = json.dumps({"from": "시스템", "emoji": "⚠️", "text": msg}).encode()
                        _post_local(f'http://localhost:3000/api/agent-msg/{cid}', json.loads(payload))
                    except Exception as e:
                        print(f"[nudge] notify failed: {e}")
                reply_raw = ''

            if reply_raw and 'No reply from agent' not in reply_raw:
                _ESCALATION_COUNTS.pop(f"{cid}:{aid}", None)
                lines = reply_raw.split('\n')
                # Keep system command lines ([TASK_ADD:, [APPROVAL:, etc.) but strip metadata lines
                def _is_content_line(l):
                    stripped = l.strip()
                    if not stripped:
                        return False
                    if l.startswith('(agent'):
                        return False
                    if l.startswith('===') or l.startswith('---'):
                        return False
                    # Strip metadata lines starting with [ but NOT system commands
                    if l.startswith('[') and not re.match(r'\[(TASK_|APPROVAL:|CRON_|TASK_DONE)', l):
                        return False
                    return True
                clean = '\n'.join(l for l in lines if _is_content_line(l)).strip()
                # Guardrail: require action — commands or @mentions (no free passes for long text)
                prep_patterns = ['파악하겠', '확인하겠', '상황을 파악', '상황부터', '먼저 현재', 'check', 'assess', 'analyze first', 'let me',
                                 '검토하겠', '분석하겠', '살펴보겠', '조사하겠', '정리하겠', '계획을 세우', '방안을 마련']
                is_prep = len(clean) < GUARDRAIL_PREP_MAX_LEN and any(p in clean.lower() for p in prep_patterns)
                has_command = bool(re.search(r'\[TASK_|\[APPROVAL:|\[CRON_|\[TASK_DONE', clean))
                has_mention = bool(re.search(r'@[A-Za-z]', clean))
                # Leaders MUST delegate (@mention), not just add tasks
                if is_leader:
                    has_action = has_mention  # leader needs @mention regardless of commands
                else:
                    has_action = has_command or has_mention
                if clean and (is_prep or (not has_action)):
                    print(f"[guardrail] {agent_id} response rejected ({len(clean)}ch cmd={has_command} mention={has_mention}), retrying...")
                    if is_leader:
                        other_agents = [a['id'].upper() for a in (company.get('agents',[]) if company else []) if a['id'] != aid][:4]
                        agent_list = ', '.join(f'@{a}' for a in other_agents) if other_agents else '@CTO, @CMO'
                        enforce_prompt = (
                            f"⚠️ SYSTEM REJECTION: Your response had ZERO system commands.\n"
                            f"You MUST include commands. Copy-paste these formats exactly:\n\n"
                            f"To delegate: {agent_list} (describe the task)\n"
                            f"To add task: [TASK_ADD:작업명:high]\n"
                            f"To complete: [TASK_DONE:작업명]\n"
                            f"To request approval: [APPROVAL:category:title:detail]\n\n"
                            f"Re-do your response. Include at least 1 @mention AND 1 [TASK_ADD:...] command."
                        )
                    else:
                        leader = get_leader_id(company).upper() if company else 'CEO'
                        # Check if team lead with children — enforce delegation
                        guardrail_children = [a for a in (company.get('agents', []) if company else [])
                                             if a.get('parent_agent') == aid]
                        if guardrail_children:
                            child_list = ', '.join(f'@{a["id"].upper()}' for a in guardrail_children)
                            enforce_prompt = (
                                f"⚠️ SYSTEM REJECTION: Your response had ZERO system commands.\n"
                                f"You are a TEAM LEAD. You MUST delegate to your team:\n\n"
                                f"To delegate: {child_list} (describe specific task for each)\n"
                                f"To report: @{leader} (summary of results)\n"
                                f"To add task: [TASK_ADD:작업명:high]\n"
                                f"To complete task: [TASK_DONE:작업명]\n\n"
                                f"Re-do your response. Include @mentions to team members AND @{leader} report."
                            )
                        else:
                            enforce_prompt = (
                                f"⚠️ SYSTEM REJECTION: Your response had ZERO system commands.\n"
                                f"You MUST report results using these formats:\n\n"
                                f"To report: @{leader} (your results summary)\n"
                                f"To complete task: [TASK_DONE:작업명]\n"
                                f"To request approval: [APPROVAL:category:title:detail]\n\n"
                                f"Re-do your response. Include @{leader} mention AND [TASK_DONE:...] command."
                            )
                    try:
                        retry_raw = RUNTIME.run(agent_id, session_id, enforce_prompt, timeout=AGENT_RUN_TIMEOUT)
                        if retry_raw and len(retry_raw) > 80:
                            lines = retry_raw.split('\n')
                            retry_clean = '\n'.join(l for l in lines if _is_content_line(l)).strip()
                            # Check if retry actually has commands now
                            retry_has_cmd = bool(re.search(r'\[TASK_|\[APPROVAL:|\[CRON_', retry_clean))
                            retry_has_mention = bool(re.search(r'@[A-Za-z]', retry_clean))
                            if retry_has_cmd or retry_has_mention:
                                clean = retry_clean
                                print(f"[guardrail] {agent_id} enforced OK: {len(clean)}ch cmd={retry_has_cmd} mention={retry_has_mention}")
                            else:
                                # Second retry failed too — accept but log warning
                                clean = retry_clean
                                print(f"[guardrail] {agent_id} 2nd attempt still no commands ({len(clean)}ch), accepting anyway")
                        else:
                            clean = ''
                    except Exception as e:
                        print(f"[guardrail] {agent_id} retry error: {e}")
                        clean = ''
                # Save to memory stream
                if clean and len(clean) > 20:
                    importance = min(10, max(3, len(clean) // 50))
                    db_add_memory(cid, aid, clean[:300], importance=importance, mem_type='action')
                if clean:
                    save_agent_memory(cid, aid, clean)
                    process_task_commands(cid, clean, aid)
                    # Auto-detect approval intent even without [APPROVAL:] command
                    if '[APPROVAL:' not in clean:
                        apr_keywords = ['승인 요청', '승인이 필요', '결재 요청', '예산 승인', 'approval', 'budget approval', 'need approval']
                        if any(kw in clean.lower() for kw in apr_keywords) and len(clean) > 30:
                            # Build a meaningful title (first non-empty line, fallback to agent + summary)
                            first_line = next((l.strip() for l in clean.split('\n') if l.strip()), '')
                            # Strip markdown headers and lead chars
                            first_line = first_line.lstrip('#').strip()
                            title = (first_line[:50] if first_line else f"{agent_name} 자동 결재 요청")
                            # Fingerprint = first 100 chars of detail (collapses minor variations)
                            fp = ' '.join(clean.split())[:100]
                            existing = db_get_approvals(cid)
                            # Dedup by: same agent + same first 100 chars of detail
                            dup = any(
                                a.get('status') == 'pending'
                                and a.get('from_agent','') == agent_name
                                and ' '.join((a.get('detail','') or '').split())[:100] == fp
                                for a in existing
                            )
                            # Also: don't allow more than 3 pending auto-approvals from same agent
                            same_agent_pending = sum(
                                1 for a in existing
                                if a.get('status') == 'pending'
                                and a.get('from_agent','') == agent_name
                                and a.get('approval_type','') == 'auto'
                            )
                            if dup:
                                print(f"[auto-approval] SKIP duplicate from {agent_name}: {title[:40]}")
                            elif same_agent_pending >= 3:
                                print(f"[auto-approval] SKIP — {agent_name} already has {same_agent_pending} pending auto-approvals")
                            else:
                                append_approval(cid, {
                                    'id': str(uuid.uuid4())[:8], 'from_agent': agent_name, 'from_emoji': emoji,
                                    'approval_type': 'auto', 'title': title, 'detail': clean[:400],
                                    'status': 'pending', 'time': datetime.now().strftime('%H:%M'),
                                    'created_at': datetime.now().isoformat()
                                })
                                print(f"[auto-approval] {aid}: {title[:40]}")
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

                    # CEO delegation detection — skip if this was an escalation/approval response
                    is_escalation_response = '마스터의 결재 응답' in text or '에스컬레이션' in text
                    # Strip emoji from mentions before checking (agents may include emoji like @📈CMO)
                    clean_for_mention = re.sub(r'@[^\w\s]*(\w+)', r'@\1', clean)
                    # Dynamic mention detection: check against actual agent IDs (not just hardcoded roles)
                    existing_ids = {a['id'].lower() for a in company.get('agents', [])} if company else set()
                    mentioned_ids = {m.lower() for m in re.findall(r'@([A-Za-z0-9_-]+)', clean_for_mention)}
                    has_agent_mention = bool(mentioned_ids & (existing_ids - {aid}))
                    if not has_agent_mention:
                        # Fallback: legacy hardcoded check
                        has_agent_mention = bool(re.search(r'@(CMO|CTO|CFO|COO|HR|Designer|Sales|Legal|Support)', clean_for_mention, re.IGNORECASE))
                    if has_agent_mention:
                        mentions = ','.join(re.findall(r'@(\w+)', clean_for_mention, re.IGNORECASE))
                        chain = f"{aid}->{mentions}"
                        mention_count = _bump_mention_chain(cid, chain)
                        if mention_count > _MENTION_LIMIT:
                            print(f"[nudge] mention rate limit hit: {chain} ({mention_count})")
                            has_agent_mention = False

                    # ─── Manager Delegation Chain ───
                    # Auto-delegation: if a team lead responds WITHOUT @mentioning children,
                    # but has child agents and the task is delegatable, auto-nudge children.
                    if not is_escalation_response and company:
                        child_agents = [a for a in company.get('agents', [])
                                       if a.get('parent_agent') == aid]
                        if not child_agents and is_leader:
                            child_agents = [a for a in company.get('agents', []) if a['id'] != aid]
                        if child_agents and not has_agent_mention:
                            # Team lead responded without delegating — auto-nudge children
                            # Extract task context from the original message and response
                            delegation_context = f"[팀장 {agent_name} 지시] {text[:200]}"
                            if clean:
                                delegation_context += f"\n[팀장 분석] {clean[:300]}"
                            print(f"[auto-delegation] {aid} has {len(child_agents)} children, no mentions — auto-delegating")
                            append_chat(cid, {"from": "시스템", "emoji": "🔗",
                                "text": f"🔗 {agent_name}의 작업을 팀원에게 자동 위임합니다",
                                "time": datetime.now().strftime('%H:%M'), "type": "system"}, broadcast=True)
                            for child in child_agents[:4]:  # max 4 children to prevent overload
                                child_id = child['id'].upper()
                                child_task = f"{delegation_context}\n\n당신의 역할({child.get('role','')})에 맞는 부분을 처리하세요."
                                add_to_inbox(cid, child['id'], agent_name, child_task, company.get('lang','ko'))
                                task_title = extract_task_from_instruction(text) or text[:30]
                                add_board_task(cid, task_title, child['id'], '대기', [], '')
                                print(f"[auto-delegation] {aid} → {child['id']}: {child_task[:60]}")
                                threading.Thread(target=nudge_agent, args=(cid, child_task, child_id), daemon=True).start()
                            # Refresh board after auto-delegation
                            refreshed = get_company(cid)
                            if refreshed:
                                update_company(cid, {'board_tasks': refreshed.get('board_tasks', [])})

                        elif child_agents and has_agent_mention:
                            # Cascade nudge: team lead DID @mention children —
                            # enrich the forwarded context with WHY (original task from parent)
                            # Check delegation depth to prevent infinite loops (max 2 levels)
                            delegation_depth = int(re.search(r'\[delegation_depth:(\d+)\]', text).group(1)) if re.search(r'\[delegation_depth:(\d+)\]', text) else 0
                            if delegation_depth < 2:
                                # Add cascade context marker to forwarded messages
                                # The _handle_agent_msg will pick up @mentions and forward them;
                                # we inject parent context into the clean text so children see it
                                parent_context = f"\n\n[배경] 상위 지시: {text[:150]}"
                                depth_marker = f"\n[delegation_depth:{delegation_depth + 1}]"
                                # Modify clean to include context before it's posted via agent-msg
                                clean = clean + parent_context + depth_marker
                                print(f"[cascade] {aid} delegating with context (depth={delegation_depth})")
                            else:
                                print(f"[cascade] {aid} max delegation depth reached ({delegation_depth}), no further cascade")

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
                next_text = _AGENT_QUEUES[key].popleft()
                if not _AGENT_QUEUES[key]:
                    del _AGENT_QUEUES[key]
                threading.Thread(target=_process, args=(next_text,), daemon=True).start()

    print(f"[nudge] dispatch check: key={key} busy={key in _AGENT_BUSY} _AGENT_BUSY={_AGENT_BUSY}")
    if key in _AGENT_BUSY:
        if key not in _AGENT_QUEUES:
            _AGENT_QUEUES[key] = deque()
        if len(_AGENT_QUEUES[key]) >= AGENT_QUEUE_MAX:
            dropped = _AGENT_QUEUES[key].popleft()
            print(f"[nudge] {agent_id} queue full, dropped oldest: {dropped[:60]}")
            try:
                warn_msg = {"from": "시스템", "emoji": "⚠️", "text": f"{agent_name} 대기열 초과: 가장 오래된 요청이 삭제되었습니다", "time": datetime.now().strftime('%H:%M'), "type": "system"}
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
            self._handle_company_delete(path, body)

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
                with _AB_RESULTS_LOCK:
                    _AB_RESULTS[test_id]['agents'][idx]['response'] = clean or '(응답 없음)'
                    _AB_RESULTS[test_id]['agents'][idx]['elapsed'] = elapsed
                    _AB_RESULTS[test_id]['agents'][idx]['status'] = 'done'
                    _AB_RESULTS[test_id]['agents'][idx]['tokens'] = max(len(clean) // 4, 1)
            except Exception as e:
                with _AB_RESULTS_LOCK:
                    _AB_RESULTS[test_id]['agents'][idx]['response'] = f'오류: {e}'
                    _AB_RESULTS[test_id]['agents'][idx]['status'] = 'error'
            # Check if all done
            with _AB_RESULTS_LOCK:
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
            to_agent = agents[0]['id'] if agents else get_leader_id(to_company)
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
        ceo = get_leader(company) or (agents[0] if agents else None)
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

        # Normalize emoji-prefixed mentions: @📈CMO → @CMO, @📈 CMO → @CMO
        text = re.sub(r'@[^\w@]*(\w+)', r'@\1', text)

        # @마스터 멘션 감지 → 채팅에만 표시 (결재 자동 생성 제거 — 무한 루프 방지)
        master_mention = re.search(r'@\uB9C8\uC2A4\uD130 ?(.*)', text, re.DOTALL)
        master_request = ''
        if master_mention:
            master_request = master_mention.group(1).strip()
            text = re.sub(r'@\uB9C8\uC2A4\uD130 ?', '', text).strip()
            # @마스터 보고 → 채팅에만 표시 (결재 자동 생성 안함)
            chat_msg = {"from": from_agent, "emoji": emoji, "text": f"@마스터 {master_request}", "time": time_str, "type": "agent", "mention": True}
            append_chat(cid, chat_msg, broadcast=True)
            append_activity(cid, {"time": time_str, "agent": from_agent, "text": f"@마스터 {master_request[:50]}{'...' if len(master_request)>50 else ''}"})
            print(f"[master] {from_agent} → @마스터: {master_request[:60]}")
            # Detect choice pattern (A/B/C/D options) → create approval with options
            choice_pattern = re.findall(r'(?:^|\n)\s*(?:옵션\s*)?([A-D])[.):]\s*(.+?)(?:\n|$)', master_request)
            if not choice_pattern:
                choice_pattern = re.findall(r'(?:^|\n)\s*(?:Option\s*)?([A-D])[.):]\s*(.+?)(?:\n|$)', master_request, re.IGNORECASE)
            if not choice_pattern:
                choice_pattern = re.findall(r'(?:^|\n)\s*(\d)[.):]\s*(.+?)(?:\n|$)', master_request)
            if len(choice_pattern) >= 2:
                title = master_request.split('\n')[0].strip()[:60]
                # Deduplicate: skip if same title already pending
                existing_approvals = db_get_approvals(cid)
                has_dup = any(a.get('title','') == title and a.get('status')=='pending' for a in existing_approvals)
                if has_dup:
                    print(f"[choice] duplicate skipped: {title[:40]}")
                else:
                    options = [{"key": k.strip(), "label": v.strip()[:80]} for k, v in choice_pattern]
                    append_approval(cid, {
                        'id': str(uuid.uuid4())[:8], 'from_agent': from_agent, 'from_emoji': emoji,
                        'approval_type': 'choice', 'category': 'decision', 'title': title,
                        'detail': master_request[:500],
                        'options': json.dumps(options, ensure_ascii=False),
                        'status': 'pending', 'time': time_str,
                        'created_at': datetime.now().isoformat()
                    })
                    print(f"[choice] {from_agent}: {title[:40]} ({len(options)} options)")
        
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

        # Agent-to-agent mentions: nudge mentioned agents (with permission check)
        from_id = next((a['id'] for a in company.get('agents', []) if a.get('name','').upper() == from_agent.upper() or a['id'].upper() == from_agent.upper()), from_agent.lower())
        if mention_text:
            existing_ids = {a['id'].lower() for a in company.get('agents', [])}
            for ml in mention_text.split('\n'):
                ml = ml.strip()
                if not ml: continue
                for m in re.findall(r'@([A-Za-z0-9]+)', ml):
                    if m.lower() in existing_ids and m.upper() != from_agent.upper():
                        target = m.upper()
                        if not can_communicate(company, from_id, target.lower()):
                            print(f"[comm-blocked] {from_agent} → @{target}: permission denied (mode={company.get('comm_permissions',{}).get('mode','all')})")
                            append_chat(cid, {"from": "시스템", "emoji": "🚫", "text": f"{from_agent}→@{target} 직접 통신이 권한 설정에 의해 차단되었습니다.", "time": datetime.now().strftime('%H:%M'), "type": "system"}, broadcast=True)
                            continue
                        instruction = re.sub(r'@[A-Za-z0-9]+\s*', '', ml).strip()
                        if not instruction:
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

        # Chain mentions (에이전트→에이전트) with permission check
        existing_ids = {a['id'].lower() for a in company.get('agents', [])}
        block_re = re.compile(r'@(\w+)\s*```([\s\S]*?)```', re.MULTILINE)
        line_re = re.compile(r'@(\w+)\s+(.+)')
        seen = set()
        pending_mentions = []
        for bm in block_re.finditer(mention_text or text):
            m_name = bm.group(1)
            instruction = bm.group(2).strip()
            upper = m_name.upper()
            if upper != from_agent.upper() and m_name.lower() in existing_ids and upper not in seen:
                if not can_communicate(company, from_id, m_name.lower()):
                    print(f"[comm-blocked] {from_agent} → @{upper}: chain mention blocked")
                    continue
                seen.add(upper)
                pending_mentions.append((upper, instruction))
        for line in (mention_text or text).split('\n'):
            lm = line_re.match(line.strip())
            if lm:
                m_name = lm.group(1)
                instruction = lm.group(2).strip()
                upper = m_name.upper()
                if upper != from_agent.upper() and m_name.lower() in existing_ids and upper not in seen:
                    if not can_communicate(company, from_id, m_name.lower()):
                        print(f"[comm-blocked] {from_agent} → @{upper}: chain mention blocked")
                        continue
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
        agent_ids = [a.get('agent_id', '') for a in company.get('agents', []) if a.get('agent_id')]
        # Clear busy state immediately
        with _AGENT_STATE_LOCK:
            for k in [k for k in _AGENT_BUSY if k.startswith(f"{cid}:")]:
                _AGENT_BUSY.discard(k)
        for k in [k for k in _ESCALATION_COUNTS if k.startswith(f"{cid}:")]:
            _ESCALATION_COUNTS.pop(k, None)
        # Delete DB and files SYNCHRONOUSLY (so UI sees it gone immediately)
        db_delete_company(cid)
        for suffix in ['.json', '.json.bak', '-queue.json', '-queue.json.bak']:
            f = DATA / f"{cid}{suffix}"
            if f.exists():
                try: f.unlink()
                except OSError: pass
        try:
            companies = load_json(COMPANIES_FILE)
            companies = [c for c in companies if c["id"] != cid]
            save_json(COMPANIES_FILE, companies)
        except Exception: pass
        # Broadcast + respond
        sse_broadcast('company_update', {"id": cid, "deleted": True})
        self._json({"ok": True})
        # Agent process cleanup in background (slow, best-effort)
        def _bg_delete_agents():
            import signal
            for aid in agent_ids:
                try:
                    result = subprocess.run(['pgrep', '-f', aid], capture_output=True, text=True, timeout=5)
                    for pid in result.stdout.strip().split('\n'):
                        if pid.strip():
                            try: os.kill(int(pid.strip()), signal.SIGTERM)
                            except: pass
                except: pass
            failed = []
            for aid in agent_ids:
                try:
                    ok = RUNTIME.delete(aid)
                    print(f"[delete] agent {aid} {'removed' if ok else 'failed'}")
                    if not ok: failed.append(aid)
                except Exception as e:
                    failed.append(aid)
                    print(f"[delete] agent {aid} error: {e}")
            for attempt in range(2):
                if not failed: break
                time.sleep(5)
                still_failed = []
                for aid in failed:
                    try:
                        if RUNTIME.delete(aid): print(f"[delete] agent {aid} removed (retry {attempt+1})")
                        else: still_failed.append(aid)
                    except: still_failed.append(aid)
                failed = still_failed
            if failed: print(f"[delete] orphan agents: {failed}")
            else: print(f"[delete] company {cid} fully cleaned")
        threading.Thread(target=_bg_delete_agents, daemon=True).start()

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

        parent_agent = body.get('parent_agent', '')
        model = body.get('model', '')
        self._do_add_agent(cid, company, name, role, emoji, prompt, parent_agent, model)

    def _do_add_agent(self, cid, company, name, role, emoji, prompt, parent_agent='', model=''):
        aid = re.sub(r'[^a-z0-9]', '-', name.lower())
        agent_id = f"{cid}-{aid}"
        agent_workspace = DATA / cid / "workspaces" / aid
        register_agent(agent_id, agent_workspace, name, role, company.get('name',''), emoji, lang=company.get('lang','ko'), wait=True, company_id=cid)
        agent = {
            "id": aid, "agent_id": agent_id, "name": name, "emoji": emoji,
            "role": role, "status": "active",
            "tasks": [], "messages": [], "prompt": prompt,
            "cost": {"total_tokens": 0, "total_cost": 0.0, "last_run_cost": 0.0},
        }
        if parent_agent:
            agent['parent_agent'] = parent_agent
        if model:
            agent['model'] = model
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
        agent_id = body.get('agent_id', get_leader_id(company) if company else 'ceo')
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
        parts = path.rstrip('/').split('/')
        cid = parts[-2] if len(parts) >= 2 else parts[-1]
        task_id = body.get('task_id', '') or (parts[-1] if len(parts) >= 2 and parts[-1] != cid else '')
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
            response_text = body.get('response', '').strip()
            from_agent = approval.get('from_agent', '')
            is_escalation = approval.get('approval_type','') == 'escalation' or approval.get('type','') == 'escalation'
            status_emoji = '✅' if resolution == 'approved' else '❌'
            status_label = 'approved' if resolution == 'approved' else 'rejected'
            chat_text = f"{status_emoji} {status_label}: {approval.get('title', approval.get('detail',''))[:100]}"
            if response_text:
                chat_text += f"\n📝 {response_text[:200]}"
            append_chat(cid, {"from": "마스터", "emoji": "👤", "text": chat_text,
                              "time": datetime.now().strftime('%H:%M'), "type": "user"}, broadcast=True)
            print(f"[approval] {status_label}: {from_agent} — {response_text[:60] if response_text else '(no response)'}")
            # Notify agent to proceed (skip escalation to prevent loops)
            if resolution == 'approved' and from_agent and not is_escalation:
                company = get_company(cid)
                leader_id = get_leader_id(company) if company else 'ceo'
                detail = approval.get('detail', '')[:200]
                nudge_text = f"마스터가 승인했습니다. 즉시 실행하세요: {detail}"
                agent_target = from_agent.lower() if from_agent.lower() != 'master' else leader_id
                threading.Thread(target=nudge_agent, args=(cid, nudge_text, agent_target.upper()), daemon=True).start()

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

class _CallContext(Handler):
    """Fake 'self' that captures _json() calls from Handler methods, inheriting all helper methods."""
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

# ── Request ID middleware: tag every request with X-Request-Id for tracing ──
import uuid as _uuid
from starlette.middleware.base import BaseHTTPMiddleware
from config import REQUEST_ID_HEADER

class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        rid = request.headers.get(REQUEST_ID_HEADER) or _uuid.uuid4().hex[:12]
        request.state.request_id = rid
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = rid
        return response

app.add_middleware(RequestIdMiddleware)

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

@app.get("/api/narrative/{cid}")
def api_get_narrative(cid: str):
    return {"events": generate_narrative(cid)}

@app.get("/api/inbox/{cid}/{agent_id}")
def api_get_inbox(cid: str, agent_id: str):
    return {"inbox": read_agent_inbox(cid, agent_id)}

@app.get("/api/standup/{cid}/{agent_id}")
def api_get_standup(cid: str, agent_id: str):
    return {"standup": read_agent_standup(cid, agent_id)}

@app.get("/api/task-list/{cid}")
def api_get_task_list(cid: str):
    return get_recurring_tasks(cid)

@app.get("/api/file/{file_path:path}")
def api_get_file(file_path: str):
    import mimetypes
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
            mime, _ = mimetypes.guess_type(str(fp))
            if not mime:
                mime = 'application/octet-stream'
            # Binary files (images, etc.) use FileResponse
            if not mime.startswith('text/'):
                return FileResponse(str(fp), media_type=mime)
            # Text files
            return PlainTextResponse(fp.read_text(encoding='utf-8'), media_type=mime)
    except (OSError, ValueError) as e:
        print(f"[WARN] file read error: {e}")
    raise HTTPException(status_code=404, detail="file not found")

@app.post("/api/upload/{cid}")
async def api_upload_file(cid: str, request: Request):
    """Upload a file to company shared deliverables. Accepts multipart/form-data."""
    import shutil
    content_type = request.headers.get('content-type', '')
    if 'multipart/form-data' not in content_type:
        raise HTTPException(status_code=400, detail="multipart/form-data required")
    form = await request.form()
    uploaded = form.get('file')
    if not uploaded:
        raise HTTPException(status_code=400, detail="no file provided")
    dest_dir = DATA / cid / '_shared' / 'deliverables'
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Sanitize filename
    safe_name = uploaded.filename.replace('/', '_').replace('\\', '_').replace('..', '_')
    dest = dest_dir / safe_name
    with open(dest, 'wb') as f:
        content = await uploaded.read()
        f.write(content)
    rel_path = f"_shared/deliverables/{safe_name}"
    print(f"[upload] {cid}: {safe_name} ({len(content)} bytes)")
    return {"ok": True, "path": rel_path, "name": safe_name, "size": len(content)}

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
        for f in sorted(shared_dir.rglob('*'), key=lambda x: x.stat().st_mtime, reverse=True):
            if f.is_file() and not f.name.startswith('.'):
                size = f.stat().st_size
                mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime('%m-%d %H:%M')
                rel = str(f.relative_to(DATA / cid))
                files.append({'path': rel, 'size': size, 'modified': mtime})
    return files[:100]

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

@app.get("/api/comm-permissions/{cid}")
def api_get_comm_permissions(cid: str):
    company = get_company(cid)
    if not company:
        raise HTTPException(status_code=404, detail="not found")
    return company.get('comm_permissions', DEFAULT_COMM_PERMISSIONS)

@app.post("/api/comm-permissions/{cid}")
async def api_set_comm_permissions(cid: str, request: Request):
    body = await request.json()
    company = get_company(cid)
    if not company:
        raise HTTPException(status_code=404, detail="not found")
    mode = body.get('mode', 'all')
    if mode not in ('all', 'ceo_only', 'custom'):
        return JSONResponse({"error": "mode must be all, ceo_only, or custom"}, status_code=400)
    perms = {"mode": mode, "custom_rules": body.get('custom_rules', company.get('comm_permissions', {}).get('custom_rules', {}))}
    update_company(cid, {"comm_permissions": perms})
    return {"ok": True, "comm_permissions": perms}

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

# ─── Sprint API ───────────────────────────────────────────────────────────

@app.get("/api/sprints/{cid}")
def api_get_sprints(cid: str):
    return db_get_sprints(cid)

@app.post("/api/sprint-add/{cid}")
async def api_add_sprint(cid: str, request: Request):
    body = await request.json()
    sprint = db_add_sprint(cid, body)
    return {"ok": True, "sprint": sprint}

@app.post("/api/sprint-update/{cid}/{sprint_id}")
async def api_update_sprint(cid: str, sprint_id: str, request: Request):
    body = await request.json()
    db_update_sprint(cid, sprint_id, body)
    return {"ok": True}

@app.get("/api/sprint-tasks/{cid}/{sprint_id}")
def api_get_sprint_tasks(cid: str, sprint_id: str):
    return db_get_sprint_tasks(cid, sprint_id)

@app.post("/api/sprint-end/{cid}/{sprint_id}")
async def api_end_sprint(cid: str, sprint_id: str):
    """End sprint and auto-generate retrospective."""
    tasks = db_get_sprint_tasks(cid, sprint_id)
    done = [t for t in tasks if t.get('status') == '완료']
    pending = [t for t in tasks if t.get('status') != '완료']
    total = len(tasks)
    rate = round(len(done)/total*100) if total else 0
    retro = f"## 스프린트 회고\n- 완료율: {rate}% ({len(done)}/{total})\n"
    retro += f"- ✅ 완료: {', '.join(t.get('title','') for t in done[:10])}\n" if done else "- ✅ 완료: 없음\n"
    retro += f"- ⏳ 미완료: {', '.join(t.get('title','') for t in pending[:10])}\n" if pending else ""
    db_update_sprint(cid, sprint_id, {'status': 'done', 'retro': retro})
    return {"ok": True, "retro": retro, "completion_rate": rate}

# ─── Wiki / Knowledge Base API ────────────────────────────────────────────

@app.get("/api/wiki/{cid}")
def api_get_wiki(cid: str, category: str | None = None):
    return db_get_wiki_pages(cid, category)

@app.get("/api/wiki/{cid}/{page_id}")
def api_get_wiki_page(cid: str, page_id: str):
    page = db_get_wiki_page(cid, page_id)
    if not page:
        raise HTTPException(status_code=404, detail="not found")
    return page

@app.post("/api/wiki/{cid}")
async def api_save_wiki(cid: str, request: Request):
    body = await request.json()
    page = db_save_wiki_page(cid, body)
    return {"ok": True, "page": page}

@app.delete("/api/wiki/{cid}/{page_id}")
def api_delete_wiki(cid: str, page_id: str):
    db_delete_wiki_page(cid, page_id)
    return {"ok": True}

# ─── KPI Dashboard API ────────────────────────────────────────────────────

@app.get("/api/kpi/{cid}")
def api_get_kpi(cid: str):
    company = get_company(cid)
    if not company:
        raise HTTPException(status_code=404, detail="not found")
    agents = company.get('agents', [])
    tasks = db_get_tasks(cid)
    total_tasks = len(tasks)
    done_tasks = len([t for t in tasks if t.get('status') == '완료'])
    in_progress = len([t for t in tasks if t.get('status') == '진행중'])
    total_cost = sum(a.get('cost', {}).get('total_cost', 0) for a in agents)
    total_tokens = sum(a.get('cost', {}).get('total_tokens', 0) for a in agents)
    active_agents = len([a for a in agents if a.get('status') == 'active'])

    agent_kpis = []
    for a in agents:
        a_tasks = [t for t in tasks if t.get('agent_id') == a['id']]
        a_done = len([t for t in a_tasks if t.get('status') == '완료'])
        a_total = len(a_tasks)
        cost = a.get('cost', {})
        agent_kpis.append({
            'id': a['id'], 'name': a['name'], 'emoji': a.get('emoji', '🤖'),
            'status': a.get('status', ''),
            'task_total': a_total, 'task_done': a_done,
            'completion_rate': round(a_done/a_total*100) if a_total else 0,
            'total_cost': cost.get('total_cost', 0),
            'total_tokens': cost.get('total_tokens', 0),
        })

    sprints = db_get_sprints(cid)
    active_sprint = next((s for s in sprints if s.get('status') == 'active'), None)

    return {
        'company': {'name': company.get('name',''), 'topic': company.get('topic','')},
        'summary': {
            'total_tasks': total_tasks, 'done_tasks': done_tasks, 'in_progress': in_progress,
            'completion_rate': round(done_tasks/total_tasks*100) if total_tasks else 0,
            'total_cost': round(total_cost, 6), 'total_tokens': total_tokens,
            'active_agents': active_agents, 'total_agents': len(agents),
        },
        'agents': agent_kpis,
        'active_sprint': active_sprint,
    }

# ─── Auto Standup API ─────────────────────────────────────────────────────

@app.post("/api/standup-run/{cid}")
async def api_run_standup(cid: str):
    """Trigger standup for all agents and return aggregated report."""
    company = get_company(cid)
    if not company:
        raise HTTPException(status_code=404, detail="not found")
    agents = company.get('agents', [])
    results = {}

    def _agent_standup(a):
        aid = a['id']
        agent_id = a.get('agent_id', f"{cid}-{aid}")
        tasks = [t for t in db_get_tasks(cid) if t.get('agent_id') == aid]
        done = [t for t in tasks if t.get('status') == '완료']
        doing = [t for t in tasks if t.get('status') == '진행중']
        prompt = (
            f"당신은 {a['name']}입니다. 스탠드업 보고를 작성하세요.\n"
            f"완료: {', '.join(t.get('title','') for t in done[-3:]) or '없음'}\n"
            f"진행중: {', '.join(t.get('title','') for t in doing[:3]) or '없음'}\n"
            f"다음 형식으로만 답하세요:\n어제: [한 줄]\n오늘: [한 줄]\n블로커: [한 줄 또는 '없음']"
        )
        try:
            reply = RUNTIME.run(agent_id, f"{agent_id}-standup", prompt, timeout=30)
            clean = '\n'.join(l for l in reply.split('\n') if not l.startswith('[') and not l.startswith('(agent') and l.strip()).strip()
            results[aid] = clean or '(무응답)'
            db_save_doc(cid, 'standup', aid, clean)
        except Exception:
            results[aid] = '(타임아웃)'

    threads = [threading.Thread(target=_agent_standup, args=(a,), daemon=True) for a in agents if a.get('status') != 'registering']
    for t in threads: t.start()
    for t in threads: t.join(timeout=40)

    # Aggregate
    report = f"# 📋 데일리 스탠드업 ({datetime.now().strftime('%Y-%m-%d %H:%M')})\n\n"
    for a in agents:
        standup = results.get(a['id'], '(미참여)')
        report += f"## {a.get('emoji','')} {a['name']}\n{standup}\n\n"

    db_save_doc(cid, 'standup_report', '', report)
    return {"ok": True, "report": report, "agents": results}

# ─── Meeting Notes API ─────────────────────────────────────────────────────

@app.get("/api/meetings/{cid}")
def api_get_meetings(cid: str):
    return db_get_meetings(cid)

@app.post("/api/meeting-save/{cid}")
async def api_save_meeting(cid: str, request: Request):
    body = await request.json()
    meeting = db_add_meeting(cid, body)
    return {"ok": True, "meeting": meeting}

# ─── Milestones API ────────────────────────────────────────────────────────

@app.get("/api/milestones/{cid}")
def api_get_milestones(cid: str):
    return db_get_milestones(cid)

@app.post("/api/milestone-add/{cid}")
async def api_add_milestone(cid: str, request: Request):
    body = await request.json()
    ms = db_add_milestone(cid, body)
    return {"ok": True, "milestone": ms}

@app.post("/api/milestone-update/{cid}/{mid}")
async def api_update_milestone(cid: str, mid: str, request: Request):
    body = await request.json()
    db_update_milestone(cid, mid, body)
    return {"ok": True}

@app.delete("/api/milestone/{cid}/{mid}")
def api_delete_milestone(cid: str, mid: str):
    db_delete_milestone(cid, mid)
    return {"ok": True}

# ─── Risks API ─────────────────────────────────────────────────────────────

@app.get("/api/risks/{cid}")
def api_get_risks(cid: str):
    return db_get_risks(cid)

@app.post("/api/risk-add/{cid}")
async def api_add_risk(cid: str, request: Request):
    body = await request.json()
    risk = db_add_risk(cid, body)
    return {"ok": True, "risk": risk}

@app.post("/api/risk-update/{cid}/{rid}")
async def api_update_risk(cid: str, rid: str, request: Request):
    body = await request.json()
    db_update_risk(cid, rid, body)
    return {"ok": True}

@app.delete("/api/risk/{cid}/{rid}")
def api_delete_risk(cid: str, rid: str):
    db_delete_risk(cid, rid)
    return {"ok": True}

# ─── Performance Review API ───────────────────────────────────────────────

@app.get("/api/performance/{cid}")
def api_performance_review(cid: str):
    """Generate performance review for all agents."""
    company = get_company(cid)
    if not company:
        raise HTTPException(status_code=404, detail="not found")
    tasks = db_get_tasks(cid)
    agents = company.get('agents', [])
    reviews = []
    for a in agents:
        a_tasks = [t for t in tasks if t.get('agent_id') == a['id']]
        done = [t for t in a_tasks if t.get('status') == '완료']
        blocked = [t for t in a_tasks if t.get('status') == '검토']
        cost = a.get('cost', {})
        total = len(a_tasks)
        rate = round(len(done)/total*100) if total else 0
        # Score: completion rate weighted + cost efficiency
        score = min(100, rate + (10 if cost.get('total_cost',0) < 0.01 else 0) - len(blocked)*5)
        grade = '🌟 S' if score>=90 else '✅ A' if score>=75 else '📊 B' if score>=50 else '⚠️ C' if score>=25 else '❌ D'
        reviews.append({
            'id': a['id'], 'name': a['name'], 'emoji': a.get('emoji','🤖'),
            'role': a.get('role',''), 'status': a.get('status',''),
            'task_total': total, 'task_done': len(done), 'task_blocked': len(blocked),
            'completion_rate': rate, 'cost': cost.get('total_cost',0),
            'tokens': cost.get('total_tokens',0),
            'score': max(0,score), 'grade': grade,
        })
    reviews.sort(key=lambda r: r['score'], reverse=True)
    return {'reviews': reviews, 'generated_at': datetime.now().isoformat()}

# ─── Onboarding API ───────────────────────────────────────────────────────

@app.post("/api/onboard/{cid}/{agent_id}")
async def api_onboard_agent(cid: str, agent_id: str):
    """Run onboarding for a specific agent — teach them about the company context."""
    company = get_company(cid)
    if not company:
        raise HTTPException(status_code=404, detail="not found")
    agent = next((a for a in company.get('agents',[]) if a['id']==agent_id), None)
    if not agent:
        raise HTTPException(status_code=404, detail="agent not found")
    # Build onboarding context
    wiki_pages = db_get_wiki_pages(cid)
    wiki_summary = '\n'.join(f"- {p.get('title','')}: {(p.get('content',''))[:100]}" for p in wiki_pages[:5])
    milestones = db_get_milestones(cid)
    ms_summary = '\n'.join(f"- {m.get('title','')} (기한: {m.get('deadline','미정')})" for m in milestones[:5])
    risks = db_get_risks(cid)
    risk_summary = '\n'.join(f"- [{r.get('severity','')}] {r.get('title','')}" for r in risks[:5] if r.get('status')=='open')
    tasks = db_get_tasks(cid)
    my_tasks = [t for t in tasks if t.get('agent_id')==agent_id]

    onboard_prompt = (
        f"당신은 {agent['name']}({agent.get('role','')})으로서 '{company.get('name','')}' 회사에 합류했습니다.\n"
        f"회사 주제: {company.get('topic','')}\n\n"
        f"=== 지식 베이스 ===\n{wiki_summary or '(아직 없음)'}\n\n"
        f"=== 마일스톤 ===\n{ms_summary or '(아직 없음)'}\n\n"
        f"=== 리스크 ===\n{risk_summary or '(없음)'}\n\n"
        f"=== 당신의 할당 작업 ({len(my_tasks)}건) ===\n"
        + '\n'.join(f"- [{t.get('status','')}] {t.get('title','')}" for t in my_tasks[:10]) + '\n\n'
        "위 컨텍스트를 숙지하고, 당신의 역할과 앞으로의 계획을 간단히 보고하세요."
    )
    full_agent_id = agent.get('agent_id', f"{cid}-{agent_id}")
    def _run():
        try:
            reply = RUNTIME.run(full_agent_id, f"{full_agent_id}-onboard", onboard_prompt, timeout=60)
            clean = '\n'.join(l for l in reply.split('\n') if not l.startswith('[') and not l.startswith('(agent') and l.strip()).strip()
            if clean:
                db_save_doc(cid, 'onboard', agent_id, clean)
                append_chat(cid, {"from": agent['name'], "emoji": agent.get('emoji','🤖'),
                    "text": f"📚 온보딩 완료!\n{clean}", "time": datetime.now().strftime('%H:%M'), "type": "agent"}, broadcast=True)
                print(f"[onboard] {agent_id} completed")
        except Exception as e:
            print(f"[onboard] {agent_id} error: {e}")
    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "message": f"{agent['name']} 온보딩 시작됨"}

# ─── Announcements API ─────────────────────────────────────────────────────

@app.get("/api/announcements/{cid}")
def api_get_announcements(cid: str):
    return db_get_announcements(cid)

@app.post("/api/announcement-add/{cid}")
async def api_add_announcement(cid: str, request: Request):
    body = await request.json()
    ann = db_add_announcement(cid, body)
    db_add_audit(cid, 'announcement', body.get('author','master'), '', body.get('title',''))
    sse_broadcast('company_update', {"id": cid, "company": get_company(cid)})
    return {"ok": True, "announcement": ann}

@app.delete("/api/announcement/{cid}/{aid}")
def api_delete_announcement(cid: str, aid: str):
    db_delete_announcement(cid, aid)
    return {"ok": True}

# ─── Work Journals API ─────────────────────────────────────────────────────

@app.get("/api/journals/{cid}")
def api_get_journals(cid: str, date: str | None = None, agent_id: str | None = None):
    return db_get_journals(cid, date, agent_id)

@app.post("/api/journal-auto/{cid}")
async def api_auto_journals(cid: str):
    """Auto-generate work journals for all agents based on today's activity."""
    company = get_company(cid)
    if not company:
        raise HTTPException(status_code=404, detail="not found")
    today = datetime.now().strftime('%Y-%m-%d')
    tasks = db_get_tasks(cid)
    for a in company.get('agents', []):
        a_tasks = [t for t in tasks if t.get('agent_id') == a['id']]
        done = [t.get('title','') for t in a_tasks if t.get('status') == '완료']
        doing = [t.get('title','') for t in a_tasks if t.get('status') == '진행중']
        waiting = [t.get('title','') for t in a_tasks if t.get('status') == '대기']
        db_add_journal(cid, {
            'agent_id': a['id'], 'date': today,
            'tasks_done': ', '.join(done[-5:]) or '없음',
            'tasks_next': ', '.join(doing[:3] + waiting[:2]) or '없음',
            'issues': '', 'notes': ''
        })
    db_add_audit(cid, 'journal_auto', 'system', '', f'{today} 업무일지 자동 생성')
    return {"ok": True, "date": today}

# ─── Policies API ──────────────────────────────────────────────────────────

@app.get("/api/policies/{cid}")
def api_get_policies(cid: str):
    return db_get_policies(cid)

@app.post("/api/policy-add/{cid}")
async def api_add_policy(cid: str, request: Request):
    body = await request.json()
    p = db_add_policy(cid, body)
    db_add_audit(cid, 'policy_add', body.get('author','master'), '', body.get('title',''))
    return {"ok": True, "policy": p}

@app.delete("/api/policy/{cid}/{pid}")
def api_delete_policy(cid: str, pid: str):
    db_delete_policy(cid, pid)
    return {"ok": True}

# ─── Budgets API ───────────────────────────────────────────────────────────

@app.get("/api/budgets/{cid}")
def api_get_budgets(cid: str):
    return db_get_budgets(cid)

@app.post("/api/budget-set/{cid}")
async def api_set_budget(cid: str, request: Request):
    body = await request.json()
    b = db_set_budget(cid, body)
    db_add_audit(cid, 'budget_set', 'master', body.get('department',''), f'allocated={body.get("allocated",0)}')
    return {"ok": True, "budget": b}

# ─── Votes API ─────────────────────────────────────────────────────────────

@app.get("/api/votes/{cid}")
def api_get_votes(cid: str):
    return db_get_votes(cid)

@app.post("/api/vote-add/{cid}")
async def api_add_vote(cid: str, request: Request):
    body = await request.json()
    v = db_add_vote(cid, body)
    db_add_audit(cid, 'vote_create', body.get('author','master'), '', body.get('title',''))
    return {"ok": True, "vote": v}

@app.post("/api/vote-cast/{cid}/{vid}")
async def api_cast_vote(cid: str, vid: str, request: Request):
    body = await request.json()
    ok = db_cast_vote(cid, vid, body.get('voter',''), body.get('choice',''))
    return {"ok": ok}

# ─── Audit Log API ─────────────────────────────────────────────────────────

@app.get("/api/audit/{cid}")
def api_get_audit(cid: str):
    return db_get_audit(cid, limit=200)

# ─── Report Templates API ─────────────────────────────────────────────────

@app.post("/api/report-generate/{cid}/{report_type}")
async def api_generate_report(cid: str, report_type: str):
    """Generate weekly/monthly report from data."""
    company = get_company(cid)
    if not company:
        raise HTTPException(status_code=404, detail="not found")
    tasks = db_get_tasks(cid)
    done = [t for t in tasks if t.get('status') == '완료']
    doing = [t for t in tasks if t.get('status') == '진행중']
    sprints = db_get_sprints(cid)
    risks = db_get_risks(cid)
    open_risks = [r for r in risks if r.get('status') == 'open']
    milestones = db_get_milestones(cid)
    agents = company.get('agents', [])
    total_cost = sum(a.get('cost', {}).get('total_cost', 0) for a in agents)

    if report_type == 'weekly':
        report = f"# 📊 주간 보고서\n기간: {datetime.now().strftime('%Y-%m-%d')} 기준\n\n"
    else:
        report = f"# 📊 월간 보고서\n기간: {datetime.now().strftime('%Y-%m')} 기준\n\n"
    report += f"## 요약\n- 전체 작업: {len(tasks)}건 (완료 {len(done)}, 진행 {len(doing)})\n"
    report += f"- 완료율: {round(len(done)/len(tasks)*100) if tasks else 0}%\n"
    report += f"- 총 비용: ${total_cost:.4f}\n"
    report += f"- 리스크: {len(open_risks)}건 미해결\n\n"
    report += f"## 완료 작업\n" + '\n'.join(f"- ✅ {t.get('title','')}" for t in done[-10:]) + '\n\n'
    report += f"## 진행 중\n" + '\n'.join(f"- ⏳ {t.get('title','')} ({t.get('agent_id','')})" for t in doing[:10]) + '\n\n'
    if open_risks:
        report += f"## 리스크\n" + '\n'.join(f"- ⚠️ [{r.get('severity','')}] {r.get('title','')}" for r in open_risks[:5]) + '\n\n'
    report += f"## 팀원 성과\n"
    for a in agents:
        a_done = len([t for t in done if t.get('agent_id') == a['id']])
        report += f"- {a.get('emoji','')} {a['name']}: {a_done}건 완료, ${a.get('cost',{}).get('total_cost',0):.4f}\n"

    db_save_doc(cid, f'report_{report_type}', '', report)
    db_add_audit(cid, f'report_{report_type}', 'system', '', f'{report_type} report generated')
    return {"ok": True, "report": report}

# ─── CRM API ───────────────────────────────────────────────────────────────

@app.get("/api/crm/{cid}")
def api_get_contacts(cid: str, status: str | None = None):
    return db_get_contacts(cid, status)

@app.post("/api/crm-add/{cid}")
async def api_add_contact(cid: str, request: Request):
    body = await request.json()
    c = db_add_contact(cid, body)
    db_add_audit(cid, 'crm_add', body.get('owner','master'), body.get('name',''), body.get('status','lead'))
    return {"ok": True, "contact": c}

@app.post("/api/crm-update/{cid}/{contact_id}")
async def api_update_contact(cid: str, contact_id: str, request: Request):
    body = await request.json()
    body['id'] = contact_id
    db_add_contact(cid, body)  # INSERT OR REPLACE
    return {"ok": True}

@app.delete("/api/crm/{cid}/{contact_id}")
def api_delete_contact(cid: str, contact_id: str):
    db_delete_contact(cid, contact_id)
    return {"ok": True}

# ─── Agent Priorities API ──────────────────────────────────────────────────

@app.get("/api/priorities/{cid}")
def api_get_priorities(cid: str):
    c = get_company(cid)
    if c:
        agent_ids = [a['id'] for a in c.get('agents', [])]
        db_init_default_priorities(cid, agent_ids)
    matrix = db_get_priorities(cid)
    return {"categories": PRIORITY_CATEGORIES, "matrix": matrix}

@app.post("/api/priority-set/{cid}")
async def api_set_priority(cid: str, request: Request):
    body = await request.json()
    agent_id = body.get('agent_id', '')
    category = body.get('category', '')
    priority = body.get('priority', 3)
    if not agent_id or category not in PRIORITY_CATEGORIES:
        return {"ok": False, "error": "invalid agent_id or category"}
    result = db_set_priority(cid, agent_id, category, priority)
    return {"ok": True, "result": result}

# ─── Model Management API ──────────────────────────────────────────────────

@app.get("/api/models")
def api_get_models():
    """Return available models from openclaw config."""
    config_path = Path.home() / '.openclaw' / 'openclaw.json'
    models = []
    primary = ''
    try:
        cfg = json.loads(config_path.read_text())
        primary = cfg.get('agents', {}).get('defaults', {}).get('model', {}).get('primary', '')
        for provider, info in cfg.get('models', {}).get('providers', {}).items():
            for m in info.get('models', []):
                mid = f"{provider}/{m['id']}"
                models.append({'id': mid, 'name': m.get('name', m['id']), 'provider': provider,
                               'reasoning': m.get('reasoning', False),
                               'cost_input': m.get('cost', {}).get('input', 0),
                               'cost_output': m.get('cost', {}).get('output', 0)})
    except Exception:
        pass
    return {'models': models, 'default': primary}

@app.get("/api/agent-model/{cid}/{agent_id}")
def api_get_agent_model(cid: str, agent_id: str):
    """Get current model for an agent."""
    try:
        result = subprocess.run(['openclaw', 'agents', 'list'], capture_output=True, text=True, timeout=10)
        full_id = f"{cid}-{agent_id}"
        for line in result.stdout.split('\n'):
            if 'Model:' in line:
                model = line.split('Model:')[1].strip()
                return {'agent_id': agent_id, 'model': model}
            if full_id in line:
                # Next lines contain model info
                continue
    except Exception:
        pass
    return {'agent_id': agent_id, 'model': 'unknown'}

@app.post("/api/agent-model/{cid}/{agent_id}")
async def api_set_agent_model(cid: str, agent_id: str, request: Request):
    """Change model for an agent by re-registering."""
    body = await request.json()
    new_model = body.get('model', '')
    if not new_model:
        return JSONResponse({"error": "model required"}, status_code=400)
    # Update openclaw global config default
    config_path = Path.home() / '.openclaw' / 'openclaw.json'
    try:
        cfg = json.loads(config_path.read_text())
        # Set per-agent model override (store in company data)
        company = get_company(cid)
        if company:
            for a in company.get('agents', []):
                if a['id'] == agent_id:
                    a['model'] = new_model
                    break
            update_company(cid, {'agents': company['agents']})
        db_add_audit(cid, 'model_change', 'master', agent_id, f'model→{new_model}')
        return {"ok": True, "model": new_model}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ─── i18n API ──────────────────────────────────────────────────────────────

I18N_DIR = Path(__file__).parent / "i18n"
I18N_DIR.mkdir(exist_ok=True)

@app.get("/api/i18n/languages")
def api_i18n_languages():
    langs = [f.stem for f in I18N_DIR.glob("*.json")]
    return {"languages": sorted(langs), "setup_done": len(langs) > 0}

@app.get("/api/i18n/{lang}")
def api_i18n_get(lang: str):
    f = I18N_DIR / f"{lang}.json"
    if f.exists():
        return json.loads(f.read_text(encoding='utf-8'))
    raise HTTPException(status_code=404, detail=f"not found")

@app.post("/api/i18n/generate")
async def api_i18n_generate(request: Request):
    body = await request.json()
    lang_input = body.get('language', '').strip()
    if not lang_input:
        return JSONResponse({"error": "language required"}, status_code=400)
    en_file = I18N_DIR / "en.json"
    if not en_file.exists():
        return JSONResponse({"error": "en.json missing"}, status_code=500)
    en_strings = json.loads(en_file.read_text(encoding='utf-8'))

    # Collect en role map for all templates
    en_roles = {aid: (tpl.get('role', {}).get('en') if isinstance(tpl.get('role'), dict) else '') or aid
                for aid, tpl in AGENT_TEMPLATES.items()}

    # English welcome template (reference to translate from)
    welcome_en = {
        "greeting_tpl": "Hello Master! 👋\n\nI'm the CEO of '{name}'.\n\nTopic: {topic}\nTeam: {team}\n\nUse @mention to instruct team members. What should we start with?",
        "waiting": "⏳ Preparing agents, please wait...",
        "ready": "✅ All agents are ready! You can start the conversation.",
        "log_tpl": "🏢 '{name}' project started. Topic: {topic}"
    }

    bundle = {
        "ui": en_strings,
        "roles": en_roles,
        "welcome": welcome_en,
    }
    keys_json = json.dumps(bundle, indent=2, ensure_ascii=False)
    prompt = (
        f"Translate this JSON bundle to {lang_input}. "
        f"Return ONLY valid JSON with the same top-level keys (ui, roles, welcome) and same inner keys. "
        f"Keep emoji prefixes exactly. Keep placeholders like {{name}}, {{topic}}, {{team}}, {{count}} unchanged. "
        f"Be natural and concise. For 'roles', translate job titles (e.g. Executive, Marketing, Finance) naturally.\n\n"
        f"{keys_json}"
    )

    def _gen():
        try:
            result = RUNTIME.run('main', f'i18n-{lang_input[:10]}', prompt, timeout=75)
            m = re.search(r'\{[\s\S]*\}', result)
            if not m:
                return {'ok': False, 'error': 'no JSON in response'}
            translated = json.loads(m.group())

            # Resolve lang_code
            lang_map = {'korean':'ko','english':'en','japanese':'ja','chinese':'zh','spanish':'es','french':'fr','german':'de','portuguese':'pt','russian':'ru','arabic':'ar','hindi':'hi','thai':'th','vietnamese':'vi','indonesian':'id','turkish':'tr','italian':'it','dutch':'nl'}
            lang_map.update({chr(0xD55C)+chr(0xAD6D)+chr(0xC5B4):'ko'})
            lang_code = lang_input[:2].lower()
            for name, code in lang_map.items():
                if name in lang_input.lower():
                    lang_code = code
                    break

            # Extract pieces (tolerate missing sub-keys)
            ui_strings = translated.get('ui') if isinstance(translated.get('ui'), dict) else translated
            roles_map = translated.get('roles') if isinstance(translated.get('roles'), dict) else {}
            welcome_tpl = translated.get('welcome') if isinstance(translated.get('welcome'), dict) else {}

            # Save UI strings
            (I18N_DIR / f"{lang_code}.json").write_text(
                json.dumps(ui_strings, indent=2, ensure_ascii=False), encoding='utf-8'
            )

            # Merge roles into roles.json
            if roles_map:
                rt_roles = _load_runtime_roles()
                rt_roles.setdefault(lang_code, {})
                for aid, label in roles_map.items():
                    if aid in AGENT_TEMPLATES and isinstance(label, str) and label.strip():
                        rt_roles[lang_code][aid] = label.strip()
                _save_runtime_roles(rt_roles)

            # Merge welcome into welcome.json
            if welcome_tpl:
                welcome_file = I18N_DIR / "welcome.json"
                try:
                    all_welcome = json.loads(welcome_file.read_text(encoding='utf-8'))
                except (OSError, json.JSONDecodeError):
                    all_welcome = {}
                # Only keep valid keys
                clean_tpl = {
                    k: welcome_tpl[k]
                    for k in ('greeting_tpl', 'waiting', 'ready', 'log_tpl')
                    if isinstance(welcome_tpl.get(k), str) and welcome_tpl.get(k).strip()
                }
                if clean_tpl:
                    all_welcome[lang_code] = clean_tpl
                    welcome_file.write_text(
                        json.dumps(all_welcome, indent=2, ensure_ascii=False), encoding='utf-8'
                    )

            print(f"[i18n] Generated {lang_code}: ui={len(ui_strings)} roles={len(roles_map)} welcome={len(welcome_tpl)}")
            return {
                'ok': True,
                'lang_code': lang_code,
                'keys': len(ui_strings),
                'roles': len(roles_map),
                'welcome': bool(welcome_tpl),
            }
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor() as pool:
        try:
            result = pool.submit(_gen).result(timeout=120)
            return result
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/i18n/patch")
async def api_i18n_patch(request: Request):
    """Translate only missing keys and merge into existing language file."""
    body = await request.json()
    lang = body.get('lang', '')
    missing = body.get('missing', {})
    if not lang or not missing:
        return JSONResponse({"error": "lang and missing required"}, status_code=400)
    keys_json = json.dumps(missing, indent=2, ensure_ascii=False)
    lang_name_map = {'ko':'Korean','en':'English','ja':'Japanese','zh':'Chinese','es':'Spanish','fr':'French','de':'German','pt':'Portuguese','ru':'Russian','ar':'Arabic','hi':'Hindi','th':'Thai','vi':'Vietnamese','id':'Indonesian','tr':'Turkish','it':'Italian'}
    lang_name = lang_name_map.get(lang, lang)
    prompt = f"Translate to {lang_name}. Return ONLY JSON, same keys, keep emoji. \n\n{keys_json}"
    def _gen():
        try:
            result = RUNTIME.run('main', f'i18n-patch-{lang}', prompt, timeout=45)
            cleaned = result.strip()
            if cleaned.startswith('```'): cleaned = cleaned.split('\n',1)[-1].rsplit('```',1)[0].strip()
            try: translated = json.loads(cleaned)
            except: 
                m = re.search(r'\{[\s\S]*\}', result)
                if not m: return {'ok':False}
                translated = json.loads(m.group())
            # Merge into existing file
            f = I18N_DIR / f"{lang}.json"
            if f.exists():
                existing = json.loads(f.read_text(encoding='utf-8'))
                existing.update(translated)
                f.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding='utf-8')
            print(f"[i18n] Patched {lang}: +{len(translated)} keys")
            return {'ok':True,'translated':translated}
        except Exception as e:
            return {'ok':False,'error':str(e)}
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor() as pool:
        try: return pool.submit(_gen).result(timeout=60)
        except: return JSONResponse({"error":"timeout"}, status_code=500)

# ─── Deliverables Download API ─────────────────────────────────────────────

@app.get("/api/download/{cid}")
def api_download_deliverables(cid: str):
    """Download all deliverables as ZIP."""
    import zipfile, io
    company = get_company(cid)
    if not company:
        raise HTTPException(status_code=404, detail="not found")
    deliverables_dir = DATA / cid / "_shared" / "deliverables"
    if not deliverables_dir.exists():
        raise HTTPException(status_code=404, detail="no deliverables")
    # Create ZIP in memory
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in deliverables_dir.rglob('*'):
            if f.is_file():
                arcname = str(f.relative_to(deliverables_dir))
                zf.write(f, arcname)
    buf.seek(0)
    from fastapi.responses import Response
    return Response(
        content=buf.getvalue(),
        media_type='application/zip',
        headers={'Content-Disposition': f'attachment; filename="{cid}_deliverables.zip"'}
    )

@app.get("/api/download-all/{cid}")
def api_download_all(cid: str):
    """Download entire company workspace as ZIP (deliverables + workspaces + shared)."""
    import zipfile, io
    company = get_company(cid)
    if not company:
        raise HTTPException(status_code=404, detail="not found")
    company_dir = DATA / cid
    if not company_dir.exists():
        raise HTTPException(status_code=404, detail="no data")
    buf = io.BytesIO()
    skip = {'.db', '.db-journal', '.db-wal', '.lock'}
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in company_dir.rglob('*'):
            if f.is_file() and f.suffix not in skip:
                arcname = str(f.relative_to(company_dir))
                try:
                    zf.write(f, arcname)
                except Exception:
                    pass
    buf.seek(0)
    from fastapi.responses import Response
    return Response(
        content=buf.getvalue(),
        media_type='application/zip',
        headers={'Content-Disposition': f'attachment; filename="{cid}_workspace.zip"'}
    )

# ── Static files ──────────────────

@app.get("/")
def serve_index():
    return FileResponse(str(BASE / "dashboard" / "index.html"), media_type="text/html", headers={"Cache-Control":"no-cache,no-store,must-revalidate","Pragma":"no-cache","Expires":"0"})

app.mount("/", StaticFiles(directory=str(BASE / "dashboard"), html=True), name="static")

# ─── Server Setup ───

class ReusableTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

def ensure_agents_registered():
    """On startup, re-register all agents from companies data. Runs non-blocking."""
    companies = db_get_all_companies()
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
        # Warn about empty workspace files (don't auto-overwrite — may be user-customized)
        for ag in company.get('agents', []):
            ws = DATA / cid / "workspaces" / ag['id']
            if not ws.exists():
                continue
            for fname in ['SOUL.md', 'IDENTITY.md', 'TOOLS.md']:
                f = ws / fname
                if f.exists() and f.stat().st_size == 0:
                    print(f"[WARN] {ag['id']}/{fname} is empty (0 bytes) — agent may not function correctly")
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
                c = get_company(cid)
                if c:
                    for a in c.get('agents', []):
                        if a['id'] == agent['id']:
                            a['status'] = 'active'
                            break
                    update_company(cid, {'agents': c['agents']})
    # Warmup disabled — sessions activate on first real request to avoid lock conflicts

def _warmup_all_sessions(companies):
    """Activate sessions for all registered agents (not just CEO)."""
    def _warmup_agent(agent_id, name, role):
        try:
            sessions_dir = Path.home() / '.openclaw' / 'agents' / agent_id / 'sessions'
            # Check if any session file exists (skip if already warmed)
            if sessions_dir.exists():
                session_files = [f for f in sessions_dir.iterdir() if f.suffix == '.jsonl' and not f.name.endswith('.lock')]
                if session_files:
                    print(f"[warmup] {agent_id} session already exists ({len(session_files)} files), skipping")
                    return
            # Use a dedicated warmup session (not main) to avoid conflicts
            warmup_session = f"{agent_id}-warmup"
            print(f"[warmup] activating session for {agent_id} ({name})...")
            RUNTIME.run(agent_id, warmup_session,
                        f'당신은 {name}({role})입니다. 준비 완료라고만 답하세요.', timeout=30)
            print(f"[warmup] {agent_id} session activated")
        except Exception as e:
            print(f"[warmup] {agent_id} failed: {e}")

    threads = []
    for company in companies:
        for agent in company.get('agents', []):
            agent_id = agent.get('agent_id', '')
            if not agent_id or agent.get('status') == 'registering':
                continue
            t = threading.Thread(target=_warmup_agent,
                                 args=(agent_id, agent.get('name',''), agent.get('role','')),
                                 daemon=True)
            threads.append(t)
            t.start()
    # Don't block startup — threads run in background
    print(f"[warmup] {len(threads)} agent sessions warming up in background")

def _preflight_check():
    """Server startup self-healing: fix common issues automatically."""
    agents_dir = Path.home() / '.openclaw' / 'agents'
    openclaw_config = Path.home() / '.openclaw' / 'openclaw.json'
    fixes = []

    # 1. Clean stale lock files
    if agents_dir.exists():
        for lock_file in agents_dir.rglob('*.lock'):
            try:
                content = lock_file.read_text().strip()
                pid = None
                m = re.search(r'"pid"\s*:\s*(\d+)', content)
                if m:
                    pid = int(m.group(1))
                if pid:
                    try:
                        os.kill(pid, 0)
                        continue  # Process alive, skip
                    except OSError:
                        pass
                lock_file.unlink()
                fixes.append('lock')
            except Exception:
                try: lock_file.unlink(); fixes.append('lock')
                except: pass

    # 2. Check default model and allow override via env var
    env_model = os.environ.get('OPENCLAW_MODEL', '')
    if openclaw_config.exists():
        try:
            cfg = json.loads(openclaw_config.read_text())
            model = cfg.get('agents', {}).get('defaults', {}).get('model', {})
            primary = model.get('primary', '')
            if env_model:
                # User explicitly set model via env var
                model['primary'] = env_model
                cfg['agents']['defaults']['model'] = model
                openclaw_config.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
                fixes.append(f'model→{env_model}(env)')
            elif primary:
                print(f"[preflight] model: {primary} (set OPENCLAW_MODEL env to override)")
        except Exception as e:
            print(f"[preflight] config check failed: {e}")

    # 3. Kill orphaned openclaw-agent processes
    try:
        import subprocess as _sp
        result = _sp.run(['pgrep', '-f', 'openclaw-agent'], capture_output=True, text=True)
        pids = result.stdout.strip().split('\n')
        for pid in pids:
            if pid.strip():
                try:
                    os.kill(int(pid.strip()), 9)
                    fixes.append(f'kill:{pid.strip()}')
                except: pass
    except: pass

    if fixes:
        print(f"[preflight] auto-fixed: {', '.join(fixes)}")
    else:
        print("[preflight] all checks passed")

_preflight_check()
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
            _match(fields[4], (now.weekday() + 1) % 7, 0, 6)  # 0=Sunday (cron standard)
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
                        agent_id = task.get('agent_id', get_leader_id(company) if company else 'ceo')
                        threading.Thread(target=nudge_agent, args=(cid, prompt, agent_id.upper()), daemon=True).start()
        except Exception as e:
            print(f"[cron] scheduler error: {e}")

threading.Thread(target=_cron_scheduler, daemon=True).start()

def _auto_standup_scheduler():
    """Run auto standup every day at 09:00."""
    _last_run = None
    while True:
        try:
            time.sleep(60)
            now = datetime.now()
            if now.hour == 9 and now.minute == 0 and _last_run != now.date():
                _last_run = now.date()
                print(f"[standup] auto standup triggered at {now}")
                for cid_entry in db_get_all_companies():
                    cid = cid_entry['id']
                    try:
                        import urllib.request as _ur
                        req = _ur.Request(f'http://localhost:{PORT}/api/standup-run/{cid}', method='POST',
                                         headers={'Content-Type':'application/json'}, data=b'{}')
                        _ur.urlopen(req, timeout=120)
                        print(f"[standup] {cid} standup done")
                    except Exception as e:
                        print(f"[standup] {cid} error: {e}")
        except Exception as e:
            print(f"[standup] scheduler error: {e}")

threading.Thread(target=_auto_standup_scheduler, daemon=True).start()

uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
