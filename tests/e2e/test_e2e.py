"""Headless end-to-end sessions: real `claude -p` runs with the plugin loaded.

These tests cost money and take minutes, so they run only when STE_E2E=1:

    STE_E2E=1 uv run --no-project --with pytest --with pytest-xdist pytest -q -rP -n 4 tests/e2e

Each session runs in a temporary folder with its own STE_CONFIG_DIR, and
``--setting-sources project,local`` keeps the user's own plugins and CLAUDE.md out.
The raw stream-json of each session is saved next to its folder for debugging.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from conftest import ROOT, SCRIPTS

import checker

pytestmark = pytest.mark.skipif(os.environ.get("STE_E2E") != "1",
                                reason="set STE_E2E=1 to run real Claude sessions")

CLAUDE = shutil.which("claude")
# Skill must be allowed in -p, or the model-invoked skill is denied (slash commands still work).
TOOLS = ("Skill", "Read", "Glob", "Write", "Bash(uv run *)", "PowerShell(uv run *)")
BUDGET = os.environ.get("STE_E2E_BUDGET", "1.50")      # US dollars for each session


@dataclass
class Run:
    text: str
    session: str
    cost: float
    tools: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    denied: set[str] = field(default_factory=set)        # tool_use ids the session refused

    def hook_stdout(self) -> str:
        return "".join(e.get("stdout", "") for e in self.events
                       if e.get("subtype") == "hook_response" and e.get("hook_event") == "SessionStart")

    def plugins(self) -> set[str]:
        init = next(e for e in self.events if e.get("subtype") == "init")
        return {p["name"] for p in init.get("plugins", []) if p.get("path") != "builtin"}

    def skill_fired(self) -> bool:
        # "ste" or "ste:ste"; a substring test would also match other skills ("steward").
        return any(t["name"] == "Skill"
                   and str(t.get("input", {}).get("skill", "")).split(":")[-1] == "ste"
                   and t["id"] not in self.denied for t in self.tools)


def claude(prompt: str, cwd: Path, *, plugin: bool = True, tools: tuple[str, ...] | None = TOOLS,
           resume: str | None = None, persist: bool = False, max_turns: int = 14,
           label: str = "run") -> Run:
    """Run one headless session. The prompt goes in on stdin (claude is a .cmd on Windows)."""
    assert CLAUDE, "claude is not on PATH"
    cmd = [CLAUDE, "-p", "--setting-sources", "project,local", "--output-format", "stream-json",
           "--verbose", "--max-turns", str(max_turns), "--max-budget-usd", BUDGET]
    if plugin:
        cmd += ["--plugin-dir", str(ROOT)]
    if not persist:
        cmd.append("--no-session-persistence")
    if resume:
        cmd += ["--resume", resume]
    if tools:
        cmd += ["--allowedTools", *tools]
    env = {**os.environ, "STE_CONFIG_DIR": str(cwd.parent / "cfg"), "STE_NO_OPEN": "1"}
    p = subprocess.run(cmd, input=prompt, cwd=cwd, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=1200)
    (cwd.parent / f"{label}.jsonl").write_text(p.stdout + "\n---stderr---\n" + p.stderr,
                                               encoding="utf-8")
    events = [json.loads(line) for line in p.stdout.splitlines() if line.startswith("{")]
    done = [e for e in events if e.get("type") == "result"]
    assert done, f"no result event (exit {p.returncode}): {p.stderr[-2000:]}"
    tools_used = [c for e in events if e.get("type") == "assistant"
                  for c in e["message"].get("content", []) if c.get("type") == "tool_use"]
    run = Run(done[-1].get("result") or "", done[-1].get("session_id", ""),
              float(done[-1].get("total_cost_usd") or 0), tools_used, events,
              {d["tool_use_id"] for d in done[-1].get("permission_denials", [])})
    print(f"[{label}] cost ${run.cost:.3f}, tools {[t['name'] for t in tools_used]}")
    return run


def ste_score(text: str, level: int = 80) -> float:
    result = checker.check_documents([("reply.md", text)], level=level)
    return result["score"]["score"]


def findings_per_100_words(text: str) -> float:
    """All findings (also the open low ones) for each 100 words: a stricter signal than the score."""
    result = checker.check_documents([("reply.md", text)])
    found = [f for f in result["findings"] if f["source"] != "house"]
    return round(100 * len(found) / max(result["score"]["words"], 1), 1)


def hook_output(cfg: Path, cwd: Path) -> str:
    env = {**os.environ, "STE_CONFIG_DIR": str(cfg), "CLAUDE_PROJECT_DIR": str(cwd)}
    return subprocess.run([sys.executable, str(SCRIPTS / "ste.py"), "hook"], env=env, cwd=cwd,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout


def reports(cwd: Path, pattern: str) -> list[Path]:
    return sorted((cwd / "ste-reports").glob(pattern))


@pytest.fixture
def cwd(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    return work


GUIDE = """# Restart the gateway

In order to restart the gateway, you should utilize the following command, and it is \
imperative that you ensure the pod has been drained prior to commencing.

```bash
kubectl rollout restart deployment/gateway -n edge
```

The documentation can be found at https://example.com/docs/gateway, and the maximum \
payload weight for the rack is 20 kg.

If the client reports ERR_CONN_RESET, the connection was dropped by the load balancer; \
wait approximately 30 seconds and retry.
"""
LITERALS = ("```bash\nkubectl rollout restart deployment/gateway -n edge\n```",
            "https://example.com/docs/gateway", "20 kg", "ERR_CONN_RESET")

NOTES = """# Backup notes

Backups should be verified on a weekly basis, i.e. every Monday, so that data loss \
can't go unnoticed. The operator, having checked the logs, must then rotate the keys; \
this is done via the admin console. Don't paste <script>alert(1)</script> into the \
comment field.

1. Utilize the restore wizard and select the snapshot.
2. The restore is started by the operator after the volume has been mounted.
"""


# ---------------------------------------------------------------- E1-E2 explain


def test_e1_explain_slash_command(cwd):
    run = claude("/ste explain how a fuse protects a circuit", cwd, label="e1")
    assert run.plugins() == {"ste"}, "other user plugins leaked into the session"
    assert run.hook_stdout() == "", "the hook must be silent when the default style is off"
    score = ste_score(run.text)
    print(f"E1 score {score}")
    assert score >= 80


def test_e2_natural_prompt_with_and_without_plugin(cwd, tmp_path):
    prompt = "Explain in ASD-STE100 Simplified Technical English how a car battery is charged."
    with_plugin = claude(prompt, cwd, label="e2-with")
    bare_cwd = tmp_path / "bare" / "work"
    bare_cwd.mkdir(parents=True)
    without = claude(prompt, bare_cwd, plugin=False, label="e2-without")
    a, b = ste_score(with_plugin.text), ste_score(without.text)
    print(f"E2 score with plugin {a}, without {b}, difference {a - b:+.1f}; findings per 100 "
          f"words {findings_per_100_words(with_plugin.text)} vs "
          f"{findings_per_100_words(without.text)}")
    assert with_plugin.skill_fired(), "the skill did not trigger on a natural prompt"
    assert a >= 80


# ---------------------------------------------------------------- E3 rewrite


def test_e3_rewrite_keeps_literals(cwd):
    (cwd / "guide.md").write_text(GUIDE, encoding="utf-8")
    run = claude("/ste rewrite guide.md", cwd, label="e3")
    out = cwd / "guide.ste.md"
    assert out.is_file(), run.text[-1500:]
    text = out.read_text(encoding="utf-8")
    for literal in LITERALS:
        assert literal in text, f"literal changed: {literal!r}"
    before, after = ste_score(GUIDE), ste_score(text)
    print(f"E3 score before {before}, after {after}")
    assert after >= 80 and after > before
    assert (cwd / "guide.md").read_text(encoding="utf-8") == GUIDE, "the original was changed"


# ---------------------------------------------------------------- E4-E6 audit


def test_e4_audit_file(cwd):
    (cwd / "notes.md").write_text(NOTES, encoding="utf-8")
    run = claude("/ste audit notes.md", cwd, label="e4")
    html = reports(cwd, "*.html")
    merged = reports(cwd, "*.reviewed.json")
    assert html and merged, run.text[-1500:]
    result = json.loads(merged[-1].read_text(encoding="utf-8"))
    assert result["reviewed"] is True
    assert {f["status"] for f in result["findings"]} & {"confirmed", "rejected", "added"}
    page = html[-1].read_text(encoding="utf-8")
    assert "<script>alert" not in page
    assert run.text.lstrip("* ").startswith(("PASS", "FAIL")), run.text[:200]
    assert (cwd / "notes.md").read_text(encoding="utf-8") == NOTES


def test_e5_quoted_glob_and_threshold(cwd):
    docs = cwd / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("Make sure that the valve is closed.\n", encoding="utf-8")
    (docs / "b.md").write_text("You should utilize the valve prior to commencing.\n",
                               encoding="utf-8")
    (docs / "c.txt").write_text("Not matched by the glob.\n", encoding="utf-8")
    run = claude('/ste audit "docs/*.md" --threshold 50', cwd, label="e5")
    results = [p for p in reports(cwd, "*.json") if not p.name.endswith(".review.json")]
    assert results, run.text[-1500:]
    result = json.loads(results[-1].read_text(encoding="utf-8"))
    assert sorted(Path(f["file"]).name for f in result["files"]) == ["a.md", "b.md"]
    assert result["score"]["pass_mark"] == 50


def test_e6_audit_last_reply(cwd):
    first = claude("/ste explain what a circuit breaker does, in about 120 words", cwd,
                   persist=True, label="e6-explain")
    assert first.session
    run = claude("/ste audit last", cwd, resume=first.session, persist=True, label="e6-audit")
    last = cwd / "ste-reports" / "input" / "last-reply.md"
    assert last.is_file(), run.text[-1500:]
    words = set(first.text.lower().split())
    saved = set(last.read_text(encoding="utf-8").lower().split())
    assert len(words & saved) >= 0.8 * len(saved), "last-reply.md is not the previous reply"
    assert reports(cwd, "*.html")


# ---------------------------------------------------------------- E7-E8 settings


def test_e7_default_on_then_off(cwd):
    cfg = cwd.parent / "cfg"
    claude("/ste default on standard", cwd, label="e7-on")
    config = json.loads((cfg / "config.json").read_text(encoding="utf-8"))
    assert config["default"] is True and config["level"] == 80
    assert "additionalContext" in hook_output(cfg, cwd)

    fresh = claude("How does a heat pump heat a house? Answer in about 150 words.", cwd,
                   tools=None, label="e7-fresh")
    assert "additionalContext" in fresh.hook_stdout(), "the hook did not inject the card"
    score = ste_score(fresh.text)
    print(f"E7 score with the default style on {score}")
    assert not fresh.skill_fired(), "the hook alone must set the style"
    assert score >= 75

    claude("/ste default off", cwd, label="e7-off")
    config = json.loads((cfg / "config.json").read_text(encoding="utf-8"))
    assert config["default"] is False
    assert hook_output(cfg, cwd).strip() == ""


def test_e8_project_override_and_allowed_tools(cwd):
    (cwd / ".claude").mkdir()
    (cwd / ".claude" / "ste.json").write_text('{"level": 100, "allow": ["gateway"]}',
                                                 encoding="utf-8")
    # No --allowedTools: the shell grant must come from the skill's allowed-tools.
    run = claude("/ste status", cwd, tools=None, label="e8")
    ran = [t for t in run.tools if t["name"] in ("Bash", "PowerShell")]
    assert ran, f"the skill did not run the CLI: {run.text[-800:]}"
    assert "100" in run.text and "gateway" in run.text
    assert "project" in run.text.lower()
