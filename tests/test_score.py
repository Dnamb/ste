"""Banded score, levels, review merge and the schema-1 result."""
import json
import random

import pytest

from checker import (Finding, Segment, apply_review, check_documents, counted,
                     enforced_tier, parse_level, score)


def segs(n: int) -> list[Segment]:
    return [Segment(id=f"s{i}", file="a.md", line=i, block=i, index=0, kind="desc",
                    text="The pump is on.", masked="The pump is on.", words=4)
            for i in range(1, n + 1)]


def find(seg: str, tier: int, conf: str = "high", **kw) -> Finding:
    return Finding(rule="1.1", tier=tier, conf=conf, message="m", seg=seg, file="a.md", **kw)


def test_levels():
    assert parse_level("strict") == 100 and parse_level(" Lite ") == 60
    assert parse_level("70") == 70
    for bad in ("101", "-1", "hard"):
        with pytest.raises(ValueError):
            parse_level(bad)
    assert [enforced_tier(n) for n in (0, 60, 61, 80, 81, 100)] == [60, 60, 80, 80, 100, 100]


@pytest.mark.parametrize("worst_tier,minimum", [(80, 60), (100, 80)])
def test_band_guarantee(worst_tier, minimum):
    """Every segment has a finding above preset P, but none at or below it: score >= P."""
    s = segs(5)
    found = [find(x.id, worst_tier) for x in s]
    assert score(s, found)["score"] >= minimum
    assert score(s, found)["score"] < 100


def test_only_clean_text_scores_100():
    s = segs(50)
    assert score(s, [])["score"] == 100
    assert score(s, [find("s7", 100)])["score"] < 100


def test_monotonic():
    rng = random.Random(7)
    s = segs(20)
    found: list[Finding] = []
    last = score(s, found)["score"]
    for _ in range(60):
        found.append(find(f"s{rng.randint(1, 20)}", rng.choice((60, 80, 100))))
        now = score(s, found)["score"]
        assert now <= last
        last = now


def test_findings_that_do_not_count():
    s = segs(3)
    ignored = [find("s1", 60, source="house"), find("s1", 60, status="rejected"),
               find("s2", 60, conf="low"), find("", 60)]
    assert not any(counted(f) for f in ignored)
    assert score(s, ignored)["score"] == 100
    assert counted(find("s2", 60, conf="low", status="confirmed"))


def test_shown_score_never_rounds_up_to_a_pass():
    s = segs(3)                              # 1 of 3 segments fails tier 100
    sc = score(s, [find("s1", 100)], pass_mark=94)
    assert sc["score"] == 93.3 and not sc["passed"]


def test_empty_and_titles_only():
    sc = score([], [])
    assert sc["score"] == 100 and sc["no_text"] and sc["passed"]
    t = segs(1)
    t[0].kind = "title"
    assert score(t, [find("s1", 60)])["no_text"]


def test_violations_per_100_words():
    sc = score(segs(5), [find("s1", 60), find("s1", 100)])
    assert sc["violations"] == 2 and sc["per_100_words"] == 10.0


# --------------------------------------------------------------------------
# Golden examples (common STE mistakes, corrected)
# --------------------------------------------------------------------------

BAD = ("It is imperative that the operator ensures the hydraulic reservoir is "
       "replenished prior to commencing operation.")
CLEAN = ["Make sure that the hydraulic reservoir is full before you start the operation.",
         "WARNING: Do not touch the brake unit until it is cool. Hot parts can cause injury.",
         "The pump supplies fuel to the engine when the switch is on.",
         "Wait for approximately 10 minutes."]


@pytest.mark.parametrize("text", CLEAN)
def test_golden_clean(text):
    r = check_documents([("a.md", text)])
    assert r["score"]["score"] == 100, r["findings"]


def test_golden_bad():
    r = check_documents([("a.md", BAD)])
    assert {"ensures", "prior to", "commencing"} <= {f["quote"] for f in r["findings"]}
    assert not r["score"]["passed"]


# --------------------------------------------------------------------------
# Results and review merge
# --------------------------------------------------------------------------

def test_result_is_json_and_per_file():
    r = check_documents([("a.md", BAD), ("b.md", CLEAN[0]), ("c.md", "")], level=70)
    json.dumps(r)
    assert r["schema"] == 1 and r["pass_mark"] == 70 and r["enforced_tier"] == 80
    by_file = {f["file"]: f for f in r["files"]}
    assert by_file["a.md"]["score"] == 0 and by_file["b.md"]["score"] == 100
    assert by_file["c.md"]["no_text"]
    assert r["score"]["segments"] == 2 and r["score"]["score"] == 50
    assert len({s["id"] for s in r["segments"]}) == len(r["segments"])


def test_review_confirm_reject_and_unknown():
    r = check_documents([("a.md", "Remove the cover and clean the filter.")])
    low = next(f for f in r["findings"] if f["rule"] == "5.2")
    assert r["score"]["score"] == 100                       # open low: not counted
    confirmed = apply_review(r, {"confirm": [low["id"]], "reject": ["F99"]})
    assert confirmed["reviewed"] and confirmed["score"]["score"] < 100
    assert any("F99" in w for w in confirmed["warnings"])
    rejected = apply_review(r, {"reject": [{"id": low["id"], "reason": "one action"}]})
    f = next(f for f in rejected["findings"] if f["id"] == low["id"])
    assert f["status"] == "rejected" and f["note"] == "one action"
    assert rejected["score"]["score"] == 100


def test_review_add_anchors():
    text = "The pump supplies fuel.\n\nThe valve controls the flow of the fuel."
    r = check_documents([("a.md", text)])
    rv = apply_review(r, {"add": [
        {"rule": "1.4", "file": "a.md", "line": 2, "quote": "controls  the FLOW"},  # line +-2
        {"rule": "1.4", "file": "a.md", "line": 40, "quote": "supplies fuel"},     # file search
        {"rule": "1.4", "file": "a.md", "line": 1, "quote": "not in the text"},    # unanchored
        {"rule": "1.4", "file": "a.md", "line": 3},                                # no quote
        {"rule": "8.5", "quote": "x"},                                             # counting rule
        {"rule": "nope"}]})
    added = [f for f in rv["findings"] if f["source"] == "review"]
    assert [f["id"] for f in added] == ["R1", "R2", "R3", "R4"]
    assert added[0]["quote"] == "controls the flow" and added[0]["line"] == 3
    assert added[1]["seg"] == "s1"
    assert added[2]["seg"] == "" and added[2]["status"] == "added"
    assert added[3]["quote"] == "The valve controls the flow of the fuel."
    assert len(rv["warnings"]) == 3
    assert rv["score"]["score"] == 80                       # tier-100 finding in each segment


def test_review_rewrites_are_rechecked():
    r = check_documents([("a.md", BAD)])
    rv = apply_review(r, {"rewrites": [
        {"file": "a.md", "line": 1, "original": "prior to", "rewrite": "Utilize the pump."},
        {"file": "a.md", "line": 1, "rewrite": ""}], "notes": "ok"})
    assert rv["rewrites"][0]["original"] == BAD and rv["rewrites"][0]["seg"] == "s1"
    assert rv["rewrites"][0]["rewrite_findings"] == ["1.1: Utilize"]
    assert len(rv["rewrites"]) == 1 and rv["notes"] == "ok"


def test_review_input_errors():
    r = check_documents([("a.md", BAD)])
    with pytest.raises(ValueError):
        apply_review(r, ["F1"])
    with pytest.raises(ValueError):
        apply_review({"schema": 2}, {})
    with pytest.raises(ValueError):
        apply_review({"schema": 1, "segments": [{"id": "s1"}]}, {})
