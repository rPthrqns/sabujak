"""OpenClaw subprocess-based agent runtime."""
import subprocess, threading, os, signal, time, tempfile
from pathlib import Path
from .base import AgentRuntime


class OpenClawRuntime(AgentRuntime):
    """Executes agents via the `openclaw` CLI subprocess."""

    def run(self, agent_id: str, session_id: str, prompt: str,
            timeout: int = 120) -> str:
        """Run openclaw and poll session JSONL for new assistant response.
        Detects new responses by checking mtime changes and reading last assistant entry."""
        import json as _json
        sessions_dir = Path.home() / '.openclaw' / 'agents' / agent_id / 'sessions'
        # Record last assistant message ID before launch
        pre_id = None
        if sessions_dir.exists():
            for sf in sorted(sessions_dir.glob('*.jsonl'), key=lambda x: x.stat().st_mtime, reverse=True):
                try:
                    for line in reversed(sf.read_text(errors='replace').strip().split('\n')):
                        entry = _json.loads(line)
                        if entry.get('type') == 'message' and entry.get('message',{}).get('role') == 'assistant':
                            pre_id = entry.get('id')
                            break
                except: pass
                if pre_id: break
        launch_ts = time.time()
        # Launch openclaw
        proc = subprocess.Popen(
            ['openclaw', 'agent', '--agent', agent_id,
             '--session-id', session_id, '--local', '-m', prompt],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Poll: check for JSONL files modified AFTER launch
        start = time.time()
        result = ''
        last_check_size = 0
        while time.time() - start < timeout:
            time.sleep(3)
            # Early exit: if process died immediately (bad args, missing binary)
            if proc.poll() is not None and time.time() - start > 5:
                rc = proc.returncode
                if rc != 0 and not result:
                    raise subprocess.TimeoutExpired(['openclaw', 'agent'], timeout,
                                                     f"process exited with code {rc}")
            if not sessions_dir.exists():
                continue
            for f in sorted(sessions_dir.glob('*.jsonl'), key=lambda x: x.stat().st_mtime, reverse=True):
                try:
                    st = f.stat()
                    # Only check files modified after launch
                    if st.st_mtime < launch_ts - 1:
                        continue
                    # Only check if file grew since last check
                    if st.st_size == last_check_size:
                        continue
                    last_check_size = st.st_size
                except OSError:
                    continue
                # Read file, find NEW assistant text (not seen before launch)
                try:
                    lines = f.read_text(errors='replace').strip().split('\n')
                    for line in reversed(lines):
                        if not line.strip():
                            continue
                        try:
                            entry = _json.loads(line)
                            if entry.get('type') == 'message':
                                msg = entry.get('message', {})
                                if msg.get('role') == 'assistant':
                                    eid = entry.get('id', '')
                                    if eid == pre_id:
                                        break  # Reached pre-launch message, stop
                                    texts = [c.get('text', '') for c in msg.get('content', []) if c.get('type') == 'text']
                                    candidate = '\n'.join(texts).strip() if texts else ''
                                    if candidate and candidate != 'NO_REPLY' and len(candidate) > 5:
                                        result = candidate
                                        break
                        except _json.JSONDecodeError:
                            pass
                except Exception:
                    pass
                if result:
                    break
            if result:
                break
        # Kill process (it hangs after responding)
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
        # Clean up any lock files left behind
        if sessions_dir.exists():
            for lf in sessions_dir.glob('*.lock'):
                try:
                    lf.unlink()
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
