"""End-to-end check of the analysis on the generated sample shoot (no real photos).

  uv run pytest -q
"""
import os

import pytest

from tests.make_samples import make


@pytest.fixture(scope="module")
def job(tmp_path_factory):
    os.environ["PHOTOCULL_HOME"] = str(tmp_path_factory.mktemp("home"))
    shoot = make(tmp_path_factory.mktemp("shoot"))
    from photocull.job import Job

    j = Job(shoot)
    j.analyse()
    return j


def by_name(job):
    return {s["name"]: s for s in job.shots()["shots"]}


def test_everything_read(job):
    assert job.progress["phase"] == "done"
    assert job.progress["errors"] == 0
    assert len(by_name(job)) == 9


def test_burst_grouped_and_best_suggested(job):
    s = by_name(job)
    burst = {n for n in s if s[n]["group"] == s["DSC00101"]["group"]}
    assert burst == {"DSC00101", "DSC00102", "DSC00103", "DSC00104"}
    best = [n for n in burst if s[n]["suggested"]]
    assert best in (["DSC00101"], ["DSC00104"])
    assert "eyes open" in s[best[0]]["reasons"]


def test_scenes_stay_apart(job):
    s = by_name(job)
    assert len({s[f"DSC0011{i}"]["group"] for i in range(3)}) == 3


def test_problems_flagged(job):
    s = by_name(job)
    assert "blurry" in s["DSC00102"]["problems"]
    assert "eyes closed" in s["DSC00103"]["problems"]
    assert "too dark" in s["DSC00120"]["problems"]
    assert "blurry" in s["DSC00121"]["problems"]
    assert s["DSC00110"]["problems"] == []


def test_marks_and_selects(job):
    job.mark(["cardA/DSC00104"], rating=4, flag="pick")
    assert by_name(job)["DSC00104"]["flag"] == "pick"
    first = job.copy_selects(["cardA/DSC00104"])
    assert first["copied"] == ["DSC00104.JPG"] and not first["failed"]
    again = job.copy_selects(["cardA/DSC00104"])
    assert again["copied"] == [] and again["skipped"] == ["DSC00104.JPG"]
    assert (job.root / "DSC00104.JPG").exists() is False  # nothing moved out of the card folder
    assert (job.root / "cardA" / "DSC00104.JPG").exists()


def test_reopen_is_incremental(job):
    from photocull.job import Job

    again = Job(job.root)
    again.analyse()
    assert again.progress["total"] == 0  # nothing re-analysed
    assert by_name(again)["DSC00104"]["rating"] == 4


def test_raw_preview_turned_upright(monkeypatch, tmp_path):
    """A RAW whose preview is stored sideways, with the camera's orientation only in the RAW itself."""
    import io

    from PIL import Image

    from photocull import photos

    buf = io.BytesIO()
    Image.new("RGB", (300, 200), "white").save(buf, "JPEG")
    monkeypatch.setattr(photos, "_raw_preview", lambda path: (buf.getvalue(), (6000, 4000), 6))
    img, size, _ = photos.open_photo(tmp_path / "P1000001.RW2")
    assert img.size == (200, 300) and size == (4000, 6000)


def test_failed_copy_leaves_nothing(job, monkeypatch):
    import shutil

    def half_copy(src, dst):
        with open(dst, "wb") as f:
            f.write(b"partial")
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "copy2", half_copy)
    result = job.copy_selects(["cardA/DSC00110"])
    assert result["failed"] and not (job.root / "Selects" / "DSC00110.JPG").exists()


def test_two_cameras_at_once_keep_their_bursts():
    from photocull.job import _group

    def shot(cam, t, n):
        return {"key": f"{cam}/P{n}", "camera": cam, "serial": None, "t": t, "phash": None, "vec": None}

    a = [shot("A", t, i) for i, t in enumerate((0.0, 0.5, 1.0))]
    b = [shot("B", t, i) for i, t in enumerate((0.2, 0.7, 1.2))]
    groups = _group(sorted(a + b, key=lambda r: r["t"]))
    assert sorted(len(g) for g in groups) == [3, 3]


def test_photoshop_sidecar_goes_to_selects(job):
    """A RAW's .xmp (Camera Raw / Photoshop edits) is copied with it, refreshed when the original is edited again,
    and never overwrites edits made later to the Selects copy."""
    import os
    import time

    card = job.root / "cardA"
    (card / "DSC00111.ARW").write_bytes(b"raw")
    (card / "DSC00111.xmp").write_text("edit 1", "utf-8")
    job.analyse()
    key = "cardA/DSC00111"
    job.copy_selects([key])
    side = job.root / "Selects" / "DSC00111.xmp"
    assert side.read_text("utf-8") == "edit 1"
    assert job.selects_copies([key])[key].name == "DSC00111.ARW"

    (card / "DSC00111.xmp").write_text("edit 2", "utf-8")
    later = time.time() + 5
    os.utime(card / "DSC00111.xmp", (later, later))
    job.copy_selects([key])
    assert side.read_text("utf-8") == "edit 2"

    side.write_text("edited in Selects", "utf-8")
    os.utime(side, (later + 5, later + 5))
    job.copy_selects([key])
    assert side.read_text("utf-8") == "edited in Selects"


def test_rejects_to_deletes_and_back(job):
    """Rejects move (never delete) into <shoot>/Deletes with their sidecars, and can be put back."""
    from photocull import job as jobs

    card = job.root / "cardA"
    (card / "DSC00112.ARW").write_bytes(b"raw")
    (card / "DSC00112.xmp").write_text("edits", "utf-8")
    job.analyse()
    key = "cardA/DSC00112"
    r = jobs.move_to_deletes(job, [key])
    assert sorted(r["moved"]) == ["DSC00112.ARW", "DSC00112.JPG", "DSC00112.xmp"] and not r["failed"]
    assert not (card / "DSC00112.JPG").exists() and (job.root / "Deletes" / "DSC00112.JPG").exists()
    assert key not in {s["key"] for s in job.shots()["shots"]}
    assert jobs.moved_count(job) == 3
    job.analyse()  # files in Deletes are not analysed as part of the shoot
    assert key not in {s["key"] for s in job.shots()["shots"]}

    back = jobs.put_back(job)
    job._thread.join()
    assert sorted(back["restored"]) == ["DSC00112.ARW", "DSC00112.JPG", "DSC00112.xmp"]
    assert (card / "DSC00112.xmp").read_text("utf-8") == "edits"
    assert key in {s["key"] for s in job.shots()["shots"]} and jobs.moved_count(job) == 0


def test_sharpness_score(job):
    s = by_name(job)
    assert all(0 <= x["sharp_score"] <= 100 for x in s.values() if x.get("sharp_score") is not None)
    assert s["DSC00121"]["sharp_score"] < s["DSC00110"]["sharp_score"]  # the blurred scene vs a sharp one
    assert s["DSC00102"]["sharp_score"] < s["DSC00101"]["sharp_score"]  # blurred burst frame vs the sharp one


def test_exclude_a_person(job):
    """Click a face, find the same person across the burst, mark those X, undo."""
    from photocull import job as jobs

    s = by_name(job)
    face = s["DSC00101"]["face_boxes"][0][6]
    matches = {m["key"]: m for m in jobs.find_person(job, "cardA/DSC00101", face)}
    assert {"cardA/DSC00101", "cardA/DSC00104"} <= {k for k, m in matches.items() if m["likely"]}
    assert not any(k.startswith("cardA/DSC0011") for k in matches)  # the scenes have no faces

    keys = [k for k, m in matches.items() if m["likely"]]
    assert by_name(job)["DSC00104"]["flag"] == "pick"  # picked earlier in these tests
    jobs.exclude_person(job, "cardA/DSC00101", face, keys)
    s = by_name(job)
    assert s["DSC00104"]["excluded"] and s["DSC00104"]["flag"] == "reject"
    people = jobs.excluded_people(job)
    assert len(people) == 1 and people[0]["photos"] == len(keys)
    assert jobs.face_crop(job, "cardA/DSC00101", face)[:2] == b"\xff\xd8"  # a JPEG

    jobs.stop_excluding(job, people[0]["id"])
    s = by_name(job)
    assert not s["DSC00104"]["excluded"] and s["DSC00104"]["flag"] == "pick"  # back to the pick it had before
