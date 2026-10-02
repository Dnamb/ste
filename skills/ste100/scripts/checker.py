"""STE100 checker: markdown segmentation, STE word count, detectors and score.

Stdlib only (Python 3.11+). Rule text is paraphrased in references/rules.md,
which is the single source of truth for rule tiers and methods.

Offsets: every segment keeps its original ``text`` and a ``masked`` copy of the
same length. Masking turns markup into spaces and code/URLs into ``_`` runs, so
detectors can use regexes on ``masked`` and report spans in ``text``.
"""
from __future__ import annotations

import bisect
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("ste100")

SKILL_DIR = Path(__file__).resolve().parent.parent
RULES_PATH = SKILL_DIR / "references" / "rules.md"
WORDS_PATH = SKILL_DIR / "assets" / "words.tsv"

KINDS = ("proc", "desc", "warning", "caution", "note", "title", "cell")


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------

def decode_bytes(raw: bytes, name: str = "<input>") -> str:
    """Decode UTF-8 (BOM optional) or UTF-16 (BOM required); fall back to cp1252."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16")
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            log.warning("%s is not UTF-8; reading it as cp1252", name)
            text = raw.decode("cp1252", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_source(path: Path) -> str:
    """Read a text file for checking. Raises OSError if it cannot be read."""
    return decode_bytes(path.read_bytes(), str(path))


# --------------------------------------------------------------------------
# Masking (same length as the input)
# --------------------------------------------------------------------------

_MASK_RE = re.compile(
    r"""
      (?P<code>(?P<ticks>`+).+?(?P=ticks))
    | (?P<comment><!--.*?-->)
    | (?P<autolink><(?:https?://|mailto:)[^>\s]+>)
    | (?P<image>!\[[^\]\n]*\]\([^)\n]*\))
    | (?P<linkend>\]\([^)\s]*(?:\s+"[^"\n]*")?\)|\]\[[^\]\n]*\])
    | (?P<footnote>\[\^[^\]\n]+\])
    | (?P<url>(?:https?://|www\.)[^\s<>()\[\]]*[^\s<>()\[\].,;:!?'"])
    | (?P<tag></?[A-Za-z][\w-]*(?:\s[^<>\n]*)?/?>)
    | (?P<entity>&(?:[A-Za-z]+|\#\d+|\#x[0-9A-Fa-f]+);)
    | (?P<lbracket>\[(?=[^\]\n]*\][(\[]))
    | (?P<checkbox>^\[[ xX]\](?=\s))
    | (?P<emph>\*\*|__|~~|(?<![\w*])\*(?=\S)|(?<=\S)\*(?![\w*])
              |(?<!\w)_(?=[^\s_])|(?<=[^\s_])_(?!\w))
    """,
    re.X | re.M,
)
_PLACEHOLDER_GROUPS = {"code", "autolink", "url"}
_QUOTE_MAP = str.maketrans({"\u2019": "'", "\u2018": "'", "\u201c": '"',
                            "\u201d": '"', "\u00a0": " "})


def mask(text: str) -> str:
    """Return ``text`` with markup blanked and code/URLs turned into ``_`` runs.

    The result has the same length as ``text``. A ``_`` run counts as one word
    and detectors skip it.
    """
    def repl(m: re.Match[str]) -> str:
        n = len(m.group(0))
        return "_" * n if m.lastgroup in _PLACEHOLDER_GROUPS else " " * n

    return _MASK_RE.sub(repl, text).translate(_QUOTE_MAP)


def is_placeholder(token: str) -> bool:
    """True for a masked code span or URL."""
    return token.startswith("_") and token.strip("_") == ""


# --------------------------------------------------------------------------
# Blocks (markdown structure)
# --------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}(?:\s+(.*?))?(?:\s+#+)?\s*$")
_SETEXT_RE = re.compile(r"^\s{0,3}(?:=+|-+)\s*$")
_HR_RE = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$")
_ITEM_RE = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*$")
_QUOTE_RE = re.compile(r"^\s{0,3}>\s?")
_CALLOUT_RE = re.compile(r"^\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\][+-]?\s*$", re.I)
_CALLOUT_KIND = {"note": "note", "tip": "note", "important": "note",
                 "warning": "warning", "caution": "caution"}


@dataclass
class Block:
    """A run of source lines that forms one paragraph, item, heading or cell."""
    kind: str                       # para | title | item | num | cell
    lines: list[tuple[int, str]] = field(default_factory=list)  # (line no, content)
    signal: str = ""                # warning | caution | note (from a callout)


def _split_row(content: str) -> list[str]:
    # ponytail: splits on every unescaped "|", so a "|" inside a code span breaks the cell
    row = content.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|") and not row.endswith("\\|"):
        row = row[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", row)]


def split_blocks(text: str) -> list[Block]:
    """Split markdown into prose blocks. Code, front matter and comments are dropped."""
    raw = text.split("\n")
    blocks: list[Block] = []
    cur: Block | None = None
    fence: str | None = None
    in_comment = False
    in_icode = False
    in_table = False
    list_ctx = False
    prev_blank = True
    prev_depth = 0
    callout = ""
    start = 0

    # Front matter (YAML or TOML) only at the first line.
    if raw and raw[0].strip() in ("---", "+++"):
        closer = ("---", "...") if raw[0].strip() == "---" else ("+++",)
        for j in range(1, len(raw)):
            if raw[j].strip() in closer:
                start = j + 1
                break

    def close() -> None:
        nonlocal cur
        if cur is not None and cur.lines:
            blocks.append(cur)
        cur = None

    for i in range(start, len(raw)):
        lineno = i + 1
        line = raw[i]

        # Blockquote: strip ">" markers; a change in depth ends the block.
        depth = 0
        while (m := _QUOTE_RE.match(line)) is not None:
            line = line[m.end():]
            depth += 1
        if depth != prev_depth:
            close()
            in_table = False
            if depth == 0:
                callout = ""
            prev_depth = depth

        if fence is not None:
            stripped = line.strip()
            if stripped.startswith(fence) and stripped.strip(fence[0]) == "":
                fence = None
            prev_blank = False
            continue
        if in_comment:
            if "-->" in line:
                in_comment = False
            continue

        m = _FENCE_RE.match(line)
        if m:
            close()
            fence = m.group(1)
            continue
        if line.lstrip().startswith("<!--") and "-->" not in line:
            close()
            in_comment = True
            continue

        if not line.strip():
            close()
            in_table = False
            prev_blank = True
            continue
        if in_icode:
            if line.startswith(("    ", "\t")):
                continue
            in_icode = False

        # Indented code: only after a blank line and outside a list.
        if prev_blank and not list_ctx and line.startswith(("    ", "\t")):
            close()
            in_icode = True
            continue
        prev_blank = False

        if depth and (mc := _CALLOUT_RE.match(line)):
            close()
            callout = _CALLOUT_KIND[mc.group(1).lower()]
            continue

        if _SETEXT_RE.match(line) and cur is not None and cur.kind == "para":
            cur.kind = "title"
            close()
            continue
        if _HR_RE.match(line):
            close()
            list_ctx = False
            continue

        m = _HEADING_RE.match(line)
        if m:
            close()
            blocks.append(Block("title", [(lineno, m.group(1) or "")], callout))
            list_ctx = False
            continue

        # Tables: a row starts with "|", or a "|" line is followed by a separator.
        is_row = line.lstrip().startswith("|") or (
            "|" in line and (in_table or (i + 1 < len(raw) and "|" in raw[i + 1]
                                          and _TABLE_SEP_RE.match(raw[i + 1]))))
        if is_row:
            close()
            in_table = True
            if _TABLE_SEP_RE.match(line):
                continue
            for cell in _split_row(line):
                if cell:
                    blocks.append(Block("cell", [(lineno, cell)], callout))
            continue

        m = _ITEM_RE.match(line)
        if m:
            close()
            kind = "num" if m.group(2)[0].isdigit() else "item"
            cur = Block(kind, [(lineno, m.group(3))], callout)
            list_ctx = True
            continue

        if cur is not None:
            cur.lines.append((lineno, line.strip()))
        else:
            # Indented text after a blank line inside a list continues the item.
            if list_ctx and not line.startswith((" ", "\t")):
                list_ctx = False
            cur = Block("para", [(lineno, line.strip())], callout)

    close()
    return blocks


# --------------------------------------------------------------------------
# Sentences and segments
# --------------------------------------------------------------------------

# Abbreviations that never end a sentence. "etc." can, so it is not here.
_NO_SPLIT = {"e.g", "i.e", "cf", "vs", "viz", "approx", "fig", "figs", "no", "nos",
             "mr", "mrs", "ms", "dr", "st", "jr", "sr", "inc", "ltd", "co", "corp",
             "vol", "ch", "sec", "ref", "eq", "al", "u.s", "pp", "para"}
_END_RE = re.compile(r"""[.!?]+["')\]]*(?=\s+["'(\[]*[A-Z0-9_])|:(?=[ \t]*\n)""")
# Upper case with or without a colon ("WARNING Do not ..."), any case with a colon.
_SIGNAL_RE = re.compile(r"^\s*(?:(WARNING|CAUTION|NOTE)\b\s*[:.\-\u2013\u2014]?"
                        r"|((?i:warning|caution|note))\s*:)\s*")
_CONDITION_RE = re.compile(
    r"^(?:if|when|before|after|while|unless|until|once|during|for|to|in case|as soon as)"
    r"\b[^,]{0,120},\s*", re.I)

# ponytail: a closed verb list, not a POS tagger; a noun-first sentence that
# starts with one of these words ("Set screws hold ...") is read as an instruction.
IMPERATIVE_VERBS = frozenset("""
add adjust align apply attach bend calculate change clean clear click close
compare complete connect continue copy cut decrease delete disconnect do download
drain drill edit enter erase examine fill find follow get give go hold identify
increase install keep let lift loosen lower lubricate make measure monitor move
obey open paste prepare press prevent pull push put raise read record refer release
remove repeat replace reset restart run save see select send set show start stay
stop supply switch tell test tighten touch turn type update upload use wait wear
write never always please
""".split())

TOKEN_RE = re.compile(r"\w[\w'.\-/]*\w|\w")
_UNITS = frozenset("""
mm cm m km ft yd mi kg g mg lb lbs oz t N Nm kN psi bar mbar Pa kPa MPa V mV kV mA
W kW MW Wh kWh Hz kHz MHz GHz s ms us ns sec secs second seconds min mins minute
minutes h hr hrs hour hours day days week weeks month months year years
KB MB GB TB PB KiB MiB GiB TiB kB Mb Gb bit bits byte bytes rpm L l ml mL dB px pt
em rem fps Mbps Gbps percent degrees deg
""".split())


@dataclass
class Segment:
    """One sentence (or heading, or table cell) with its location and kind."""
    id: str
    file: str
    line: int
    block: int
    index: int          # sentence index inside the block
    kind: str
    text: str
    masked: str
    words: int = 0

    def to_json(self) -> dict[str, object]:
        return {"id": self.id, "file": self.file, "line": self.line, "block": self.block,
                "index": self.index, "kind": self.kind, "text": self.text,
                "words": self.words}


def is_imperative(masked: str) -> bool:
    """True if the sentence starts with a command verb, after an optional condition."""
    s = _CONDITION_RE.sub("", masked.strip(), count=1)
    toks = TOKEN_RE.findall(s)
    if not toks:
        return False
    first = toks[0].lower()
    if first == "you" and len(toks) > 1 and toks[1].lower() in ("must", "should", "need", "have"):
        return True
    return first in IMPERATIVE_VERBS


def _top_spans(masked: str) -> list[tuple[int, int]]:
    """Top-level parenthesized and double-quoted spans, in order."""
    spans: list[tuple[int, int]] = []
    depth = 0
    open_at = -1
    in_quote = -1
    for i, ch in enumerate(masked):
        if in_quote >= 0:
            if ch == '"':
                spans.append((in_quote, i + 1))
                in_quote = -1
            continue
        if ch == "(":
            if depth == 0:
                open_at = i
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
            if depth == 0:
                spans.append((open_at, i + 1))
        elif ch == '"' and depth == 0:
            in_quote = i
    return spans


def word_units(masked: str) -> list[tuple[int, int]]:
    """Return the spans that STE counts as one word each (rules 8.5-8.7)."""
    spans = _top_spans(masked)
    units: list[tuple[int, int, str]] = []   # (start, end, kind)
    pos = 0
    for a, b in spans + [(len(masked), len(masked))]:
        for m in TOKEN_RE.finditer(masked, pos, a):
            units.append((m.start(), m.end(), "tok"))
        if a < b:
            units.append((a, b, "span"))
        pos = b

    merged: list[tuple[int, int, str]] = []
    for u in units:
        if merged and u[2] == "tok" and merged[-1][2] in ("tok", "num", "title"):
            pa, pb, pk = merged[-1]
            gap = masked[pb:u[0]]
            word = masked[u[0]:u[1]]
            prev = masked[pa:pb]
            # A number and its unit count as one word: "20 kg", "5-10 mm".
            if pk == "tok" and re.fullmatch(r"\d[\d.,\-\u2013]*", prev) and word in _UNITS \
                    and gap in ("", " "):
                merged[-1] = (pa, u[1], "num")
                continue
            # A Title Case run after the first word is one proper noun: "Claude Code".
            if gap == " " and word[0].isupper() and prev[0].isupper() and len(merged) > 1:
                merged[-1] = (pa, u[1], "title")
                continue
        merged.append(u)
    return [(a, b) for a, b, _ in merged]


def count_words(masked: str) -> int:
    """STE word count of a masked sentence."""
    return len(word_units(masked))


def _split_sentences(masked: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    pos = 0
    for m in _END_RE.finditer(masked):
        if m.group(0)[0] == ".":
            before = re.search(r"[A-Za-z.]+$", masked[:m.start()])
            word = before.group(0).lower().strip(".") if before else ""
            if word in _NO_SPLIT:
                continue
        spans.append((pos, m.end()))
        pos = m.end()
    spans.append((pos, len(masked)))
    out = []
    for a, b in spans:
        while a < b and masked[a].isspace():
            a += 1
        while b > a and masked[b - 1].isspace():
            b -= 1
        if a < b and TOKEN_RE.search(masked, a, b):
            out.append((a, b))
    return out


def segment_text(text: str, file: str = "<text>", doc_type: str = "auto",
                 start_id: int = 1) -> list[Segment]:
    """Split markdown or plain text into segments.

    ``doc_type`` is ``auto``, ``proc`` or ``desc``. It overrides the kind of
    ordinary sentences, but not titles, cells or safety blocks.
    """
    if doc_type not in ("auto", "proc", "desc"):
        raise ValueError(f"doc_type must be auto, proc or desc, not {doc_type!r}")
    segs: list[Segment] = []
    for bi, block in enumerate(split_blocks(text)):
        joined = "\n".join(c for _, c in block.lines)
        starts: list[int] = []
        off = 0
        for _, c in block.lines:
            starts.append(off)
            off += len(c) + 1
        masked = mask(joined)

        signal = block.signal
        m = _SIGNAL_RE.match(masked)
        if m and block.kind != "title" and m.end() < len(masked):
            signal = (m.group(1) or m.group(2)).lower()
            masked = " " * m.end() + masked[m.end():]

        if block.kind in ("title", "cell"):
            a, b = len(masked) - len(masked.lstrip()), len(masked.rstrip())
            spans = [(a, b)] if TOKEN_RE.search(masked) else []
        else:
            spans = _split_sentences(masked)

        for si, (a, b) in enumerate(spans):
            sm = masked[a:b]
            if block.kind == "title":
                kind = "title"
            elif block.kind == "cell":
                kind = "cell"
            elif signal:
                kind = signal
            elif doc_type != "auto":
                kind = doc_type
            elif block.kind == "num" and si == 0:
                kind = "proc"
            else:
                kind = "proc" if is_imperative(sm) else "desc"
            line = block.lines[bisect.bisect_right(starts, a) - 1][0]
            segs.append(Segment(id=f"s{start_id + len(segs)}", file=file, line=line,
                                block=bi, index=si, kind=kind, text=joined[a:b],
                                masked=sm, words=count_words(sm)))
    return segs
