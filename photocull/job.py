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

ANALYSIS_V = 2  # bump to re-analyse every shot after changing what we measure
WORKERS = 2  # at most 2 cores busy (measured: 1.7 on average); the rest of the computer stays free
THUMB_PX = {"s": 400, "l": quality.ANALYSE_PX}  # RAW previews are only 1600-1900 px anyway
FOCUS_FROM, FOCUS_FULL = 15.0, 45.0  # local sharpness where the focus overlay starts to glow, and where it's full
THUMB_DAYS = 60  # previews of shoots not opened for this long are deleted (marks are kept; they rebuild on reopen)
SELECTS = "Selects"
DELETES = "Deletes"  # rejects are MOVED here (never deleted); every move is recorded so it can be put back

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


def prune_old_thumbs(keep=None):
    """Delete the previews of shoots not opened for THUMB_DAYS, so a year of jobs doesn't fill a small SSD.
    Marks (cull.db) stay; previews are made again if the shoot is reopened."""
    cutoff = time.time() - THUMB_DAYS * 86400
    for f in (state_root() / "jobs").glob("*/job.json"):
        try:
            opened = json.loads(f.read_text("utf-8")).get("opened", 0)
        except (OSError, ValueError):
            continue
        if opened < cutoff and f.parent != keep:
            shutil.rmtree(f.parent / "thumbs", ignore_errors=True)


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

        cv2.setNumThreads(1)  # no hidden extra threads inside OpenCV
    except Exception:
        pass


def background_thread():
    """Mark the calling (analysis) thread as background work. On a Mac this is the 'utility' quality of service:
    macOS runs it on the efficiency cores when it can, gives the screen and browser first call on the fast
    cores, and slows it down further if the laptop gets warm. Threads it starts (MediaPipe's) inherit it."""
    if sys.platform != "darwin":
        return
    try:
        import ctypes

        libc = ctypes.CDLL("/usr/lib/libSystem.dylib")
        libc.pthread_set_qos_class_self_np(ctypes.c_uint(0x11), ctypes.c_int(0))  # QOS_CLASS_UTILITY
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
CREATE TABLE IF NOT EXISTS moves (dst TEXT PRIMARY KEY, src TEXT, key TEXT, at REAL);
CREATE TABLE IF NOT EXISTS face_vecs (key TEXT, i INTEGER, sig TEXT, vec BLOB, PRIMARY KEY (key, i));
CREATE TABLE IF NOT EXISTS people_out (id INTEGER PRIMARY KEY, key TEXT, i INTEGER, vec BLOB, created REAL);
CREATE TABLE IF NOT EXISTS excluded (key TEXT, person INTEGER, sim REAL, PRIMARY KEY (key, person));
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
        self._gen = 0  # bumped on every change, so a ranking computed from older data is never kept
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
                for size in THUMB_PX:
                    self.thumb(k, size).unlink(missing_ok=True)
                (self.thumbs / f"{self._thumb_name(k)}-focus.png").unlink(missing_ok=True)
        todo = []
        for key, shot in shots.items():
            try:
                st = (self.root / shot.source).stat()
            except OSError:
                continue
            sig = f"{shot.source}|{st.st_size}|{int(st.st_mtime)}"
            if known.get(key) != sig or not self.thumb(key, "s").exists():  # new, changed, or previews pruned
                todo.append((shot, sig))
            else:  # the RAW/JPEG pairing may have changed without the source changing
                with self.lock:
                    self.db.execute("UPDATE shots SET files = ? WHERE key = ?", (json.dumps(shot.files), key))
        p.update(phase="analysing", total=len(todo))
        self._invalidate()
        with ThreadPoolExecutor(WORKERS, initializer=background_thread) as pool:
            for row in pool.map(self._measure, todo):
                if self._stop.is_set():
                    break
                with self.lock:
                    self.db.execute(f"INSERT OR REPLACE INTO shots ({','.join(row)}) VALUES ({','.join('?' * len(row))})",
                                    list(row.values()))
                p["done"] += 1
                p["errors"] += bool(row.get("error"))
                if p["done"] % 10 == 0:
                    self._invalidate()
        self._invalidate()
        if not self._stop.is_set():
            try:
                check_excluded(self)
            except Exception as e:  # never let this spoil the analysis itself
                print(f"checking excluded people failed: {e!r}", flush=True)
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
            img = photos.shrink(img, quality.ANALYSE_PX)
            q = quality.assess(img)
            row.update(taken=exif.get("taken"), camera=exif.get("camera"), serial=exif.get("serial"), iso=exif.get("iso"),
                       exposure=exif.get("exposure"), fnumber=exif.get("fnumber"), focal=exif.get("focal"),
                       width=w, height=h, sharpness=q["sharpness"], brightness=q["brightness"], dark=q["dark"],
                       blown=q["blown"], faces=json.dumps(q["faces"]), phash=q["phash"],
                       vec=q["vec"].astype(np.float32).tobytes())
            name = self._thumb_name(shot.key)
            for size, px in THUMB_PX.items():
                tmp = self.thumbs / f"{name}-{size}.tmp"
                photos.shrink(img, px).save(tmp, "JPEG", quality=85)
                tmp.replace(self.thumbs / f"{name}-{size}.jpg")
        except Exception as e:
            row.update(sig=None, error=_describe(path, e))
        return row

    @staticmethod
    def _thumb_name(key):
        return hashlib.sha1(key.encode()).hexdigest()

    def thumb(self, key, size):
        return self.thumbs / f"{self._thumb_name(key)}-{size}.jpg"

    def focus_map(self, key):
        """A transparent PNG, the size of the large preview, that glows green where the photo is in focus (like a
        camera's focus peaking). The scale is fixed, not stretched per photo, so a blurry photo barely glows."""
        import cv2
        from PIL import Image

        src = self.thumb(key, "l")
        out = self.thumbs / f"{self._thumb_name(key)}-focus.png"
        if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
            return out
        gray = np.asarray(Image.open(src).convert("L"), dtype=np.float32)
        energy = cv2.GaussianBlur(np.abs(cv2.Laplacian(gray, cv2.CV_32F, ksize=3)), (0, 0), 6)
        alpha = np.clip((energy - FOCUS_FROM) / (FOCUS_FULL - FOCUS_FROM), 0, 1) * 170
        rgba = np.zeros(gray.shape + (4,), np.uint8)
        rgba[..., 0], rgba[..., 1], rgba[..., 2] = 40, 255, 90
        rgba[..., 3] = alpha.astype(np.uint8)
        tmp = out.with_suffix(".tmp")
        Image.fromarray(rgba, "RGBA").save(tmp, "PNG", optimize=True)
        tmp.replace(out)
        return out

    # ---------- marks ----------

    def mark(self, keys, rating=None, flag=None):
        with self.lock:
            for key in keys:
                self.db.execute("INSERT OR IGNORE INTO marks (key) VALUES (?)", (key,))
                if rating is not None:
                    self.db.execute("UPDATE marks SET rating = ?, updated = ? WHERE key = ?", (int(rating), time.time(), key))
                if flag is not None:
                    self.db.execute("UPDATE marks SET flag = ?, updated = ? WHERE key = ?", (flag, time.time(), key))
        self._invalidate()

    def _marks(self):
        with self.lock:
            return {k: (r or 0, f or "") for k, r, f in self.db.execute("SELECT key, rating, flag FROM marks")}

    # ---------- groups and suggestions ----------

    def _invalidate(self):
        self._gen += 1
        self._cache = None

    def shots(self):
        """{shots: [...in time order...]} with each shot's group, score, problems and whether it's the suggested
        pick of its group."""
        cache = self._cache
        if cache is None:
            gen = self._gen
            cache = self._rank()
            if gen == self._gen:  # nothing changed while ranking (the analysis may still be writing)
                self._cache = cache
        return cache

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
        scores = _sharp_scores(ok)
        with self.lock:
            excluded = {k for (k,) in self.db.execute("SELECT DISTINCT key FROM excluded")}
        for s in out:
            s["sharp_score"] = scores.get(s["key"])
            s["excluded"] = s["key"] in excluded
        for r in rows:
            if r["error"] or not r["sig"]:
                out.append({"key": r["key"], "name": Path(r["key"]).name, "files": json.loads(r["files"]), "error": r["error"] or "not analysed yet",
                            "group": None, "rating": marks.get(r["key"], (0, ""))[0], "flag": marks.get(r["key"], (0, ""))[1]})
        return {"shots": out}

    # ---------- output ----------

    def selects_plan(self, keys):
        """[(shot key, source file, target file, kind)] for copying these shots into Selects. kind is "photo", or
        "sidecar" for a RAW's .xmp from Camera Raw / Photoshop / Lightroom (its edits and rating)."""
        dest = self.root / SELECTS
        by_key = {s["key"]: s for s in self.shots()["shots"]}
        plan, taken = [], set()
        for key in keys:
            shot = by_key.get(key)
            if not shot:
                continue
            files = list(shot["files"])
            sides = [Path(f).with_suffix(".xmp").as_posix() for f in files
                     if Path(f).suffix.lower() in photos.RAW_EXTS and (self.root / Path(f).with_suffix(".xmp")).exists()]
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
            plan.extend((key, f, t, "photo") for f, t in zip(files, targets))
            plan.extend((key, f, dest / (stem + ".xmp"), "sidecar") for f in sides)
        return plan

    def selects_copies(self, keys):
        """{key: the shot's copy in Selects to open (the RAW if there is one)} for shots already copied there."""
        out = {}
        for key, f, target, kind in self.selects_plan(keys):
            if kind == "photo" and target.exists() and (key not in out or target.suffix.lower() in photos.RAW_EXTS):
                out[key] = target
        return out

    def copy_selects(self, keys, write_ratings=True):
        """Copy the shots' files into <shoot>/Selects. Never touches the originals. Photos are never overwritten.
        A RAW's .xmp sidecar (Camera Raw / Photoshop edits) always comes along, and is refreshed when the original's
        is newer, but never replaces edits made later on the Selects copy. A RAW without one gets a new .xmp holding
        the star rating, so Bridge, Camera Raw and Lightroom show it."""
        plan = self.selects_plan(keys)
        marks = self._marks()
        copied, skipped, failed = [], [], []
        (self.root / SELECTS).mkdir(exist_ok=True)
        for key, f, target, kind in plan:
            src = self.root / f
            if target.exists() and (kind == "photo" or target.stat().st_mtime >= src.stat().st_mtime):
                skipped.append(target.name)  # an earlier copy (same size), or a sidecar edited since
                continue
            try:
                shutil.copy2(src, target)  # final name straight away: Drive uploads one finished file
                if target.stat().st_size != src.stat().st_size:
                    raise OSError("copy is incomplete")
                copied.append(target.name)
            except OSError as e:
                if kind == "photo":
                    try:  # never leave a half-copied photo behind; it didn't exist before this attempt
                        target.unlink(missing_ok=True)
                    except OSError:
                        pass
                failed.append(f"{Path(f).name}: {e}")
                continue
            if write_ratings and kind == "photo" and target.suffix.lower() in photos.RAW_EXTS:
                side = target.with_suffix(".xmp")
                if not side.exists() and not any(t == side for _, _, t, _ in plan):
                    rating = marks.get(key, (0, ""))[0]
                    side.write_text(_xmp(rating), "utf-8")
        return {"folder": str(self.root / SELECTS), "copied": copied, "skipped": skipped, "failed": failed}


# ---------- Deletes: set rejects aside, reversibly ----------

def _deletes_plan(job, keys):
    """[(shot key, source file, target file)] for moving these shots, sidecars included, into <shoot>/Deletes.
    Names never collide with something already there: a whole shot gets " (2)" etc. instead."""
    dest = job.root / DELETES
    by_key = {s["key"]: s for s in job.shots()["shots"]}
    plan, taken = [], set()
    for key in keys:
        shot = by_key.get(key)
        if not shot:
            continue
        files = list(shot["files"])
        files += [Path(f).with_suffix(".xmp").as_posix() for f in shot["files"]
                  if Path(f).suffix.lower() in photos.RAW_EXTS and (job.root / Path(f).with_suffix(".xmp")).exists()]
        stem, n = Path(key).name, 1
        while True:
            targets = [dest / (stem + Path(f).suffix) for f in files]
            if not any(t.exists() or t in taken for t in targets):
                break
            n += 1
            stem = f"{Path(key).name} ({n})"
        taken.update(targets)
        plan.extend((key, f, t) for f, t in zip(files, targets))
    return plan


def _move(src, dst):
    """Move one file without ever overwriting anything."""
    if dst.exists():
        raise FileExistsError(f"{dst.name} is already there")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))  # a rename within the shoot folder; copies only across drives


def move_to_deletes(job, keys):
    """Move these shots' files into <shoot>/Deletes. Nothing is deleted; each move is recorded for put_back."""
    moved, failed = [], []
    for key, f, target in _deletes_plan(job, keys):
        try:
            _move(job.root / f, target)
        except OSError as e:
            failed.append(f"{Path(f).name}: {e}")
            continue
        with job.lock:
            job.db.execute("INSERT OR REPLACE INTO moves VALUES (?, ?, ?, ?)",
                           (target.relative_to(job.root).as_posix(), f, key, time.time()))
            job.db.execute("DELETE FROM shots WHERE key = ?", (key,))
        moved.append(Path(f).name)
    job._invalidate()
    return {"folder": str(job.root / DELETES), "moved": moved, "failed": failed}


def put_back(job, keys=None):
    """Move files Photo Cull put in Deletes back where they came from (all of them, or these shots'). Files someone
    has since removed from Deletes, or whose old place is taken again, are left alone and reported."""
    with job.lock:
        rows = job.db.execute("SELECT dst, src, key FROM moves").fetchall()
    restored, failed = [], []
    for dst, src, key in rows:
        if keys is not None and key not in keys:
            continue
        d, s = job.root / dst, job.root / src
        if not d.exists():
            failed.append(f"{d.name}: no longer in Deletes")
        else:
            try:
                _move(d, s)
                restored.append(s.name)
            except OSError as e:
                failed.append(f"{d.name}: {e}")
                continue
        with job.lock:
            job.db.execute("DELETE FROM moves WHERE dst = ?", (dst,))
    job.start()  # put-back photos are analysed again (their previews went when they were moved)
    return {"restored": restored, "failed": failed}


def moved_count(job):
    """How many recorded moves still have their file sitting in Deletes."""
    with job.lock:
        rows = job.db.execute("SELECT dst FROM moves").fetchall()
    return sum((job.root / d).exists() for (d,) in rows)


# ---------- excluding a person (someone who asked not to be in any photos) ----------

SAME_PERSON = 0.363  # SFace cosine similarity: OpenCV's recommended "same person" threshold
MAYBE_PERSON = 0.28  # shown for her to decide, but not ticked


def _face_vecs(job, keys=None):
    """{(key, i): unit vector} for every face in the shoot (or these shots), worked out from the large previews the
    first time and kept. Faces too small to recognise are left out."""
    model = quality.sface_model(state_root() / "models")
    with job.lock:
        rows = job.db.execute("SELECT key, sig, faces FROM shots WHERE error IS NULL AND sig IS NOT NULL").fetchall()
        have = {(k, i): (s, v) for k, i, s, v in job.db.execute("SELECT key, i, sig, vec FROM face_vecs")}
    out = {}
    for key, sig, faces in rows:
        if keys is not None and key not in keys:
            continue
        faces = json.loads(faces or "[]")
        if not faces:
            continue
        if all(have.get((key, i), (None,))[0] == sig for i in range(len(faces))):
            vecs = [have[(key, i)][1] for i in range(len(faces))]
            vecs = [np.frombuffer(v, dtype=np.float32) if v else None for v in vecs]
        else:
            from PIL import Image

            try:
                img = Image.open(job.thumb(key, "l")).convert("RGB")
            except OSError:
                continue
            vecs = quality.face_vectors(img, [f["box"] for f in faces], model)
            with job.lock:
                for i, v in enumerate(vecs):
                    job.db.execute("INSERT OR REPLACE INTO face_vecs VALUES (?, ?, ?, ?)",
                                   (key, i, sig, v.astype(np.float32).tobytes() if v is not None else None))
        for i, v in enumerate(vecs):
            if v is not None:
                out[(key, i)] = v
    return out


def find_person(job, key, i):
    """Every photo in the shoot that seems to include the person whose face is face i of shot key: [{key, i, sim,
    likely}] best match per photo, most alike first. 'likely' ones are ticked for her; the rest are 'maybe'."""
    vecs = _face_vecs(job)
    target = vecs.get((key, i))
    if target is None:
        raise ValueError("that face is too small or unclear to recognise; try a photo where they're bigger")
    best = {}
    for (k, j), v in vecs.items():
        sim = float(target @ v)
        if sim >= MAYBE_PERSON and sim > best.get(k, (None, -1))[1]:
            best[k] = (j, sim)
    best[key] = (i, 1.0)
    return sorted(({"key": k, "i": j, "sim": round(s, 3), "likely": s >= SAME_PERSON} for k, (j, s) in best.items()),
                  key=lambda m: -m["sim"])


def exclude_person(job, key, i, keys):
    """Remember this person as excluded and mark these photos X with an "excluded person" badge."""
    vec = _face_vecs(job, {key}).get((key, i))
    if vec is None:
        raise ValueError("that face is too small or unclear to recognise")
    with job.lock:
        cur = job.db.execute("INSERT INTO people_out (key, i, vec, created) VALUES (?, ?, ?, ?)",
                             (key, i, vec.astype(np.float32).tobytes(), time.time()))
        person = cur.lastrowid
        for k in keys:
            job.db.execute("INSERT OR REPLACE INTO excluded VALUES (?, ?, ?)", (k, person, None))
    job.mark(keys, flag="reject")
    return {"person": person, "marked": len(keys)}


def check_excluded(job):
    """After an analysis: photos added since that clearly show an excluded person get marked X too."""
    with job.lock:
        people = job.db.execute("SELECT id, vec FROM people_out").fetchall()
        done = {(k, p) for k, p in job.db.execute("SELECT key, person FROM excluded")}
        seen = {k for (k,) in job.db.execute("SELECT DISTINCT key FROM face_vecs")}
        keys = {k for (k,) in job.db.execute("SELECT key FROM shots WHERE error IS NULL AND sig IS NOT NULL")}
    if not people:
        return
    new = keys - seen  # shots whose faces haven't been compared yet
    if not new:
        return
    vecs = _face_vecs(job, new)
    hits = []
    for person, pv in people:
        pv = np.frombuffer(pv, dtype=np.float32)
        for (k, _), v in vecs.items():
            if (k, person) not in done and float(pv @ v) >= SAME_PERSON:
                hits.append((k, person, float(pv @ v)))
                done.add((k, person))
    if hits:
        with job.lock:
            for k, person, sim in hits:
                job.db.execute("INSERT OR REPLACE INTO excluded VALUES (?, ?, ?)", (k, person, sim))
        job.mark(sorted({k for k, _, _ in hits}), flag="reject")


def excluded_people(job):
    with job.lock:
        rows = job.db.execute("SELECT p.id, p.key, p.i, COUNT(e.key) FROM people_out p LEFT JOIN excluded e "
                              "ON e.person = p.id GROUP BY p.id ORDER BY p.created").fetchall()
    return [{"id": pid, "key": k, "i": i, "photos": n} for pid, k, i, n in rows]


def stop_excluding(job, person):
    """Forget an excluded person. Their photos lose the badge, and lose the X unless another excluded person is in them."""
    with job.lock:
        keys = [k for (k,) in job.db.execute("SELECT key FROM excluded WHERE person = ?", (person,))]
        job.db.execute("DELETE FROM excluded WHERE person = ?", (person,))
        job.db.execute("DELETE FROM people_out WHERE id = ?", (person,))
        still = {k for (k,) in job.db.execute("SELECT DISTINCT key FROM excluded")}
    unmark = [k for k in keys if k not in still]
    marks = job._marks()
    job.mark([k for k in unmark if marks.get(k, (0, ""))[1] == "reject"], flag="")
    return {"unmarked": len(unmark)}


def face_crop(job, key, i):
    """A small square JPEG of face i in shot key, cut from the large preview, for the review list."""
    import io

    from PIL import Image

    with job.lock:
        row = job.db.execute("SELECT faces FROM shots WHERE key = ?", (key,)).fetchone()
    faces = json.loads(row[0] or "[]") if row else []
    if not 0 <= i < len(faces):
        raise ValueError("no such face")
    img = Image.open(job.thumb(key, "l"))
    x, y, w, h = faces[i]["box"]
    cx, cy, side = (x + w / 2) * img.width, (y + h / 2) * img.height, max(w * img.width, h * img.height) * 1.6
    crop = img.crop((int(cx - side / 2), int(cy - side / 2), int(cx + side / 2), int(cy + side / 2))).convert("RGB")
    crop.thumbnail((160, 160))
    buf = io.BytesIO()
    crop.save(buf, "JPEG", quality=85)
    return buf.getvalue()


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
    shots like its recent members, so a long event never chains into one giant group. Each camera body has its
    own open group, so two cameras firing at the same moment don't break each other's bursts."""
    groups, open_group = [], {}
    for r in ordered:
        cam = _camera(r)
        g = open_group.get(cam)
        if g and r["t"] is not None and g[-1]["t"] is not None:
            gap = r["t"] - g[-1]["t"]
            if 0 <= gap <= BURST_GAP:
                g.append(r)
                continue
            if gap <= MOMENT_GAP and r["t"] - g[0]["t"] <= MOMENT_SPAN and any(_look_alike(r, m) for m in g[-3:]):
                g.append(r)
                continue
        open_group[cam] = [r]
        groups.append(open_group[cam])
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


def _sharp_scores(shots):
    """{key: 0-100} sharpness compared with the rest of this shoot: 90 = sharper than 90% of it. Photos of people
    are scored on their faces against the other photos of people (focus on the face is what matters there); the
    rest on their sharpest part against the whole shoot. Raw sharpness numbers mean little on their own."""
    def percentiles(values):
        ordered = sorted(values.values())
        n = len(ordered)
        if n < 2:
            return {k: 100 for k in values}
        return {k: round(100 * float(np.searchsorted(ordered, v, side="left")) / (n - 1)) for k, v in values.items()}

    frame = percentiles({r["key"]: r["sharpness"] for r in shots})
    faces = {}
    for r in shots:
        fs = [f["sharp"] for f in _main_faces(r) if f["sharp"] is not None]
        if fs:
            faces[r["key"]] = max(fs)
    by_face = percentiles(faces) if len(faces) >= 3 else {}
    return {k: by_face.get(k, v) for k, v in frame.items()}


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
        # every face, background ones too: [x, y, w, h, eyes shut, main face, index]
        "face_boxes": [f["box"] + [1 if f["closed"] is not None and f["closed"] >= quality.EYES_CLOSED else 0, int(f["main"]), i]
                       for i, f in enumerate(r["faces"])],
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
