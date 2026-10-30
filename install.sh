#!/bin/bash
# Install or update Photo Cull from the latest release, without the download/unzip/"Open Anyway" steps.
# Used by Photo Cull's Update button; also works as one line in Terminal:
#
#   curl -fsSL https://github.com/joelyoungman/photo-cull/releases/latest/download/install.sh | bash
set -euo pipefail

ZIP="${PHOTOCULL_ZIP:-https://github.com/joelyoungman/photo-cull/releases/latest/download/PhotoCull-mac.zip}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Downloading Photo Cull…"
curl -fsSL "$ZIP" -o "$TMP/PhotoCull-mac.zip" || { echo "Couldn't download Photo Cull: is the Mac online?"; exit 1; }
ditto -x -k "$TMP/PhotoCull-mac.zip" "$TMP"
PAYLOAD="$TMP/Photo Cull/Install Photo Cull.app/Contents/Resources/payload"
[ -d "$PAYLOAD" ] || PAYLOAD="$TMP/Photo Cull"  # development zip built off a Mac
bash "$PAYLOAD/install-mac.sh"
