"""Observability helpers: prompt dumping and request tracing.

Enable with env vars:
    DEBUG_PROMPTS=1
    PROMPT_DUMP_DIR=/tmp/aichub-prompts
"""
from datetime import datetime
from pathlib import Path
from config import DEBUG_PROMPTS, PROMPT_DUMP_DIR


def dump_prompt(agent_id: str, prompt: str, reply: str | None = None, kind: str = 'nudge') -> None:
    """Write the full prompt (and optional reply) to disk for offline inspection.
    No-op unless DEBUG_PROMPTS is enabled.
    """
    if not DEBUG_PROMPTS:
        return
    try:
        PROMPT_DUMP_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d-%H%M%S-%f')[:-3]
        fname = f"{ts}_{kind}_{agent_id}.md"
        path = PROMPT_DUMP_DIR / fname
        body = f"# {kind} :: {agent_id} @ {datetime.now().isoformat()}\n\n## PROMPT\n\n{prompt}\n"
        if reply is not None:
            body += f"\n## REPLY\n\n{reply}\n"
        path.write_text(body, encoding='utf-8')
    except OSError:
        # Never let observability break the actual workflow
        pass


def dump_event(event: str, **fields) -> None:
    """Append a structured event line to the prompt dump dir."""
    if not DEBUG_PROMPTS:
        return
    try:
        PROMPT_DUMP_DIR.mkdir(parents=True, exist_ok=True)
        log_path = PROMPT_DUMP_DIR / 'events.log'
        ts = datetime.now().isoformat()
        parts = [f"{ts}", event] + [f"{k}={v}" for k, v in fields.items()]
        with log_path.open('a', encoding='utf-8') as f:
            f.write(' | '.join(parts) + '\n')
    except OSError:
        pass
