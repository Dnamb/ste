"""Heuristic detectors: precision gate on labeled sentences, and rule coverage."""
from pathlib import Path

import pytest

from checker import DETECTORS, analyze, load_rules, segment_text

LABELED = Path(__file__).parent / "fixtures" / "labeled.tsv"


def load_labeled() -> list[tuple[str, set[str]]]:
    rows = []
    for line in LABELED.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        text, labels = line.split("\t")
        rows.append((text.replace("\\n", "\n"),
                     set() if labels.strip() == "-" else set(labels.split(","))))
    return rows


def test_labeled_precision_and_recall():
    rows = load_labeled()
    assert len(rows) >= 150
    tp = {"high": 0, "low": 0}
    fp = {"high": 0, "low": 0}
    found_truth = total_truth = 0
    misses = []
    for text, truth in rows:
        found = analyze(segment_text(text))
        for conf in ("high", "low"):
            pred = {f.rule for f in found if f.conf == conf}
            tp[conf] += len(pred & truth)
            fp[conf] += len(pred - truth)
            if pred - truth:
                misses.append(f"FP {conf} {sorted(pred - truth)}: {text!r}")
        pred_all = {f.rule for f in found}
        found_truth += len(pred_all & truth)
        total_truth += len(truth)
        if truth - pred_all:
            misses.append(f"FN {sorted(truth - pred_all)}: {text!r}")
    prec = {c: tp[c] / max(1, tp[c] + fp[c]) for c in tp}
    recall = found_truth / total_truth
    report = f"precision {prec}, recall {recall:.2f}\n" + "\n".join(misses)
    assert prec["high"] >= 0.95, report
    assert prec["low"] >= 0.6, report
    assert recall >= 0.8, report


def test_every_detectable_rule_has_a_detector():
    rules = load_rules()
    registered = {r for reg in DETECTORS for r in reg.rules}
    assert registered <= set(rules)
    detectable = {r.id for r in rules.values() if r.method in ("auto", "heuristic")}
    assert detectable <= registered, detectable - registered


@pytest.mark.parametrize("text,rule,conf", [
    ("Before removing the cover, disconnect the power.", "3.5", "high"),
    ("The cover is removed by the technician.", "3.6", "high"),
    ("The cover must be removed.", "3.6", "high"),
    ("WARNING: The surface is hot.", "7.2", "high"),
    ("Do an inspection of the seal.", "3.7", "low"),
    ("Remove the pump motor bracket bolt.", "2.1", "low"),
    ("Remove the cover and clean the filter.", "5.2", "low"),
    ("Close the valve if the pressure increases.", "5.4", "low"),
    ("NOTE: Close the valve first.", "5.5", "low"),
    ("Remove cover.", "4.5", "low"),
    ("This is dangerous.", "GR-4", "low"),
])
def test_heuristic_examples(text, rule, conf):
    assert any(f.rule == rule and f.conf == conf for f in analyze(segment_text(text)))


def test_passive_in_description_is_not_flagged():
    found = analyze(segment_text("Errors are written to the log file."))
    assert not [f for f in found if f.rule == "3.6"]
