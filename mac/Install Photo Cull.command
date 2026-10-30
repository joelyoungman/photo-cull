#!/bin/bash
# Photo Cull installer for macOS. Double-click it (the first time, macOS asks you to allow it: see "Read me first").
# Everything goes in ~/Library/Application Support/Photo Cull, plus "Photo Cull" in Applications.
# Nothing else on the Mac is changed, and no admin password is needed.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
APP_HOME="$HOME/Library/Application Support/Photo Cull"
APPS="${PHOTOCULL_APPS_DIR:-/Applications}"
UV_VERSION="0.13.6"
PYTHON_VERSION="3.12"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\n\033[31mInstall failed: %s\033[0m\n' "$*"; echo "Take a screenshot of this window and send it to Joel."; exit 1; }
trap 'fail "a step stopped unexpectedly (line $LINENO)"' ERR

say "Installing Photo Cull…"
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

# 4. The "Photo Cull" app: a tiny launcher that starts Photo Cull and opens it in the browser.
say "Adding Photo Cull to Applications…"
if ! mkdir -p "$APPS" 2>/dev/null || [ ! -w "$APPS" ]; then APPS="$HOME/Applications"; mkdir -p "$APPS"; fi
BUNDLE="$APPS/Photo Cull.app"
rm -rf "$BUNDLE"
mkdir -p "$BUNDLE/Contents/MacOS" "$BUNDLE/Contents/Resources"
cp "$HERE/app/mac/launcher.sh" "$BUNDLE/Contents/MacOS/Photo Cull"
chmod +x "$BUNDLE/Contents/MacOS/Photo Cull"
cp "$HERE/app/mac/Info.plist" "$BUNDLE/Contents/Info.plist"
ICONSET="$(mktemp -d)/PhotoCull.iconset"
mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z $s $s "$HERE/app/mac/icon.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null 2>&1 || true
  sips -z $((s*2)) $((s*2)) "$HERE/app/mac/icon.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null 2>&1 || true
done
iconutil -c icns "$ICONSET" -o "$BUNDLE/Contents/Resources/PhotoCull.icns" 2>/dev/null || true
# Files we made here never came from the internet, so macOS won't ask about them again.
xattr -dr com.apple.quarantine "$BUNDLE" "$APP_HOME/app" 2>/dev/null || true
# A free "ad-hoc" signature made on this Mac (no Apple account): newer macOS refuses to open apps with none at all
# (error -10669). Then tell macOS's app list about it.
codesign --force --sign - "$BUNDLE" >/dev/null 2>&1 || echo "(couldn't sign the app; it may still open)"
LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
[ -x "$LSREGISTER" ] && "$LSREGISTER" -f "$BUNDLE" >/dev/null 2>&1 || true
touch "$BUNDLE"

say "Done! Photo Cull is in $APPS."
echo "Open it like any app (Launchpad, Spotlight, or the Applications folder). It opens in your web browser."
if [ -z "${PHOTOCULL_NO_OPEN:-}" ]; then
  if ! open "$BUNDLE" 2>/dev/null; then
    # Installed fine, but macOS wouldn't open the app this time: start Photo Cull directly instead.
    "$BUNDLE/Contents/MacOS/Photo Cull" || true
  fi
  echo "You can close this window."
fi
