#!/bin/bash
# What "Photo Cull.app" runs: start Photo Cull in the background (or reuse the running one) and open the browser.
APP_HOME="$HOME/Library/Application Support/Photo Cull"
LOG="$HOME/Library/Logs/Photo Cull.log"
mkdir -p "$(dirname "$LOG")"
cd "$APP_HOME/app" || { osascript -e 'display alert "Photo Cull isn'"'"'t installed properly" message "Run the installer again."'; exit 1; }
nohup "$APP_HOME/venv/bin/python" -m photocull --open >> "$LOG" 2>&1 &
