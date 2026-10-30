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
# The page may only load from and talk to this server, so nothing in it can send photos anywhere.
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")

state = {"job": None}
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
        script = ('set f to choose folder with prompt "Choose the shoot folder to cull"\n'
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


def open_with(path, app=None):
    """Open a file in Photoshop (or the default app), or show it in Finder/Explorer."""
    path = str(path)
    if sys.platform == "darwin":
        if app == "reveal":
            return subprocess.run(["open", "-R", path]).returncode == 0
        if app == "photoshop":
            if subprocess.run(["open", "-b", "com.adobe.Photoshop", path]).returncode == 0:
                return True
        return subprocess.run(["open", path]).returncode == 0
    if sys.platform == "win32":
        if app == "reveal":
            subprocess.run(["explorer", "/select,", path])
            return True
        os.startfile(path)  # Windows: RAW/JPEG open in whatever app is set for them
        return True
    return False


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
    return JSONResponse({"app": "photo-cull", "pid": os.getpid()})


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
    data = await run_in_threadpool(j.shots)
    return JSONResponse({"path": str(j.root), "progress": j.progress, **data})


async def image(request):
    j = need_job()
    size = request.query_params.get("size", "s")
    key = request.query_params.get("key", "")
    if size not in jobs.THUMB_PX:
        raise ValueError("bad size")
    path = j.thumb(key, size)
    if not path.is_file():
        return Response(status_code=404)
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
                             "files": [{"from": f, "to": t.name, "exists": t.exists()} for _, f, t in plan]})
    result = await run_in_threadpool(j.copy_selects, keys, bool(body.get("write_ratings", True)))
    return JSONResponse(result)


async def open_file(request):
    j = need_job()
    body = await request.json()
    key = known_keys(j, [body["key"]])[0]
    shot = next(s for s in j.shots()["shots"] if s["key"] == key)
    files = shot["files"]
    raw = next((f for f in files if Path(f).suffix.lower() in jobs.photos.RAW_EXTS), None)
    target = j.root / (raw or files[0])  # Photoshop: the RAW opens in Camera Raw
    ok = await run_in_threadpool(open_with, target, body.get("app"))
    return JSONResponse({"ok": ok, "file": target.name})


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
    Route("/api/reanalyse", endpoint(reanalyse), methods=["POST"]),
    Route("/api/quit", endpoint(quit_app), methods=["POST"]),
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
