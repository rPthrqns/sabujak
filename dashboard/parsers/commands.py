"""Pure regex parsers for agent system commands.

These are extracted from server.process_task_commands() so they can be
unit-tested without DB or LLM dependencies. Each function returns a list
of structured dicts; mutations to DB happen in the caller.
"""
import re

# ─── Pattern constants ───
# Legacy formats (still supported)
RE_TASK_ADD = re.compile(r'\[TASK_ADD:([^:]+):([^\]]+)\]')
RE_TASK_DONE = re.compile(r'\[TASK_DONE:([^\]]+)\]')
RE_TASK_START = re.compile(r'\[TASK_START:([^\]]+)\]')
RE_TASK_BLOCK = re.compile(r'\[TASK_BLOCK:([^:]+):([^\]]+)\]')
RE_CRON_ADD = re.compile(r'\[CRON_ADD:([^:]+):(\d+):([^\]]+)\]')
RE_CRON_DEL = re.compile(r'\[CRON_DEL:([^\]]+)\]')

# Unified format: [TASK:verb:args] / [CRON:verb:args]
RE_TASK_UNIFIED = re.compile(r'\[TASK:(add|start|done|block):([^\]]+)\]')
RE_CRON_UNIFIED = re.compile(r'\[CRON:(add|del):([^\]]+)\]')

RE_APPROVAL = re.compile(r'\[APPROVAL:([^:\]]+):([^:\]]+)(?::([^\]]*))?\]')
RE_MENTION = re.compile(r'@([A-Za-z\w]+)')
RE_HAS_COMMAND = re.compile(r'\[TASK[_:]|\[APPROVAL:|\[CRON[_:]')

APPROVAL_CATEGORIES = {
    '예산', '구매', '프로젝트', '인사', '정책', '기타',
    'budget', 'purchase', 'project', 'hr', 'policy', 'general',
}


def _split_args(s, n):
    """Split colon-separated args, returning exactly n items (last absorbs the rest)."""
    parts = s.split(':', n - 1)
    while len(parts) < n:
        parts.append('')
    return [p.strip() for p in parts]


def parse_task_add(text):
    """Returns: list of {'title': str, 'priority': str}.
    Supports both [TASK_ADD:title:priority] and [TASK:add:title:priority]."""
    out = [{'title': m.group(1).strip(), 'priority': m.group(2).strip()}
           for m in RE_TASK_ADD.finditer(text)]
    for m in RE_TASK_UNIFIED.finditer(text):
        if m.group(1) != 'add':
            continue
        title, priority = _split_args(m.group(2), 2)
        if title:
            out.append({'title': title, 'priority': priority or 'normal'})
    return out


def parse_task_done(text):
    """Supports [TASK_DONE:title] and [TASK:done:title]."""
    out = [m.group(1).strip() for m in RE_TASK_DONE.finditer(text)]
    for m in RE_TASK_UNIFIED.finditer(text):
        if m.group(1) == 'done':
            t = m.group(2).strip()
            if t:
                out.append(t)
    return out


def parse_task_start(text):
    """Supports [TASK_START:title] and [TASK:start:title]."""
    out = [m.group(1).strip() for m in RE_TASK_START.finditer(text)]
    for m in RE_TASK_UNIFIED.finditer(text):
        if m.group(1) == 'start':
            t = m.group(2).strip()
            if t:
                out.append(t)
    return out


def parse_task_block(text):
    """Supports [TASK_BLOCK:title:reason] and [TASK:block:title:reason]."""
    out = [{'title': m.group(1).strip(), 'reason': m.group(2).strip()}
           for m in RE_TASK_BLOCK.finditer(text)]
    for m in RE_TASK_UNIFIED.finditer(text):
        if m.group(1) != 'block':
            continue
        title, reason = _split_args(m.group(2), 2)
        if title:
            out.append({'title': title, 'reason': reason})
    return out


def parse_cron_add(text):
    """Supports [CRON_ADD:title:interval:prompt] and [CRON:add:title:interval:prompt]."""
    out = [{'title': m.group(1).strip(),
            'interval': int(m.group(2)),
            'prompt': m.group(3).strip()}
           for m in RE_CRON_ADD.finditer(text)]
    for m in RE_CRON_UNIFIED.finditer(text):
        if m.group(1) != 'add':
            continue
        title, interval, prompt_text = _split_args(m.group(2), 3)
        try:
            iv = int(interval)
        except ValueError:
            continue
        if title and prompt_text:
            out.append({'title': title, 'interval': iv, 'prompt': prompt_text})
    return out


def parse_cron_del(text):
    out = [m.group(1).strip() for m in RE_CRON_DEL.finditer(text)]
    for m in RE_CRON_UNIFIED.finditer(text):
        if m.group(1) == 'del':
            t = m.group(2).strip()
            if t:
                out.append(t)
    return out


def parse_approval(text):
    """Returns: list of {'category': str, 'title': str, 'detail': str}.

    Supports both [APPROVAL:cat:title:detail] and [APPROVAL:title:detail].
    """
    out = []
    for m in RE_APPROVAL.finditer(text):
        parts = [m.group(1).strip(), m.group(2).strip(), (m.group(3) or '').strip()]
        if len(parts[0]) <= 10 and parts[0] in APPROVAL_CATEGORIES:
            cat, title, detail = parts[0], parts[1], parts[2]
        else:
            cat, title, detail = 'general', parts[0], parts[1]
        out.append({'category': cat, 'title': title, 'detail': detail})
    return out


def extract_mentions(text):
    """Extract @mentions, e.g. ['CEO', 'CTO']."""
    return [m.group(1) for m in RE_MENTION.finditer(text)]


def has_system_command(text):
    """True if text contains any [TASK_|[APPROVAL:|[CRON_ command."""
    return bool(RE_HAS_COMMAND.search(text))


def has_mention(text):
    """True if text contains @mention."""
    return bool(RE_MENTION.search(text))
