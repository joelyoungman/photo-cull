"""Build the Mac download: dist/PhotoCull-mac.zip.

On a Mac (CI builds the releases there) the zip holds "Install Photo Cull.app", a double-click installer made with
osacompile, with everything it installs inside it (Contents/Resources/payload). On other systems, for development
only, the payload goes in a plain folder instead.

  python3 scripts/build_zip.py
"""
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist" / "PhotoCull-mac.zip"
STAGE = ROOT / "build" / "Photo Cull"
APP_FILES = ["pyproject.toml", "uv.lock", ".python-version", "THIRD-PARTY.md"]
APP_DIRS = ["photocull", "models", "mac"]
EXECUTABLE = {"install-mac.sh", "launcher.sh"}


def payload_files():
    """(source, path inside the payload) for everything the installer installs."""
    yield ROOT / "mac" / "install-mac.sh", "install-mac.sh"
    for name in APP_FILES:
        yield ROOT / name, f"app/{name}"
    for d in APP_DIRS:
        for path in sorted((ROOT / d).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                yield path, f"app/{path.relative_to(ROOT).as_posix()}"


def copy_payload(dest):
    for src, rel in payload_files():
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
        target.chmod(0o755 if src.name in EXECUTABLE else 0o644)


def run(*cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)


def build_mac():
    shutil.rmtree(STAGE.parent, ignore_errors=True)
    STAGE.mkdir(parents=True)
    app = STAGE / "Install Photo Cull.app"
    run("osacompile", "-o", str(app), str(ROOT / "mac" / "installer.applescript"))
    resources = app / "Contents" / "Resources"
    copy_payload(resources / "payload")
    plist = str(app / "Contents" / "Info.plist")
    run("plutil", "-replace", "CFBundleIdentifier", "-string", "nz.co.gbiz.photocull.installer", plist)
    iconset = STAGE.parent / "PhotoCull.iconset"
    iconset.mkdir()
    for s in (16, 32, 128, 256, 512):
        for scale, suffix in ((1, ""), (2, "@2x")):
            run("sips", "-z", str(s * scale), str(s * scale), str(ROOT / "mac" / "icon.png"),
                "--out", str(iconset / f"icon_{s}x{s}{suffix}.png"))
    run("iconutil", "-c", "icns", str(iconset), "-o", str(resources / "applet.icns"))
    subprocess.run(["plutil", "-remove", "CFBundleIconName", plist], capture_output=True)
    (resources / "Assets.car").unlink(missing_ok=True)
    run("codesign", "--force", "--deep", "--sign", "-", str(app))
    shutil.copyfile(ROOT / "mac" / "Read me first.txt", STAGE / "Read me first.txt")
    OUT.unlink(missing_ok=True)
    run("ditto", "-c", "-k", "--keepParent", str(STAGE), str(OUT))  # keeps permissions and the signature


def build_plain():
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for src, rel in payload_files():
            info = zipfile.ZipInfo.from_file(src, f"Photo Cull/{rel}")
            info.external_attr = (0o100000 | (0o755 if src.name in EXECUTABLE else 0o644)) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, src.read_bytes())


def main():
    OUT.parent.mkdir(exist_ok=True)
    if sys.platform == "darwin":
        build_mac()
    else:
        print("not a Mac: building a development zip without the installer app")
        build_plain()
    print(OUT, f"{OUT.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
