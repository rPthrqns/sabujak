# AI Company Hub

> Autonomous multi-agent company simulation platform. Create AI-powered virtual companies where CEO, CMO, CTO and custom agents collaborate, delegate, and deliver real results — all visible through a live "virtual office" dashboard.

![Python](https://img.shields.io/badge/Python-3.12+-3776ab?style=flat&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat&logo=fastapi&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?style=flat&logo=sqlite&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green?style=flat)

## Quick Start

```bash
git clone https://github.com/rPthrqns/ai-company-hub.git
cd ai-company-hub
pip install -r requirements.txt

# Start server
python3 -u dashboard/server.py
# → http://localhost:3000
```

## What It Does

You create a company, pick a topic, and AI agents start working autonomously. They communicate via `@mentions`, create kanban tasks with `[TASK_ADD:]`, submit approvals with `[APPROVAL:]`, and escalate decisions to you — the "Master".

```
Master → @CEO "Build company website, simple & modern design"
  CEO → @CMO "Design mockup" + @CTO "Develop frontend"
       → [TASK_ADD:회사 홈페이지 제작:high]
       → [APPROVAL:purchase:도메인 구매:호스팅 비용 승인 필요]
  CMO → (creates design, reports back to CEO)
  CTO → (builds site, saves to deliverables)
```

## UI: Virtual Office

The dashboard shows a **living agent floor** — each agent is a card with real-time speech bubbles, status indicators, and delegation arrows.

### Layout
- **Agent Cards Grid** — Each card shows avatar, status glow, latest speech bubble, current task badge, delegation arrows
- **Command Bar** (bottom) — `@CEO instruction` style input replaces traditional chat
- **Side Drawer** (right) — Slide-out panels for Tasks, Approvals, Plan Tree, Files
- **Approval Banner** — Pending decisions appear at the top for quick approve/reject

### Card States
| State | Visual |
|-------|--------|
| Idle | Gray status dot, "대기 중" |
| Thinking | Yellow pulsing dot, glowing border, "⚡ 생각 중..." |
| Working | Blue glowing border |
| Delegating | Purple arrow card: `CEO → @CTO @CMO` |

## Features

### Core System
| Feature | Description |
|---------|-------------|
| **Multi-Company** | Run multiple companies simultaneously with isolated SQLite DBs |
| **Agent Hierarchy** | Master → CEO → Team members with dynamic leader detection |
| **System Commands** | Agents use `[TASK_ADD:]`, `[TASK_DONE:]`, `[APPROVAL:]`, `[CRON_ADD:]` |
| **Guardrail Enforcement** | Responses without commands/mentions are rejected and retried |
| **Memory Stream** | Stanford GenAgents pattern: recency × importance × relevance |
| **Structured Output** | MetaGPT-style format enforcement with retry |
| **Cost Tracking** | Per-agent token usage and cost monitoring |
| **Real-time SSE** | Live card updates via Server-Sent Events |

### Company Operations
| Feature | Description |
|---------|-------------|
| **Auto Standup** | Each agent reports status on demand |
| **Sprint System** | Time-boxed work cycles with auto-retrospective |
| **Escalation Chain** | Agent fails → supervisor → CEO → Master (max 2 escalations) |
| **Plan Tree** | Claude Code-style interactive tree, auto-generated from agent actions |
| **Knowledge Base** | Wiki with categories (SOP, Guide, Decision, Reference) |
| **KPI Dashboard** | Completion rate, cost efficiency, agent rankings |
| **Performance Review** | S/A/B/C/D grades with scoring |
| **Milestones** | Project milestones linked to kanban tasks |
| **Risk Register** | Severity-sorted risk tracking with mitigation plans |
| **Meeting System** | Multi-agent meetings with auto-saved minutes |
| **Outsourcing** | Cross-company delegation via `🔗` — Company A's CEO can outsource to Company B's Designer |
| **Onboarding** | Context injection for new agents (wiki + tasks + risks) |

### Governance & Analytics
| Feature | Description |
|---------|-------------|
| **Approval Gates** | `[APPROVAL:category:title:detail]` + auto-detection from keywords |
| **Announcements** | Company-wide announcements |
| **Policies** | Company rules/regulations |
| **Budget Tracking** | Per-company budget management |
| **Voting System** | Multi-agent voting on decisions |
| **Audit Log** | Full audit trail of all actions |
| **CRM** | Contact management |
| **Daily/Weekly Reports** | Auto-generated reports |
| **Work Journals** | Per-agent work journal auto-generation |

### i18n
| Feature | Description |
|---------|-------------|
| **LLM Translation** | First-visit language setup — type any language, LLM generates UI strings |
| **Detection Patterns** | Built-in patterns for Korean, English, Japanese, Chinese |
| **Config Externalized** | Agent templates and topics in `config.json`, not hardcoded |

## Architecture

### Agent Communication

```
User Command → Server queues + nudges agent
            → Agent reads context (newspaper + inbox + memory + tasks)
            → Agent responds with system commands
            → Guardrail checks: must have @mention or [COMMAND:]
               ├─ Pass → parse commands, update kanban/approvals/plan
               └─ Fail → reject + retry with enforcement prompt
            → If @mentions found → nudge target agents
            → If failure → escalate to supervisor
```

### Guardrail System
All agent responses are validated:
1. Must contain `@mention` or system command (`[TASK_ADD:]`, `[APPROVAL:]`, etc.)
2. Prep-only responses ("확인하겠습니다") under 150 chars are rejected
3. On rejection: agent gets enforcement prompt with exact command formats
4. Second failure: accepted with warning log
5. Auto-approval detection: keyword-based safety net when `[APPROVAL:]` not used

### Concurrency Model
- **FIFO Queue**: Max 3 pending messages per agent
- **Semaphore**: Configurable max concurrent agents
- **Busy Tracking**: Per-agent busy state, no deadlocks
- **Lock Cleanup**: Automatic `.lock` file cleanup on process kill and startup

### 3-Tier Retry + Escalation
1. **Attempt 1**: Normal call (120s timeout)
2. **Attempt 2**: Lock cleanup + 2s wait + retry with fresh session
3. **Attempt 3**: Full session reset + fresh attempt
4. **Escalation**: Auto-route to parent → leader → Master (max 2 levels)

### Memory Stream (Stanford GenAgents)
Each agent maintains a memory stream with weighted retrieval:
- **Recency**: Recent memories score higher (exponential decay)
- **Importance**: Scored 1-10 based on response length/significance
- **Relevance**: Keyword matching against current query
- Capped at 100 memories per agent with auto-pruning

### Newspaper Model
Auto-generated briefing included in every agent prompt:
- Team status (working/active/idle)
- Task progress from kanban
- Pending approvals
- Recent deliverables
- Memory stream (top 5 relevant)
- Recurring task schedule

## Agent Commands

Agents control the system by including commands in their responses:

```
[TASK_ADD:Task Name:high]                    — Add kanban task
[TASK_START:Task Name]                       — Start task
[TASK_DONE:Task Name]                        — Complete task
[TASK_BLOCK:Task Name:Reason]                — Block task with reason
[APPROVAL:category:title:detail]             — Submit approval to Master
[CRON_ADD:Name:Interval(min):Prompt]         — Schedule recurring task
[CRON_DEL:Name]                              — Delete recurring task
```

## API Reference

### Companies & Chat
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/companies` | GET | List all companies |
| `/api/company/{cid}` | GET | Company details with agents, chat, tasks |
| `/api/companies` | POST | Create company `{name, topic, lang}` |
| `/api/company/delete` | POST | Delete company and all agents |
| `/api/chat/{cid}` | POST | Send user message `{text}` |
| `/api/search?q=` | GET | Full-text chat search |
| `/api/sse` | GET | Real-time Server-Sent Events stream |

### Agents
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/agent-add/{cid}` | POST | Add agent `{name, role, emoji, parent_agent, model}` |
| `/api/agent-delete/{cid}/{aid}` | POST | Remove agent |
| `/api/agent-reactivate/{cid}/{aid}` | POST | Reactivate agent |
| `/api/agent-model/{cid}/{aid}` | GET/POST | Get/set agent model |
| `/api/onboard/{cid}/{aid}` | POST | Run onboarding |
| `/api/standup/{cid}/{aid}` | GET | Agent standup |
| `/api/inbox/{cid}/{aid}` | GET | Agent inbox |
| `/api/models` | GET | Available LLM models |

### Work Management
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/board-tasks/{cid}` | GET | Kanban tasks |
| `/api/board-task-add/{cid}` | POST | Add task |
| `/api/task-status/{cid}/{tid}` | POST | Update task status |
| `/api/goals/{cid}` | GET/POST | Goals CRUD |
| `/api/sprints/{cid}` | GET | List sprints |
| `/api/sprint-add/{cid}` | POST | Create sprint |
| `/api/sprint-end/{cid}/{sid}` | POST | End sprint (auto retro) |
| `/api/standup-run/{cid}` | POST | Trigger team standup |
| `/api/plan-tasks/{cid}` | GET | Plan tree tasks |
| `/api/plan-task-add/{cid}` | POST | Add plan task |
| `/api/plan-task-update/{cid}` | POST | Update plan task status |
| `/api/plan-task-delete/{cid}` | POST | Delete plan task |

### Governance
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/approvals/{cid}?status=pending` | GET | Pending approvals |
| `/api/approval-approve/{cid}` | POST | Approve |
| `/api/approval-reject/{cid}` | POST | Reject |
| `/api/milestones/{cid}` | GET/POST | Milestones CRUD |
| `/api/risks/{cid}` | GET/POST | Risk register CRUD |
| `/api/meetings/{cid}` | GET | Meeting notes |
| `/api/meeting/{cid}` | POST | Start multi-agent meeting |
| `/api/policies/{cid}` | GET/POST | Company policies |
| `/api/budgets/{cid}` | GET | Budget data |
| `/api/budget-set/{cid}` | POST | Set budget |
| `/api/votes/{cid}` | GET/POST | Voting system |
| `/api/announcements/{cid}` | GET/POST | Announcements |
| `/api/audit/{cid}` | GET | Audit log |

### Analytics & Knowledge
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/kpi/{cid}` | GET | KPI dashboard data |
| `/api/performance/{cid}` | GET | Performance reviews |
| `/api/newspaper/{cid}` | GET | Auto-generated briefing |
| `/api/wiki/{cid}` | GET/POST | Wiki pages CRUD |
| `/api/costs/{cid}` | GET | Cost tracking |
| `/api/deliverables/{cid}` | GET | Shared deliverables |
| `/api/daily-report/{cid}` | POST | Generate daily report |
| `/api/report-generate/{cid}/{type}` | POST | Generate report (daily/weekly) |
| `/api/crm/{cid}` | GET/POST | CRM contacts |
| `/api/journals/{cid}` | GET | Work journals |

### Cross-Company & Snapshots
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/cross-nudge` | POST | Outsource task to another company `{from_cid, to_cid, to_agent, text}` |
| `/api/snapshot/{cid}` | POST | Save state snapshot |
| `/api/snapshots/{cid}` | GET | List snapshots |
| `/api/fork/{snap_id}` | POST | Fork company from snapshot |
| `/api/restore/{cid}/{snap_id}` | POST | Restore from snapshot |
| `/api/download/{cid}` | GET | Download deliverables as ZIP |
| `/api/comm-permissions/{cid}` | GET/POST | Agent communication permissions |

### i18n
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/i18n/{lang}` | GET | Get UI strings for language |
| `/api/i18n/generate` | POST | Generate UI strings via LLM `{language}` |
| `/api/i18n/languages` | GET | Available languages |

## Project Structure

```
ai-company-hub/
├── dashboard/
│   ├── server.py          # FastAPI + Uvicorn (~4700 lines)
│   │                      #   - Agent nudge/guardrail/escalation
│   │                      #   - Plan tree auto-generation
│   │                      #   - Memory stream integration
│   │                      #   - 100+ API endpoints
│   ├── db.py              # SQLite per-company sharding (~1550 lines)
│   │                      #   - 20+ tables per company
│   │                      #   - Memory stream with weighted retrieval
│   │                      #   - Full-text search
│   ├── index.html         # Single-page virtual office UI (~720 lines)
│   │                      #   - Agent card grid with live speech bubbles
│   │                      #   - Command bar + side drawer
│   │                      #   - Claude Code-style plan tree
│   ├── config.json        # Externalized agent templates & topics
│   ├── i18n/              # UI translation strings
│   │   ├── en.json
│   │   └── ko.json
│   └── runtime/
│       ├── base.py        # AgentRuntime ABC
│       └── openclaw.py    # OpenClaw CLI (JSONL polling)
├── data/                  # Company data (.gitignored)
│   ├── hub.db             # Meta database
│   └── {company-id}/
│       ├── company.db     # Per-company SQLite (20+ tables)
│       ├── _shared/       # Newspaper, deliverables
│       └── workspaces/    # Per-agent workspaces
│           └── {agent}/
│               ├── SOUL.md        # Agent personality/context
│               ├── IDENTITY.md    # Role definition
│               ├── TOOLS.md       # Available system commands
│               ├── inbox/         # Inter-agent messages
│               └── memory/        # Agent memory files
├── requirements.txt       # fastapi, uvicorn
└── README.md
```

## Database Schema

### Meta DB (`hub.db`)
- `companies` — Company metadata (name, topic, lang, budget)
- `snapshots` — State snapshots for fork/restore
- `webhook_routes` — Event routing

### Per-Company DB (`{cid}/company.db`)

| Table | Purpose |
|-------|---------|
| `chat_messages` + `chat_fts` | Messages with full-text search |
| `board_tasks` | Kanban tasks (대기/진행중/완료) |
| `approvals` | Approval workflow (pending/approved/rejected) |
| `activity_log` | System activity trail |
| `documents` | Standup, newspaper, onboard cache |
| `plan_tasks` | Plan tree (auto-generated by leader) |
| `sprints` + `sprint_tasks` | Sprint management |
| `wiki_pages` | Knowledge base |
| `milestones` | Project milestones |
| `risks` | Risk register |
| `meeting_notes` | Auto-saved meeting minutes |
| `memory_stream` | Agent memory (Stanford GenAgents) |
| `crm_contacts` | CRM data |
| `announcements` | Company announcements |
| `work_journals` | Per-agent journals |
| `policies` | Company policies/rules |
| `budgets` | Budget records |
| `votes` | Voting system |
| `audit_log` | Full audit trail |

## Server Management

```bash
# Start (background, unbuffered logs)
nohup python3 -u dashboard/server.py > /tmp/ai-company-hub.log 2>&1 &

# Restart
pkill -f 'python3.*server.py'; sleep 2
nohup python3 -u dashboard/server.py > /tmp/ai-company-hub.log 2>&1 &

# Check status
curl -s http://localhost:3000/api/companies | python3 -m json.tool

# Tail logs
tail -f /tmp/ai-company-hub.log

# Preflight checks (automatic on startup)
# - Lock file cleanup
# - Model availability check (OPENCLAW_MODEL env)
# - Orphan process cleanup
```

## Requirements
- Python 3.12+
- [OpenClaw](https://openclaw.io) (agent runtime)
- LLM API key (z.ai or compatible)

## License
MIT
