"""End-to-end check of the analysis on the generated sample shoot (no real photos).

  uv run pytest -q
"""
import os
from pathlib import Path

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
