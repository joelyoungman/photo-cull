"""Build the Mac download: dist/PhotoCull-mac.zip. Standard library only, runs on any OS.

  python scripts/build_zip.py
"""
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist" / "PhotoCull-mac.zip"
TOP = "Photo Cull"
APP_FILES = ["pyproject.toml", "uv.lock", ".python-version", "THIRD-PARTY.md"]
APP_DIRS = ["photocull", "models", "mac"]
EXECUTABLE = {"Install Photo Cull.command", "Uninstall Photo Cull.command", "launcher.sh"}


def add(z, path, arcname):
    info = zipfile.ZipInfo.from_file(path, arcname)
    mode = 0o755 if path.name in EXECUTABLE else 0o644
    info.external_attr = (0o100000 | mode) << 16  # regular file + permissions, so the .command stays runnable
    info.compress_type = zipfile.ZIP_DEFLATED
    z.writestr(info, path.read_bytes())


def main():
    OUT.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(OUT, "w") as z:
        for name in ("Install Photo Cull.command", "Uninstall Photo Cull.command"):
            add(z, ROOT / "mac" / name, f"{TOP}/{name}")
        add(z, ROOT / "mac" / "Read me first.txt", f"{TOP}/Read me first.txt")
        for name in APP_FILES:
            add(z, ROOT / name, f"{TOP}/app/{name}")
        for d in APP_DIRS:
            for path in sorted((ROOT / d).rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts and not path.name.endswith(".command"):
                    add(z, path, f"{TOP}/app/{path.relative_to(ROOT).as_posix()}")
    print(OUT, f"{OUT.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
