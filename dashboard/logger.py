"""Centralized logging.

Wraps Python logging so that existing print('[tag] msg') calls in server.py
can keep working while we incrementally migrate to log.info(...) style.

Usage:
    from logger import log
    log.info("[task_cmds] %s: %s", agent_id, msg)

Configuration via env:
    LOG_LEVEL=DEBUG|INFO|WARNING|ERROR  (default INFO)
    LOG_FILE=/path/to/file              (default None — stderr only)
"""
import logging
import os
import sys

LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
LOG_FILE = os.environ.get('LOG_FILE', '')

_FMT = '%(asctime)s %(levelname)-5s %(message)s'
_DATE = '%H:%M:%S'

handlers: list[logging.Handler] = []
stream = logging.StreamHandler(sys.stdout)
stream.setFormatter(logging.Formatter(_FMT, datefmt=_DATE))
handlers.append(stream)

if LOG_FILE:
    try:
        from logging.handlers import RotatingFileHandler
        fh = RotatingFileHandler(LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=3)
        fh.setFormatter(logging.Formatter(_FMT, datefmt=_DATE))
        handlers.append(fh)
    except Exception as e:
        print(f"[logger] failed to attach file handler: {e}", file=sys.stderr)

logging.basicConfig(level=getattr(logging, LEVEL, logging.INFO), handlers=handlers, force=True)

log = logging.getLogger('aichub')
