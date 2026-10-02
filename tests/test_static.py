"""Static checks: SKILL.md frontmatter and commands, references, manifests."""
import json
import re

import checker
import ste
import ste_config
from conftest import ROOT

SKILL = ROOT / "skills" / "ste"
SKILL_MD = (SKILL / "SKILL.md").read_text(encoding="utf-8")


def frontmatter(text: str) -> dict[str, str | list[str]]:
    """A small parser for the frontmatter shapes that SKILL.md uses (scalars, >- blocks, lists)."""
    body = text.split("---\n")[1]
    out: dict[str, str | list[str]] = {}
    key = ""
    for line in body.splitlines():
        if not line.startswith(" "):
            key, _, value = line.partition(":")
            value = value.strip()
            out[key] = [] if not value else ("" if value == ">-" else value.strip('"'))
        elif line.startswith("  - "):
            out[key].append(line[4:])                                   # type: ignore[union-attr]
        else:
            out[key] = f"{out[key]} {line.strip()}".strip()             # folded block
    return out


def test_frontmatter():
    fm = frontmatter(SKILL_MD)
    assert fm["name"] == "ste"
    assert 200 < len(fm["description"]) < 1536
    assert fm["description"].startswith("ASD-STE100 Simplified Technical English")
    assert fm["argument-hint"].startswith("[explain|rewrite|audit|")
    assert isinstance(fm["allowed-tools"], list) and "Bash(uv run *)" in fm["allowed-tools"]
    assert len(SKILL_MD.splitlines()) < 500


def test_card_lines_are_tagged():
    card = ste_config.CARD_RE.search(SKILL_MD).group(1)
    bullets = [line for line in card.splitlines() if line.startswith("- ")]
    assert len(bullets) > 20
    tags = [ste_config.CARD_TAG_RE.match(b) for b in bullets]
    assert all(tags), [b for b, t in zip(bullets, tags) if not t]
    assert {t.group(1) for t in tags} == {"60", "80", "100"}
    assert len(ste_config.style_card(100).splitlines()) > len(ste_config.style_card(60).splitlines())


def test_skill_commands_exist_in_the_cli():
    """Each `STE <command> --flag` in SKILL.md is a real subcommand and option."""
    parser = ste.build_parser()
    subs = next(a for a in parser._actions if a.dest == "command").choices
    used = set(re.findall(r"`STE (\w+)", SKILL_MD))
    assert used >= {"check", "report", "status", "default", "level", "allow"}
    assert used <= set(subs), used - set(subs)
    flags = {o for p in subs.values() for a in p._actions for o in a.option_strings}
    assert set(re.findall(r"(--[a-z][a-z-]+)", SKILL_MD)) - {"--no-project", "--quiet", "--with"} <= flags


def test_references_have_no_skill_dir_variable():
    for path in (SKILL / "references").glob("*.md"):
        assert "${CLAUDE_SKILL_DIR}" not in path.read_text(encoding="utf-8"), path


def test_audit_example_review_matches_the_merge():
    """The review JSON example in audit.md merges with no anchor or schema warnings."""
    text = (SKILL / "references" / "audit.md").read_text(encoding="utf-8")
    review = json.loads(re.search(r"```json\n(.*?)```", text, re.S).group(1))
    assert set(review) == {"confirm", "reject", "add", "rewrites", "notes"}
    lines = ["# Setup", ""] + ["Prior to commencing, ensure the unit is powered off."] + [""] * 10
    lines[13:] = ["1. Disconnect the battery cable."]
    result = checker.check_documents([("docs/setup.md", "\n".join(lines) + "\n")])
    merged = checker.apply_review(result, review)
    assert [w for w in merged["warnings"] if "unknown finding id" not in w] == []
    [added] = [f for f in merged["findings"] if f["id"] == "R1"]
    assert added["seg"] and added["line"] == 14
    assert merged["rewrites"][0]["rewrite_findings"] == []


def test_manifests_agree():
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    assert plugin["version"] == checker.VERSION
    [entry] = market["plugins"]
    assert entry["name"] == plugin["name"] == "ste" and entry["source"] == "./"
    assert "hooks" not in plugin                    # hooks/hooks.json loads by itself
