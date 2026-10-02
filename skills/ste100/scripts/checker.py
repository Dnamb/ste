"""STE100 checker: markdown segmentation, STE word count, detectors and score.

Stdlib only (Python 3.11+). Rule text is paraphrased in references/rules.md,
which is the single source of truth for rule tiers and methods.

Offsets: every segment keeps its original ``text`` and a ``masked`` copy of the
same length. Masking turns markup into spaces and code/URLs into ``_`` runs, so
detectors can use regexes on ``masked`` and report spans in ``text``.
"""
from __future__ import annotations

import bisect
import functools
import math
import logging
import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import NamedTuple

import dictionary
from ste_config import PRESETS, enforced_tier, parse_level  # noqa: F401  (re-exported)

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
check verify ensure perform execute create place inspect insert configure enable
disable navigate launch choose pick take determine confirm specify define avoid try
look consider allow provide mount unscrew assemble disassemble rotate fasten flush
rinse wipe unplug reboot quit cancel accept approve notify inform explain describe
convert divide join mix pour ask retry rename deploy locate discard detach bring
carry hang wrap uninstall
utilize utilise commence begin obtain employ initiate proceed implement modify submit
terminate eliminate evaluate establish retain observe maintain request acquire attempt
indicate assist facilitate accomplish achieve generate produce dispose finish decide
combine split reduce protect
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
    steps: list[tuple[int, Segment, bool]] = []     # (block, first sentence, imperative)
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
            if block.kind == "num" and si == 0 and kind == "proc":
                steps.append((bi, segs[-1], is_imperative(sm)))

    # A numbered list is a procedure only if at least half of its items are commands.
    # If not, it is a numbered description ("1. The element melts.").
    # ponytail: a list is a run of adjacent numbered blocks, so a nested bullet splits it;
    # use the list structure from split_blocks if that gives wrong kinds.
    lists: list[list[tuple[int, Segment, bool]]] = []
    for step in steps:
        if lists and step[0] == lists[-1][-1][0] + 1:
            lists[-1].append(step)
        else:
            lists.append([step])
    for items in lists:
        if 2 * sum(imp for _, _, imp in items) < len(items):
            for _, seg, imp in items:
                if not imp:
                    seg.kind = "desc"
    return segs


# --------------------------------------------------------------------------
# Rules and findings
# --------------------------------------------------------------------------

_RULE_ID_RE = re.compile(r"^(\d+\.\d+|GR-\d+|HS)$")
SENTENCE_KINDS = ("proc", "desc", "warning", "caution", "note")


class Rule(NamedTuple):
    id: str
    text: str
    tier: int | None        # None = counting rule (never gives a finding)
    method: str             # auto | heuristic | judgment | counting
    example: str


@functools.cache
def load_rules(path: Path = RULES_PATH) -> dict[str, Rule]:
    """Parse the rule table in references/rules.md (the single source of truth)."""
    rules: dict[str, Rule] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| "):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 5 or not _RULE_ID_RE.match(cells[0]):
            continue
        rid, text, tier, method, example = cells
        rules[rid] = Rule(rid, text, None if tier == "-" else int(tier), method, example)
    if not rules:
        raise ValueError(f"no rule rows found in {path}")
    return rules


@dataclass
class Finding:
    """One rule violation. ``start``/``end`` are offsets in the segment text."""
    rule: str
    tier: int
    conf: str                   # high | low (low needs a review to count)
    message: str
    fix: str = ""
    seg: str = ""               # segment id; "" = document level
    file: str = ""
    line: int = 0
    start: int = 0
    end: int = 0
    quote: str = ""
    source: str = "ste"         # ste | house | review | dict
    status: str = "open"        # open | confirmed | rejected | added
    id: str = ""
    note: str = ""              # review reason or comment

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def _f(seg: Segment, rule: str, a: int, b: int, message: str, fix: str = "",
       conf: str = "high", tier: int | None = None, source: str = "ste") -> Finding:
    if tier is None:
        tier = load_rules()[rule].tier or 100
    return Finding(rule=rule, tier=tier, conf=conf, message=message, fix=fix, seg=seg.id,
                   file=seg.file, line=seg.line + seg.text.count("\n", 0, a), start=a, end=b,
                   quote=seg.text[a:b], source=source)


class _Reg(NamedTuple):
    rules: tuple[str, ...]
    kinds: tuple[str, ...]
    doc: bool
    fn: Callable[..., Iterable[Finding]]


DETECTORS: list[_Reg] = []


def detector(*rules: str, kinds: tuple[str, ...] = KINDS, doc: bool = False):
    """Register a detector. ``doc=True`` detectors get the full segment list."""
    def deco(fn):
        DETECTORS.append(_Reg(rules, kinds, doc, fn))
        return fn
    return deco


def _clause_start(masked: str) -> int:
    """Offset of the main clause: after leading space and an optional "If ...," part."""
    s = masked.lstrip()
    m = _CONDITION_RE.match(s)
    return len(masked) - len(s) + (m.end() if m else 0)


def _first_token_at(masked: str) -> int:
    m = TOKEN_RE.search(masked, _clause_start(masked))
    return m.start() if m else -1


def _prev_word(masked: str, pos: int) -> str:
    toks = TOKEN_RE.findall(masked, 0, pos)
    return toks[-1].lower() if toks else ""


def _next_word(masked: str, pos: int) -> str:
    m = TOKEN_RE.search(masked, pos)
    return m.group(0).lower() if m else ""


def _keep_case(src: str, repl: str) -> str:
    return repl[:1].upper() + repl[1:] if src[:1].isupper() else repl


# --------------------------------------------------------------------------
# Word lists for verb detectors
# --------------------------------------------------------------------------

IRREGULAR_PP = frozenset("""
been done gone seen given taken made built sent kept held found shown told written
broken chosen run set put cut shut hit let left lost paid said sold spent stood brought
bought caught taught thought fought sought begun drawn driven eaten fallen flown
forgotten frozen grown hidden known ridden risen shaken spoken stolen sworn thrown torn
worn woken become come got gotten heard led lit meant met read sat slept understood won
wound bound fed fled hung laid dealt felt dug spun struck stuck swung withdrawn
overridden rewritten undone rebuilt reset upset split spread sunk shrunk
""".split())
_ED_NOT_PP = frozenset("hundred red bed shed sled naked wicked sacred embed shred bred "
                       "need seed feed speed weed bleed breed deed heed reed tweed".split())
# Words that end in "ing" but are not -ing verb forms.
_NOT_ING = frozenset("ceiling morning evening nothing something anything everything during "
                     "pudding awning earring sibling darling inkling sterling herring "
                     "lightning".split())
# -ing adjectives that are normal after BE ("The file is missing").
ADJ_ING = frozenset("""
interesting confusing existing pending missing remaining boring amazing annoying surprising
misleading exciting challenging demanding frustrating promising outstanding encouraging
alarming disturbing overwhelming satisfying tiring willing charming convincing appealing
compelling daunting corresponding ongoing upcoming incoming outgoing
""".split())
_BE_FORMS = ("am", "is", "are", "was", "were", "be")
_MODALS = ("will", "would", "shall", "should", "can", "could", "may", "might", "must")


def is_participle(word: str) -> bool:
    """True for a past participle form (regular -ed or a listed irregular form)."""
    w = word.lower()
    if w in IRREGULAR_PP:
        return True
    if len(w) < 4 or not w.endswith("ed") or w in _ED_NOT_PP:
        return False
    return not w.endswith("eed") or w in ("agreed", "freed", "guaranteed", "decreed")


def is_ing(word: str) -> bool:
    """True for an -ing verb form (not "thing", "ceiling", "during" ...)."""
    w = word.lower()
    return (w.endswith("ing") and len(w) > 4 and w not in _NOT_ING
            and re.search(r"[aeiouy]", w[:-3]) is not None)


# --------------------------------------------------------------------------
# Deterministic detectors
# --------------------------------------------------------------------------

@detector("5.1", "6.3", kinds=SENTENCE_KINDS)
def _length(seg: Segment) -> Iterator[Finding]:
    if seg.kind == "desc" or (seg.kind in ("warning", "caution")
                              and not is_imperative(seg.masked)):
        rule, limit, what = "6.3", 25, "description"
    elif seg.kind == "note":
        rule, limit, what = "5.1", 25, "note"
    else:
        rule, limit, what = "5.1", 20, "instruction"
    if seg.words > limit:
        yield _f(seg, rule, 0, len(seg.text),
                 f"{seg.words} words; the limit for a {what} is {limit}.",
                 "Divide it into shorter sentences, or use a vertical list.")


@detector("6.6", doc=True)
def _paragraph(segs: list[Segment]) -> Iterator[Finding]:
    count: dict[tuple[str, int], int] = defaultdict(int)
    for seg in segs:
        if seg.kind != "desc":
            continue
        key = (seg.file, seg.block)
        count[key] += 1
        if count[key] == 7:
            yield _f(seg, "6.6", 0, len(seg.text),
                     "The paragraph has more than 6 sentences.",
                     "Divide the paragraph. Give each paragraph one topic.")


@detector("8.1")
def _semicolon(seg: Segment) -> Iterator[Finding]:
    for m in re.finditer(";", seg.masked):
        yield _f(seg, "8.1", m.start(), m.end(), "Do not use semicolons.",
                 "Write two sentences, or use a vertical list.")


_CONTRACTION_RE = re.compile(
    r"\b(\w+)(n't|'re|'ve|'ll|'d|'m)\b"
    r"|\b(it|that|there|here|what|who|let|he|she|where|how)('s)\b", re.I)
_CONTRACTION_FIX = {"n't": " not", "'re": " are", "'ve": " have", "'ll": " will",
                    "'d": " would (or had)", "'m": " am", "'s": " is (or has)"}
_NOT_IRREGULAR = {"can": "cannot", "ca": "cannot", "wo": "will not", "sha": "shall not"}


@detector("4.2")
def _contractions(seg: Segment) -> Iterator[Finding]:
    for m in _CONTRACTION_RE.finditer(seg.masked):
        base, suffix = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        suffix = suffix.lower()
        if suffix == "n't" and base.lower() in _NOT_IRREGULAR:
            fix = _NOT_IRREGULAR[base.lower()]
        elif base.lower() == "let" and suffix == "'s":
            fix = "let us"
        else:
            fix = base.lower() + _CONTRACTION_FIX[suffix]
        yield _f(seg, "4.2", m.start(), m.end(), "Do not use contractions.",
                 _keep_case(m.group(0), fix))


_LATIN = {"eg": "for example", "ie": "that is", "etc": "and other items (or give the list)",
          "viz": "that is", "cf": "refer to", "etal": "and others", "vs": "compared to, or"}
_LATIN_RE = re.compile(
    r"(?<![\w.])(e\.\s?g\b\.?|i\.\s?e\b\.?|etc\b\.?|viz\.|cf\.|et\s+al\b\.?|vs\b\.?)", re.I)


@detector("GR-6")
def _latin(seg: Segment) -> Iterator[Finding]:
    for m in _LATIN_RE.finditer(seg.masked):
        key = re.sub(r"[\s.]", "", m.group(1).lower())
        yield _f(seg, "GR-6", m.start(), m.end(), "Do not use Latin abbreviations.",
                 _LATIN[key])


_HAVE_BEEN_RE = re.compile(r"\b(?:has|have|had|having)\s+been\b", re.I)
_MODAL_HAVE_RE = re.compile(rf"\b(?:{'|'.join(_MODALS)})\s+(?:not\s+)?have\s+(\w+)", re.I)
_BE_BEING_RE = re.compile(r"\b(?:am|is|are|was|were|be)\s+being\b", re.I)
_GOING_TO_RE = re.compile(r"\b(?:am|is|are|was|were)\s+going\s+to\b", re.I)
_USED_TO_RE = re.compile(r"\bused\s+to\s+(\w+)", re.I)
_SUBJECT_PRON = ("i", "we", "you", "they", "he", "she", "it", "this", "that", "there", "people")


@detector("3.4", kinds=SENTENCE_KINDS)
def _complex_verbs(seg: Segment) -> Iterator[Finding]:
    s = seg.masked
    for m in _MODAL_HAVE_RE.finditer(s):
        if is_participle(m.group(1)):
            yield _f(seg, "3.4", m.start(), m.end(),
                     "Do not use a complex verb structure (modal + have + participle).",
                     "Use the simple past or the simple future.")
    for m in _HAVE_BEEN_RE.finditer(s):
        if _prev_word(s, m.start()) not in _MODALS:
            yield _f(seg, "3.4", m.start(), m.end(),
                     "Do not use a complex verb structure (has/have/had been).",
                     "Use the simple present or the simple past.")
    for m in _BE_BEING_RE.finditer(s):
        yield _f(seg, "3.4", m.start(), m.end(),
                 "Do not use a complex verb structure (is being).", "Use the simple present.")
    for m in _GOING_TO_RE.finditer(s):
        yield _f(seg, "3.4", m.start(), m.end(), "Do not use \"going to\" for the future.",
                 "Use WILL.")
    for m in _USED_TO_RE.finditer(s):
        prev = _prev_word(s, m.start())
        if prev in _BE_FORMS + ("been", "being", "get", "gets", "got") \
                or m.group(1).lower() in ("it", "this", "that", "the"):
            continue   # "is used to remove" (passive) or "get used to it"
        # ponytail: "the tool used to cut" (a reduced relative) looks the same as the
        # habit form, so only a pronoun subject gives high confidence. Needs a parser.
        yield _f(seg, "3.4", m.start(), m.end() - len(m.group(1)) - 1,
                 "Do not use \"used to\" for a past habit.", "Use the simple past.",
                 conf="high" if prev in _SUBJECT_PRON else "low")


_PERFECT_RE = re.compile(
    r"\b(has|have|had)\s+(?:(?:not|never|already|just|also|always|recently|now|since)\s+)?"
    r"([A-Za-z]+)\b", re.I)
_PROG_RE = re.compile(
    r"\b(am|is|are|was|were|be)\s+(?:(?:not|still|currently|now|also|always|already|"
    r"constantly|actively)\s+)?([A-Za-z]+)\b", re.I)
_DETERMINERS = frozenset("a an the this that these those its their our your my his her "
                         "each all some any no every it them".split())
_PREPOSITIONS = frozenset("to from in on at by for with into onto of over under through "
                          "after before during since off out up down".split())


@detector("3.2", kinds=SENTENCE_KINDS)
def _tenses(seg: Segment) -> Iterator[Finding]:
    s = seg.masked
    for m in _PERFECT_RE.finditer(s):
        word = m.group(2)
        if word.lower() == "been" or not is_participle(word) \
                or _prev_word(s, m.start()) in _MODALS:
            continue   # 3.4 covers "has been" and "will have done"
        nxt = _next_word(s, m.end())
        clear = nxt == "" or nxt in _DETERMINERS or nxt in _PREPOSITIONS
        yield _f(seg, "3.2", m.start(), m.end(),
                 "Do not use the perfect tense (has/have/had + participle).",
                 "Use the simple past or the simple present.",
                 conf="high" if clear else "low")
    for m in _PROG_RE.finditer(s):
        word = m.group(2)
        lw = word.lower()
        if not is_ing(word) or lw in ADJ_ING or lw == "being" \
                or (lw == "going" and _next_word(s, m.end()) == "to"):
            continue
        yield _f(seg, "3.2", m.start(), m.end(),
                 "Do not use the progressive tense (BE + -ing).",
                 "Use the simple present (\"The pump operates\").")


_MAKE_SURE_RE = re.compile(
    r"\b(?:make|makes|made|making)\s+sure\b(?=\s+\w)(?!\s+(?:that|of)\b)", re.I)


@detector("GR-1")
def _make_sure(seg: Segment) -> Iterator[Finding]:
    for m in _MAKE_SURE_RE.finditer(seg.masked):
        yield _f(seg, "GR-1", m.start(), m.end(), "Keep \"that\" after \"make sure\".",
                 _keep_case(m.group(0), m.group(0).lower() + " that"))


def _british_map() -> dict[str, str]:
    """British -> US spellings, from explicit stems (no generic regex)."""
    m: dict[str, str] = {}
    for s in ("colour behaviour favour honour labour neighbour flavour humour rumour harbour "
              "vapour armour odour endeavour").split():
        for suf in ("", "s", "ed", "ing", "ful", "less", "able", "ably", "ite", "ites", "er",
                    "ers", "al", "ally", "hood", "hoods"):
            m[s + suf] = s[:-3] + "or" + suf
    for s in ("centre metre litre fibre theatre calibre lustre sombre spectre meagre "
              "kilometre centimetre millimetre micrometre nanometre millilitre").split():
        us = s[:-2] + "er"
        m.update({s: us, s + "s": us + "s", s[:-1] + "ed": us + "ed",
                  s[:-1] + "ing": us + "ing"})
    ise = ("apologis authoris capitalis categoris centralis characteris criticis customis "
           "emphasis finalis generalis harmonis initialis itemis legalis localis magnetis "
           "maximis memoris minimis mobilis modernis monetis neutralis normalis optimis "
           "organis parameteris personalis polaris prioritis randomis rationalis realis "
           "recognis serialis sanitis specialis stabilis standardis sterilis summaris "
           "symbolis synchronis tokenis visualis vaporis virtualis").split()
    for stem in ise:
        for suf in ("e", "es", "ed", "ing", "ation", "ations", "er", "ers"):
            m[stem + suf] = stem[:-1] + "z" + suf
    for stem in ("analys", "paralys", "catalys", "hydrolys"):   # not "analyses" (a noun)
        for suf in ("e", "ed", "ing", "er", "ers"):
            m[stem + suf] = stem[:-1] + "z" + suf
    for v in ("travel label cancel model signal fuel level total channel tunnel marshal "
              "dial funnel equal jewel counsel").split():
        m.update({v + "led": v + "ed", v + "ling": v + "ing", v + "ler": v + "er",
                  v + "lers": v + "ers"})
    m.update({
        "defence": "defense", "licence": "license", "licences": "licenses",
        "offence": "offense", "pretence": "pretense", "catalogue": "catalog",
        "catalogues": "catalogs", "analogue": "analog", "programme": "program",
        "programmes": "programs", "grey": "gray", "aluminium": "aluminum", "tyre": "tire",
        "tyres": "tires", "mould": "mold", "moulds": "molds", "moulded": "molded",
        "moulding": "molding", "draught": "draft", "cheque": "check", "manoeuvre": "maneuver",
        "manoeuvres": "maneuvers", "aeroplane": "airplane", "ageing": "aging",
        "judgement": "judgment", "acknowledgement": "acknowledgment", "artefact": "artifact",
        "artefacts": "artifacts", "enrolment": "enrollment", "fulfil": "fulfill",
        "fulfilment": "fulfillment", "instalment": "installment", "skilful": "skillful",
        "wilful": "willful", "plough": "plow", "sulphur": "sulfur", "kerb": "curb",
        "amongst": "among", "learnt": "learned", "spelt": "spelled", "sceptical": "skeptical",
        "practise": "practice", "practised": "practiced", "practising": "practicing",
        "cosy": "cozy", "paediatric": "pediatric", "anaesthetic": "anesthetic",
    })
    return m


_BRITISH = _british_map()
_BRITISH_RE = re.compile(
    r"(?<![\w'-])(" + "|".join(sorted(map(re.escape, _BRITISH), key=len, reverse=True))
    + r")(?![\w'-])", re.I)


@detector("1.14")
def _spelling(seg: Segment) -> Iterator[Finding]:
    for m in _BRITISH_RE.finditer(seg.masked):
        word = m.group(1)
        yield _f(seg, "1.14", m.start(), m.end(), "Use American English spelling.",
                 _keep_case(word, _BRITISH[word.lower()]))


_NUMBER_WORDS = ("one two three four five six seven eight nine ten eleven twelve fifteen "
                 "twenty thirty forty fifty sixty seventy eighty ninety hundred thousand "
                 "half dozen").split()
_ABOUT_NUM_RE = re.compile(
    r"\babout(?=\s+(?:\d|(?:a\s+|an\s+)?(?:" + "|".join(_NUMBER_WORDS) + r")\b))", re.I)
_FOLLOW_RE = re.compile(
    r"\b(follow(?:s|ed)?)\s+(?:(?:the|these|all|this|those|our|my|your|its|their|any)\s+)?"
    r"(?:\w+\s+)?(?:instructions?|procedures?|steps?|rules?|guidelines?|guide|advice|"
    r"directions?|recommendations?|regulations?|precautions?|polic(?:y|ies)|prompts?)\b",
    re.I)


@detector("1.3")
def _meanings(seg: Segment) -> Iterator[Finding]:
    for m in _ABOUT_NUM_RE.finditer(seg.masked):
        yield _f(seg, "1.3", m.start(), m.end(),
                 "ABOUT means \"concerned with\" in STE, not \"approximately\".",
                 _keep_case(m.group(0), "approximately"), tier=80)
    for m in _FOLLOW_RE.finditer(seg.masked):
        yield _f(seg, "1.3", m.start(), m.end(1),
                 "FOLLOW means \"come after\" in STE. For instructions, use OBEY.",
                 _keep_case(m.group(1), "obey"), tier=80)


_GENDER_RE = re.compile(r"\b(he/she|she/he|s/he|his/her|him/her|himself|herself|he|she|him|"
                        r"his|hers|her)\b", re.I)
_GENDER_NOUN_RE = re.compile(r"\b(man|woman|men|women|manpower|workman|workmen)\b", re.I)
_PERSON_RE = re.compile(r"\b(?:users?|operators?|developers?|engineers?|technicians?|"
                        r"persons?|customers?|readers?|administrators?|admins?|someone|"
                        r"anyone|everyone|each|every|pilots?|mechanics?|drivers?|students?|"
                        r"workers?|employees?|clients?|owners?|authors?|writers?|"
                        r"programmers?|members?|visitors?|callers?|reviewers?)\b", re.I)


@detector("GR-7")
def _inclusive(seg: Segment) -> Iterator[Finding]:
    # ponytail: a pronoun for a named person is correct; only a role noun in the
    # same sentence gives high confidence. A coreference check would do better.
    generic = _PERSON_RE.search(seg.masked) is not None
    for m in _GENDER_RE.finditer(seg.masked):
        yield _f(seg, "GR-7", m.start(), m.end(),
                 "Do not use \"he\" or \"she\" for a person in general.",
                 "Repeat the noun, use \"they\", or write the sentence again.",
                 conf="high" if generic else "low")
    for m in _GENDER_NOUN_RE.finditer(seg.masked):
        yield _f(seg, "GR-7", m.start(), m.end(), "Use a word that is not gender-specific.",
                 "person, personnel, operator", conf="low")


# --------------------------------------------------------------------------
# Curated word substitutions (assets/words.tsv)
# --------------------------------------------------------------------------

class WordRow(NamedTuple):
    word: str
    pos: str
    alt: str
    rule: str
    tier: int
    source: str             # ste | house
    ctx: str                # any | v (match only in a verb context)
    conf: str               # high | low
    note: str


_WORD_COLS = ("word", "pos", "alternative", "rule", "tier", "source", "ctx", "conf", "note")


@functools.cache
def load_words(path: Path = WORDS_PATH) -> tuple[WordRow, ...]:
    """Read and validate words.tsv. Raises ValueError on a bad row."""
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.startswith("#")]
    if not lines or tuple(lines[0].split("\t")) != _WORD_COLS:
        raise ValueError(f"{path}: the header must be: {' '.join(_WORD_COLS)}")
    rules = load_rules()
    rows: list[WordRow] = []
    for n, line in enumerate(lines[1:], 2):
        cells = line.split("\t")
        cells += [""] * (len(_WORD_COLS) - len(cells))   # an editor can strip a trailing tab
        if len(cells) != len(_WORD_COLS):
            raise ValueError(f"{path}: data row {n} has {len(cells)} columns")
        word, pos, alt, rule, tier, source, ctx, conf, note = (c.strip() for c in cells)
        if (rule not in rules or tier not in ("60", "80", "100") or source not in ("ste", "house")
                or ctx not in ("any", "v") or conf not in ("high", "low") or not word or not alt):
            raise ValueError(f"{path}: data row {n} ({word!r}) has a bad value")
        rows.append(WordRow(word.lower(), pos, alt, rule, int(tier), source, ctx, conf, note))
    return tuple(rows)


_IRREGULAR_VERBS = {"begin": ("began", "begun"), "take": ("took", "taken"),
                    "choose": ("chose", "chosen"), "understand": ("understood",),
                    "find": ("found",), "get": ("got", "gotten"), "make": ("made",),
                    "have": ("has", "had", "having")}
_NEVER_FLAG = frozenset({"remaining"})        # REMAINING (adj) is approved
_VERB_CTX = frozenset("to must can cannot will not you we they i please shall should may "
                      "might would could don't doesn't didn't won't can't".split())
_OBJ = r"(?:\s+(?:it|them|this|that|these|those|everything))?"


def word_forms(word: str, pos: str) -> set[str]:
    """Inflected forms of one word. Extra forms that are not English are harmless."""
    w = word
    if pos == "n":
        return {w, w[:-1] + "ies" if re.search(r"[^aeiou]y$", w) else
                w + ("es" if w.endswith(("s", "x", "z", "ch", "sh")) else "s")}
    if pos != "v":
        return {w}
    forms = {w, *_IRREGULAR_VERBS.get(w, ())}
    if w.endswith("e") and not w.endswith("ee"):
        forms |= {w + "s", w + "d", w[:-1] + "ing"}
    elif re.search(r"[^aeiou]y$", w):
        forms |= {w[:-1] + "ies", w[:-1] + "ied", w + "ing"}
    else:
        forms |= {w + ("es" if w.endswith(("s", "x", "z", "ch", "sh")) else "s"),
                  w + "ed", w + "ing"}
        if re.search(r"[^aeiou][aeiou][^aeiouwxy]$", w):     # stop -> stopped, stopping
            forms |= {w + w[-1] + "ed", w + w[-1] + "ing"}
    return forms


def _row_pattern(row: WordRow) -> str:
    first, *rest = row.word.split()
    forms = sorted(word_forms(first, row.pos), key=len, reverse=True)
    pat = "(?:" + "|".join(map(re.escape, forms)) + ")"
    if rest:
        pat += (_OBJ if row.rule == "9.3" else "") + r"\s+" + r"\s+".join(map(re.escape, rest))
    return pat


@functools.cache
def _word_matchers() -> tuple[tuple[re.Pattern[str], tuple[WordRow, ...]], ...]:
    """One regex per source (ste, house), so a house phrase never hides an STE word."""
    out = []
    for source in ("ste", "house"):
        # Longer phrases first, so "instead of" wins over "instead".
        rows = sorted((r for r in load_words() if r.source == source),
                      key=lambda r: (-len(r.word.split()), -len(r.word)))
        alts = "|".join(f"(?P<r{i}>{_row_pattern(r)})" for i, r in enumerate(rows))
        out.append((re.compile(rf"(?<![\w'-])(?:{alts})(?![\w'-])", re.I), tuple(rows)))
    return tuple(out)


_POS_NAME = {"v": "verb", "n": "noun", "adj": "adjective", "adv": "adverb",
             "prep": "preposition", "conj": "conjunction", "pron": "pronoun"}


def _word_message(row: WordRow, quote: str) -> str:
    if row.source == "house":
        msg = f"\"{quote}\" adds words but no meaning (house style, not scored)."
    elif row.rule == "1.2":
        msg = f"\"{row.word}\" is not approved as a {_POS_NAME.get(row.pos, row.pos)}."
    elif row.rule == "1.3":
        msg = f"\"{row.word}\" is approved only with a different meaning."
    elif row.rule == "9.3":
        msg = f"\"{quote}\" is a phrasal verb."
    else:
        msg = f"\"{row.word}\" ({row.pos}) is not an approved STE word."
    return msg + (f" Note: {row.note.rstrip('.')}." if row.note else "")


@detector("1.1", "1.2", "1.3", "9.3", "HS")
def _words(seg: Segment) -> Iterator[Finding]:
    s = seg.masked
    first_at = _first_token_at(s)
    for rx, rows in _word_matchers():
        for m in rx.finditer(s):
            row = rows[int(m.lastgroup[1:])]
            text = m.group(0)
            if text.lower() in _NEVER_FLAG:
                continue
            # A Title Case word inside a sentence is a name ("Main Street"), not prose.
            if seg.kind != "title" and m.start() > first_at and text[0].isupper() \
                    and not text.split()[0].isupper():
                continue
            if row.ctx == "v":
                prev = _prev_word(s, m.start())
                if not (m.start() == first_at or prev in _VERB_CTX
                        or (seg.kind == "proc" and prev in ("and", "then", "or"))):
                    continue
            yield _f(seg, row.rule, m.start(), m.end(), _word_message(row, text), row.alt,
                     conf=row.conf, tier=row.tier, source=row.source)


# --------------------------------------------------------------------------
# Imported dictionary (dictionary mode)
# --------------------------------------------------------------------------

MAX_UNKNOWN = 50                    # unknown-word findings in one check
_DICT_WORD = re.compile(r"[A-Za-z][a-z]*(?:'s)?")      # camelCase and ACRONYMS are skipped


def _dictionary_findings(segments: list[Segment], table: dictionary.Lookup,
                         allow: Iterable[str]) -> Iterator[Finding]:
    """Rule 1.1 and 1.4 findings from the user's imported dictionary.

    - a word that is only unapproved: high (low for a verb outside a verb context,
      because many verbs are also technical nouns);
    - a form of an approved word that the dictionary does not list: low (rule 1.4);
    - a word that is not in the dictionary: low, one finding for each word, no more
      than MAX_UNKNOWN. The reviewer decides if it is a technical noun or verb.
    Acronyms, names (Title Case inside a sentence), hyphenated words, words with
    digits and allow terms are skipped.
    """
    skip = {t.strip().lower() for t in allow}
    seen: set[str] = set()
    unknown = 0
    for seg in segments:
        s = seg.masked
        first_at = _first_token_at(s)
        for m in TOKEN_RE.finditer(s):
            tok = m.group(0)
            if not _DICT_WORD.fullmatch(tok) or len(tok) < 2 or tok.isupper():
                continue
            if seg.kind != "title" and m.start() > first_at and tok[0].isupper():
                continue
            word = tok[:-2] if tok.lower().endswith("'s") else tok
            if word.lower() in skip:
                continue
            kind, info = dictionary.classify(word, table)
            if kind == "unapproved":
                entries = info
                verb_only = all(e["pos"] == "v" for e in entries)
                prev = _prev_word(s, m.start())
                verb_ctx = m.start() == first_at or prev in _VERB_CTX
                alts = list(dict.fromkeys(a for e in entries for a in e.get("alts", [])))
                heads = ", ".join(f"{e['word']} ({e['pos']})" for e in entries)
                yield _f(seg, "1.1", m.start(), m.start() + len(word),
                         f"\"{word}\" is not an approved STE word (dictionary: {heads}).",
                         " or ".join(alts) or "Use an approved word.",
                         conf="low" if verb_only and not verb_ctx else "high",
                         source="dict")
            elif kind == "form" and not word.lower().endswith("ing"):     # -ing: rule 3.5
                yield _f(seg, "1.4", m.start(), m.start() + len(word),
                         f"\"{word}\" is not a form of {str(info).upper()} that the "
                         "dictionary lists.", f"Use a listed form of {str(info).upper()}.",
                         conf="low", source="dict")
            elif kind == "unknown" and word.lower() not in seen and unknown < MAX_UNKNOWN:
                seen.add(word.lower())
                unknown += 1
                yield _f(seg, "1.1", m.start(), m.start() + len(word),
                         f"\"{word}\" is not in the STE dictionary. Reject this finding if it "
                         "is a technical noun or verb of the subject.",
                         "Use an approved word, or add the term to the allow list.",
                         conf="low", source="dict")


# --------------------------------------------------------------------------
# Heuristic detectors
# --------------------------------------------------------------------------
# ponytail: no POS tagger. These detectors use word lists and word order, so most
# of their findings are "low" and count only when the LLM review confirms them.
# Upgrade path: an optional POS tagger (for example spaCy) behind a --with extra.

_FUNCTION = frozenset("""
a an the this that these those it its they them their theirs there here he she his her we
our ours you your yours i my me us him one ones is are was were be been being am has have
had do does did will would shall should can could may might must not no nor and or but if
then than so as at by for from in into of off on onto out over to under up down with
without about after before between during through until when where which while who whom
whose why how what all any each every some many much more most few less least other
another such only also very too just even still again always never often both either
neither per via against across along around behind below beneath beside beyond inside
outside near since upon within toward towards because although though unless whether once
yet else first next now later soon same own
""".split())
_LY_NOUNS = frozenset("supply assembly reply apply family anomaly fly butterfly multiply ally "
                      "rally jelly belly monopoly poly italy".split())
# Third-person verb forms end a noun run: "The fuel pump motor stops."
_VERB_S = frozenset({v[:-1] + "ies" if re.search(r"[^aeiou]y$", v) else
                     v + ("es" if v.endswith(("s", "x", "z", "ch", "sh")) else "s")
                     for v in IMPERATIVE_VERBS}
                    | set("contains includes requires operates protects reduces causes receives "
                          "produces generates transmits comes goes becomes".split()))


def _is_adverb(lw: str) -> bool:
    return lw.endswith("ly") and len(lw) > 3 and lw not in _LY_NOUNS


_ING_STRONG = frozenset("by before after while when without".split())
_ING_WEAK = frozenset("for of in on from to".split())
_ING_PREPS = frozenset("according regarding concerning including excluding following "
                       "considering notwithstanding".split())


@detector("3.5", kinds=SENTENCE_KINDS)
def _ing_forms(seg: Segment) -> Iterator[Finding]:
    s = seg.masked
    toks = list(TOKEN_RE.finditer(s))
    for i, m in enumerate(toks):
        w = m.group(0)
        lw = w.lower()
        if not is_ing(w) or lw in ADJ_ING or lw in _ING_PREPS or lw == "being":
            continue
        prev = toks[i - 1].group(0).lower() if i else ""
        if prev in _BE_FORMS or prev in ("been", "being"):
            continue   # 3.2 covers "is running"
        nxt = toks[i + 1].group(0).lower() if i + 1 < len(toks) else ""
        if prev in _ING_STRONG:
            conf = "high"
        elif prev in _ING_WEAK:
            # "for cleaning the filter" is a verb; "for cooling" can be a noun.
            conf = "high" if nxt in _DETERMINERS else "low"
        elif i == 0 or s[toks[i - 1].end():m.start()].strip() == ",":
            conf = "low"
        else:
            continue
        yield _f(seg, "3.5", m.start(), m.end(), "Do not use an -ing form as a verb.",
                 "Use a clause with a subject (\"before you remove\") or a verb.", conf=conf)


_PASSIVE_RE = re.compile(
    r"\b(am|is|are|was|were|be|been|being)\s+(?:(?:not|also|then|still|always|usually|"
    r"automatically|now|only|never|often|immediately|first|already|fully|correctly|easily|"
    r"quickly)\s+)?([A-Za-z]+)\b", re.I)
_AGENT_RE = re.compile(r"(?:\s+[\w'-]+){0,3}?\s+[Bb]y\s+(?i:the|a|an|this|that|these|those|its|"
                       r"their|your|our|you|us|them|users?|operators?|people)\b")
# Participles that usually name a state after BE ("The valve is closed").
ADJ_PP = frozenset("""
closed damaged broken worn connected disconnected attached installed locked unlocked sealed
cracked bent blocked clogged contaminated corroded enabled disabled filled loaded unloaded
mounted located based tired interested pressurized energized charged discharged finished done
gone frozen lost stuck aligned rugged complicated qualified experienced detailed
""".split())


@detector("3.6", kinds=SENTENCE_KINDS)
def _passive(seg: Segment) -> Iterator[Finding]:
    s = seg.masked
    for m in _PASSIVE_RE.finditer(s):
        word = m.group(2)
        lw = word.lower()
        if lw == "been" or not is_participle(word):
            continue
        before = [t.lower() for t in TOKEN_RE.findall(s, 0, m.start())[-3:] if t.lower() != "not"]
        # "must be removed", "is to be removed": an instruction in the passive.
        instr = m.group(1).lower() == "be" and bool(before) and (
            before[-1] in ("must", "should", "shall")
            or (before[-1] == "to" and len(before) > 1
                and before[-2] in ("is", "are", "has", "have", "needs", "need")))
        if _AGENT_RE.match(s, m.end()) or instr:
            conf = "high"
        elif lw in ADJ_PP or seg.kind not in ("proc", "warning", "caution"):
            continue   # descriptive text may use the passive when the agent is not known
        else:
            conf = "low"
        yield _f(seg, "3.6", m.start(), m.end(), "Use the active voice.",
                 "Say who or what does the action: \"Remove the cover.\"", conf=conf)


_NOMINAL_RE = re.compile(
    r"\b(?:perform(?:s|ed|ing)?|carr(?:y|ies|ied|ying)\s+out|conduct(?:s|ed|ing)?|"
    r"mak(?:e|es|ing)|made|do|does|did|doing|giv(?:e|es|ing)|gave|tak(?:e|es|ing)|took)\s+"
    r"(?:(?:a|an|the|another|one|some|any|this|that)\s+)?"
    r"(\w+(?:tion|sion|ment|ance|ence|ysis)|removal|approval|disposal|refusal|renewal|"
    r"retrieval|arrival|failure|closure|exposure|review)\s+(?:of|on|to|for|with)\b", re.I)
_NOT_NOMINAL = frozenset("station section option question function position condition solution "
                         "caution portion sequence evidence audience science licence license "
                         "sentence document element segment fragment instrument equipment "
                         "environment department".split())


@detector("3.7", kinds=SENTENCE_KINDS)
def _nominals(seg: Segment) -> Iterator[Finding]:
    for m in _NOMINAL_RE.finditer(seg.masked):
        if m.group(1).lower() not in _NOT_NOMINAL:
            yield _f(seg, "3.7", m.start(), m.end(), "Use a verb, not a noun, to show the action.",
                     f"Use the verb for \"{m.group(1)}\".", conf="low")


# The particle is in a lookahead, so "to back up" can still match "back up".
_PARTICLE_RE = re.compile(r"\b([A-Za-z]+)(?=(\s+(?:up|out|off|down|away|back))\b"
                          r"(?!\s+(?:to|from)\b))", re.I)
# Verbs that are literal with a direction word ("Push the lever down" is not a phrasal verb).
_LITERAL_MOVE = frozenset("go come move push pull lift lower raise slide fall walk climb run "
                          "step put sit stand lie bend look".split())
_PHRASAL_VERBS = (IMPERATIVE_VERBS | set("get pick shut break log sign wake figure sort point fill "
                                         "back hook plug power boot wind burn wear cool warm heat "
                                         "dry wipe throw call carry write cut".split())) - _LITERAL_MOVE


def _verb_base(w: str) -> str:
    """Base form of an inflected verb if it is in _PHRASAL_VERBS, else ""."""
    cands = [w]
    if w.endswith("ing"):
        cands += [w[:-3], w[:-3] + "e", w[:-4]]
    elif w.endswith("ied") or w.endswith("ies"):
        cands += [w[:-3] + "y"]
    elif w.endswith("ed"):
        cands += [w[:-2], w[:-1], w[:-3]]
    elif w.endswith("s"):
        cands += [w[:-1], w[:-2]]
    return next((c for c in cands if c in _PHRASAL_VERBS), "")


@detector("9.3", kinds=SENTENCE_KINDS)
def _phrasal(seg: Segment) -> Iterator[Finding]:
    for m in _PARTICLE_RE.finditer(seg.masked):
        if _verb_base(m.group(1).lower()):
            end = m.end() + len(m.group(2))
            yield _f(seg, "9.3", m.start(), end, f"\"{seg.text[m.start():end]}\" can be a "
                     "phrasal verb.", "Use one verb with a clear meaning.", conf="low")


_SUBJECTS = frozenset("it this that which who he she".split())


@detector("2.1", kinds=SENTENCE_KINDS)
def _noun_clusters(seg: Segment) -> Iterator[Finding]:
    s = seg.masked
    toks = list(TOKEN_RE.finditer(s))
    runs: list[list[re.Match[str]]] = [[]]
    for i, m in enumerate(toks):
        w = m.group(0)
        lw = w.lower()
        joined = i > 0 and s[toks[i - 1].end():m.start()].isspace()
        if not joined:
            runs.append([])
        run = runs[-1]
        content = not (lw in _FUNCTION or _is_adverb(lw) or is_placeholder(w)
                       or any(c.isdigit() for c in w))
        if content and run and ((lw.endswith("ed") and is_participle(lw)) or lw in _VERB_S):
            content = False    # a verb after the nouns ends the noun run
        if content and not run and lw in _VERB_S and i \
                and toks[i - 1].group(0).lower() in _SUBJECTS:
            content = False    # "It protects sensitive parts": a verb, not a noun
        if content and not run and lw in IMPERATIVE_VERBS:
            prev = toks[i - 1].group(0).lower() if i else ""
            if not joined or prev in _VERB_CTX or prev in ("and", "then", "or"):
                content = False    # "Install new ...", "can configure ..."
        if content:
            run.append(m)
        elif run:
            runs.append([])
    for run in runs:
        # A Title Case pair ("Claude Code") counts as one word (rule 8.6).
        units = len(run) - sum(1 for a, b in zip(run, run[1:])
                               if a.group(0)[0].isupper() and b.group(0)[0].isupper())
        if units >= 4 and not all(t.group(0)[0].isupper() for t in run):
            yield _f(seg, "2.1", run[0].start(), run[-1].end(),
                     f"{units} words in a row can be one multi-word noun; the limit is 3.",
                     "Use a preposition (\"the torque of the bolt\") or a hyphen.", conf="low")


_HOLD_PAIR = ("press", "push", "click", "tap")


@detector("5.2", kinds=("proc",))
def _two_instructions(seg: Segment) -> Iterator[Finding]:
    s = seg.masked
    toks = [(m, m.group(0).lower()) for m in TOKEN_RE.finditer(s)]
    for i, (m, w) in enumerate(toks):
        if i == 0 or w not in ("and", "then") or (w == "then" and toks[i - 1][1] == "and"):
            continue
        j = i + 1
        while j < len(toks) and toks[j][1] in ("then", "carefully", "slowly", "also",
                                                "immediately", "never"):
            j += 1
        if j + 1 < len(toks) and toks[j][1] == "do" and toks[j + 1][1] == "not":
            j += 2
        if j >= len(toks) or toks[j][1] not in IMPERATIVE_VERBS:
            continue
        if toks[j][1] == "hold" and toks[i - 1][1] in _HOLD_PAIR:
            continue   # "Push and hold" is one action
        yield _f(seg, "5.2", m.start(), toks[j][0].end(), "Write one instruction in each sentence.",
                 "Put each action in its own sentence or step.", conf="low")


_NOT_VERB_START = frozenset("the a an this that these those it its there your our their you we "
                            "they i he she one each all users user".split())
_YOU_MUST_RE = re.compile(r"\b(?:you|users?|the\s+user|operators?|the\s+operator)\s+"
                          r"(?:must|should|need\s+to|needs\s+to|have\s+to|has\s+to|will\s+need\s+to|"
                          r"are\s+required\s+to|is\s+required\s+to)\b", re.I)


@detector("5.3", kinds=("proc", "desc"))
def _imperative(seg: Segment) -> Iterator[Finding]:
    s = seg.masked
    hits = list(_YOU_MUST_RE.finditer(s))
    for m in hits:
        yield _f(seg, "5.3", m.start(), m.end(), "Write the instruction as a command.",
                 "Start with the verb: \"Close the valve.\"", conf="low")
    if seg.kind == "proc" and seg.index == 0 and not hits and not is_imperative(s):
        m = TOKEN_RE.search(s, _clause_start(s))
        if m and (m.group(0).lower() in _NOT_VERB_START or is_ing(m.group(0))):
            yield _f(seg, "5.3", m.start(), m.end(), "A step must start with a command verb.",
                     "Start with the verb: \"Close the valve.\"", conf="low")


_LATE_COND_RE = re.compile(r"\s(if|when|unless|whenever|in\s+case|provided|as\s+soon\s+as)\b",
                           re.I)
_ASK_VERBS = frozenset("check see verify determine find examine ask know test confirm tell show "
                       "decide learn inspect".split())


@detector("5.4", kinds=("proc",))
def _condition_first(seg: Segment) -> Iterator[Finding]:
    s = seg.masked
    if not is_imperative(s):
        return
    for m in _LATE_COND_RE.finditer(s, _clause_start(s)):
        if _prev_word(s, m.start()) not in _ASK_VERBS:   # "Check if ..." means "whether"
            yield _f(seg, "5.4", m.start(1), m.end(1), "Put the condition first, then a comma.",
                     "If <condition>, <instruction>.", conf="low")
            return


@detector("5.5", kinds=("note",))
def _note_instruction(seg: Segment) -> Iterator[Finding]:
    if is_imperative(seg.masked):
        yield _f(seg, "5.5", _first_token_at(seg.masked), len(seg.text),
                 "A note gives information. It does not give an instruction.",
                 "Put the instruction in a step, or write the note as a fact.", conf="low")


_STATEMENT_START = frozenset("the a an this these those it there that its".split())


@detector("7.2", kinds=("warning", "caution"))
def _safety_start(seg: Segment) -> Iterator[Finding]:
    s = seg.masked
    if seg.index != 0 or is_imperative(s):
        return
    m = TOKEN_RE.search(s, _clause_start(s))
    if not m:
        return
    has_cond = _clause_start(s) > len(s) - len(s.lstrip())
    high = not has_cond and m.group(0).lower() in _STATEMENT_START
    yield _f(seg, "7.2", m.start(), m.end(),
             "Start a safety instruction with a command or a condition.",
             "Start with the command: \"Do not touch the surface. It is hot.\"",
             conf="high" if high else "low")


_RISK_RE = re.compile(r"\b(?:because|can|could|will|risk|danger|injur\w*|damage\w*|kill\w*|"
                      r"hot|toxic|flammable|explosive|sharp|poison\w*|pressuri[sz]ed|burn\w*|"
                      r"shock\w*)\b", re.I)


@detector("7.3", doc=True)
def _safety_reason(segs: list[Segment]) -> Iterator[Finding]:
    groups: dict[tuple[str, int], list[Segment]] = defaultdict(list)
    for seg in segs:
        if seg.kind in ("warning", "caution"):
            groups[(seg.file, seg.block)].append(seg)
    for group in groups.values():
        if len(group) == 1 and not _RISK_RE.search(group[0].masked):
            yield _f(group[0], "7.3", 0, len(group[0].text), "Give a short explanation of the risk.",
                     "Add a sentence such as \"The fluid can burn your skin.\"", conf="low")


_INJURY_RE = re.compile(r"\b(?:injur(?:y|ies|e|ed)|kills?|killed|death|fatal|die|burns?|skin|"
                        r"eyes?|electric\s+shock|hurt|cuts?\s+(?:you|your))\b", re.I)


@detector("7.1", kinds=("caution",))
def _signal_word(seg: Segment) -> Iterator[Finding]:
    m = _INJURY_RE.search(seg.masked)
    if m:
        yield _f(seg, "7.1", m.start(), m.end(),
                 "A risk of injury needs WARNING, not CAUTION.", "WARNING", conf="low")


_NO_ARTICLE_OK = frozenset("""
clear clean open closed tight loose free dry full empty clockwise counterclockwise
anticlockwise aside away back forward together apart upward upwards downward downwards sure
help care water oil grease power air fuel soap heat force paint data text access information
""".split())
_STEP_PREFIX = frozenset("please never always carefully slowly then now next first finally".split())


@detector("4.5", kinds=("proc",))
def _articles(seg: Segment) -> Iterator[Finding]:
    s = seg.masked
    toks = list(TOKEN_RE.finditer(s, _clause_start(s)))
    words = [t.group(0).lower() for t in toks]
    i = 0
    while i < len(toks):
        if words[i] in _STEP_PREFIX:
            i += 1
        elif words[i] == "do" and i + 1 < len(toks) and words[i + 1] == "not":
            i += 2
        else:
            break
    if i + 1 >= len(toks) or words[i] not in IMPERATIVE_VERBS:
        return
    noun, lw = toks[i + 1], words[i + 1]
    w = noun.group(0)
    if (not s[toks[i].end():noun.start()].isspace() or w[0].isupper() or lw in _FUNCTION
            or lw in _NO_ARTICLE_OK or lw in IMPERATIVE_VERBS or _is_adverb(lw)
            or is_placeholder(w) or any(c.isdigit() for c in w) or is_ing(w)
            or (lw.endswith("s") and not lw.endswith("ss"))):   # a generic plural is fine
        return
    after = toks[i + 2].group(0) if i + 2 < len(toks) else ""
    if after and (after[0].isdigit() or (len(after) == 1 and after.isupper())):
        return   # a label: "valve 3", "button A"
    yield _f(seg, "4.5", noun.start(), noun.end(),
             "Use an article or a demonstrative before the noun.", f"the {w}", conf="low")


_THIS_RE = re.compile(r"\b(this)\s+(?:is|was|can|could|will|would|causes?|caused|means|makes|"
                      r"lets|gives|shows|helps|occurs|prevents|results|happens|does|has|also)\b",
                      re.I)


@detector("GR-4", kinds=SENTENCE_KINDS)
def _this(seg: Segment) -> Iterator[Finding]:
    for m in _THIS_RE.finditer(seg.masked):
        yield _f(seg, "GR-4", m.start(1), m.end(1), "\"This\" needs a clear referent.",
                 "Add a noun: \"This procedure ...\"", conf="low")


# --------------------------------------------------------------------------
# Analysis entry point
# --------------------------------------------------------------------------

def _allow_regex(allow: Iterable[str]) -> re.Pattern[str] | None:
    terms = sorted({t.strip() for t in allow if t and t.strip()}, key=len, reverse=True)
    if not terms:
        return None
    body = "|".join(r"\s+".join(map(re.escape, t.split())) for t in terms)
    return re.compile(rf"(?<![\w'-])(?:{body})(?:s|es|d|ed|ing)?(?![\w'-])", re.I)


def analyze(segments: list[Segment], allow: Iterable[str] = (),
            table: dictionary.Lookup | None = None) -> list[Finding]:
    """Run every detector. Returns findings in document order with ids F1, F2 ...

    ``table`` is the user's imported dictionary (see dictionary.build_lookup).

    A finding is dropped when it is inside an allowlist term or inside a
    double-quoted span, or when it overlaps an earlier finding of the same rule.
    """
    found: list[Finding] = []
    for reg in DETECTORS:
        if reg.doc:
            found.extend(reg.fn(segments))
        else:
            for seg in segments:
                if seg.kind in reg.kinds:
                    found.extend(reg.fn(seg))
    if table is not None:            # after the curated words, so their messages win
        found.extend(_dictionary_findings(segments, table, allow))

    allow_rx = _allow_regex(allow)
    order = {s.id: i for i, s in enumerate(segments)}
    quotes: dict[str, list[tuple[int, int]]] = {}
    allowed: dict[str, list[tuple[int, int]]] = {}
    for seg in segments:
        quotes[seg.id] = [(a, b) for a, b in _top_spans(seg.masked) if seg.masked[a] == '"']
        allowed[seg.id] = [m.span() for m in allow_rx.finditer(seg.masked)] if allow_rx else []

    def shielded(f: Finding) -> bool:
        return (any(a < f.start and f.end < b for a, b in quotes.get(f.seg, ()))
                or any(a <= f.start and f.end <= b for a, b in allowed.get(f.seg, ())))

    found.sort(key=lambda f: (order.get(f.seg, len(segments)), f.start, -(f.end - f.start)))
    out: list[Finding] = []
    last_end: dict[tuple[str, str], int] = {}
    for f in found:
        if f.seg and shielded(f):
            continue
        key = (f.seg, f.rule)
        if f.seg and last_end.get(key, -1) > f.start:
            continue   # overlaps an earlier finding of the same rule
        last_end[key] = max(last_end.get(key, -1), f.end)
        f.id = f"F{len(out) + 1}"
        out.append(f)
    return out


# --------------------------------------------------------------------------
# Levels and score
# --------------------------------------------------------------------------

BANDS = ((60, 60), (80, 20), (100, 20))     # (tier, points)
SCHEMA = 1
VERSION = "0.1.0"           # keep in step with .claude-plugin/plugin.json


def counted(f: Finding) -> bool:
    """True if a finding lowers the score (low confidence needs a confirmation)."""
    if f.source == "house" or f.status == "rejected" or not f.seg:
        return False
    return f.status in ("confirmed", "added") or f.conf == "high"


def score(segments: list[Segment], findings: list[Finding], pass_mark: int = 80) -> dict:
    """Banded score: 60 points for tier 60, 20 for tier 80 and 20 for tier 100.

    For each tier t, c_t is the share of scored segments (not titles) with no
    counted finding of tier <= t, and score = sum(points_t * c_t). So text that
    is clean in every tier up to preset P scores at least P.
    """
    scored = [s for s in segments if s.kind != "title" and s.words > 0]
    if not scored:
        return {"score": 100.0, "pass_mark": pass_mark, "passed": True, "no_text": True,
                "bands": {str(t): 1.0 for t, _ in BANDS}, "segments": 0, "words": 0,
                "violations": 0, "per_100_words": 0.0}
    ids = {s.id for s in scored}
    worst: dict[str, int] = {}
    n = 0
    for f in findings:
        if counted(f) and f.seg in ids:
            worst[f.seg] = min(worst.get(f.seg, 999), f.tier)
            n += 1
    bands = {t: sum(1 for s in scored if worst.get(s.id, 999) > t) / len(scored)
             for t, _ in BANDS}
    total = sum(points * bands[t] for t, points in BANDS)
    words = sum(s.words for s in scored)
    # Round down, so a shown "80.0" never fails a pass mark of 80.
    shown = math.floor(total * 10 + 1e-9) / 10
    return {"score": shown, "pass_mark": pass_mark, "passed": shown >= pass_mark,
            "no_text": False, "bands": {str(t): round(c, 4) for t, c in bands.items()},
            "segments": len(scored), "words": words, "violations": n,
            "per_100_words": round(100 * n / words, 2)}


# --------------------------------------------------------------------------
# Review merge
# --------------------------------------------------------------------------

def _int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _locate(segments: list[Segment], file: str, line: int,
            quote: str) -> tuple[Segment, int, int] | None:
    """Find ``quote`` in a segment near ``line`` of ``file``, then anywhere in the file.

    Without a quote, the segment nearest to the line is used. Returns None if
    nothing matches.
    """
    cands = [s for s in segments if s.kind != "title" and (not file or s.file == file)]
    near = sorted((s for s in cands if line and s.line - 2 <= line
                   <= s.line + s.text.count("\n") + 2), key=lambda s: abs(s.line - line))
    words = re.sub(r"\s+", " ", quote.translate(_QUOTE_MAP)).strip().split()
    if not words:
        return (near[0], 0, len(near[0].text)) if near else None
    pat = re.compile(r"\s+".join(map(re.escape, words)), re.I)
    for group in (near, cands):
        for s in group:
            m = pat.search(s.text.translate(_QUOTE_MAP))   # same length as s.text
            if m:
                return s, m.start(), m.end()
    return None


def _id_and_note(item: object) -> tuple[str, str]:
    if isinstance(item, dict):
        return str(item.get("id", "")), str(item.get("reason") or item.get("note") or "")
    return str(item), ""


def merge_review(segments: list[Segment], findings: list[Finding], review: dict,
                 allow: Iterable[str] = (), table: dictionary.Lookup | None = None,
                 ) -> tuple[list[Finding], list[dict], list[str], str]:
    """Apply an LLM review to checker findings.

    Returns (findings, rewrites, warnings, notes). ``confirm``/``reject`` take
    finding ids. ``add`` and ``rewrites`` items are anchored by file, line and
    quote; an ``add`` that does not anchor becomes a document note (not scored).
    """
    if not isinstance(review, dict):
        raise ValueError("the review must be a JSON object")
    warnings: list[str] = []
    by_id = {f.id: f for f in findings}
    for key, status in (("confirm", "confirmed"), ("reject", "rejected")):
        for item in review.get(key) or []:
            fid, note = _id_and_note(item)
            if fid not in by_id:
                warnings.append(f"{key}: unknown finding id {fid!r}")
                continue
            by_id[fid].status, by_id[fid].note = status, note

    rules = load_rules()
    next_id = 1 + sum(1 for f in findings if f.source == "review")
    added: list[Finding] = []
    for n, item in enumerate(review.get("add") or [], 1):
        rid = str(item.get("rule", "")) if isinstance(item, dict) else ""
        if rid not in rules or rules[rid].tier is None:
            warnings.append(f"add #{n}: {rid or item!r} is not a rule that gives findings")
            continue
        file, line = str(item.get("file") or ""), _int(item.get("line"))
        quote = str(item.get("quote") or "")
        f = Finding(rule=rid, tier=rules[rid].tier or 100, conf="high",
                    message=str(item.get("message") or rules[rid].text),
                    fix=str(item.get("fix") or ""), file=file, line=line, quote=quote,
                    source="review", status="added", id=f"R{next_id}",
                    note=str(item.get("note") or ""))
        next_id += 1
        loc = _locate(segments, file, line, quote)
        if loc:
            seg, a, b = loc
            f.seg, f.file, f.start, f.end = seg.id, seg.file, a, b
            f.line, f.quote = seg.line + seg.text.count("\n", 0, a), seg.text[a:b]
        else:
            warnings.append(f"add #{n} ({rid}): the quote was not found; "
                            "kept as a document note (not scored)")
        added.append(f)

    rewrites: list[dict] = []
    for n, item in enumerate(review.get("rewrites") or [], 1):
        text = str(item.get("rewrite") or "").strip() if isinstance(item, dict) else ""
        if not text:
            warnings.append(f"rewrites #{n}: no rewrite text")
            continue
        file, line = str(item.get("file") or ""), _int(item.get("line"))
        original = str(item.get("original") or "")
        loc = _locate(segments, file, line, original)
        if loc is None:
            warnings.append(f"rewrites #{n}: the original sentence was not found")
        left = [f"{f.rule}: {f.quote}" for f in analyze(segment_text(text), allow, table)
                if counted(f)]
        rewrites.append({"seg": loc[0].id if loc else "", "file": loc[0].file if loc else file,
                         "line": loc[0].line if loc else line,
                         "original": loc[0].text if loc else original,
                         "rewrite": text, "rewrite_findings": left})
    return findings + added, rewrites, warnings, str(review.get("notes") or "")


# --------------------------------------------------------------------------
# Results (schema 1)
# --------------------------------------------------------------------------

_SEG_FIELDS = {f.name for f in fields(Segment)} - {"masked"}
_FINDING_FIELDS = {f.name for f in fields(Finding)}


def build_result(files: list[str], segments: list[Segment], findings: list[Finding], *,
                 level: int, pass_mark: int, doc_type: str = "auto",
                 dictionary: str = "curated", rewrites: Iterable[dict] = (), notes: str = "",
                 reviewed: bool = False, warnings: Iterable[str] = (),
                 allow: Iterable[str] = ()) -> dict:
    """The JSON result that ``check`` writes and ``report`` reads."""
    per_file = []
    for name in files:
        sc = score([s for s in segments if s.file == name],
                   [f for f in findings if f.file == name], pass_mark)
        per_file.append({"file": name, **sc})
    return {"schema": SCHEMA, "tool": "ste100", "version": VERSION, "level": level,
            "enforced_tier": enforced_tier(level), "pass_mark": pass_mark,
            "doc_type": doc_type, "dictionary": dictionary, "reviewed": reviewed,
            "score": score(segments, findings, pass_mark), "files": per_file,
            "segments": [s.to_json() for s in segments],
            "findings": [f.to_json() for f in findings], "rewrites": list(rewrites),
            "notes": notes, "warnings": list(warnings), "allow": list(allow)}


def check_documents(docs: list[tuple[str, str]], *, level: int = 80,
                    pass_mark: int | None = None, doc_type: str = "auto",
                    allow: Iterable[str] = (), table: dictionary.Lookup | None = None) -> dict:
    """Segment, analyze and score (name, text) pairs. Segments from all files are pooled.

    ``table`` turns on dictionary mode (the user's imported dictionary).
    """
    allow = list(allow)
    segments: list[Segment] = []
    for name, text in docs:
        segments += segment_text(text, file=name, doc_type=doc_type,
                                 start_id=len(segments) + 1)
    findings = analyze(segments, allow=allow, table=table)
    return build_result([n for n, _ in docs], segments, findings, level=level,
                        pass_mark=level if pass_mark is None else pass_mark,
                        doc_type=doc_type, allow=allow,
                        dictionary="imported" if table is not None else "curated")


def apply_review(result: dict, review: dict, table: dictionary.Lookup | None = None) -> dict:
    """Merge a review into a check result and score it again. Raises ValueError.

    Give ``table`` when the check used the imported dictionary, so that the
    rewrites are checked the same way.
    """
    if not isinstance(result, dict) or result.get("schema") != SCHEMA:
        raise ValueError(f"the check result is not schema {SCHEMA}")
    try:
        segments = [Segment(**{k: v for k, v in s.items() if k in _SEG_FIELDS},
                            masked=mask(s["text"])) for s in result["segments"]]
        findings = [Finding(**{k: v for k, v in f.items() if k in _FINDING_FIELDS})
                    for f in result["findings"]]
        files = [f["file"] for f in result["files"]]
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"the check result is damaged: {exc}") from exc
    allow = list(result.get("allow", []))
    findings, rewrites, warnings, notes = merge_review(segments, findings, review, allow, table)
    return build_result(files, segments, findings, level=result["level"],
                        pass_mark=result["pass_mark"], doc_type=result.get("doc_type", "auto"),
                        dictionary=result.get("dictionary", "curated"),
                        rewrites=list(result.get("rewrites", [])) + rewrites,
                        notes=notes or result.get("notes", ""), reviewed=True,
                        warnings=list(result.get("warnings", [])) + warnings, allow=allow)
