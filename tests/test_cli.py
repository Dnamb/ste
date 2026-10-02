"""ste.py check/report: inputs, exit codes, encodings and the HTML report."""
import io
import json
import os
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

import report
import ste
from checker import apply_review, check_documents
from conftest import SCRIPTS

BAD = "It is imperative that the operator ensures the reservoir is full prior to commencing."
CLEAN = "Make sure that the hydraulic reservoir is full before you start the operation."
SCRIPT = "<script>alert(1)</script>"
VOID = {"meta", "br", "hr", "img", "input", "link", "wbr"}


@pytest.fixture
def cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STE_NO_OPEN", "1")
    return tmp_path


def run(*argv: str) -> int:
    return ste.main(list(argv))


def result_of(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TagBalance(HTMLParser):
    """Fails on an end tag that does not close the open element."""

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.errors.append(f"unexpected </{tag}> at {self.getpos()}")


def assert_well_formed(page: str) -> None:
    p = TagBalance()
    p.feed(page)
    p.close()
    assert not p.errors and not p.stack, (p.errors, p.stack)


# --------------------------------------------------------------------------
# Exit codes
# --------------------------------------------------------------------------

def test_exit_codes(cwd, capsys):
    (cwd / "good.md").write_text(CLEAN, encoding="utf-8")
    (cwd / "bad.md").write_text(BAD, encoding="utf-8")
    assert run("check", "good.md", "--json-out", "g.json") == 0
    assert run("check", "bad.md", "--json-out", "b.json") == 1
    assert run("check", "missing.md") == 2
    assert run("check", "nothing/*.md") == 2
    (cwd / "broken.json").write_text("{not json", encoding="utf-8")
    assert run("report", "broken.json") == 2
    (cwd / "list.json").write_text("[1]", encoding="utf-8")
    assert run("report", "list.json") == 2
    (cwd / "wrong.json").write_text('{"schema": 1}', encoding="utf-8")
    assert run("report", "wrong.json") == 2
    assert run("report", "b.json", "--review", "list.json") == 2
    assert "Score" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        run("check", "good.md", "--level", "hard")
    assert exc.value.code == 2


def test_level_and_threshold(cwd):
    (cwd / "bad.md").write_text(BAD, encoding="utf-8")
    run("check", "bad.md", "--level", "strict", "--json-out", "s.json")
    r = result_of(cwd / "s.json")
    assert r["level"] == 100 and r["pass_mark"] == 100 and r["enforced_tier"] == 100
    assert run("check", "bad.md", "--level", "lite", "--threshold", "0", "--json-out", "t.json") == 0
    r = result_of(cwd / "t.json")
    assert r["level"] == 60 and r["pass_mark"] == 0 and "created" in r


def test_default_output_path(cwd):
    (cwd / "My Guide.md").write_text(CLEAN, encoding="utf-8")
    assert run("check", "My Guide.md") == 0
    [out] = (cwd / "ste-reports").glob("*.json")
    assert out.name.startswith("My-Guide-")


# --------------------------------------------------------------------------
# Inputs: globs, folders, stdin, encodings
# --------------------------------------------------------------------------

def test_quoted_glob_and_folder(cwd):
    docs = cwd / "docs"
    for rel in ("a.md", "sub/b.md", "sub/c.txt", "sub/skip.py", ".git/x.md",
                "node_modules/y.md", "ste-reports/z.md"):
        (docs / rel).parent.mkdir(parents=True, exist_ok=True)
        (docs / rel).write_text(CLEAN, encoding="utf-8")
    run("check", "docs/**/*.md", "--json-out", "glob.json")
    assert [f["file"] for f in result_of(cwd / "glob.json")["files"]] == [
        "docs/a.md", "docs/sub/b.md"]                             # glob skips them too
    run("check", "docs/ste-reports/*.md", "--json-out", "named.json")
    assert [f["file"] for f in result_of(cwd / "named.json")["files"]] == ["docs/ste-reports/z.md"]
    run("check", "docs", "docs/a.md", "--json-out", "dir.json")
    assert [f["file"] for f in result_of(cwd / "dir.json")["files"]] == [
        "docs/a.md", "docs/sub/b.md", "docs/sub/c.txt"]          # skips dot/vendor dirs, dedupes


def test_stdin(cwd, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(BAD.encode("utf-8"))))
    assert run("check", "-", "--json-out", "in.json") == 1
    r = result_of(cwd / "in.json")
    assert r["files"][0]["file"] == "<stdin>" and r["findings"]


@pytest.mark.parametrize("encode", [
    lambda t: t.replace("\n", "\r\n").encode("utf-8"),
    lambda t: b"\xef\xbb\xbf" + t.encode("utf-8"),
    lambda t: t.encode("utf-16"),                       # BOM + UTF-16 LE, as PowerShell writes
], ids=["crlf", "utf8-bom", "utf16"])
def test_encodings_match_plain_utf8(cwd, encode):
    text = f"# Title\n\n{BAD}\n\n1. Utilize the wrench.\n"
    (cwd / "plain.md").write_text(text, encoding="utf-8", newline="\n")
    (cwd / "other.md").write_bytes(encode(text))
    run("check", "plain.md", "--json-out", "p.json")
    run("check", "other.md", "--json-out", "o.json")
    key = lambda r: [(f["rule"], f["line"], f["quote"]) for f in r["findings"]]  # noqa: E731
    assert key(result_of(cwd / "p.json")) == key(result_of(cwd / "o.json"))


def test_cp1252_console_never_crashes(tmp_path):
    """Quotes with any character print on a cp1252 console (real subprocess)."""
    (tmp_path / "u.md").write_text("Utilize the → arrow; it is 中.", encoding="utf-8")
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "STE_NO_OPEN": "1"}
    p = subprocess.run([sys.executable, str(SCRIPTS / "ste.py"), "check", "u.md",
                        "--json-out", "u.json"], cwd=tmp_path, env=env, capture_output=True)
    assert p.returncode == 1, p.stderr
    assert b"Utilize" in p.stdout


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

def test_report_html_and_review(cwd, capsys):
    (cwd / "doc.md").write_text(f"{BAD}\n\nRemove the cover and clean the filter.\n",
                                encoding="utf-8")
    run("check", "doc.md", "--json-out", "out/doc.json")
    r = result_of(cwd / "out/doc.json")
    low = next(f["id"] for f in r["findings"] if f["conf"] == "low")
    (cwd / "review.json").write_text(json.dumps({
        "confirm": [low], "rewrites": [{"file": "doc.md", "line": 1, "rewrite": CLEAN}],
        "notes": "checked"}), encoding="utf-8")
    assert run("report", "out/doc.json", "--review", "review.json") == 1
    merged = result_of(cwd / "out/doc.reviewed.json")
    assert merged["reviewed"] and merged["rewrites"][0]["rewrite_findings"] == []
    page = (cwd / "out/doc.html").read_text(encoding="utf-8")
    assert_well_formed(page)
    for letter in "ABCDEF":
        assert f'id="p{letter}"' in page
    assert 'class="titleblock"' in page and "Content-Security-Policy" in page
    assert "STE rewrite" in page and "checked" in page
    assert "audit:" in capsys.readouterr().out


def test_no_raw_markup_from_inputs():
    """Text from prose, file names, rewrites, notes and warnings is always escaped."""
    name = f"{SCRIPT}.md"
    r = check_documents([(name, f"Utilize the pump. {SCRIPT} is text here.")])
    r = apply_review(r, {
        "add": [{"rule": "1.1", "file": name, "line": 1, "quote": SCRIPT, "message": SCRIPT,
                 "fix": SCRIPT},
                {"rule": "1.1", "file": name, "line": 1, "quote": "not there " + SCRIPT}],
        "reject": [{"id": "F99", "reason": SCRIPT}],
        "rewrites": [{"file": name, "line": 1, "rewrite": SCRIPT},
                     {"file": "nowhere.md", "line": 9, "original": SCRIPT, "rewrite": SCRIPT}],
        "notes": SCRIPT})
    page = report.render(r)
    assert_well_formed(page)
    assert "<script" not in page.lower()
    assert page.count("&lt;script&gt;") >= 5


def test_open_respects_no_open(cwd, monkeypatch):
    (cwd / "doc.md").write_text(CLEAN, encoding="utf-8")
    run("check", "doc.md", "--json-out", "doc.json")
    opened: list[str] = []
    monkeypatch.setattr(ste.webbrowser, "open", lambda url: opened.append(url))
    assert run("report", "doc.json", "--open") == 0
    assert opened == []
    monkeypatch.delenv("STE_NO_OPEN")
    assert run("report", "doc.json", "--open", "--out", "x/y.html") == 0
    assert opened == [(cwd / "x/y.html").resolve().as_uri()]
