"""Welcome message templates for new companies.

Loads translations from dashboard/i18n/welcome.json which is extended at
runtime by /api/i18n/generate when a new language is set up.

Fallback: Korean if language not found.
"""
import json
from pathlib import Path

_WELCOME_FILE = Path(__file__).resolve().parent.parent / "i18n" / "welcome.json"


def _load_all() -> dict:
    try:
        return json.loads(_WELCOME_FILE.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}


def welcome_msg(name: str, topic: str, agents: list, lang: str = 'ko') -> dict:
    """Build the localized welcome message bundle for a new company.

    Returns dict with keys: greeting, waiting, ready, log
    """
    team = ', '.join(a['name'] for a in agents[1:])
    all_msgs = _load_all()
    tpl = all_msgs.get(lang) or all_msgs.get('ko') or {
        'greeting_tpl': "Hello Master! I'm the CEO of '{name}'.\nTopic: {topic}\nTeam: {team}",
        'waiting': 'Preparing agents...',
        'ready': 'All ready!',
        'log_tpl': "'{name}' started.",
    }
    fmt_vars = {'name': name, 'topic': topic, 'team': team}
    return {
        'greeting': tpl.get('greeting_tpl', '').format(**fmt_vars),
        'waiting': tpl.get('waiting', ''),
        'ready': tpl.get('ready', ''),
        'log': tpl.get('log_tpl', '').format(**fmt_vars),
    }
