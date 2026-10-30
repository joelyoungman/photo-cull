#!/bin/bash
# Photo Cull installer for macOS. Run by "Install Photo Cull.app" (double-click), by install.sh (one line in
# Terminal), or by the app's Update button. Everything goes in ~/Library/Application Support/Photo Cull, plus
# "Photo Cull" in Applications. Nothing else on the Mac is changed, and no admin password is needed.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
APP_HOME="$HOME/Library/Application Support/Photo Cull"
APPS="${PHOTOCULL_APPS_DIR:-/Applications}"
UV_VERSION="0.13.6"
PYTHON_VERSION="3.12"

say() {
  printf '\n\033[1m%s\033[0m\n' "$*"
  if [ -n "${PHOTOCULL_STATUS:-}" ]; then echo "$*" >> "$PHOTOCULL_STATUS"; fi  # the installer app's progress line
}
fail() {
  printf '\n\033[31mInstall failed: %s\033[0m\n' "$*"
  echo "Take a screenshot of this window and send it to Joel."
  # Updating from inside the app: bring the existing Photo Cull back rather than leave her with nothing.
  if [ -n "${PHOTOCULL_UPDATING:-}" ]; then PHOTOCULL_NO_BROWSER=1 "$APP_HOME/app/mac/launcher.sh" >/dev/null 2>&1 & fi
  exit 1
}
trap 'fail "a step stopped unexpectedly (line $LINENO)"' ERR

say "Installing Photo Cull…"
if [ -n "${PHOTOCULL_UPDATING:-}" ]; then
  # Started by the app's Update button: stop the running copy so nothing changes under it.
  curl -fs -X POST -H "X-Photo-Cull: 1" http://127.0.0.1:8765/api/quit >/dev/null 2>&1 || true
  for _ in $(seq 1 20); do curl -fs http://127.0.0.1:8765/api/ping >/dev/null 2>&1 || break; sleep 0.5; done
fi
case "$(uname -m)" in
  arm64) UV_ARCH="aarch64-apple-darwin" ;;
  x86_64) UV_ARCH="x86_64-apple-darwin" ;;
  *) fail "unknown Mac type $(uname -m)" ;;
esac
mkdir -p "$APP_HOME"

# 1. uv: a small tool that downloads the right Python and the libraries Photo Cull needs. Kept private to Photo Cull.
UV="$APP_HOME/uv/uv"
if [ ! -x "$UV" ] || [ "$("$UV" --version | awk '{print $2}')" != "$UV_VERSION" ]; then
  say "Downloading the installer helper (uv $UV_VERSION)…"
  TMP="$(mktemp -d)"
  URL="https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$UV_ARCH.tar.gz"
  curl -fsSL "$URL" -o "$TMP/uv.tar.gz" || fail "couldn't download uv: is the Mac online?"
  curl -fsSL "$URL.sha256" -o "$TMP/uv.sha256" || fail "couldn't download uv's checksum"
  WANT="$(awk '{print $1}' "$TMP/uv.sha256")"
  GOT="$(shasum -a 256 "$TMP/uv.tar.gz" | awk '{print $1}')"
  [ "$WANT" = "$GOT" ] || fail "uv download is damaged (checksum mismatch)"
  tar -xzf "$TMP/uv.tar.gz" -C "$TMP"
  mkdir -p "$APP_HOME/uv"
  mv -f "$TMP/uv-$UV_ARCH/uv" "$UV"
  rm -rf "$TMP"
fi

# 2. Photo Cull's own code and models.
say "Copying Photo Cull…"
mkdir -p "$APP_HOME/app"
rsync -a --delete "$HERE/app/" "$APP_HOME/app/"

# 3. Python and libraries (first install downloads ~250 MB; updates only what changed).
say "Setting up Python and libraries (a few minutes the first time)…"
export UV_PYTHON_INSTALL_DIR="$APP_HOME/python"
export UV_CACHE_DIR="$APP_HOME/cache"
export UV_PROJECT_ENVIRONMENT="$APP_HOME/venv"
"$UV" sync --frozen --no-dev --python "$PYTHON_VERSION" --managed-python --project "$APP_HOME/app" \
  || fail "couldn't set up the libraries: is the Mac online?"
"$UV" cache prune --ci >/dev/null 2>&1 || true

say "Checking it works…"
(cd "$APP_HOME/app" && "$APP_HOME/venv/bin/python" -c "import photocull.quality, photocull.server; print('ok')") \
  || fail "Photo Cull didn't start its self-check"

# 4. The "Photo Cull" app. It's built here by macOS's own AppleScript compiler (osacompile), which gives a native
#    Apple Silicon app (a shell-script app would make macOS ask to install Rosetta). All it does is run launcher.sh,
#    which starts Photo Cull in the background and opens the browser.
say "Adding Photo Cull to Applications…"
if ! mkdir -p "$APPS" 2>/dev/null || [ ! -w "$APPS" ]; then APPS="$HOME/Applications"; mkdir -p "$APPS"; fi
BUNDLE="$APPS/Photo Cull.app"
VERSION="$(grep -m1 '^version' "$HERE/app/pyproject.toml" | cut -d'"' -f2)"
chmod +x "$APP_HOME/app/mac/launcher.sh"
SCRIPT="$(mktemp -d)/Photo Cull.applescript"
cat > "$SCRIPT" <<'APPLESCRIPT'
set launcher to POSIX path of (path to library folder from user domain) & "Application Support/Photo Cull/app/mac/launcher.sh"
do shell script quoted form of launcher & " > /dev/null 2>&1 &"
APPLESCRIPT
rm -rf "$BUNDLE"
osacompile -o "$BUNDLE" "$SCRIPT" || fail "couldn't build the Photo Cull app"
PLIST="$BUNDLE/Contents/Info.plist"
plutil -replace CFBundleIdentifier -string "nz.co.gbiz.photocull" "$PLIST"
plutil -replace CFBundleName -string "Photo Cull" "$PLIST"
plutil -replace CFBundleShortVersionString -string "$VERSION" "$PLIST"
plutil -replace CFBundleVersion -string "$VERSION" "$PLIST"
plutil -replace LSUIElement -bool true "$PLIST"  # no Dock icon: Photo Cull itself lives in the browser
# Our icon instead of the AppleScript one.
ICONSET="$(mktemp -d)/PhotoCull.iconset"
mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z $s $s "$HERE/app/mac/icon.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null 2>&1 || true
  sips -z $((s*2)) $((s*2)) "$HERE/app/mac/icon.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null 2>&1 || true
done
if iconutil -c icns "$ICONSET" -o "$BUNDLE/Contents/Resources/applet.icns" 2>/dev/null; then
  plutil -remove CFBundleIconName "$PLIST" >/dev/null 2>&1 || true  # else macOS shows the icon in Assets.car
  rm -f "$BUNDLE/Contents/Resources/Assets.car"
  plutil -replace CFBundleIconFile -string "applet" "$PLIST"
fi
# Files we made here never came from the internet, so macOS won't ask about them again.
xattr -dr com.apple.quarantine "$BUNDLE" "$APP_HOME/app" 2>/dev/null || true
# Re-seal it with a free "ad-hoc" signature made on this Mac (no Apple account needed), then tell macOS about it.
codesign --force --deep --sign - "$BUNDLE" >/dev/null 2>&1 || echo "(couldn't sign the app; it may still open)"
LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
[ -x "$LSREGISTER" ] && "$LSREGISTER" -f "$BUNDLE" >/dev/null 2>&1 || true
touch "$BUNDLE"

say "Done! Photo Cull is in $APPS."
echo "Open it like any app (Launchpad, Spotlight, or the Applications folder). It opens in your web browser."
if [ -n "${PHOTOCULL_UPDATING:-}" ]; then
  # The browser tab that asked for the update is still open and reloads itself when Photo Cull is back.
  PHOTOCULL_NO_BROWSER=1 "$APP_HOME/app/mac/launcher.sh" || true
elif [ -z "${PHOTOCULL_NO_OPEN:-}" ]; then
  if ! open "$BUNDLE" 2>/dev/null; then
    # Installed fine, but macOS wouldn't open the app this time: start Photo Cull directly instead.
    "$APP_HOME/app/mac/launcher.sh" || true
  fi
  echo "You can close this window."
fi
