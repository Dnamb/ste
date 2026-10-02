"""ste100 command line: check, report.

Run it with ``uv run --no-project --quiet ste.py <command> ...`` (stdlib only).
Exit codes: 0 = pass (or success), 1 = the score is below the pass mark, 2 = usage
or input error. Results go to stdout, log messages to stderr.
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import re
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import checker  # noqa: E402

log = logging.getLogger("ste100")

TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".rst"}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "ste-reports", "__pycache__"}
MAX_LISTED = 50


class InputError(Exception):
    """Bad input: a missing file, a damaged JSON file, an empty glob."""


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------

def _display_name(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _folder_files(folder: Path) -> list[Path]:
    found = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        found += [Path(root) / f for f in sorted(files) if Path(f).suffix.lower() in TEXT_SUFFIXES]
    return found


def expand_inputs(args: list[str]) -> list[Path | None]:
    """Files for each argument: a file, a folder (searched), a glob, or ``-`` (stdin = None).

    Globs are expanded here, because PowerShell and cmd do not expand them.
    """
    out: list[Path | None] = []
    for arg in args:
        if arg == "-":
            out.append(None)
            continue
        p = Path(arg)
        if p.is_dir():
            files = _folder_files(p)
        elif p.is_file():
            files = [p]
        elif glob.has_magic(arg):
            # Skip vendor and report folders here too, unless the glob names them itself.
            skip = SKIP_DIRS - set(p.parts)
            files = [Path(m) for m in sorted(glob.glob(arg, recursive=True))
                     if Path(m).is_file() and not skip & set(Path(m).parts)]
        else:
            raise InputError(f"file not found: {arg}")
        if not files:
            raise InputError(f"no text files match: {arg}")
        out += files
    seen: set[str] = set()
    unique: list[Path | None] = []
    for f in out:          # drop duplicates, keep order
        key = "-" if f is None else str(f.resolve()).lower()
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


def read_inputs(paths: list[Path | None]) -> list[tuple[str, str]]:
    docs = []
    for p in paths:
        try:
            if p is None:
                docs.append(("<stdin>", checker.decode_bytes(sys.stdin.buffer.read(), "<stdin>")))
            else:
                docs.append((_display_name(p), checker.read_source(p)))
        except OSError as exc:
            raise InputError(f"cannot read {p or '<stdin>'}: {exc}") from exc
    return docs


def load_json(path: Path) -> dict:
    try:
        data = json.loads(checker.decode_bytes(path.read_bytes(), str(path)))
    except OSError as exc:
        raise InputError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise InputError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise InputError(f"{path} must contain a JSON object")
    return data


def write_text(path: Path, text: str) -> None:
    """Write UTF-8 atomically (a temporary file, then a rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _slug(names: list[str]) -> str:
    stem = Path(names[0]).stem if names and names[0] != "<stdin>" else "stdin"
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-")[:40] or "text"
    return slug + (f"-and-{len(names) - 1}-more" if len(names) > 1 else "")


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def summary(result: dict, path: Path | None = None) -> str:
    """A short ASCII-friendly summary for the terminal and for Claude to read."""
    sc = result["score"]
    fs = [checker.Finding(**f) for f in result["findings"]]
    n_open = sum(1 for f in fs if not checker.counted(f) and f.status != "rejected"
                 and f.source != "house" and f.seg)
    n_house = sum(1 for f in fs if f.source == "house")
    verdict = "PASS" if sc["passed"] else "FAIL"
    bands = ", ".join(f"tier {t} {float(c) * 100:.0f}%" for t, c in sc["bands"].items())
    lines = [
        f"STE100 {'audit' if result.get('reviewed') else 'check'}: {len(result['files'])} file(s), "
        f"{sc['segments']} sentences, {sc['words']} words",
        f"Score {sc['score']:.1f} / pass mark {sc['pass_mark']}: {verdict}"
        + (" (no text)" if sc["no_text"] else ""),
        f"Clean sentences: {bands}",
        f"Findings: {sc['violations']} counted, {n_open} need review, {n_house} house style"
        f" ({sc['per_100_words']:.1f} counted per 100 words)",
    ]
    if len(result["files"]) > 1:
        lines += [f"  {f['file']}: {f['score']:.1f}" for f in result["files"]]
    if path:
        lines.append(f"Saved: {path}")
    shown = [f for f in fs if f.status != "rejected"][:MAX_LISTED]
    if shown:
        lines.append("")
        lines.append("id   location  rule [conf, status] text -> fix")
    for f in shown:
        mark = "counted" if checker.counted(f) else ("house" if f.source == "house" else f.status)
        quote = f.quote if len(f.quote) <= 70 else f.quote[:67] + "..."
        lines.append(f"{f.id} {f.file}:{f.line} {f.rule} [{f.conf}, {mark}] {quote!r}"
                     + (f" -> {f.fix}" if f.fix else ""))
    if len(fs) > len(shown):
        lines.append(f"... {len(fs) - len(shown)} more in the JSON file")
    for w in result.get("warnings", []):
        lines.append(f"warning: {w}")
    return "\n".join(lines)


def open_in_browser(path: Path) -> None:
    if os.environ.get("STE100_NO_OPEN"):
        log.info("STE100_NO_OPEN is set; not opening %s", path)
        return
    try:
        webbrowser.open(path.resolve().as_uri())
    except Exception as exc:  # a missing browser must not fail the audit
        log.warning("cannot open the browser: %s", exc)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_check(a: argparse.Namespace) -> int:
    paths = expand_inputs(a.inputs)
    docs = read_inputs(paths)
    level = a.level if a.level is not None else 80
    result = checker.check_documents(docs, level=level, pass_mark=a.threshold, doc_type=a.type)
    result["created"] = datetime.now().isoformat(timespec="seconds")
    out = Path(a.json_out) if a.json_out else (
        Path(a.out_dir) / f"{_slug([n for n, _ in docs])}-{datetime.now():%Y%m%d-%H%M%S}.json")
    write_text(out, json.dumps(result, indent=1, ensure_ascii=False))
    print(summary(result, out))
    return 0 if result["score"]["passed"] else 1


def cmd_report(a: argparse.Namespace) -> int:
    import report  # only the report command needs the template

    src = Path(a.result)
    result = load_json(src)
    if a.review:
        try:
            result = checker.apply_review(result, load_json(Path(a.review)))
        except ValueError as exc:
            raise InputError(str(exc)) from exc
        merged = src.with_name(src.stem + ".reviewed.json")
        write_text(merged, json.dumps(result, indent=1, ensure_ascii=False))
    out = Path(a.out) if a.out else src.with_suffix(".html")
    try:
        page = report.render(result)
    except (KeyError, TypeError, ValueError) as exc:
        raise InputError(f"{src} is not a usable check result: {exc!r}") from exc
    write_text(out, page)
    print(summary(result, out))
    if a.open:
        open_in_browser(out)
    return 0 if result["score"]["passed"] else 1


def _level(value: str) -> int:
    try:
        return checker.parse_level(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ste.py", description="ASD-STE100 checker and report.")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("check", help="check files, folders, globs or stdin (-)")
    c.add_argument("inputs", nargs="+")
    c.add_argument("--level", type=_level, help="0-100 or lite|standard|strict (default 80)")
    c.add_argument("--threshold", type=_level, help="pass mark for this run (default: the level)")
    c.add_argument("--type", choices=("auto", "proc", "desc"), default="auto")
    c.add_argument("--json-out", help="where to write the result JSON")
    c.add_argument("--out-dir", default="ste-reports")
    c.set_defaults(func=cmd_check)

    r = sub.add_parser("report", help="merge a review and write the HTML report")
    r.add_argument("result", help="the JSON file from check")
    r.add_argument("--review", help="review JSON (confirm, reject, add, rewrites, notes)")
    r.add_argument("--out", help="HTML path (default: next to the result)")
    r.add_argument("--open", action="store_true", help="open the report in the browser")
    r.set_defaults(func=cmd_report)
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="ste100: %(message)s")
    # Quotes from audited files can hold any character; never crash on a cp1252 console.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except InputError as exc:
        log.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
