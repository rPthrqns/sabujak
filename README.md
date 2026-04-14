# Sabujak (사부작)

> **AI agents quietly getting work done, one small task at a time.**
>
> *"Sabujak-sabujak"* is a Korean onomatopoeia describing the act of **quietly and persistently chipping away at small tasks**. Build a virtual company, drop in a CEO/CTO/CMO, and let autonomous AI agents delegate via `@mentions`, manage work on a kanban, request approvals, and deliver real artifacts — while you sleep.

![Python](https://img.shields.io/badge/Python-3.12+-3776ab?style=flat&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat&logo=fastapi&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?style=flat&logo=sqlite&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green?style=flat)

## Quick Start

```bash
git clone https://github.com/rPthrqns/sabujak.git
cd sabujak
pip install -r requirements.txt

python3 -u dashboard/server.py
# → http://localhost:3000
```

On first visit you'll be asked to pick a language. Type any language name (e.g. `English`, `Deutsch`, `한국어`, `日本語`, `עברית`) and the LLM translates the whole UI — including agent welcome messages, role labels, and even RTL layout for Hebrew/Arabic — in one pass.

## How It Works

Create a company, give it a topic. **Only the CEO is created first** — the CEO then analyzes the topic and proposes the team:

```
You → Create "Acme Games" (topic: 2D MMORPG)
  ↓
CEO created → auto-analyzes topic → proposes team:
  → [HIRE:CTO:Game Tech Lead:💻]
  → [HIRE:Designer:2D Pixel Artist:🎨]
  → [HIRE:SoundEng:Audio Design:🎵]
  ↓
You approve/reject each hire in the approval panel
  ↓
Approved agents are created with LLM-generated role-specific SOUL.md
  ↓
You → @CEO "Build the game"
  CEO → @CTO "Set up server" + @Designer "Create sprites"
      → [TASK_ADD:Build game:high]
  CTO → (builds, reports back)
  Designer → (creates art, saves to deliverables)
```

Every agent response is validated by a **guardrail** — responses without `@mentions` or `[COMMAND:]` (just prep talk like "I'll check on that...") are rejected and retried with an enforcement prompt.

## UI

### Layout

```
┌──────────────────────────────────────────┐
│  Header (company tabs / search / 🤖＋ / 🔗) │
├────┬─────────────────────────────────────┤
│ 👔 │  👤 You                  14:23      │
│ 📈 │  @CEO do market research            │
│ 💻 │                                     │
│ 🎨 │  👔 CEO                  14:24      │
│ 👥 │  ## Market Research                 │
│    │  **Competitor analysis** complete   │
│    │  | Item | Result |                  │
│    │  @CMO collect marketing data        │
│    │                                     │
├────┴─────────────────────────────────────┤
│  [📎] @agent your instruction...  [⏎]    │
└──────────────────────────────────────────┘
                         Side drawer →
                         📋 Tasks / 🔔 Approvals
                         🗂️ Plan / 📂 Files
```

- **Left sidebar** — agent icons with per-state visual cues + cost labels + ■ stop button (when busy)
- **Right main** — threaded chat (messages grouped by user command → agent responses, collapsible)
- **Bottom** — command bar + file attach (📎) + approval approve/reject mode
- **Side drawer** — Tasks, Approvals, Plan Tree, Files (with previews), Agent Comms (paired request↔response view)
- **📊 Dashboard** — multi-company overview (header button)
- **🧠 Persona** — double-click any agent icon to customize their personality
- **🔥 Dismiss** — right-click any agent icon to propose dismissal
- **📲 PWA** — installable as a home screen app, works offline with cached data

### Agent Icon States

| State | Visual |
|-------|--------|
| Working | Blue border + pulsing glow + ■ stop button |
| Thinking | Yellow border + rapid pulse + blinking dot + ■ stop button |
| Idle (active) | Green border (online) |
| Inactive | Gray + semi-transparent |
| Registering | Purple dashed + spinning |

Each icon also shows a **cost label** (`$0.0177`) and supports:
- **Click** — fill @mention in command bar
- **Double-click** — edit persona (personality notes → SOUL.md)
- **Right-click** — propose dismissal (creates approval, requires confirmation)
- **■ button** — stop running agent (visible when working/thinking)

### Chat Rendering

Messages are **threaded**: a user command and all subsequent agent responses are grouped together. Threads with 2+ replies are collapsible (`▶ N replies` / `▼ collapse`).

Agent responses are rendered from **Markdown to HTML**:

- Headings (`#`, `##`, `###`), bold/italic, code blocks, tables
- Lists, blockquotes, horizontal rules
- Links, images, @mention highlighting
- Delegation tags (`→ 🤖CTO`) shown below messages that @mention other agents

### Plan Tree

Press the 🗂️ button for a full-screen overlay:

```
┌──────────────────────────────────────────┐
│  🗂️ Work Plan                         ✕  │
├──────────────────────────────────────────┤
│  [◐ 67%]  Total:12  Done:8  WIP:2  Wait:2 │
├──────────────────────────────────────────┤
│  🤖CEO ████████░░ 4/5                     │
│  💰CFO ██████░░░░ 3/5                     │
├──────────────────────────────────────────┤
│  💻 Development                3/5 ██████░ │
│    ✓ Build API server          👨‍💻CTO      │
│    ↻ DB schema design          👨‍💻CTO      │
│                                           │
│  🎨 Design                     2/2 ████████ │
│    (done — auto-collapsed)                │
│                                           │
│  📢 Marketing                  1/3 ███░░░░ │
│    ↻ SNS content plan          📊CMO       │
└──────────────────────────────────────────┘
```

- **Circular progress ring** (SVG) — overall completion
- **Per-agent progress bars** — who's done how much
- **Category grouping** — Development / Planning / Marketing / Design / Operations
- **Kanban tasks auto-merged** — plan_tasks + board_tasks unified view
- **Completed items auto-collapse**
- **Inline add/delete** — manual task management

## Core Features

### Agent System
| Feature | Description |
|---------|-------------|
| **Multi-company** | Run multiple companies simultaneously, isolated SQLite DBs per company |
| **Agent hierarchy** | Master → CEO → team members with dynamic leader detection |
| **System commands** | `[TASK_ADD:]`, `[TASK_DONE:]`, `[APPROVAL:]`, `[CRON_ADD:]` + unified `[TASK:add:]` form |
| **Guardrails** | Every reply must contain an @mention or system command — prep talk is rejected and retried |
| **Memory Stream** | Stanford GenAgents pattern: recency × importance × relevance |
| **Agent stop** | ■ button to kill a running agent mid-task (clears queue + kills process) |
| **Agent persona** | Double-click icon → edit personality notes → injected into SOUL.md |
| **SOUL generation** | Every agent gets an LLM-generated role+company-specific SOUL.md on hire |
| **Agent dismissal** | Right-click icon or `[FIRE:Name:Reason]` → approval → agent removed |
| **CEO-first hiring** | Only CEO created initially; CEO proposes team via `[HIRE:]` commands |
| **Cost tracking** | Per-agent cost label on icons + header stats |
| **Real-time SSE** | Live updates via Server-Sent Events |

### Communication
| Feature | Description |
|---------|-------------|
| **Threaded chat** | Messages grouped by user command → agent responses; collapsible threads |
| **Delegation chains** | `@mentions` in agent messages show visual `→ 🤖CTO` tags |
| **Agent comms** | 💬 drawer tab with paired request↔response view (who asked what, who answered) |
| **File upload** | 📎 button for images/docs, forwarded to agents |
| **File previews** | .md rendered as HTML, .json formatted, code files with syntax view |
| **Image serving** | PNG/JPG returned with correct MIME + inline thumbnails |
| **Outsourcing** | Cross-company delegation (Company A → Company B) |
| **Full-text search** | Search the entire chat history |

### Work Management
| Feature | Description |
|---------|-------------|
| **Kanban board** | waiting → in-progress → done |
| **Plan tree** | Category-grouped, auto-generated, circular progress |
| **Approvals** | `[APPROVAL:]` + keyword auto-detection, deduplicated |
| **Sprints** | Time-boxed work cycles with auto-retrospective |
| **Escalation** | Failure → supervisor → CEO → Master (max 2 levels) |
| **Recurring tasks** | `[CRON_ADD:]` schedules repeating work |

### Governance & Analytics
| Feature | Description |
|---------|-------------|
| **Approval dedup** | Pending approvals with the same title are skipped |
| **Budget** | Per-company budget tracking with auto-approval on overrun |
| **KPI dashboard** | Completion rate, cost efficiency, agent rankings |
| **Wiki** | Categorized knowledge base (SOP, Guide, Decision, Reference) |
| **Risk register** | Severity-sorted risks with mitigation plans |
| **Audit log** | Full action trail |
| **Multi-company dashboard** | 📊 button → overview cards with agent count, task progress, cost per company |
| **i18n (LLM-powered)** | Type any language on first visit — LLM translates UI, roles, welcome messages in one bundle |

## Agent SOUL Generation

Every agent runs based on its `SOUL.md` — a personality/protocol file in its workspace. Sabujak uses the LLM to **auto-generate a role-and-company-specific SOUL.md** for every agent:

```
Company created: "Acme Games" (topic: "2D MMORPG")
  ↓
For EACH agent (CEO, CTO, CMO, Designer, ...):
  ↓
LLM receives: agent name, role, "Acme Games", "2D MMORPG", language
  ↓
Generates tailored SOUL.md:
  · Identity & expertise specific to this role at THIS company
  · Skills relevant to the company's topic
  · Deliverable types they should produce
  · Communication rules (@mentions, reporting chain)
  · System commands reference
  ↓
Saved to workspace/{agent}/SOUL.md
```

This means a **game company CEO** and an **ad agency CEO** get completely different SOULs — different priorities, different expertise, different deliverables.

- Runs in a **background thread** (doesn't block registration)
- If LLM fails, a generic fallback template is used
- Works for all hire paths: initial company creation, manual 🤖＋, CEO recommendation, approval-based hiring
- You can further customize any agent by **double-clicking their icon** → editing persona notes

## Internationalization (i18n)

Sabujak has first-class support for any language:

1. **First visit** — language overlay. Type anything: `English`, `Deutsch`, `한국어`, `日本語`, `Français`, `हिन्दी`, `עברית`, `العربية`...
2. The server calls the LLM **once** and translates a bundle containing:
   - All UI strings (buttons, modals, toasts, notifications)
   - Agent role labels (CEO → "Chief Executive", "Geschäftsführer", etc.)
   - Welcome message template
3. Translations are cached to `dashboard/i18n/{code}.json`, `roles.json`, `welcome.json`
4. The UI applies immediately (no reload)
5. **RTL support**: Hebrew, Arabic, Persian, Urdu, Yiddish automatically mirror the layout via `<html dir="rtl">` + CSS overrides
6. **Locale-aware time formatting** via `Intl.DateTimeFormat`

Supported by default: Korean, English, Japanese, Chinese.
Any other language is auto-generated on demand.

## Mobile & PWA

Sabujak is fully responsive and installable as a Progressive Web App:

### Responsive Layout (≤640px)
```
┌─────────────────────────┐
│ 🏢 Sabujak  🔍🔔🌍🤖🔗│  ← header
├─────────────────────────┤
│ [Company A] [Company B] │  ← tabs (horizontal scroll)
├─────────────────────────┤
│ 👔 📈 💻 🎨            │  ← agent strip
├─────────────────────────┤
│     Chat area            │  ← scrollable
├─────────────────────────┤
│ 📋 🔔 🗂️ 📂 💬         │  ← drawer icons
│ [📎] @input...     [⏎]  │  ← command bar
└─────────────────────────┘
```

### Install as App
- **Android Chrome**: in-app `📲 Install App` button appears automatically (via `beforeinstallprompt`)
- **iOS Safari**: tooltip guides user to Share → Add to Home Screen
- Installed app runs in **standalone mode** (no browser chrome)
- `manifest.json` + service worker included

### Offline Support
- Static assets (HTML/CSS/JS) cached on first visit → load instantly
- API responses cached with network-first strategy → last-seen data shown offline
- SSE and POST requests are excluded from caching

## Architecture

### Communication Pipeline

```
User command → queued (max 10 per agent)
             → agent loads context (newspaper + inbox + memory + tasks)
             → agent response
             → guardrail check
                ├─ pass → parse commands (kanban / approvals / plan update)
                └─ fail → reject + retry with enforcement prompt
             → @mentions resolved → target agents nudged
             → on failure → escalate upward
```

### 3-Tier Retry + Escalation
1. **Attempt 1**: normal call (120s timeout)
2. **Attempt 2**: lock cleanup + 2s wait + new session retry
3. **Attempt 3**: full session reset + retry
4. **Escalation**: supervisor → leader → Master (max 2 hops)

### Memory Stream (Stanford GenAgents)
- **Recency** — recent memories score higher (exponential decay)
- **Importance** — scored 1–10 by response length/significance
- **Relevance** — keyword match vs. current query
- Capped at 100 memories per agent, auto-pruned

### Concurrency
- FIFO queue, up to 10 pending messages per agent
- Semaphore-bounded concurrent agent execution
- Per-agent busy tracking — no deadlocks
- Automatic `.lock` cleanup on process kill and startup

## Agent Commands

Agents control the system by including commands in their responses. **Both formats supported**:

### Legacy format (current prompts use this)
```
[TASK_ADD:name:high]            — add kanban task
[TASK_START:name]               — start task
[TASK_DONE:name]                — complete task
[TASK_BLOCK:name:reason]        — block task
[APPROVAL:category:title:detail] — request approval
[CRON_ADD:name:minutes:prompt]  — schedule recurring task
[CRON_DEL:name]                 — delete recurring task
[HIRE:Name:Role:Emoji]          — propose hiring (CEO/leaders, requires approval)
[FIRE:Name:Reason]              — propose dismissal (CEO/leaders, requires approval)
```

### Unified format (optional)
```
[TASK:add:name:high]
[TASK:start:name]
[TASK:done:name]
[TASK:block:name:reason]
[CRON:add:name:minutes:prompt]
[CRON:del:name]
```

Both formats can coexist in the same response.

## API

### Companies & Chat
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/companies` | GET | List companies |
| `/api/company/{cid}` | GET | Company detail (agents, chat, tasks) |
| `/api/companies` | POST | Create company `{name, topic, lang}` |
| `/api/company/delete` | POST | Delete company |
| `/api/chat/{cid}` | POST | Send message `{text}` |
| `/api/upload/{cid}` | POST | File upload (multipart/form-data) |
| `/api/search?q=` | GET | Full-text chat search |
| `/api/sse` | GET | Real-time SSE stream |

### Agents
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/agent-add/{cid}` | POST | Add agent `{name, role, emoji}` |
| `/api/agent-delete/{cid}/{aid}` | POST | Delete agent |
| `/api/agent-reactivate/{cid}/{aid}` | POST | Reactivate agent |
| `/api/agent-stop/{cid}/{aid}` | POST | Stop running agent (kill process, clear queue) |
| `/api/agent-persona/{cid}/{aid}` | GET/POST | Get/set agent personality notes (→ SOUL.md) |
| `/api/models` | GET | Available LLM models |

### Work Management
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/board-tasks/{cid}` | GET | Kanban tasks |
| `/api/plan-tasks/{cid}` | GET | Plan tree |
| `/api/plan-task-add/{cid}` | POST | Add plan task |
| `/api/approvals/{cid}?status=pending` | GET | Pending approvals |
| `/api/approval-approve/{cid}` | POST | Approve |
| `/api/approval-reject/{cid}` | POST | Reject |

### Files & Analytics
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/deliverables/{cid}` | GET | Deliverables (with image previews) |
| `/api/file/{cid}/{path}` | GET | File download (auto-detected MIME type) |
| `/api/download/{cid}` | GET | All deliverables as ZIP |
| `/api/costs/{cid}` | GET | Cost tracking |
| `/api/kpi/{cid}` | GET | KPI dashboard |
| `/api/narrative/{cid}` | GET | Activity log |

### Cross-Company
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/cross-nudge` | POST | Outsource `{from_cid, to_cid, text}` |
| `/api/snapshot/{cid}` | POST | Save snapshot |
| `/api/fork/{snap_id}` | POST | Fork from snapshot |

### i18n
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/i18n/{lang}` | GET | Get UI strings for a language |
| `/api/i18n/generate` | POST | Generate UI + roles + welcome via LLM `{language}` |
| `/api/i18n/languages` | GET | List available languages |

## Project Structure

```
sabujak/
├── dashboard/
│   ├── server.py            # FastAPI server (~5100 lines, 100+ endpoints)
│   ├── db.py                # SQLite per-company sharding (~1600 lines)
│   ├── pool.py              # DB connection pool
│   │
│   ├── index.html           # SPA shell (references external CSS/JS)
│   ├── app.css              # Stylesheet (~300 lines, incl. RTL + mobile responsive)
│   ├── app.js               # Frontend logic (~950 lines)
│   ├── manifest.json        # PWA manifest (home screen install)
│   ├── sw.js                # Service worker (PWA requirement)
│   │
│   ├── config.py            # Centralized magic numbers/timeouts (env overridable)
│   ├── logger.py            # Central logging adapter
│   ├── observability.py     # request_id + prompt dump
│   │
│   ├── parsers/             # Pure parsers (no DB — unit-testable)
│   │   ├── commands.py      #   [TASK_*], [APPROVAL:], [CRON_*] + unified [TASK:add:]
│   │   ├── guardrails.py    #   prep-talk detection, required-action check
│   │   ├── categories.py    #   task category classifier
│   │   └── heuristics.json  #   externalized prep keywords + category keywords
│   │
│   ├── prompts/
│   │   └── welcome.py       # Localized welcome messages (loads welcome.json)
│   │
│   ├── config.json          # Agent templates & topic presets
│   ├── i18n/                # i18n strings
│   │   ├── en.json, ko.json, ja.json   # UI translations
│   │   ├── welcome.json     # Welcome message templates
│   │   └── roles.json       # Runtime role translations
│   └── runtime/
│       ├── base.py          # AgentRuntime ABC
│       └── openclaw.py      # OpenClaw CLI runtime (JSONL polling)
│
├── tests/                   # pytest unit tests (47)
│   ├── test_command_parser.py
│   ├── test_guardrails.py
│   ├── test_categories.py
│   └── test_welcome.py
│
├── data/                    # Company data (.gitignored)
│   ├── hub.db               # Meta DB
│   └── {company-id}/
│       ├── company.db       # Per-company SQLite (20+ tables)
│       ├── _shared/         # Deliverables, shared files
│       └── workspaces/      # Per-agent workspaces
│
├── pytest.ini
├── requirements.txt
└── README.md
```

## Server Management

```bash
# Start
nohup python3 -u dashboard/server.py > /tmp/sabujak.log 2>&1 &

# Restart
pkill -f 'python3.*server.py'; sleep 2
nohup python3 -u dashboard/server.py > /tmp/sabujak.log 2>&1 &

# Health
curl -s http://localhost:3000/api/companies | python3 -m json.tool

# Logs
tail -f /tmp/sabujak.log

# Watch mode (auto-restart on file changes)
./scripts/watch.sh
```

## Tests

```bash
pytest                                # run all tests (47 total)
pytest tests/test_command_parser.py -v
```

Pure parsers (commands / guardrails / categories) are unit-tested without DB or LLM dependencies — so they run in milliseconds.

## Environment Variables

| Var | Default | Description |
|-----|---------|-------------|
| `PORT` | `3000` | Server port |
| `DATA_DIR` | `data/` | Company data directory |
| `OPENCLAW_MODEL` | `zai/glm-5` | Agent LLM model |
| `AGENT_TIMEOUT` | `180` | Agent call timeout (seconds) |
| `AGENT_RETRY_TIMEOUT` | `120` | Retry timeout |
| `MAX_CONCURRENT` | `5` | Concurrent agents |
| `AGENT_QUEUE_MAX` | `10` | Max queue per agent |
| `LOG_LEVEL` | `INFO` | Logging level (DEBUG/INFO/WARNING/ERROR) |
| `LOG_FILE` | *(none)* | Log file path (rotating if set) |
| `DEBUG_PROMPTS` | `0` | Set to `1` to dump nudge prompts & replies to files |
| `PROMPT_DUMP_DIR` | `/tmp/sabujak-prompts` | Where to write prompt dumps |

## Observability

- **Request tracing** — every HTTP response carries an `X-Request-Id` header:
  ```bash
  curl -sD - http://localhost:3000/api/companies | grep -i request-id
  # x-request-id: c82aafd0d801
  ```
- **Prompt dumps** — with `DEBUG_PROMPTS=1`, every `nudge_agent` call saves the full prompt and response as Markdown:
  - Filename: `{timestamp}_nudge_{agent_id}.md`
  - Contents: full prompt + agent reply

## Requirements
- Python 3.12+
- [OpenClaw](https://openclaw.io) (agent runtime)
- An LLM API key
- (optional) `pytest` for running unit tests

## License
MIT

---

*"사부작사부작 일하다 보면 어느새 완성되어 있다." — AI agents, quietly, one task at a time.*
