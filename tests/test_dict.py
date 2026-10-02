"""dict import: the PDF row parser, the lookup and dictionary mode in the checker.

The rows below are handwritten. They copy the table layout of the specification
only, not its text. The real-PDF test runs only when STE100_DICT_PDF is set, and
it checks counts and three facts, so no dictionary text is in this repo.
"""
import json
import os
from pathlib import Path

import pytest

import checker
import dictionary as d
import ste
from dictionary import Row

ROWS = [
    Row(1, "Words before the first entry", "", "", ""),
    Row(1, "ADD (v)", "To put one thing with another", "Add oil.", ""),
    Row(1, "ADDS, ADDED, ADDED", "", "", ""),
    Row(1, "aperture (n)", "OPENING (n)", "Look through the opening.", "Look through the aperture."),
    Row(1, "CLOSE (v)", "To move a part so that it stops a flow", "Close the cover.", ""),
    Row(1, "CLOSES, CLOSED, CLOSED", "", "", ""),
    Row(1, "INSTRUCTION", "A step that", "", ""),
    Row(1, "(n)", "you do", "", ""),
    Row(1, "IN (prep)", "Inside", "", ""),
    Row(1, "MAKE SURE (v)", "To do a check", "", ""),
    Row(1, "MUST (v)", "Necessary", "", ""),
    Row(1, "NOT (adv)", "Negative", "", ""),
    Row(1, "OPEN (v)", "To move a part so that a flow can start", "", ""),
    Row(1, "POSITION (n)", "Where a thing is", "", ""),
    Row(1, "seal off (v)", "CLOSE (v)", "", ""),
    Row(1, "shut (v)", "CLOSE (v), or stop (v)", "Close the cover.", "Shut the cover."),
    Row(1, "STAY (v)", "To continue in a condition", "", ""),
    Row(1, "STAYS, STAYED, STAYED", "", "", ""),
    Row(1, "THE (art)", "A word that", "", ""),
    Row(1, "VALVE (n)", "A part that controls a flow", "", ""),
    Row(1, "YOU (pron)", "The reader", "", ""),
]


@pytest.fixture(scope="module")
def entries():
    return d.parse_rows(ROWS)


@pytest.fixture(scope="module")
def table(entries):
    return d.build_lookup(entries)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("STE100_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("STE100_NO_OPEN", "1")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def by_word(entries):
    return {e["word"]: e for e in entries}


def test_parse_rows(entries):
    e = by_word(entries)
    assert "Words before the first entry" not in e
    assert e["ADD"] == {"word": "ADD", "pos": "v", "approved": True, "alts": [],
                        "forms": ["ADDS", "ADDED"]}
    assert e["shut"]["approved"] is False
    assert e["shut"]["alts"] == ["CLOSE (v)"]          # lowercase "stop (v)" is not an alternative
    assert e["aperture"]["alts"] == ["OPENING (n)"]
    assert e["INSTRUCTION"]["pos"] == "n"               # wrapped keyword is merged
    assert e["MAKE SURE"]["approved"] is True
    assert len(entries) == len(ROWS) - 5                # intro, 3 form lines and the wrap


def test_save_and_load(tmp_path, entries):
    path = d.save(entries, Path("my-copy.pdf"), tmp_path / "dictionary.json")
    data = d.load(path)
    assert data["source"] == "my-copy.pdf" and data["entries"] == entries
    assert d.load(tmp_path / "missing.json") is None
    path.write_text('{"schema": 99, "entries": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="schema"):
        d.load(path)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="cannot read"):
        d.load(path)


def test_lemmas():
    assert ("stop", "v") in d.lemmas("stopped")
    assert ("close", "v") in d.lemmas("closed")
    assert ("valve", "nv") in d.lemmas("valves")
    assert d.lemmas("is") == []                         # too short to strip


@pytest.mark.parametrize("word, kind", [
    ("close", "approved"), ("Closed", "approved"), ("stays", "approved"),
    ("valves", "approved"),                             # plural of an approved noun
    ("make", "approved"), ("sure", "approved"),         # words of a multi-word entry
    ("shut", "unapproved"), ("shutting", "unapproved"), ("apertures", "unapproved"),
    ("opened", "form"), ("adding", "form"),
    ("noted", "unknown"),                               # NOT is an adverb, not a verb
    ("gasket", "unknown"),
])
def test_classify(table, word, kind):
    assert d.classify(word, table)[0] == kind


def test_seal_off_is_left_to_the_review(table):
    assert "seal" not in table.unapproved               # multi-word unapproved entry


def dict_findings(text, table, allow=()):
    result = checker.check_documents([("a.md", text)], table=table, allow=allow)
    assert result["dictionary"] == "imported"
    return [f for f in result["findings"] if f["source"] == "dict"]


def test_unapproved_word_conf_follows_verb_context(table):
    first = dict_findings("Shut the valve.", table)
    assert [(f["rule"], f["conf"], f["fix"]) for f in first] == [("1.1", "high", "CLOSE (v)")]
    after_must = dict_findings("You must shut the valve.", table)
    assert [f["conf"] for f in after_must] == ["high"]
    adjective = dict_findings("The shut valve stays in position.", table)
    assert [(f["quote"], f["conf"]) for f in adjective] == [("shut", "low")]


def test_unapproved_noun_is_high_anywhere(table):
    assert [(f["quote"], f["conf"]) for f in dict_findings("The apertures stay open.", table)] \
        == [("apertures", "high")]


def test_skips_acronyms_names_allow_terms_and_possessives(table):
    text = "You must close the NASA valve in the Boeing gasket position. The valve's position."
    assert dict_findings(text, table, allow=["Gasket"]) == []


def test_form_finding_skips_ing(table):
    found = dict_findings("You must not add the valve. You opened the valve. Adding the valve.", table)
    assert [(f["rule"], f["quote"], f["conf"]) for f in found] == [("1.4", "opened", "low")]


def test_unknown_words_once_each_and_capped(table):
    once = dict_findings("The gasket stays in the gasket position.", table)
    assert [(f["quote"], f["conf"]) for f in once] == [("gasket", "low")]
    made_up = ["zq" + a + b for a in "abc" for b in "abcdefghijklmnopqrstu"]   # 63 words
    text = " ".join(" ".join(made_up[i:i + 9]).capitalize() + "." for i in range(0, 63, 9))
    unknown = [f for f in dict_findings(text, table) if "not in the STE" in f["message"]]
    assert len(unknown) == checker.MAX_UNKNOWN


def test_curated_message_wins_a_tie(entries):
    extra = d.build_lookup(entries + [{"word": "ensure", "pos": "v", "approved": False,
                                       "alts": ["MAKE SURE (v)"], "forms": []}])
    result = checker.check_documents([("a.md", "You must ensure the valve stays closed.")],
                                     table=extra)
    hits = [f for f in result["findings"] if f["quote"] == "ensure" and f["rule"] == "1.1"]
    assert len(hits) == 1 and hits[0]["source"] == "ste"


def test_no_table_means_curated(table):
    result = checker.check_documents([("a.md", "Shut the valve.")])
    assert result["dictionary"] == "curated"
    assert not [f for f in result["findings"] if f["source"] == "dict"]


# ---------------------------------------------------------------- CLI


def test_cmd_dict_import(cfg, monkeypatch, capsys, caplog):
    pdf = cfg / "my-copy.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    monkeypatch.setattr(d, "extract_rows", lambda path: ROWS)
    assert ste.main(["dict", "import", str(pdf)]) == 0
    out = capsys.readouterr().out
    assert f"Saved: {len(ROWS) - 5} entries" in out and "do not share" in out
    assert "probably not the full dictionary" in caplog.text
    assert d.load()["source"] == "my-copy.pdf"

    assert ste.main(["status"]) == 0
    assert "dictionary: 16 entries from my-copy.pdf" in capsys.readouterr().out

    (cfg / "a.md").write_text("Shut the valve.\n", encoding="utf-8")
    assert ste.main(["check", "a.md", "--level", "100", "--json-out", "r.json"]) == 1
    result = json.loads((cfg / "r.json").read_text(encoding="utf-8"))
    assert result["dictionary"] == "imported"
    assert [f["rule"] for f in result["findings"] if f["source"] == "dict"] == ["1.1"]

    # The rewrite re-check in `report` uses the dictionary too.
    review = {"rewrites": [{"file": "a.md", "line": 1, "rewrite": "Shut the cover."}]}
    (cfg / "r.review.json").write_text(json.dumps(review), encoding="utf-8")
    ste.main(["report", "r.json", "--review", "r.review.json"])
    merged = json.loads((cfg / "r.reviewed.json").read_text(encoding="utf-8"))
    assert merged["rewrites"][0]["rewrite_findings"] == ["1.1: Shut"]


def test_cmd_dict_errors(cfg, monkeypatch, caplog):
    assert ste.main(["dict", "import", str(cfg / "missing.pdf")]) == 2
    pdf = cfg / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    def no_module(path):
        raise ModuleNotFoundError("No module named 'pdfplumber'")

    monkeypatch.setattr(d, "extract_rows", no_module)
    assert ste.main(["dict", "import", str(pdf)]) == 2
    assert "--with pdfplumber" in caplog.text
    monkeypatch.setattr(d, "extract_rows", lambda path: [Row(1, "no entries here", "")])
    assert ste.main(["dict", "import", str(pdf)]) == 2
    assert not d.dict_path().exists()


def test_damaged_dictionary_falls_back(cfg, capsys, caplog):
    d.dict_path().parent.mkdir(parents=True)
    d.dict_path().write_text("{broken", encoding="utf-8")
    assert ste.main(["status"]) == 0
    assert "dictionary: damaged" in capsys.readouterr().out
    (cfg / "a.md").write_text("Shut the valve.\n", encoding="utf-8")
    ste.main(["check", "a.md", "--json-out", "r.json"])
    assert json.loads((cfg / "r.json").read_text(encoding="utf-8"))["dictionary"] == "curated"
    assert "curated word list only" in caplog.text


@pytest.mark.skipif(not os.environ.get("STE100_DICT_PDF"), reason="set STE100_DICT_PDF to "
                    "the path of your own ASD-STE100 Issue 9 PDF")
def test_real_pdf():
    pytest.importorskip("pdfplumber")
    entries = d.parse_rows(d.extract_rows(Path(os.environ["STE100_DICT_PDF"])))
    assert len(entries) >= d.MIN_ENTRIES
    assert 500 <= sum(e["approved"] for e in entries) < len(entries)
    table = d.build_lookup(entries)
    kind, hits = d.classify("ensure", table)
    assert kind == "unapproved" and "MAKE SURE (v)" in hits[0]["alts"]
    assert d.classify("approximately", table)[0] == "approved"
    assert any(e["word"] == "test" and e["pos"] == "v" and not e["approved"] for e in entries)
    assert any(e["word"] == "TEST" and e["pos"] == "n" and e["approved"] for e in entries)
