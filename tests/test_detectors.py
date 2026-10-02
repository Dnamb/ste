"""Deterministic detectors and curated word substitutions."""
import pytest

from checker import analyze, load_rules, load_words, segment_text, word_forms


def rules(text: str, conf: str | None = None, **kw) -> list[str]:
    allow = kw.pop("allow", ())
    found = analyze(segment_text(text, **kw), allow=allow)
    return [f.rule for f in found if conf is None or f.conf == conf]


@pytest.mark.parametrize("text,rule", [
    ("Test the circuit.", "1.2"),
    ("Wait about 10 minutes.", "1.3"),
    ("Follow the instructions.", "1.3"),
    ("Don't open the valve.", "4.2"),
    ("Utilize the clamp.", "1.1"),
    ("Remove the cover in order to clean it.", "HS"),
    ("Paint the colour band.", "1.14"),
    ("The pump stops; the light comes on.", "8.1"),
    ("Use tools, e.g. a wrench.", "GR-6"),
    ("The motor has been hot.", "3.4"),
    ("The valve has opened.", "3.2"),
    ("The light is flashing.", "3.2"),
    ("Make sure the valve is closed.", "GR-1"),
    ("Find out the cause.", "9.3"),
    ("The operator must lock his panel.", "GR-7"),
])
def test_positive(text, rule):
    assert rule in rules(text, conf="high")


@pytest.mark.parametrize("text", [
    "Wait for approximately 10 minutes.",
    "Do a test of the circuit.",
    "Make sure that the hydraulic reservoir is full before you start the operation.",
    "The file is missing.",
    "The remaining fluid is hot.",
    "Obey the instructions.",
    "Do not touch the terminals.",
    "Open `don't; e.g.` and https://x.io/utilize first.",
    'Push the "Utilize" button.',
])
def test_clean(text):
    assert rules(text) == []


def test_length_limits():
    proc = "Remove " + "the very small and old " * 4 + "cover."
    assert "5.1" in rules(proc)
    assert rules("The pump is " + "very " * 22 + "hot.") == ["6.3"]


def test_paragraph_limit_on_seventh_sentence():
    para = " ".join(f"The part {i} is hot." for i in range(8))
    found = [f for f in analyze(segment_text(para)) if f.rule == "6.6"]
    assert len(found) == 1 and found[0].quote == "The part 6 is hot."


def test_ctx_v_only_in_verb_context():
    assert "1.2" not in rules("The test failed.")
    assert "1.2" in rules("You must check the oil.")


def test_allowlist_and_title_case():
    assert rules("Execute the job.", allow=["execute"]) == []
    assert "1.1" not in rules("Go to Main Street.")


def test_ids_and_offsets():
    found = analyze(segment_text("Utilize it.\n\nDon't stop."))
    assert [f.id for f in found] == ["F1", "F2"]
    assert found[1].line == 3 and found[1].quote == "Don't"


def test_word_forms():
    assert {"stopped", "stopping", "stops"} <= word_forms("stop", "v")
    assert {"modifies", "modified"} <= word_forms("modify", "v")
    assert word_forms("ability", "n") == {"ability", "abilities"}


def test_tables_are_valid():
    rows = load_words()
    assert len(rows) > 150
    assert len({(r.word, r.pos) for r in rows}) == len(rows)
    approved = {"approximately", "sufficient", "necessary", "possible", "although", "unless"}
    assert not approved & {r.word for r in rows}
    assert set(load_rules()) >= {r.rule for r in rows}
