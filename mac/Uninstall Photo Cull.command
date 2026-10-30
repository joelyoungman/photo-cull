#!/bin/bash
# Removes Photo Cull from this Mac. Your photos and any Selects folders are not touched.
APP_HOME="$HOME/Library/Application Support/Photo Cull"
echo "This removes the Photo Cull app and its saved ratings and thumbnails."
echo "Your photos, and any Selects folders it made, stay exactly as they are."
read -r -p "Type yes to remove Photo Cull: " answer
[ "$answer" = "yes" ] || { echo "Nothing removed."; exit 0; }
curl -fsS -X POST -H "X-Photo-Cull: 1" http://127.0.0.1:8765/api/quit >/dev/null 2>&1 || true
rm -rf "/Applications/Photo Cull.app" "$HOME/Applications/Photo Cull.app" "$APP_HOME"
echo "Photo Cull is removed. You can close this window."
