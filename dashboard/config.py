"""Centralized configuration for AI Company Hub.

All magic numbers, timeouts, and tunable parameters live here.
Override via environment variables when noted.
"""
import os
from pathlib import Path

# ─── Server ───
PORT = int(os.environ.get('PORT', 3000))
BASE = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get('DATA_DIR', str(BASE / "data")))
COMPANIES_FILE = DATA / "companies.json"

# ─── Agent Runtime Timeouts (seconds) ───
AGENT_RUN_TIMEOUT = int(os.environ.get('AGENT_TIMEOUT', 180))
AGENT_RETRY_TIMEOUT = int(os.environ.get('AGENT_RETRY_TIMEOUT', 120))
AGENT_INIT_TIMEOUT = 30
AGENT_POLL_INTERVAL = 3

# ─── Concurrency ───
MAX_CONCURRENT_AGENTS = int(os.environ.get('MAX_CONCURRENT', 5))
AGENT_QUEUE_MAX = int(os.environ.get('AGENT_QUEUE_MAX', 10))  # max pending msgs per agent

# ─── Watchdog ───
WATCHDOG_INTERVAL = 30
WATCHDOG_STUCK_THRESHOLD = 90

# ─── Guardrails ───
GUARDRAIL_PREP_MAX_LEN = 150     # responses shorter than this with prep keywords get rejected
GUARDRAIL_MAX_RETRIES = 1        # how many times to retry on guardrail failure
ESCALATION_MAX_LEVELS = 2        # max escalation hops

# ─── Memory Stream ───
MEMORY_MAX_PER_AGENT = 100       # cap per agent before pruning
MEMORY_TOP_K = 5                 # top memories injected into prompts

# ─── Approvals ───
AGENT_LIMIT_BEFORE_APPROVAL = 6  # adding agent #7+ requires approval

# ─── Defaults ───
DEFAULT_LANG = 'ko'
DEFAULT_BUDGET = 10.0
