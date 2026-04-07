"""OpenClaw subprocess-based agent runtime."""
import subprocess, threading, os, signal, time, tempfile
from pathlib import Path
from .base import AgentRuntime


class OpenClawRuntime(AgentRuntime):
    """Executes agents via the `openclaw` CLI subprocess."""

    def run(self, agent_id: str, session_id: str, prompt: str,
            timeout: int = 120) -> str:
        """Run openclaw and poll session JSONL for assistant response.
        openclaw --local doesn't output to stdout in subprocess and process hangs,
        so we monitor all JSONL files in sessions/ for new assistant messages."""
        import json as _json
        sessions_dir = Path.home() / '.openclaw' / 'agents' / agent_id / 'sessions'
        # Snapshot: record current last assistant message across all JSONL files
        def _get_last_assistant():
            if not sessions_dir.exists():
                return None, 0
            for f in sorted(sessions_dir.glob('*.jsonl'), key=lambda x: x.stat().st_mtime, reverse=True):
                try:
                    lines = f.read_text(errors='replace').strip().split('\n')
                    for line in reversed(lines):
                        entry = _json.loads(line)
                        if entry.get('type') == 'message':
                            msg = entry.get('message', {})
                            if msg.get('role') == 'assistant':
                                return entry.get('id'), len(lines)
                except Exception:
                    pass
            return None, 0
        pre_id, _ = _get_last_assistant()
        # Launch openclaw (stdout goes nowhere — we read from JSONL)
        proc = subprocess.Popen(
            ['openclaw', 'agent', '--agent', agent_id,
             '--session-id', session_id, '--local', '-m', prompt],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Poll for new assistant message
        start = time.time()
        result = ''
        while time.time() - start < timeout:
            time.sleep(2)
            for f in sorted(sessions_dir.glob('*.jsonl'), key=lambda x: x.stat().st_mtime, reverse=True):
                try:
                    lines = f.read_text(errors='replace').strip().split('\n')
                    for line in reversed(lines):
                        entry = _json.loads(line)
                        if entry.get('type') == 'message':
                            msg = entry.get('message', {})
                            if msg.get('role') == 'assistant' and entry.get('id') != pre_id:
                                contents = msg.get('content', [])
                                texts = [c.get('text', '') for c in contents if c.get('type') == 'text']
                                if texts:
                                    result = '\n'.join(texts).strip()
                                    break
                except Exception:
                    pass
                if result:
                    break
            if result:
                break
        # Kill process
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
        if not result:
            raise subprocess.TimeoutExpired(['openclaw', 'agent'], timeout)
        return result

    def register(self, agent_id: str, workspace: str,
                 soul_content: str = '') -> bool:
        """Register an agent workspace with openclaw."""
        try:
            result = subprocess.run(
                ['openclaw', 'agents', 'add', agent_id,
                 '--workspace', workspace, '--non-interactive'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
            )
            return result.returncode == 0
        except Exception:
            return False

    def delete(self, agent_id: str) -> bool:
        """Remove an agent from openclaw."""
        try:
            result = subprocess.run(
                ['openclaw', 'agents', 'delete', agent_id, '--force'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
            )
            return result.returncode == 0
        except Exception:
            return False

    def list_registered(self) -> str:
        """Return raw output of `openclaw agents list`."""
        try:
            result = subprocess.run(
                ['openclaw', 'agents', 'list'],
                capture_output=True, text=True, timeout=20,
            )
            return result.stdout or ''
        except Exception:
            return ''
