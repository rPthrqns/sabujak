#!/bin/bash
# Clean stale openclaw session locks (PID no longer alive)
find /home/sra/.openclaw -name "*.lock" | while read lock; do
  pid=$(cat "$lock" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | head -1)
  if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$lock"
    echo "Cleaned stale lock: $lock (pid=$pid dead)"
  fi
done
