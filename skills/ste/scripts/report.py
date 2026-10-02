"""Render a schema-1 check result as an HTML sheet in the style of an engineering drawing sheet.

Every value from the result goes through ``e()`` (html.escape with quotes). The
page has no JavaScript and a strict CSP, so text from audited files cannot run.
"""
from __future__ import annotations

import html
from collections import Counter, defaultdict
from datetime import datetime
from string import Template

from checker import BANDS, SKILL_DIR, Finding, Segment, counted, load_rules, mask
from ste_config import level_name

TEMPLATE = SKILL_DIR / "assets" / "report.html"
KIND_NAMES = {"proc": "Procedural sentence", "desc": "Descriptive sentence",
              "warning": "Warning", "caution": "Caution", "note": "Note",
              "title": "Title", "cell": "Table cell"}
WORD_RULES = {"1.1", "1.2", "1.3", "1.14"}
MAX_SENTENCES = 8
MAX_WORD_ROWS = 30

# State -> (CSS class, label). The label carries a symbol, so color is never alone.
STATES = {"counted": ("bad", "&#x2715; Counted"),
          "beyond": ("beyond", "&#x2715; Counted, beyond level"),
          "open": ("open", "? Needs review"),
          "house": ("house", "i House style"),
          "rejected": ("ok", "&#x2713; Rejected by review"),
          "doc": ("house", "i Document note, not scored")}
SEVERITY = {"counted": 4, "open": 3, "beyond": 2, "house": 1}


def e(value: object) -> str:
    return html.escape(str(value), quote=True)


def state(f: Finding, enforced: int) -> str:
    if f.status == "rejected":
        return "rejected"
    if f.source == "house":
        return "house"
    if not f.seg:
        return "doc"
    if counted(f):
        return "beyond" if f.tier > enforced else "counted"
    return "open"


def _pct(value: float, scale: float) -> str:
    return f"{100 * min(1.0, max(0.0, value / scale)) if scale else 0:.1f}%"


def _meter(value: float, scale: float, cls: str = "", limit: float | None = None) -> str:
    """A horizontal meter: a fill on a lighter track, with an optional limit tick."""
    tick = (f'<span class="tick" style="left:{_pct(limit, scale)}"></span>'
            if limit is not None else "")
    return (f'<span class="meter {cls}" aria-hidden="true"><span class="fill" '
            f'style="width:{_pct(value, scale)}"></span>{tick}</span>')


# --------------------------------------------------------------------------
# Panels
# --------------------------------------------------------------------------

def _panel(letter: str, title: str, tag: str, body: str, cls: str = "") -> str:
    return (f'<section class="panel {cls}" aria-labelledby="p{letter}">'
            f'<header><span class="letter" aria-hidden="true">{letter}</span>'
            f'<h2 id="p{letter}">{e(title)}</h2><span class="tag">{e(tag)}</span></header>'
            f'<div class="body">{body}</div></section>')


def panel_summary(r: dict, segs: list[Segment], fs: list[tuple[Finding, str]]) -> str:
    sc = r["score"]
    passed = sc["passed"]
    stamp = ('<span class="stamp pass">&#x2713; PASS</span>' if passed
             else '<span class="stamp fail">&#x2715; FAIL</span>')
    states = Counter(s for _, s in fs)
    kinds = Counter(s.kind for s in segs)
    tree = "".join(f"<li><span>{e(KIND_NAMES.get(k, k))}</span><b>{n}</b></li>"
                   for k, n in kinds.most_common())
    if r.get("reviewed"):
        review = (f"Reviewed: {sum(1 for f, _ in fs if f.status == 'confirmed')} confirmed, "
                  f"{states['rejected']} rejected, "
                  f"{sum(1 for f, _ in fs if f.status == 'added')} added by the review.")
    else:
        review = (f"Not reviewed. {states['open']} low-confidence findings do not count "
                  "until a review confirms them. Judgment rules were not checked.")
    words = ("imported ASD-STE100 dictionary" if r.get("dictionary") == "imported"
             else "curated word list (not the full dictionary)")
    no_text = '<p class="muted">The input has no text to score.</p>' if sc["no_text"] else ""
    body = f"""
<div class="hero"><span class="value">{sc['score']:.1f}</span><span class="of">/ 100</span>{stamp}</div>
<p class="passmark">Pass mark <b>{sc['pass_mark']}</b> &middot; level <b>{e(level_name(r['level']))}</b>
 &middot; rules enforced up to tier {r['enforced_tier']}</p>{no_text}
<dl class="tiles">
 <div><dt>Sentences</dt><dd>{sc['segments']}</dd></div>
 <div><dt>Words</dt><dd>{sc['words']}</dd></div>
 <div><dt>Counted findings</dt><dd>{sc['violations']}</dd></div>
 <div><dt>Per 100 words</dt><dd>{sc['per_100_words']:.1f}</dd></div>
</dl>
<h3>Segments</h3><ul class="tree">{tree or '<li>No segments</li>'}</ul>
<h3>Coverage</h3>
<ul class="plain">
 <li>Checker: rule detectors and a {e(words)}.</li>
 <li>{e(review)}</li>
 <li>{states['house']} house-style findings are advisory and never scored.</li>
</ul>"""
    return _panel("A", "Summary", "score vs pass mark", body)


def _annotate(text: str, items: list[tuple[Finding, str]]) -> str:
    """Escape ``text`` and wrap finding spans in <mark>. Whole-sentence findings are not marked."""
    n = len(text)
    spans = []
    for f, s in items:
        a, b = max(0, min(f.start, n)), max(0, min(f.end, n))
        if s in SEVERITY and b > a and not (a == 0 and b == n):
            spans.append((a, b, f, s))
    cuts = sorted({0, n, *(a for a, *_ in spans), *(b for _, b, *_ in spans)})
    out = []
    for a, b in zip(cuts, cuts[1:]):
        cover = [(f, s) for x, y, f, s in spans if x <= a and b <= y]
        chunk = e(text[a:b])
        if cover:
            top = max(cover, key=lambda c: SEVERITY[c[1]])[1]
            tip = "; ".join(f"{f.rule}: {f.message}" for f, _ in cover)
            chunk = f'<mark class="{STATES[top][0]}" title="{e(tip)}">{chunk}</mark>'
        out.append(chunk)
        out += [f'<sup class="{STATES[s][0]}">{e(f.rule)}</sup>'
                for x, y, f, s in spans if y == b]
    return "".join(out)


def panel_sentences(segs: list[Segment], fs: list[tuple[Finding, str]],
                    rewrites: list[dict]) -> str:
    by_seg: dict[str, list[tuple[Finding, str]]] = defaultdict(list)
    for f, s in fs:
        if f.seg and s != "rejected":
            by_seg[f.seg].append((f, s))
    rewrite_for = {rw["seg"]: rw for rw in rewrites if rw.get("seg")}

    def weight(sid: str) -> float:
        w = sum(3 + (100 - f.tier) / 20 if s == "counted" else 1 for f, s in by_seg[sid])
        return w + (10 if sid in rewrite_for else 0)

    order = {s.id: i for i, s in enumerate(segs)}
    worst = sorted((sid for sid in set(by_seg) | set(rewrite_for) if sid in order),
                   key=lambda sid: (-weight(sid), order[sid]))[:MAX_SENTENCES]
    if not worst:
        return _panel("B", "Annotated sentences", "worst first",
                      '<p class="ok-line">&#x2713; No sentence has a finding.</p>', "wide")
    seg_by_id = {s.id: s for s in segs}
    blocks = []
    for n, sid in enumerate(worst, 1):
        seg, items = seg_by_id[sid], by_seg[sid]
        limit = 20 if seg.kind == "proc" else 25
        over = seg.words > limit and seg.kind not in ("title", "cell")
        ruler = "" if seg.kind in ("title", "cell") else (
            f'<div class="ruler">{_meter(seg.words, max(limit, seg.words) * 1.1, "bad" if over else "", limit)}'
            f'<span class="{"bad-text" if over else "muted"}">{"&#x2715; " if over else ""}'
            f'{seg.words} words, limit {limit}</span></div>')
        labels = "".join(
            f'<li><span class="chip {STATES[s][0]}">{e(f.rule)}</span> {e(f.message)}'
            f'{" Use: " + e(f.fix) if f.fix else ""}'
            f' <span class="state">{STATES[s][1]}</span></li>'
            for f, s in sorted(items, key=lambda c: (-SEVERITY.get(c[1], 0), c[0].start)))
        rw = rewrite_for.get(sid)
        rewrite = ""
        if rw:
            left = rw.get("rewrite_findings") or []
            check = ('<span class="ok-text">&#x2713; no checker findings</span>' if not left else
                     f'<span class="open-text">? still: {e(", ".join(left))}</span>')
            rewrite = (f'<p class="rewrite"><span class="lbl">STE rewrite</span>'
                       f'<span class="mono">{e(rw["rewrite"])}</span> {check}</p>')
        blocks.append(
            f'<article class="sentence"><h3>{n} {e(KIND_NAMES.get(seg.kind, seg.kind))}'
            f' <span class="loc">{e(seg.file)}:{seg.line}</span></h3>'
            f'<p class="mono text">{_annotate(seg.text, items)}</p>{ruler}'
            f'<ul class="labels">{labels}</ul>{rewrite}</article>')
    legend = ('<p class="legend"><mark class="bad">counted</mark> <mark class="open">needs review'
              '</mark> <mark class="beyond">beyond level</mark> <mark class="house">house style'
              '</mark> &middot; superscripts are rule ids</p>')
    return _panel("B", "Annotated sentences", f"worst {len(worst)} first",
                  legend + "".join(blocks), "wide")


def panel_rules(fs: list[tuple[Finding, str]], enforced: int) -> str:
    rules = load_rules()
    rows: dict[str, Counter] = defaultdict(Counter)
    for f, s in fs:
        if s != "house":
            rows[f.rule]["counted" if s in ("counted", "beyond") else
                         "open" if s == "open" else "other"] += 1
    if not rows:
        return _panel("C", "Findings by rule", "rules.md",
                      '<p class="ok-line">&#x2713; No findings.</p>', "wide")
    top = max(c["counted"] for c in rows.values()) or 1
    out = []
    for rid in sorted(rows, key=lambda r: (-rows[r]["counted"], -rows[r]["open"], r)):
        c, rule = rows[rid], rules.get(rid)
        tier = rule.tier if rule and rule.tier else 100
        beyond = tier > enforced
        tag = ' <span class="tag">beyond level</span>' if beyond else ""
        out.append(
            f'<tr class="{"beyond-row" if beyond else ""}"><th scope="row" class="mono">{e(rid)}</th>'
            f'<td>{e(rule.text if rule else "unknown rule")}{tag}</td>'
            f'<td class="num">{tier}</td><td class="num bar-cell">{c["counted"]}'
            f'{_meter(c["counted"], top)}</td><td class="num">{c["open"]}</td>'
            f'<td class="num">{c["other"]}</td></tr>')
    return _panel("C", "Findings by rule", "not scored = rejected or document notes", (
        '<div class="tablewrap"><table><thead><tr><th scope="col">Rule</th>'
        '<th scope="col">Rule (paraphrase)</th><th scope="col">Tier</th>'
        '<th scope="col">Counted</th><th scope="col">Needs review</th>'
        f'<th scope="col">Not scored</th></tr></thead><tbody>{"".join(out)}</tbody></table></div>'),
        "wide")


def panel_words(fs: list[tuple[Finding, str]]) -> str:
    groups: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for f, s in fs:
        if f.rule in WORD_RULES and f.fix and len(f.quote.split()) <= 4:
            groups[(f.quote.lower(), f.rule, f.fix)].append(s)
    if not groups:
        return _panel("D", "Word substitutions", "curated list",
                      '<p class="ok-line">&#x2713; No unapproved words found.</p>')
    out = []
    keys = sorted(groups, key=lambda k: (-len(groups[k]), k))
    for word, rid, fix in keys[:MAX_WORD_ROWS]:
        ss = groups[(word, rid, fix)]
        if "house" in ss:
            label = STATES["house"]
        elif "counted" in ss or "beyond" in ss:
            label = ("bad", "&#x2715; Not approved")
        elif "open" in ss:
            label = STATES["open"]
        else:
            label = ("ok", "&#x2713; Accepted by review")
        out.append(f'<tr><th scope="row" class="mono">{e(word)}</th><td class="mono">{e(rid)}</td>'
                   f'<td><span class="{label[0]}-text">{label[1]}</span></td>'
                   f'<td class="mono alt">{e(fix)}</td><td class="num">{len(ss)}</td></tr>')
    more = (f'<p class="muted">{len(keys) - MAX_WORD_ROWS} more in the appendix.</p>'
            if len(keys) > MAX_WORD_ROWS else "")
    return _panel("D", "Word substitutions", "curated list", (
        '<div class="tablewrap"><table><thead><tr><th scope="col">Word</th>'
        '<th scope="col">Rule</th><th scope="col">Status</th>'
        '<th scope="col">STE alternative</th><th scope="col">Count</th></tr></thead>'
        f'<tbody>{"".join(out)}</tbody></table></div>{more}'))


def _gauge(name: str, values: list[int], limit: int, unit: str, empty: str) -> str:
    if not values:
        return (f'<div class="gauge"><div class="ghead"><b>{e(name)}</b>'
                f'<span class="muted">{e(empty)}</span></div></div>')
    worst, over = max(values), sum(1 for v in values if v > limit)
    scale = max(limit * 1.5, worst)
    state_html = (f'<span class="bad-text">&#x2715; {over} over the limit</span>' if over
                  else '<span class="ok-text">&#x2713; all within the limit</span>')
    return (f'<div class="gauge"><div class="ghead"><b>{e(name)}</b>'
            f'<span class="limit">max {limit} {e(unit)}</span></div>'
            f'{_meter(worst, scale, "bad" if over else "", limit)}'
            f'<div class="gfoot"><span>worst {worst} {e(unit)}</span>{state_html}</div></div>')


def panel_limits(r: dict, segs: list[Segment], fs: list[tuple[Finding, str]]) -> str:
    proc = [s.words for s in segs if s.kind == "proc"]
    desc = [s.words for s in segs if s.kind == "desc"]
    paras = list(Counter((s.file, s.block) for s in segs if s.kind == "desc").values())
    live = [(f, s) for f, s in fs if s in ("counted", "beyond", "open")]
    clusters = [len(f.quote.split()) for f, _ in live if f.rule == "2.1"]
    two = sum(1 for f, _ in live if f.rule == "5.2")
    gauges = [
        _gauge("Procedural sentence", proc, 20, "words", "no procedural sentences"),
        _gauge("Descriptive sentence", desc, 25, "words", "no descriptive sentences"),
        _gauge("Descriptive paragraph", paras, 6, "sentences", "no paragraphs"),
        _gauge("Multi-word noun", clusters or ([3] if segs else []), 3, "words", "no text"),
        _gauge("Instructions in a sentence", ([2] * two + [1] * (len(proc) - two)) if proc else [],
               1, "", "no procedural sentences"),
    ]
    bands = []
    for tier, points in BANDS:
        share = float(r["score"]["bands"][str(tier)])
        bands.append(f'<div class="gauge"><div class="ghead"><b>Tier {e(level_name(tier))}'
                     f'</b><span>{share * 100:.0f}% of sentences clean'
                     f'</span></div>{_meter(share, 1.0, "band")}<div class="gfoot">'
                     f'<span>{share * points:.1f} of {points} points</span></div></div>')
    return _panel("E", "Limits and score bands", "max values", (
        "".join(gauges) + '<h3>Score bands</h3><p class="muted">Score = 60 &times; tier-60 share'
        ' + 20 &times; tier-80 share + 20 &times; tier-100 share.</p>' + "".join(bands)))


def panel_files(r: dict) -> str:
    rows = ""
    for f in r["files"]:
        result = ('<span class="muted">no text</span>' if f["no_text"] else
                  '<span class="ok-text">&#x2713; PASS</span>' if f["passed"] else
                  '<span class="bad-text">&#x2715; FAIL</span>')
        rows += (f'<tr><th scope="row" class="mono">{e(f["file"])}</th>'
                 f'<td class="num">{f["segments"]}</td><td class="num">{f["words"]}</td>'
                 f'<td class="num">{f["score"]:.1f}</td><td>{result}</td></tr>')
    notes = (f'<h3>Review notes</h3><p class="notes">{e(r["notes"])}</p>'
             if r.get("notes") else "")
    loose = [rw for rw in r.get("rewrites", []) if not rw.get("seg")]
    loose_html = ("<h3>Rewrites not matched to a sentence</h3><ul class=\"plain\">" + "".join(
        f'<li><span class="mono">{e(rw.get("original", ""))}</span> &rarr; '
        f'<span class="mono">{e(rw["rewrite"])}</span></li>' for rw in loose) + "</ul>"
                  if loose else "")
    doc_level = [f for f in r["findings"] if not f.get("seg")]
    doc_html = ("<h3>Document notes (not scored)</h3><ul class=\"plain\">" + "".join(
        f'<li><span class="chip">{e(f["rule"])}</span> {e(f["message"])}'
        f'{" (" + e(f["quote"]) + ")" if f.get("quote") else ""}</li>' for f in doc_level)
                + "</ul>" if doc_level else "")
    warns = ("<h3>Warnings</h3><ul class=\"plain\">" + "".join(
        f"<li>{e(w)}</li>" for w in r.get("warnings", [])) + "</ul>"
             if r.get("warnings") else "")
    return _panel("F", "Files and notes", f"{len(r['files'])} file(s)", (
        '<div class="tablewrap"><table><thead><tr><th scope="col">File</th>'
        '<th scope="col">Sentences</th><th scope="col">Words</th><th scope="col">Score</th>'
        f'<th scope="col">Result</th></tr></thead><tbody>{rows}</tbody></table></div>'
        + notes + doc_html + loose_html + warns), "wide")


def title_block(r: dict, name: str, created: str) -> str:
    sc = r["score"]
    cells = [("Title", name, "wide"), ("Specification", "ASD-STE100 Issue 9 (rules paraphrased)", ""),
             ("Level", level_name(r["level"]), ""),
             ("Score", f"{sc['score']:.1f} (pass mark {sc['pass_mark']})", ""),
             ("Date", created, ""), ("Tool", f"ste {r.get('version', '')}", ""),
             ("Words checked by", "imported dictionary" if r.get("dictionary") == "imported"
              else "curated list", ""),
             ("Review", "yes" if r.get("reviewed") else "no", ""), ("Sheet", "1 of 1", "")]
    return '<dl class="titleblock">' + "".join(
        f'<div class="{cls}"><dt>{e(k)}</dt><dd>{e(v)}</dd></div>' for k, v, cls in cells) + "</dl>"


def appendix(fs: list[tuple[Finding, str]]) -> str:
    rows = "".join(
        f'<tr><td class="mono">{e(f.id)}</td><td class="mono">{e(f.file)}:{f.line}</td>'
        f'<td class="mono">{e(f.rule)}</td><td class="num">{f.tier}</td><td>{e(f.conf)}</td>'
        f'<td><span class="{STATES[s][0]}-text">{STATES[s][1]}</span></td>'
        f'<td class="mono">{e(f.quote)}</td><td>{e(f.message)}</td><td>{e(f.fix)}</td>'
        f'<td>{e(f.note)}</td></tr>' for f, s in fs)
    return (f'<details><summary>All findings ({len(fs)})</summary><div class="tablewrap"><table>'
            '<thead><tr><th scope="col">ID</th><th scope="col">Location</th><th scope="col">Rule</th>'
            '<th scope="col">Tier</th><th scope="col">Confidence</th><th scope="col">Status</th>'
            '<th scope="col">Text</th><th scope="col">Message</th><th scope="col">Fix</th>'
            f'<th scope="col">Review note</th></tr></thead><tbody>{rows}</tbody></table></div>'
            '</details>')


def render(r: dict) -> str:
    """Return the report HTML for a schema-1 result. Raises KeyError/TypeError if damaged."""
    segs = [Segment(**s, masked=mask(s["text"])) for s in r["segments"]]
    enforced = r["enforced_tier"]
    fs = [(f, state(f, enforced)) for f in (Finding(**d) for d in r["findings"])]
    names = [f["file"] for f in r["files"]]
    name = names[0] if len(names) == 1 else f"{len(names)} files"
    created = r.get("created") or datetime.now().isoformat(timespec="seconds")
    page = Template(TEMPLATE.read_text(encoding="utf-8"))
    return page.substitute(
        doc_title=e(f"STE audit: {name}"), name=e(name),
        panel_a=panel_summary(r, segs, fs), panel_b=panel_sentences(segs, fs, r.get("rewrites", [])),
        panel_c=panel_rules(fs, enforced), panel_d=panel_words(fs),
        panel_e=panel_limits(r, segs, fs), panel_f=panel_files(r),
        title_block=title_block(r, name, created[:10]), appendix=appendix(fs))
