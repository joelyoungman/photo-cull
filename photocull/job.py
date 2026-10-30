"""One shoot ("job"): analysing its photos, grouping bursts and look-alikes, suggesting the best of each
group, remembering the photographer's ratings, and copying picks to a Selects folder.

Nothing of ours is written inside the shoot folder except the Selects copies: shoots often live in a
syncing Google Drive folder, where a database or thumbnails would fight the sync. Our state lives in
the app's own data folder, one sub-folder per shoot (see state_root).
"""
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np

from . import photos, quality

ANALYSIS_V = 1  # bump to re-analyse every shot after changing what we measure
WORKERS = 2  # MacBook Neo: 2 performance cores; more threads only make the fan and Drive downloads fight
THUMB_PX = {"s": 400, "l": 2048}
SELECTS = "Selects"

# Grouping: shots are taken in time order; a shot joins the group before it when it's the same camera and
BURST_GAP = 1.5  # ...within this many seconds of the previous frame (a burst, GH5 6K/4K Photo included)
MOMENT_GAP = 45  # ...or within this many seconds and looks like a recent member (same moment, re-framed)
MOMENT_SPAN = 180  # a look-alike group never spans longer than this
LOOKALIKE_PHASH = 20  # pHash bits that may differ (out of 64)...
LOOKALIKE_COS = 0.80  # ...with the embedder agreeing it's the same scene


def state_root():
    if os.environ.get("PHOTOCULL_HOME"):
        return Path(os.environ["PHOTOCULL_HOME"])
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Photo Cull"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Photo Cull"
    return Path.home() / ".local" / "share" / "photo-cull"


def recent_jobs():
    """[{path, name, opened}] newest first, for folders that still exist."""
    out = []
    for f in (state_root() / "jobs").glob("*/job.json"):
        try:
            info = json.loads(f.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if Path(info["path"]).is_dir():
            out.append({"path": info["path"], "name": Path(info["path"]).name, "opened": info.get("opened", 0)})
    return sorted(out, key=lambda j: -j["opened"])


def online_only(path):
    """True for a Google Drive (or iCloud) file that's still in the cloud: reading it downloads it first."""
    try:
        st = os.stat(path)
    except OSError:
        return False
    if sys.platform == "darwin":
        return bool(getattr(st, "st_flags", 0) & 0x40000000)  # SF_DATALESS
    if sys.platform == "win32":
        return bool(getattr(st, "st_file_attributes", 0) & (0x400000 | 0x40000 | 0x1000))  # RECALL_ON_*, OFFLINE
    return False


def gentle():
    """Below-normal priority for this whole process (call once at start-up), so analysing a shoot leaves
    the laptop responsive and cool."""
    try:
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
        else:
            os.nice(10)
    except Exception:
        pass
    try:
        import cv2

        cv2.setNumThreads(1)
    except Exception:
        pass


SCHEMA = """
CREATE TABLE IF NOT EXISTS shots (
  key TEXT PRIMARY KEY, files TEXT, source TEXT, sig TEXT, error TEXT,
  taken TEXT, camera TEXT, serial TEXT, iso INTEGER, exposure TEXT, fnumber REAL, focal REAL,
  width INTEGER, height INTEGER, sharpness REAL, brightness REAL, dark REAL, blown REAL,
  faces TEXT, phash TEXT, vec BLOB
);
CREATE TABLE IF NOT EXISTS marks (key TEXT PRIMARY KEY, rating INTEGER DEFAULT 0, flag TEXT DEFAULT '', updated REAL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


class Job:
    def __init__(self, folder):
        self.root = Path(folder).expanduser().resolve()
        if not self.root.is_dir():
            raise FileNotFoundError(f"not a folder: {self.root}")
        digest = hashlib.sha1(str(self.root).lower().encode()).hexdigest()[:16]
        self.dir = state_root() / "jobs" / digest
        self.thumbs = self.dir / "thumbs"
        self.thumbs.mkdir(parents=True, exist_ok=True)
        (self.dir / "job.json").write_text(json.dumps({"path": str(self.root), "opened": time.time()}), "utf-8")
        self.lock = threading.Lock()  # one connection, shared by the server threads and the analysis
        self.db = sqlite3.connect(self.dir / "cull.db", check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        v = self.db.execute("SELECT value FROM meta WHERE key='analysis_v'").fetchone()
        if not v or int(v[0]) != ANALYSIS_V:
            self.db.execute("UPDATE shots SET sig = NULL")
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('analysis_v', ?)", (str(ANALYSIS_V),))
        self.progress = {"phase": "idle", "done": 0, "total": 0, "current": "", "downloading": False, "errors": 0,
                         "started": None, "finished": None, "seconds": None}
        self._cache = None  # ranked shot list, rebuilt after analysis or marks change
        self._thread = None
        self._stop = threading.Event()

    # ---------- analysis ----------

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._analyse_safely, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _analyse_safely(self):
        try:
            self.analyse()
        except Exception as e:  # keep the server up; show what went wrong
            self.progress.update(phase="failed", current=f"{type(e).__name__}: {e}")

    def analyse(self):
        p = self.progress
        p.update(phase="scanning", done=0, total=0, errors=0, started=time.time(), finished=None, seconds=None, current="")
        shots = photos.find_shots(self.root)
        with self.lock:
            known = dict(self.db.execute("SELECT key, sig FROM shots"))
            gone = [k for k in known if k not in shots]
            for k in gone:
                self.db.execute("DELETE FROM shots WHERE key = ?", (k,))
        todo = []
        for key, shot in shots.items():
            try:
                st = (self.root / shot.source).stat()
            except OSError:
                continue
            sig = f"{shot.source}|{st.st_size}|{int(st.st_mtime)}"
            if known.get(key) != sig:
                todo.append((shot, sig))
            else:  # the RAW/JPEG pairing may have changed without the source changing
                with self.lock:
                    self.db.execute("UPDATE shots SET files = ? WHERE key = ?", (json.dumps(shot.files), key))
        p.update(phase="analysing", total=len(todo))
        self._cache = None
        with ThreadPoolExecutor(WORKERS) as pool:
            for row in pool.map(self._measure, todo):
                if self._stop.is_set():
                    break
                with self.lock:
                    self.db.execute(f"INSERT OR REPLACE INTO shots ({','.join(row)}) VALUES ({','.join('?' * len(row))})",
                                    list(row.values()))
                p["done"] += 1
                p["errors"] += bool(row.get("error"))
                if p["done"] % 10 == 0:
                    self._cache = None
        self._cache = None
        p.update(phase="stopped" if self._stop.is_set() else "done", current="", downloading=False,
                 finished=time.time(), seconds=round(time.time() - p["started"], 1))

    def _measure(self, item):
        """One shot's database row. Errors are recorded with no sig, so the shot is retried next time."""
        shot, sig = item
        if self._stop.is_set():
            return {"key": shot.key, "files": json.dumps(shot.files), "source": shot.source, "sig": None, "error": "stopped"}
        path = self.root / shot.source
        self.progress["current"] = shot.source
        self.progress["downloading"] = online_only(path)
        row = {"key": shot.key, "files": json.dumps(shot.files), "source": shot.source, "sig": sig, "error": None}
        try:
            img, (w, h), preview = photos.open_photo(path)
            exif = photos.shot_exif(self.root, shot, preview)
            q = quality.assess(img)
            import imagehash

            vec = quality.embedding(img)
            row.update(taken=exif.get("taken"), camera=exif.get("camera"), serial=exif.get("serial"), iso=exif.get("iso"),
                       exposure=exif.get("exposure"), fnumber=exif.get("fnumber"), focal=exif.get("focal"),
                       width=w, height=h, sharpness=q["sharpness"], brightness=q["brightness"], dark=q["dark"],
                       blown=q["blown"], faces=json.dumps(q["faces"]), phash=str(imagehash.phash(img)),
                       vec=vec.astype(np.float32).tobytes())
            name = self._thumb_name(shot.key)
            for size, px in THUMB_PX.items():
                tmp = self.thumbs / f"{name}-{size}.tmp"
                photos.shrink(img, px).save(tmp, "JPEG", quality=85 if size == "s" else 90)
                tmp.replace(self.thumbs / f"{name}-{size}.jpg")
        except Exception as e:
            row.update(sig=None, error=_describe(path, e))
        return row

    @staticmethod
    def _thumb_name(key):
        return hashlib.sha1(key.encode()).hexdigest()

    def thumb(self, key, size):
        return self.thumbs / f"{self._thumb_name(key)}-{size}.jpg"

    # ---------- marks ----------

    def mark(self, keys, rating=None, flag=None):
        with self.lock:
            for key in keys:
                self.db.execute("INSERT OR IGNORE INTO marks (key) VALUES (?)", (key,))
                if rating is not None:
                    self.db.execute("UPDATE marks SET rating = ?, updated = ? WHERE key = ?", (int(rating), time.time(), key))
                if flag is not None:
                    self.db.execute("UPDATE marks SET flag = ?, updated = ? WHERE key = ?", (flag, time.time(), key))
        if self._cache is not None:
            marks = self._marks()
            for s in self._cache["shots"]:
                if s["key"] in keys:
                    s["rating"], s["flag"] = marks.get(s["key"], (0, ""))

    def _marks(self):
        with self.lock:
            return {k: (r or 0, f or "") for k, r, f in self.db.execute("SELECT key, rating, flag FROM marks")}

    # ---------- groups and suggestions ----------

    def shots(self):
        """{shots: [...in time order...], groups: {id: [keys]}} with each shot's group, score, problems and
        whether it's the suggested pick of its group."""
        if self._cache is None:
            self._cache = self._rank()
        return self._cache

    def _rank(self):
        with self.lock:
            cur = self.db.execute("SELECT * FROM shots")
            cols = [c[0] for c in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        marks = self._marks()
        ok = [r for r in rows if not r["error"] and r["sig"]]
        for r in ok:
            r["faces"] = json.loads(r["faces"] or "[]")
            r["vec"] = np.frombuffer(r["vec"], dtype=np.float32) if r["vec"] else None
            r["t"] = _seconds(r["taken"])
        ok.sort(key=lambda r: (r["t"] if r["t"] is not None else float("inf"), r["key"]))
        groups = _group(ok)
        sharp_median = float(np.median([r["sharpness"] for r in ok])) if ok else 0
        out = []
        for gid, members in enumerate(groups):
            scored = _score(members)
            best = max(scored, key=lambda s: s[1])[0] if len(members) > 1 else None
            for r, score, reasons in scored:
                out.append(_public(r, gid, len(members), score, r is best, reasons if r is best else [],
                                   _problems(r, sharp_median), marks.get(r["key"], (0, ""))))
        for r in rows:
            if r["error"] or not r["sig"]:
                out.append({"key": r["key"], "name": Path(r["key"]).name, "files": json.loads(r["files"]), "error": r["error"] or "not analysed yet",
                            "group": None, "rating": marks.get(r["key"], (0, ""))[0], "flag": marks.get(r["key"], (0, ""))[1]})
        return {"shots": out}

    # ---------- output ----------

    def selects_plan(self, keys):
        """[(shot key, source file, target file)] for copying these shots into Selects, and the notes about it."""
        dest = self.root / SELECTS
        by_key = {s["key"]: s for s in self.shots()["shots"]}
        plan, taken = [], set()
        for key in keys:
            shot = by_key.get(key)
            if not shot:
                continue
            files = list(shot["files"])
            for f in list(files):  # bring each RAW's Lightroom/Camera Raw sidecar along
                side = Path(f).with_suffix(".xmp")
                if Path(f).suffix.lower() in photos.RAW_EXTS and (self.root / side).exists():
                    files.append(side.as_posix())
            stem = Path(key).name
            n = 1
            while True:  # two cameras can both have a P1000123: never let one overwrite the other
                targets = [dest / (stem + Path(f).suffix) for f in files]
                clash = any(t.exists() and t.stat().st_size != (self.root / f).stat().st_size for t, f in zip(targets, files))
                if not clash and not any(t in taken for t in targets):
                    break
                n += 1
                stem = f"{Path(key).name} ({n})"
            taken.update(targets)
            plan.extend((key, f, t) for f, t in zip(files, targets))
        return plan

    def copy_selects(self, keys, write_ratings=True):
        """Copy the shots' files into <shoot>/Selects. Never overwrites, never touches the originals.
        A RAW without a sidecar gets a new .xmp next to its copy holding the star rating, so Bridge,
        Camera Raw (Photoshop) and Lightroom show it."""
        plan = self.selects_plan(keys)
        marks = self._marks()
        copied, skipped, failed = [], [], []
        (self.root / SELECTS).mkdir(exist_ok=True)
        for key, f, target in plan:
            src = self.root / f
            try:
                if target.exists():  # same size: an earlier copy
                    skipped.append(target.name)
                    continue
                shutil.copy2(src, target)  # final name straight away: Drive uploads one finished file
                if target.stat().st_size != src.stat().st_size:
                    raise OSError("copy is incomplete")
                copied.append(target.name)
            except OSError as e:
                failed.append(f"{Path(f).name}: {e}")
                continue
            if write_ratings and target.suffix.lower() in photos.RAW_EXTS:
                side = target.with_suffix(".xmp")
                if not side.exists() and not any(t == side for _, _, t in plan):
                    rating = marks.get(key, (0, ""))[0]
                    side.write_text(_xmp(rating), "utf-8")
        return {"folder": str(self.root / SELECTS), "copied": copied, "skipped": skipped, "failed": failed}


# ---------- helpers ----------

def _describe(path, e):
    if isinstance(e, (TimeoutError, OSError)) and online_only(path):
        return "couldn't download it from Google Drive (offline?) — will retry next time"
    if isinstance(e, FileNotFoundError):
        return "file has gone"
    try:
        if path.stat().st_size == 0:
            return "empty file (0 bytes)"
    except OSError:
        pass
    return f"can't read it: {e}"[:200]


def _seconds(taken):
    if not taken:
        return None
    try:
        return datetime.fromisoformat(taken).timestamp()
    except ValueError:
        return None


def _camera(r):
    """Which camera body: model + serial when the camera records one, else model + the card folder it came from."""
    return (r["camera"] or "?", r["serial"] or str(Path(r["key"]).parent))


def _phash_gap(a, b):
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def _look_alike(a, b):
    if a["phash"] and b["phash"] and _phash_gap(a["phash"], b["phash"]) <= LOOKALIKE_PHASH:
        return a["vec"] is None or b["vec"] is None or float(a["vec"] @ b["vec"]) >= LOOKALIKE_COS
    return a["vec"] is not None and b["vec"] is not None and float(a["vec"] @ b["vec"]) >= 0.93


def _group(ordered):
    """Split shots (in time order) into groups of one moment. A group only grows forward in time and only from
    shots like its recent members, so a long event never chains into one giant group."""
    groups = []
    for r in ordered:
        g = groups[-1] if groups else None
        if g and r["t"] is not None and g[-1]["t"] is not None and _camera(r) == _camera(g[-1]):
            gap = r["t"] - g[-1]["t"]
            if 0 <= gap <= BURST_GAP:
                g.append(r)
                continue
            if gap <= MOMENT_GAP and r["t"] - g[0]["t"] <= MOMENT_SPAN and any(_look_alike(r, m) for m in g[-3:]):
                g.append(r)
                continue
        groups.append([r])
    return groups


def _main_faces(r):
    return [f for f in r["faces"] if f["main"]]


def _score(members):
    """[(shot, score 0-100, reasons it's the best)] for one group. Within a group shots are compared with each other:
    sharpness of the faces (or the whole frame), everyone's eyes open, smiles, nobody missing, exposure."""
    faces = [_main_faces(r) for r in members]
    by_face = all(fs and all(f["sharp"] for f in fs) for fs in faces)
    sharp = [float(np.mean([f["sharp"] for f in fs])) if by_face else r["sharpness"] for r, fs in zip(members, faces)]
    top_sharp = max(sharp) or 1
    most_faces = max(len(fs) for fs in faces)
    out = []
    for r, fs, s in zip(members, faces, sharp):
        judged = [f for f in fs if f["closed"] is not None]
        eyes = sum(f["closed"] < quality.EYES_CLOSED for f in judged) / len(judged) if judged else 1
        smile = float(np.mean([f["smile"] for f in judged])) if judged else 0
        present = len(fs) / most_faces if most_faces else 1
        expo = 0 if quality.exposure_problem(r) else 1
        sharp_rel = (s / top_sharp) ** 0.5
        score = 40 * sharp_rel + 25 * eyes + 10 * smile + 15 * present + 10 * expo if most_faces else 75 * sharp_rel + 25 * expo
        reasons = []
        if s == top_sharp:
            reasons.append("sharpest faces" if by_face and most_faces else "sharpest")
        if judged and eyes == 1:
            reasons.append("everyone's eyes open" if len(judged) > 1 else "eyes open")
        smiling = sum(f["smile"] >= quality.SMILING for f in judged)
        if smiling:
            reasons.append(f"{smiling} of {len(judged)} smiling" if len(judged) > 1 else "smiling")
        if expo:
            reasons.append("well exposed")
        out.append((r, round(score, 1), reasons))
    return out


def _problems(r, sharp_median):
    out = []
    e = quality.exposure_problem(r)
    if e == "too dark":
        pass  # a dark frame has little contrast, so it always measures soft: don't call it blurry too
    elif r["sharpness"] < max(20.0, 0.25 * sharp_median):
        out.append("blurry")
    else:
        fs = [f for f in _main_faces(r) if f["sharp"] is not None]
        if fs and max(f["sharp"] for f in fs) < 25:
            out.append("faces out of focus")
    judged = [f for f in _main_faces(r) if f["closed"] is not None]
    closed = sum(f["closed"] >= quality.EYES_CLOSED for f in judged)
    if closed:
        out.append("eyes closed" if len(judged) == 1 else f"eyes closed ({closed} of {len(judged)})")
    if e:
        out.append(e)
    return out


def _public(r, gid, size, score, suggested, reasons, problems, mark):
    fs = _main_faces(r)
    judged = [f for f in fs if f["closed"] is not None]
    return {
        "key": r["key"], "name": Path(r["key"]).name, "files": json.loads(r["files"]), "source": r["source"],
        "taken": r["taken"], "camera": r["camera"], "iso": r["iso"], "exposure": r["exposure"], "fnumber": r["fnumber"],
        "focal": r["focal"], "width": r["width"], "height": r["height"],
        "sharpness": r["sharpness"], "brightness": r["brightness"],
        "faces": len(r["faces"]), "main_faces": len(fs),
        "eyes_open": sum(f["closed"] < quality.EYES_CLOSED for f in judged), "smiling": sum(f["smile"] >= quality.SMILING for f in judged),
        "face_boxes": [f["box"] + [1 if f["closed"] is not None and f["closed"] >= quality.EYES_CLOSED else 0] for f in fs],
        "group": gid, "group_size": size, "score": score, "suggested": suggested, "reasons": reasons, "problems": problems,
        "rating": mark[0], "flag": mark[1], "error": None,
    }


def _xmp(rating):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Photo Cull">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:Rating="{int(rating)}"/>
 </rdf:RDF>
</x:xmpmeta>
"""
