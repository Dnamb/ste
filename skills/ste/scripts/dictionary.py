"""Import the dictionary from the user's own ASD-STE100 PDF, and look words up in it.

The dictionary is copyright material. It is saved only in the user's settings
folder (``<config dir>/dictionary.json``) and is never part of this plugin.

Only ``extract_rows`` needs pdfplumber (``uv run --with pdfplumber``). The
parser and the lookup use the standard library, so the checker can use a saved
dictionary without extra packages.
"""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

import ste_config

SCHEMA = 1
MIN_ENTRIES = 500                   # fewer than this: the PDF is probably not Issue 9 Part 2
FOOTER = 75                         # points at the bottom of a page that are not table text
POS = r"(n|v|adj|adv|prep|conj|pron|art|tn|tv)"
_START = re.compile(r"^([A-Za-z][A-Za-z0-9'’ .-]*?)\s*\(" + POS + r"\)", re.I)
_ALT = re.compile(r"\b([A-Z][A-Z0-9'’ -]*[A-Z0-9])\s*\(" + POS + r"\)", re.I)
_BARE = re.compile(r"^[A-Za-z][A-Za-z0-9'’ -]*$")
_POS_LINE = re.compile(r"^\(" + POS + r"\)", re.I)
_FORM = re.compile(r"\b[A-Z][A-Z'-]+\b")


class Row(NamedTuple):
    """One line of the dictionary table: the page and the text in each of the 4 columns."""
    page: int
    c1: str                         # keyword (part of speech) and, if approved, its forms
    c2: str                         # approved meaning, or the approved alternatives
    c3: str = ""                    # STE example
    c4: str = ""                    # non-STE example


def dict_path() -> Path:
    return ste_config.config_dir() / "dictionary.json"


# --------------------------------------------------------------------------
# PDF -> rows -> entries
# --------------------------------------------------------------------------

def extract_rows(pdf_path: Path) -> list[Row]:
    """Read the dictionary table from the PDF: the pages with the 4-column header.

    Words are grouped into lines by their top position, and into columns by the
    x position of the header words.
    """
    import pdfplumber  # lazy: only `dict import` needs it

    rows: list[Row] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for number, page in enumerate(pdf.pages, 1):
            words = page.extract_words()
            head = [w for w in words if w["text"] == "ALTERNATIVES"]
            if not head:
                continue
            top = min(w["top"] for w in head)
            on_head = [w for w in words if abs(w["top"] - top) <= 2]
            x = {w["text"]: w["x0"] for w in reversed(on_head)}   # the first match wins
            if not {"ALTERNATIVES", "STE", "Non-STE"} <= set(x):
                continue
            edges = (x["ALTERNATIVES"] - 2, x["STE"] - 2, x["Non-STE"] - 2)
            lines: dict[float, list[str]] = {}
            for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
                if w["top"] <= top + 1 or w["top"] > page.height - FOOTER:
                    continue
                col = sum(w["x0"] >= e for e in edges)
                key = next((t for t in lines if abs(t - w["top"]) <= 2), w["top"])
                cells = lines.setdefault(key, ["", "", "", ""])
                cells[col] = f"{cells[col]} {w['text']}".strip()
            rows += [Row(number, *lines[t]) for t in sorted(lines)]
    return rows


def parse_rows(rows: list[Row]) -> list[dict]:
    """Turn table rows into entries: word, pos, approved, alts (for unapproved words), forms.

    An entry starts with ``word (pos)`` in column 1. An UPPERCASE word is approved;
    a lowercase word is not, and column 2 gives its approved alternatives.
    """
    merged: list[list] = []
    for r in rows:
        r = [r.page, r.c1, r.c2, r.c3, r.c4]
        prev = merged[-1] if merged else None
        # A long keyword can wrap: "WORD" on one line, "(pos) ..." on the next.
        if prev and prev[0] == r[0] and _POS_LINE.match(r[1]) and _BARE.match(prev[1]):
            prev[1] = f"{prev[1]} {r[1]}"
            for i in (2, 3, 4):
                prev[i] = f"{prev[i]} {r[i]}".strip()
            continue
        merged.append(r)

    entries: list[dict] = []
    alts: list[str] = []
    cur: dict | None = None
    for _page, c1, c2, _c3, _c4 in merged:
        m = _START.match(c1)
        if m:
            if cur is not None:
                cur["alts"] = _alternatives(alts)
            word = m.group(1).strip().replace("’", "'")
            cur = {"word": word, "pos": m.group(2).lower(), "approved": word.upper() == word,
                   "alts": [], "forms": []}
            entries.append(cur)
            alts, rest = [c2], c1[m.end():]
        elif cur is None:
            continue
        else:
            alts.append(c2)
            rest = c1
        if cur["approved"]:
            for form in _FORM.findall(rest):
                if form != cur["word"] and form not in cur["forms"]:
                    cur["forms"].append(form)
    if cur is not None:
        cur["alts"] = _alternatives(alts)
    for e in entries:
        if e["approved"]:
            e["alts"] = []
    return entries


def _alternatives(cells: list[str]) -> list[str]:
    return list(dict.fromkeys(f"{a.strip()} ({p.lower()})" for a, p in _ALT.findall(" ".join(cells))
                              if a.strip().upper() == a.strip()))


def save(entries: list[dict], source: Path, path: Path | None = None) -> Path:
    """Save the entries atomically. Returns the path."""
    path = path or dict_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"schema": SCHEMA, "source": source.name,
            "created": datetime.now().isoformat(timespec="seconds"), "entries": entries}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load(path: Path | None = None) -> dict | None:
    """The saved dictionary, or None if there is none. Raises ValueError if it is damaged."""
    path = path or dict_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path}: cannot read the dictionary: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA \
            or not isinstance(data.get("entries"), list):
        raise ValueError(f"{path}: not a schema {SCHEMA} dictionary. Run `dict import` again.")
    return data


# --------------------------------------------------------------------------
# Lookup
# --------------------------------------------------------------------------

def _plural(w: str) -> str:
    if re.search(r"[^aeiou]y$", w):
        return w[:-1] + "ies"
    return w + ("es" if w.endswith(("s", "x", "z", "ch", "sh")) else "s")


def _verb_forms(w: str) -> set[str]:
    if w.endswith("e") and not w.endswith("ee"):
        return {w + "s", w + "d", w[:-1] + "ing"}
    if re.search(r"[^aeiou]y$", w):
        return {w[:-1] + "ies", w[:-1] + "ied", w + "ing"}
    forms = {_plural(w), w + "ed", w + "ing"}
    if re.search(r"[^aeiou][aeiou][^aeiouwxy]$", w):        # stop -> stopped
        forms |= {w + w[-1] + "ed", w + w[-1] + "ing"}
    return forms


# (suffix, letters to add, parts of speech that can take the suffix)
_SUFFIXES = (("ies", "y", "nv"), ("ied", "y", "v"), ("ier", "y", "a"), ("iest", "y", "a"),
             ("ily", "y", "a"), ("ing", "", "v"), ("ing", "e", "v"), ("es", "", "nv"),
             ("ed", "", "v"), ("ed", "e", "v"), ("s", "", "nv"), ("er", "", "a"),
             ("er", "e", "a"), ("est", "", "a"), ("est", "e", "a"), ("ly", "", "a"))


def lemmas(word: str) -> list[tuple[str, str]]:
    """Possible (base form, part-of-speech letters) of an inflected word, most likely first.

    The letters are n (noun), v (verb) and a (adjective or adverb).
    # ponytail: suffix rules, no morphology database. Irregular forms come from the
    # forms that the dictionary lists for approved words.
    """
    w, out = word.lower(), []
    for suffix, add, pos in _SUFFIXES:
        if w.endswith(suffix) and len(w) - len(suffix) >= 2:
            base = w[: -len(suffix)] + add
            out.append((base, pos))
            if len(base) > 2 and base[-1] == base[-2] and not add:    # stopped -> stop
                out.append((base[:-1], pos))
    return list(dict.fromkeys(out))


def _pos_letter(pos: str) -> str:
    return {"n": "n", "tn": "n", "v": "v", "tv": "v", "adj": "a", "adv": "a"}.get(pos, "-")


class Lookup(NamedTuple):
    approved: frozenset[str]                    # approved surface forms (lowercase)
    heads: dict[str, str]                       # approved one-word headword -> pos letters
    unapproved: dict[str, tuple[dict, ...]]     # surface form -> unapproved entries


def build_lookup(entries: list[dict]) -> Lookup:
    """Index the entries by every form that the checker can meet in a text."""
    approved: set[str] = set()
    heads: dict[str, str] = defaultdict(str)
    for e in entries:
        if not e.get("approved"):
            continue
        words = str(e["word"]).lower().replace("...", " ").split()
        approved.update(words)                 # each word of "MAKE SURE" or "AS ... AS"
        approved.update(str(f).lower() for f in e.get("forms", []))
        if len(words) == 1:
            heads[words[0]] += _pos_letter(e["pos"])
            if e["pos"] == "n":
                approved.add(_plural(words[0]))
    unapproved: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        word = str(e["word"]).lower()
        if e.get("approved") or " " in word:
            continue      # ponytail: multi-word entries are left to words.tsv and the review
        forms = {word} | ({_plural(word)} if e["pos"] == "n" else set()) \
            | (_verb_forms(word) if e["pos"] == "v" else set())
        for form in forms - approved:
            unapproved[form].append(e)
    return Lookup(frozenset(approved), dict(heads), {k: tuple(v) for k, v in unapproved.items()})


def classify(word: str, table: Lookup) -> tuple[str, object]:
    """Return one of:

    ("approved", None), ("unapproved", entries), ("form", approved base word),
    ("unknown", None). "form" is a form of an approved word that the dictionary
    does not list (for example an -ing form of an approved verb).
    """
    w = word.lower().replace("’", "'")
    if w in table.approved:
        return "approved", None
    if w in table.unapproved:
        return "unapproved", table.unapproved[w]
    for base, pos in lemmas(w):
        hits = [e for e in table.unapproved.get(base, ()) if _pos_letter(e["pos"]) in pos]
        if hits:
            return "unapproved", tuple(hits)
        if set(table.heads.get(base, "")) & set(pos):
            return "form", base
    return "unknown", None
