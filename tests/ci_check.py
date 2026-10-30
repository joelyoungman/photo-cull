"""Drive an installed, running Photo Cull through its HTTP API, the way the page does, and check the results.
Used by the macOS GitHub Actions job after installing from the zip.

  python tests/ci_check.py SHOOT_FOLDER
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8765"
HEADERS = {"Content-Type": "application/json", "X-Photo-Cull": "1"}


def call(path, body=None):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                                 headers=HEADERS, method="GET" if body is None else "POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def rss_mb(pid):
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return int(out) / 1024 if out else 0


def analyse(shoot, pid):
    """Open a shoot and wait for the analysis: (progress, peak MB of the Photo Cull process)."""
    start = time.time()
    call("/api/open", {"path": shoot})
    peak = 0
    while True:
        p = call("/api/status")["job"]["progress"]
        peak = max(peak, rss_mb(pid))
        if p["phase"] in ("done", "failed", "stopped") and p["started"] and p["started"] >= start - 1:
            return p, peak
        if time.time() - start > 900:
            sys.exit(f"analysis took too long: {p}")
        time.sleep(0.5)


def main(shoot, big=None):
    for _ in range(120):
        try:
            pid = call("/api/ping")["pid"]
            break
        except Exception:
            time.sleep(1)
    else:
        sys.exit("Photo Cull didn't start")
    page = urllib.request.urlopen(urllib.request.Request(BASE + "/"), timeout=10).read()
    assert b"Photo Cull" in page, "review page missing"

    p, peak = analyse(shoot, pid)
    assert p["phase"] == "done", p
    shots = {s["name"]: s for s in call("/api/shots")["shots"]}
    for s in shots.values():
        print(f"{s['name']:10} group={s['group']} score={s.get('score')} {'BEST ' if s.get('suggested') else ''}"
              f"{s.get('reasons') or ''} {s.get('problems') or ''} {s.get('error') or ''}")

    errors = [s for s in shots.values() if s["error"]]
    assert not errors, errors
    burst = {n for n, s in shots.items() if s["group"] == shots["DSC00101"]["group"]}
    assert burst == {"DSC00101", "DSC00102", "DSC00103", "DSC00104"}, burst
    assert any(shots[n]["suggested"] for n in ("DSC00101", "DSC00104")), "best of burst not suggested"
    assert "blurry" in shots["DSC00102"]["problems"]
    assert "eyes closed" in shots["DSC00103"]["problems"], shots["DSC00103"]
    assert "too dark" in shots["DSC00120"]["problems"]
    assert "P1000123" in shots and "DSC09009" in shots, "RAW files (RW2/ARW) not read"
    assert shots["P1000123"]["camera"] == "Panasonic DC-GH5" and shots["DSC09009"]["camera"] == "Sony ILCE-7M3"

    call("/api/mark", {"keys": ["cardA/DSC00104", "cardC/DSC09009"], "flag": "pick", "rating": 4})
    done = call("/api/selects", {"keys": ["cardA/DSC00104", "cardC/DSC09009"]})
    assert sorted(done["copied"]) == ["DSC00104.JPG", "DSC09009.ARW", "DSC09009.JPG"] and not done["failed"], done
    assert os.path.exists(os.path.join(shoot, "Selects", "DSC09009.xmp"))
    assert os.path.exists(os.path.join(shoot, "cardC", "DSC09009.ARW")), "original moved!"

    seconds = p["seconds"]
    summary = (f"### Photo Cull on macOS\n\n- {len(shots)} photos analysed in {seconds} s "
               f"({seconds / len(shots):.2f} s per photo, CPU only: GitHub's Macs have no GPU access)\n"
               f"- peak memory of the Photo Cull process: {peak:.0f} MB\n- burst grouped, best suggested, blur / eyes closed / "
               f"dark flagged, RW2 + ARW read, picks copied to Selects with a rating sidecar\n")
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(summary)
    call("/api/quit", {})


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
