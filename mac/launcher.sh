#!/bin/bash
# What "Photo Cull.app" runs: start Photo Cull in the background (or reuse the running one) and open the browser.
APP_HOME="$HOME/Library/Application Support/Photo Cull"
LOG="$HOME/Library/Logs/Photo Cull.log"
mkdir -p "$(dirname "$LOG")"
cd "$APP_HOME/app" || { osascript -e 'display alert "Photo Cull isn'"'"'t installed properly" message "Run the installer again."'; exit 1; }
nohup "$APP_HOME/venv/bin/python" -m photocull --open >> "$LOG" 2>&1 &

# Wait for it to answer (the first start after installing takes longest), and say so if it never does,
# rather than leaving her clicking an app that seems to do nothing.
for _ in $(seq 1 120); do
  curl -fs --max-time 1 http://127.0.0.1:8765/api/ping 2>/dev/null | grep -q photo-cull && exit 0
  sleep 0.5
done
osascript -e 'display alert "Photo Cull didn'"'"'t start" message "Another program may be using its address (port 8765), or something went wrong. Restart the Mac and open Photo Cull again. If it still won'"'"'t start, send Joel the file Photo Cull.log from your Library/Logs folder."'
exit 1
