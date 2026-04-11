#!/bin/bash
# Auto-restart server on file changes
cd "$(dirname "$0")/.."
SERVER_PID=""

restart() {
    if [ -n "$SERVER_PID" ]; then
        kill $SERVER_PID 2>/dev/null
        wait $SERVER_PID 2>/dev/null
    fi
    echo "[$(date +%H:%M:%S)] 🔄 Restarting server..."
    PYTHONUNBUFFERED=1 python3 dashboard/server.py </dev/null > /tmp/sabujak.log 2>&1 &
    SERVER_PID=$!
    sleep 2
    if kill -0 $SERVER_PID 2>/dev/null; then
        echo "[$(date +%H:%M:%S)] ✅ Server running (PID $SERVER_PID)"
    else
        echo "[$(date +%H:%M:%S)] ❌ Server failed to start"
        tail -5 /tmp/sabujak.log
    fi
}

trap "kill $SERVER_PID 2>/dev/null; exit" INT TERM

restart
LAST=$(find dashboard/server.py dashboard/index.html -newer /tmp/sabujak.log 2>/dev/null | head -1)

while true; do
    sleep 3
    NEW=$(find dashboard/server.py dashboard/index.html -newer /tmp/sabujak.log 2>/dev/null | head -1)
    if [ -n "$NEW" ]; then
        # Verify syntax before restart
        python3 -m py_compile dashboard/server.py 2>/dev/null
        if [ $? -eq 0 ]; then
            restart
        else
            echo "[$(date +%H:%M:%S)] ⚠️ Syntax error, skipping restart"
            python3 -m py_compile dashboard/server.py 2>&1 | tail -3
        fi
    fi
done
