"""Settings files, the status/default/level/allow commands and the SessionStart hook."""
import json
import os
import shutil
import subprocess
import sys
import time

import pytest

import ste
import ste_config
from conftest import ROOT, SCRIPTS

STE = str(SCRIPTS / "ste.py")
RULE_80 = "Do not use -ing forms as verbs"
RULE_100 = "Use American spelling"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A clean global config dir and project dir, for in-process and subprocess runs."""
    cfg, proj = tmp_path / "cfg", tmp_path / "proj"
    proj.mkdir()
    monkeypatch.setenv("STE100_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
    monkeypatch.setenv("STE100_NO_OPEN", "1")
    monkeypatch.chdir(proj)
    return {"cfg": cfg, "proj": proj}


def hook(extra_env: dict | None = None, **kw) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, STE, "hook"], capture_output=True, timeout=20,
                          env={**os.environ, **(extra_env or {})}, **kw)


def context(p: subprocess.CompletedProcess) -> str:
    out = json.loads(p.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "SessionStart"
    return out["additionalContext"]


# --------------------------------------------------------------------------
# Hook
# --------------------------------------------------------------------------

def test_off_by_default(env):
    p = hook()
    assert p.returncode == 0 and p.stdout == b"" and p.stderr == b""


def test_on_and_level_filter(env):
    assert ste.main(["default", "on", "lite"]) == 0
    lite = context(hook())
    assert "level 60 (lite)" in lite and RULE_80 not in lite and "[60]" not in lite
    ste.main(["level", "standard"])
    std = context(hook())
    assert RULE_80 in std and RULE_100 not in std
    ste.main(["level", "70"])                               # between presets: writes like 80
    assert RULE_80 in context(hook()) and RULE_100 not in context(hook())


def test_card_is_ascii_and_short(env):
    ste.main(["default", "on", "strict"])
    ste.main(["allow", "add", *[f"term-number-{i}" for i in range(300)]])
    text = context(hook())
    assert RULE_100 in text and text.isascii()
    assert len(text) < ste_config.MAX_CONTEXT
    assert "more (see /ste100 status)" in text and text.rstrip().endswith("cause injury.\"")


def test_project_overrides_global(env):
    ste.main(["default", "on", "standard"])
    ste.main(["default", "off", "--project"])
    assert hook().stdout == b""
    ste.main(["default", "on", "--project"])
    ste.main(["level", "strict", "--project"])
    text = context(hook())
    assert "from the project settings" in text and RULE_100 in text
    assert json.loads((env["proj"] / ".claude" / "ste100.json").read_text()) == {
        "default": True, "level": 100}


@pytest.mark.parametrize("raw", [b"{broken", b'{"default": "yes"}', b'{"level": 101}',
                                 b'{"allow": "APU"}', b"\xff\x00\x00"])
def test_damaged_config_is_silent(env, raw, capsys):
    env["cfg"].mkdir()
    (env["cfg"] / "config.json").write_bytes(raw)
    p = hook()
    assert p.returncode == 0 and p.stdout == b""
    assert ste.main(["status"]) == 2
    assert "error:" in capsys.readouterr().out
    assert ste.main(["default", "on"]) == 2                 # a damaged file is never overwritten
    assert (env["cfg"] / "config.json").read_bytes() == raw


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16"])
def test_bom_and_utf16_config(env, encoding):
    env["cfg"].mkdir()
    (env["cfg"] / "config.json").write_bytes(
        json.dumps({"default": True, "level": "strict"}).encode(encoding))
    assert RULE_100 in context(hook())


def test_cp1252_console_and_open_stdin(env):
    ste.main(["default", "on"])
    ste.main(["allow", "add", "Prüfgerät"])               # non-ASCII term
    # stdin is a pipe that never closes: the hook must not wait for it.
    p = hook({"PYTHONIOENCODING": "cp1252"}, stdin=subprocess.PIPE)
    assert p.returncode == 0
    assert "Prüfgerät" in context(p) and p.stdout.isascii()


def test_hook_does_not_import_checker(env):
    ste.main(["default", "on"])
    code = ("import runpy, sys; sys.argv = [sys.argv[0], 'hook'];"
            "\ntry: runpy.run_path(%r, run_name='__main__')"
            "\nexcept SystemExit: pass"
            "\nprint('checker' in sys.modules, file=sys.stderr)") % STE
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=20)
    assert p.stderr.strip() == b"False" and context(p)


def test_hooks_json_points_at_the_script():
    spec = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    [entry] = spec["hooks"]["SessionStart"]
    [cmd] = entry["hooks"]
    assert set(entry["matcher"].split("|")) == {"startup", "resume", "clear", "compact"}
    assert cmd["command"] == "uv" and cmd["args"][-1] == "hook"
    script = cmd["args"][-2].replace("${CLAUDE_PLUGIN_ROOT}", str(ROOT))
    assert os.path.isfile(script)


@pytest.mark.skipif(not shutil.which("uv"), reason="uv is not installed")
def test_real_uv_exec_is_fast(env):
    ste.main(["default", "on"])
    args = ["uv", "run", "--no-project", "--quiet", STE, "hook"]
    subprocess.run(args, capture_output=True, timeout=60)          # warm the uv cache
    start = time.monotonic()
    p = subprocess.run(args, capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
    assert time.monotonic() - start < 5
    assert p.returncode == 0 and RULE_80 in context(p)


# --------------------------------------------------------------------------
# Settings commands
# --------------------------------------------------------------------------

def test_status_sources(env, capsys):
    assert ste.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "default style: off (from built-in)" in out and "level: 80 (standard)" in out
    assert "(not found)" in out
    ste.main(["default", "on", "60"])
    ste.main(["level", "strict", "--project"])
    capsys.readouterr()
    ste.main(["status"])
    out = capsys.readouterr().out
    assert "default style: on (from global)" in out
    assert "level: 100 (strict), rules up to tier 100 (from project)" in out


def test_unknown_key_is_a_warning(env, capsys):
    (env["proj"] / ".claude").mkdir()
    (env["proj"] / ".claude" / "ste100.json").write_text('{"levle": 60}', encoding="utf-8")
    assert ste.main(["status"]) == 0
    assert 'unknown key "levle"' in capsys.readouterr().out


def test_allow_round_trip(env, caplog):
    ste.main(["allow", "add", "APU", "torque wrench", "apu", "  "])
    ste.main(["allow", "add", "Hydraulic Mule", "--project"])
    cfg = ste_config.effective(env["proj"])
    assert cfg["allow"] == ["APU", "torque wrench", "Hydraulic Mule"]
    ste.main(["allow", "rm", "TORQUE WRENCH", "nothing"])
    assert "not in the allow list: nothing" in caplog.text
    assert ste_config.read_config(env["cfg"] / "config.json")["allow"] == ["APU"]


def test_check_uses_settings(env):
    doc = env["proj"] / "doc.md"
    doc.write_text("Utilize the pump.", encoding="utf-8")
    ste.main(["level", "lite"])
    ste.main(["check", "doc.md", "--json-out", "a.json"])
    r = json.loads((env["proj"] / "a.json").read_text(encoding="utf-8"))
    assert r["level"] == 60 and any(f["quote"] == "Utilize" for f in r["findings"])
    ste.main(["allow", "add", "utilize", "--project"])
    ste.main(["check", "doc.md", "--level", "strict", "--json-out", "b.json"])
    r = json.loads((env["proj"] / "b.json").read_text(encoding="utf-8"))
    assert r["level"] == 100 and not any(f["quote"] == "Utilize" for f in r["findings"])


def test_bad_level_in_command(env):
    with pytest.raises(SystemExit) as exc:
        ste.main(["default", "on", "hard"])
    assert exc.value.code == 2
    assert not (env["cfg"] / "config.json").exists()
