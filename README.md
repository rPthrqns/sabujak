# AI Company Hub

> Autonomous multi-agent company simulation platform. Create AI-powered companies with CEO, CMO, CTO and custom agents that collaborate, delegate, and deliver — all managed through a real-time dark-themed dashboard.

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

You create a company, pick a topic, and AI agents (CEO, CMO, CTO, etc.) start working autonomously. They communicate via `@mentions`, create tasks, write reports, and escalate decisions to you — the "Master".

```
Master → @CEO "Launch marketing campaign"
  CEO → @CMO "Design social media strategy"
  CEO → @CTO "Set up landing page"
  CMO → @Designer "Create ad visuals"
  CTO → (works autonomously, reports back)
```

## Features

### Core System
| Feature | Description |
|---------|-------------|
| **Multi-Company** | Run multiple companies simultaneously with isolated data |
| **Agent Org Chart** | Hierarchical card layout: Master → CEO → Team members |
| **@Mention Chat** | `@CEO instruction` routes messages to the right agent |
| **Kanban Board** | Auto-tracked tasks (Waiting → In Progress → Done) |
| **Approval Gates** | Agents request human approval for critical decisions |
| **Cost Tracking** | Per-agent token usage and cost monitoring |
| **Real-time SSE** | Live updates via Server-Sent Events |

### Company Operations (10 Enterprise Features)
| Feature | Description |
|---------|-------------|
| **Auto Standup** | Daily 09:00 — each agent reports Yesterday/Today/Blockers |
| **Sprint System** | Time-boxed work cycles with auto-retrospective on completion |
| **Escalation Chain** | Agent fails → auto-escalate to supervisor → CEO → Master |
| **Knowledge Base** | Wiki with categories (SOP, Guide, Decision, Reference) |
| **KPI Dashboard** | Completion rate, cost efficiency, agent rankings |
| **Performance Review** | S/A/B/C/D grades with scoring and medal rankings |
| **Milestones** | Deadline-tracked goals linked to kanban tasks |
| **Risk Register** | Severity-sorted risk tracking with mitigation plans |
| **Meeting Notes** | Auto-saved minutes with decisions and action items |
| **Onboarding** | Context injection for new agents (wiki + tasks + risks) |

### UI/UX
| Feature | Description |
|---------|-------------|
| **5-Tab Split View** | Org / Work / Manage / Analytics / Docs — each with dual panels |
| **Auto-Collapsing Header** | Header folds after 8s, hover to restore — maximizes workspace |
| **Agent Presets** | One-click add: COO, HR, PM, Dev, SEO, Security + 8 more |
| **Communication Permissions** | All / CEO-only / Custom per-agent routing |
| **Sub-Organizations** | Nest agents under other agents (CEO → CTO → Developer) |
| **Active Comm Lines** | Org chart lines glow when agents are communicating |
| **Zoom Controls** | Ctrl+Scroll to zoom org chart |
| **Chat Search** | Full-text search across all company messages |

## Architecture

### Agent Communication (Queue + Nudge)

The server never directly controls agents. It sends lightweight "nudges" and agents respond autonomously.

```
User Chat → Server queues + nudges agent (fire-and-forget)
         → Agent reads context (newspaper + inbox + standup)
         → Agent responds (stdout) → Server parses into chat
         → If @mentions found → nudge target agents
         → If failure → escalate to supervisor
```

### Concurrency Model
- **FIFO Queue**: Max 3 pending messages per agent
- **Semaphore**: Max 2 agents thinking simultaneously
- **No Locks**: Busy agents queue silently — no deadlocks possible

### 3-Tier Retry + Escalation
1. **Attempt 1**: Normal call (120s timeout)
2. **Attempt 2**: 2s wait + retry with new session
3. **Attempt 3**: Full session reset + fresh attempt
4. **Escalation**: Auto-route to parent agent → CEO → Master approval

### Newspaper Model
Auto-generated briefing refreshed every agent turn:
- Team status (working/active/idle)
- Task progress (waiting/in-progress/done today)
- Pending approvals
- Master's recent instructions
- Recent deliverables

## Agent Commands

Agents control the system by including commands in their responses:

```
[TASK_ADD:Task Name:Priority(high/medium/low)]    # Add kanban task
[TASK_START:Task Name]                              # Start task
[TASK_DONE:Task Name]                               # Complete task
[TASK_BLOCK:Task Name:Reason]                       # Block task
[CRON_ADD:Name:Interval(min):Prompt]                # Schedule recurring
[CRON_DEL:Name]                                     # Delete recurring
```

## API Reference

### Companies & Chat
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/companies` | GET | List all companies |
| `/api/company/{cid}` | GET | Company details |
| `/api/companies` | POST | Create company |
| `/api/company/delete` | POST | Delete company |
| `/api/chat/{cid}` | POST | Send user message |
| `/api/search?q=` | GET | Full-text chat search |
| `/api/sse` | GET | Real-time event stream |

### Agents
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/agent-add/{cid}` | POST | Add agent (with parent_agent support) |
| `/api/agent-delete/{cid}/{aid}` | POST | Remove agent |
| `/api/agent-reactivate/{cid}/{aid}` | POST | Reactivate agent |
| `/api/onboard/{cid}/{aid}` | POST | Run onboarding |
| `/api/standup/{cid}/{aid}` | GET | Agent standup |
| `/api/inbox/{cid}/{aid}` | GET | Agent inbox |
| `/api/comm-permissions/{cid}` | GET/POST | Communication permissions |

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

### Governance
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/approvals/{cid}?status=pending` | GET | Pending approvals |
| `/api/milestones/{cid}` | GET | List milestones |
| `/api/milestone-add/{cid}` | POST | Add milestone |
| `/api/risks/{cid}` | GET | List risks |
| `/api/risk-add/{cid}` | POST | Add risk |
| `/api/meetings/{cid}` | GET | Meeting notes |

### Analytics & Knowledge
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/kpi/{cid}` | GET | KPI dashboard data |
| `/api/performance/{cid}` | GET | Performance reviews |
| `/api/newspaper/{cid}` | GET | Auto-generated briefing |
| `/api/wiki/{cid}` | GET/POST | Wiki pages CRUD |
| `/api/costs/{cid}` | GET | Cost tracking |
| `/api/deliverables/{cid}` | GET | Shared deliverables |

## Project Structure

```
ai-company-hub/
├── dashboard/
│   ├── server.py          # FastAPI + Uvicorn server (~3800 lines)
│   ├── db.py              # SQLite with per-company sharding (~1100 lines)
│   ├── index.html         # Single-page dark UI (~2400 lines)
│   └── runtime/
│       ├── base.py        # AgentRuntime ABC
│       └── openclaw.py    # OpenClaw CLI implementation
├── data/                  # Company data (.gitignored)
│   ├── hub.db             # Meta database
│   └── {company-id}/
│       ├── company.db     # Per-company SQLite
│       ├── _shared/       # Newspaper, whiteboard, deliverables
│       └── workspaces/    # Per-agent workspaces
│           └── {agent}/
│               ├── SOUL.md, IDENTITY.md, TOOLS.md
│               ├── inbox/, memory/
│               └── HEARTBEAT.md
├── requirements.txt       # fastapi, uvicorn
└── README.md
```

## Database Schema

### Meta DB (`hub.db`)
- `companies` — Company metadata
- `snapshots` — State snapshots for fork/restore
- `webhook_routes` — Event routing

### Per-Company DB (`{cid}/company.db`)
- `chat_messages` + `chat_fts` — Messages with full-text search
- `board_tasks` — Kanban tasks
- `approvals` — Approval workflow
- `activity_log` — System activity
- `documents` — Standup, newspaper cache
- `plan_tasks` — Plan tree (auto-generated by CEO)
- `sprints` + `sprint_tasks` — Sprint management
- `wiki_pages` — Knowledge base
- `milestones` — Project milestones
- `risks` — Risk register
- `meeting_notes` — Auto-saved meeting minutes

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
```

## Requirements
- Python 3.12+
- [OpenClaw](https://openclaw.io) (agent runtime)
- LLM API key (z.ai or compatible)

## License
MIT
