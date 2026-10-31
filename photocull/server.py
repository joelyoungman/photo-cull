"""Photo Cull's local web server: the review page and its small JSON API. Listens on 127.0.0.1 only.

  python -m photocull            start, then open http://localhost:8765
  python -m photocull --open     what the app runs: reuse a running copy, else start one and open the browser
"""
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from . import job as jobs

PORT = int(os.environ.get("PHOTOCULL_PORT", 8765))
HOSTS = {f"localhost:{PORT}", f"127.0.0.1:{PORT}"}
PAGE = Path(__file__).resolve().parent / "static" / "index.html"
APP_DIR = Path(__file__).resolve().parent.parent
REPO = "joelyoungman/photo-cull"
INSTALL_SH = f"https://github.com/{REPO}/releases/latest/download/install.sh"
# The page may only load from and talk to this server, so nothing in it can send photos anywhere.
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")

state = {"job": None, "latest": None, "checked": 0}
_open_lock = threading.Lock()


def current():
    return state["job"]


def open_job(path):
    with _open_lock:
        j = current()
        if j and j.root == Path(path).expanduser().resolve():
            j.start()  # picks up photos added since
            return j
        if j:
            j.stop()
        j = jobs.Job(path)
        state["job"] = j
        j.start()
        return j


def choose_folder():
    """The system's own folder picker. Returns a path, or None if cancelled."""
    if sys.platform == "darwin":
        script = ("activate\n"  # bring the picker in front of the browser, not behind it
                  'set f to choose folder with prompt "Choose the shoot folder to cull"\n'
                  "return POSIX path of f")
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        return r.stdout.strip() or None
    if sys.platform == "win32":
        ps = ("Add-Type -AssemblyName System.Windows.Forms; $d = New-Object System.Windows.Forms.FolderBrowserDialog; "
              "$d.Description = 'Choose the shoot folder to cull'; "
              "$f = New-Object System.Windows.Forms.Form -Property @{TopMost=$true}; "
              "if ($d.ShowDialog($f) -eq 'OK') { $d.SelectedPath }")
        r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps], capture_output=True, text=True)
        return r.stdout.strip() or None
    return None


def open_with(paths, app=None):
    """Open files in Photoshop (or the default app), show one in Finder/Explorer, or open a folder."""
    paths = [str(p) for p in (paths if isinstance(paths, (list, tuple)) else [paths])]
    if sys.platform == "darwin":
        if app == "reveal":
            return subprocess.run(["open", "-R", paths[0]]).returncode == 0
        if app == "photoshop":
            if subprocess.run(["open", "-b", "com.adobe.Photoshop", *paths]).returncode == 0:
                return True
        return subprocess.run(["open", *paths]).returncode == 0
    if sys.platform == "win32":
        if app == "reveal":
            subprocess.run(["explorer", "/select,", paths[0]])
            return True
        for p in paths:
            os.startfile(p)  # Windows: RAW/JPEG open in whatever app is set for them
        return True
    return False


def version():
    import tomllib

    try:
        return tomllib.loads((APP_DIR / "pyproject.toml").read_text("utf-8"))["project"]["version"]
    except (OSError, KeyError, ValueError):
        return "0"


def _v(text):
    return tuple(int(x) for x in text.lstrip("v").split(".") if x.isdigit())


def latest_version():
    """The newest release's version from GitHub, checked at most every 20 minutes. Only the version number is
    fetched; nothing about the photos is sent. None if offline."""
    import json
    import urllib.request

    if time.time() - state["checked"] > 20 * 60:
        state["checked"] = time.time()
        try:
            req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                         headers={"Accept": "application/vnd.github+json", "User-Agent": "photo-cull"})
            with urllib.request.urlopen(req, timeout=5) as r:
                state["latest"] = json.load(r)["tag_name"].lstrip("v")
        except Exception:
            pass
    return state["latest"]


def start_update():
    """Run the online installer in its own process: it stops this server, installs the latest release and starts
    Photo Cull again. The page waits and reloads. Output goes to the install log."""
    log = Path.home() / "Library" / "Logs" / "Photo Cull install.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a") as out:
        subprocess.Popen(["/bin/bash", "-c", f"curl -fsSL '{INSTALL_SH}' | PHOTOCULL_UPDATING=1 /bin/bash"],
                         stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)


# ---------- plumbing ----------

def guard(request):
    """Only answer pages served from this computer (no DNS rebinding); changes need a custom header,
    which another web page can't send without a CORS preflight this server never approves."""
    if request.headers.get("host") not in HOSTS:
        return JSONResponse({"error": "bad host"}, 403)
    if request.method == "POST" and request.headers.get("x-photo-cull") != "1":
        return JSONResponse({"error": "missing header"}, 403)
    return None


def endpoint(fn):
    async def wrapped(request):
        response = guard(request)
        if response is None:
            try:
                response = await fn(request)
            except (ValueError, KeyError, FileNotFoundError) as e:
                response = JSONResponse({"error": str(e)}, 400)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
    return wrapped


def need_job():
    j = current()
    if not j:
        raise ValueError("no shoot open")
    return j


def known_keys(j, keys):
    have = {s["key"] for s in j.shots()["shots"]}
    keys = [k for k in keys if k in have]
    if not keys:
        raise ValueError("no such photos")
    return keys


# ---------- routes ----------

async def page(request):
    return FileResponse(PAGE, headers={"Cache-Control": "no-store", "Content-Security-Policy": CSP})


async def ping(request):
    return JSONResponse({"app": "photo-cull", "pid": os.getpid(), "version": version()})


async def update(request):
    """GET: is there a newer version? POST: install it (Mac only)."""
    if request.method == "POST":
        if sys.platform != "darwin":
            raise ValueError("updating from the page only works on a Mac")
        await run_in_threadpool(start_update)
        return JSONResponse({"ok": True})
    current = version()
    if request.query_params.get("force"):
        state["checked"] = 0
    latest = await run_in_threadpool(latest_version) if sys.platform == "darwin" else None
    return JSONResponse({"current": current, "latest": latest,
                         "available": bool(latest and _v(latest) > _v(current))})


async def status(request):
    j = current()
    return JSONResponse({
        "job": {"path": str(j.root), "name": j.root.name, "progress": j.progress} if j else None,
        "recent": jobs.recent_jobs()[:8],
    })


async def choose(request):
    path = await run_in_threadpool(choose_folder)
    if not path:
        return JSONResponse({"cancelled": True})
    j = await run_in_threadpool(open_job, path)
    return JSONResponse({"path": str(j.root)})


async def open_folder(request):
    body = await request.json()
    j = await run_in_threadpool(open_job, body["path"])
    return JSONResponse({"path": str(j.root)})


async def shots(request):
    j = need_job()
    progress = dict(j.progress)  # read first: if it says "done", the shots below include every finished photo
    data = await run_in_threadpool(j.shots)
    moved = await run_in_threadpool(jobs.moved_count, j)
    return JSONResponse({"path": str(j.root), "progress": progress, "in_deletes": moved, **data})


async def image(request):
    j = need_job()
    size = request.query_params.get("size", "s")
    key = request.query_params.get("key", "")
    if size not in jobs.THUMB_PX and size != "focus":
        raise ValueError("bad size")
    if not j.thumb(key, "l").is_file():
        return Response(status_code=404)
    path = await run_in_threadpool(j.focus_map, key) if size == "focus" else j.thumb(key, size)
    return FileResponse(path, headers={"Cache-Control": "private, max-age=60"})


async def mark(request):
    j = need_job()
    body = await request.json()
    keys = known_keys(j, body["keys"])
    rating = body.get("rating")
    flag = body.get("flag")
    if rating is not None and int(rating) not in range(0, 6):
        raise ValueError("rating is 0-5")
    if flag is not None and flag not in ("", "pick", "reject"):
        raise ValueError("flag is pick, reject or empty")
    await run_in_threadpool(j.mark, keys, rating, flag)
    return JSONResponse({"ok": True})


async def selects(request):
    """dry_run: the list of files that would be copied; otherwise copy them."""
    j = need_job()
    body = await request.json()
    keys = known_keys(j, body["keys"])
    if body.get("dry_run"):
        plan = await run_in_threadpool(j.selects_plan, keys)
        return JSONResponse({"folder": str(j.root / jobs.SELECTS),
                             "files": [{"from": f, "to": t.name, "exists": t.exists(), "kind": k} for _, f, t, k in plan]})
    result = await run_in_threadpool(j.copy_selects, keys, bool(body.get("write_ratings", True)))
    return JSONResponse(result)


async def deletes(request):
    """action "plan": what moving these shots to Deletes would move; "move": move them; "putback": move back
    everything Photo Cull put in Deletes (or just these shots); "folder": show the Deletes folder."""
    j = need_job()
    body = await request.json()
    action = body.get("action")
    if action == "plan":
        plan = await run_in_threadpool(jobs._deletes_plan, j, known_keys(j, body["keys"]))
        return JSONResponse({"folder": str(j.root / jobs.DELETES),
                             "files": [{"from": f, "to": t.name} for _, f, t in plan]})
    if action == "move":
        return JSONResponse(await run_in_threadpool(jobs.move_to_deletes, j, known_keys(j, body["keys"])))
    if action == "putback":
        return JSONResponse(await run_in_threadpool(jobs.put_back, j, body.get("keys")))
    if action == "folder":
        folder = j.root / jobs.DELETES
        if not folder.is_dir():
            raise ValueError("there's no Deletes folder yet")
        return JSONResponse({"ok": await run_in_threadpool(open_with, folder)})
    raise ValueError("unknown action")


async def open_file(request):
    j = need_job()
    body = await request.json()
    key = known_keys(j, [body["key"]])[0]
    shot = next(s for s in j.shots()["shots"] if s["key"] == key)
    files = shot["files"]
    raw = next((f for f in files if Path(f).suffix.lower() in jobs.photos.RAW_EXTS), None)
    target = j.root / (raw or files[0])  # Photoshop: the RAW opens in Camera Raw
    copy = (await run_in_threadpool(j.selects_copies, [key])).get(key)
    if copy is not None:  # edit the copy in Selects, so the originals' folder stays untouched
        target = copy
    ok = await run_in_threadpool(open_with, target, body.get("app"))
    return JSONResponse({"ok": ok, "file": target.name, "in_selects": copy is not None})


async def open_selects(request):
    """what = "folder": show the Selects folder. Otherwise open these shots' Selects copies in Photoshop."""
    j = need_job()
    body = await request.json()
    folder = j.root / jobs.SELECTS
    if not folder.is_dir():
        raise ValueError("there's no Selects folder yet: copy some picks first")
    if body.get("what") == "folder":
        ok = await run_in_threadpool(open_with, folder)
        return JSONResponse({"ok": ok})
    copies = list((await run_in_threadpool(j.selects_copies, known_keys(j, body["keys"]))).values())
    if not copies:
        raise ValueError("none of these are in Selects yet")
    ok = await run_in_threadpool(open_with, copies, "photoshop")
    return JSONResponse({"ok": ok, "count": len(copies)})


async def reanalyse(request):
    need_job().start()
    return JSONResponse({"ok": True})


async def quit_app(request):
    j = current()
    if j:
        j.stop()
    threading.Timer(0.3, lambda: os._exit(0)).start()
    return JSONResponse({"ok": True})


app = Starlette(routes=[
    Route("/", endpoint(page)),
    Route("/api/ping", endpoint(ping)),
    Route("/api/status", endpoint(status)),
    Route("/api/choose", endpoint(choose), methods=["POST"]),
    Route("/api/open", endpoint(open_folder), methods=["POST"]),
    Route("/api/shots", endpoint(shots)),
    Route("/img", endpoint(image)),
    Route("/api/mark", endpoint(mark), methods=["POST"]),
    Route("/api/selects", endpoint(selects), methods=["POST"]),
    Route("/api/openfile", endpoint(open_file), methods=["POST"]),
    Route("/api/openselects", endpoint(open_selects), methods=["POST"]),
    Route("/api/deletes", endpoint(deletes), methods=["POST"]),
    Route("/api/reanalyse", endpoint(reanalyse), methods=["POST"]),
    Route("/api/quit", endpoint(quit_app), methods=["POST"]),
    Route("/api/update", endpoint(update), methods=["GET", "POST"]),
])


def already_running():
    import json
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/ping", timeout=1) as r:
            return json.load(r).get("app") == "photo-cull"
    except Exception:
        return False


def main(argv=None):
    import webbrowser

    argv = sys.argv[1:] if argv is None else argv
    if os.environ.get("PHOTOCULL_NO_BROWSER"):  # automated tests
        argv = [a for a in argv if a != "--open"]
    url = f"http://localhost:{PORT}"
    if already_running():
        if "--open" in argv:
            webbrowser.open(url)
        print(f"Photo Cull is already running: {url}")
        return
    jobs.gentle()
    threading.Thread(target=jobs.prune_old_thumbs, daemon=True).start()
    if "--open" in argv:
        def open_when_ready():
            for _ in range(100):
                if already_running():
                    webbrowser.open(url)
                    return
                time.sleep(0.2)
        threading.Thread(target=open_when_ready, daemon=True).start()
    print(f"Photo Cull: {url}  (Quit from the page, or close this window, to stop it)")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
