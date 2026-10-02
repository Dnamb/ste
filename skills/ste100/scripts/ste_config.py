"""Levels, settings files and the SessionStart hook. Standard library only.

The hook imports only this module, so a problem in the checker can never
break a session start.

Settings (the first file that has a key wins; ``allow`` lists are merged):
  1. project: ``<project>/.claude/ste100.json``
  2. global:  ``$STE100_CONFIG_DIR/config.json``, else ``~/.config/ste100/config.json``
  3. built-in: default off, level 80, no allow terms
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

PRESETS = {"lite": 60, "standard": 80, "strict": 100}
BUILT_IN = {"default": False, "level": 80, "allow": []}
SKILL_MD = Path(__file__).resolve().parent.parent / "SKILL.md"
CARD_RE = re.compile(r"<!-- card:start -->\n(.*?)<!-- card:end -->", re.S)
CARD_TAG_RE = re.compile(r"^- \[(\d+)\] ")
MAX_CONTEXT = 4000


class ConfigError(Exception):
    """A settings file exists but cannot be used."""


# --------------------------------------------------------------------------
# Levels
# --------------------------------------------------------------------------

def parse_level(value: str | int) -> int:
    """Return a level 0-100 from a number or a preset name. Raises ValueError."""
    if isinstance(value, bool):
        raise ValueError("the level must be a number 0-100 or lite|standard|strict")
    if isinstance(value, str) and value.strip().lower() in PRESETS:
        return PRESETS[value.strip().lower()]
    level = int(value)
    if not 0 <= level <= 100:
        raise ValueError(f"the level must be 0-100, not {level}")
    return level


def enforced_tier(level: int) -> int:
    """The highest rule tier that a level enforces: the next preset at or above it."""
    return next(t for t in sorted(PRESETS.values()) if level <= t)


def level_name(level: int) -> str:
    names = {v: k for k, v in PRESETS.items()}
    return f"{level} ({names[level]})" if level in names else str(level)


# --------------------------------------------------------------------------
# Settings files
# --------------------------------------------------------------------------

def config_dir() -> Path:
    env = os.environ.get("STE100_CONFIG_DIR")
    return Path(env) if env else Path.home() / ".config" / "ste100"


def global_path() -> Path:
    return config_dir() / "config.json"


def project_dir(explicit: str | None = None) -> Path:
    return Path(explicit or os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd())


def project_path(project: Path) -> Path:
    return project / ".claude" / "ste100.json"


def _validate(data: object, path: Path) -> dict:
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: must contain a JSON object")
    if "default" in data and not isinstance(data["default"], bool):
        raise ConfigError(f"{path}: \"default\" must be true or false")
    if "level" in data:
        try:
            parse_level(data["level"])
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{path}: \"level\": {exc}") from exc
    allow = data.get("allow", [])
    if not isinstance(allow, list) or not all(isinstance(t, str) and t.strip() for t in allow):
        raise ConfigError(f"{path}: \"allow\" must be a list of terms")
    return data


def read_config(path: Path) -> dict:
    """The settings in ``path``: {} if it does not exist. Raises ConfigError if damaged."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read: {exc}") from exc
    try:
        # PowerShell writes UTF-16 with a BOM; editors can add a UTF-8 BOM.
        text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8-sig")
        return _validate(json.loads(text) if text.strip() else {}, path)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"{path}: not valid JSON: {exc}") from exc


def write_config(path: Path, data: dict) -> None:
    """Write settings atomically (a temporary file, then a rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def update_config(path: Path, **changes: object) -> dict:
    """Merge ``changes`` into the file. A damaged file is never overwritten."""
    data = read_config(path)
    data.update(changes)
    write_config(path, _validate(data, path))
    return data


def effective(project: Path) -> dict:
    """Merged settings, with the source of each value and any file errors.

    A damaged file is ignored and reported in ``errors``.
    """
    files = {"project": project_path(project), "global": global_path()}
    layers, errors, unknown = {}, [], []
    for scope, path in files.items():
        try:
            layers[scope] = read_config(path)
        except ConfigError as exc:
            layers[scope] = {}
            errors.append(str(exc))
        unknown += [f"{path}: unknown key \"{k}\"" for k in layers[scope] if k not in BUILT_IN]
    out: dict = {"files": {k: str(v) for k, v in files.items()}, "errors": errors,
                 "warnings": unknown, "source": {}}
    for key in ("default", "level"):
        scope = next((s for s in ("project", "global") if key in layers[s]), "built-in")
        out[key] = layers[scope][key] if scope != "built-in" else BUILT_IN[key]
        out["source"][key] = scope
    out["level"] = parse_level(out["level"])
    seen: dict[str, str] = {}
    for scope in ("global", "project"):           # one entry per term, case-insensitive
        for term in layers[scope].get("allow", []):
            seen.setdefault(term.strip().lower(), term.strip())
    out["allow"] = list(seen.values())
    return out


# --------------------------------------------------------------------------
# Style card and hook
# --------------------------------------------------------------------------

def style_card(level: int) -> str:
    """The style card from SKILL.md, with only the rules that ``level`` enforces."""
    match = CARD_RE.search(SKILL_MD.read_text(encoding="utf-8").replace("\r\n", "\n"))
    if not match:
        raise ValueError(f"no style card in {SKILL_MD}")
    tier, lines = enforced_tier(level), []
    for line in match.group(1).splitlines():
        tag = CARD_TAG_RE.match(line)
        if tag and int(tag.group(1)) > tier:
            continue
        lines.append("- " + line[tag.end():] if tag else line)
    return "\n".join(lines).strip()


def hook_context(project: Path) -> str:
    """The text the SessionStart hook adds, or "" when the default style is off."""
    cfg = effective(project)
    if cfg["errors"] or not cfg["default"]:
        return ""
    level = cfg["level"]
    head = (f"STE100 default style: on, level {level_name(level)} (from the "
            f"{cfg['source']['default']} settings). Write prose replies in ASD-STE100 "
            "Simplified Technical English with the rules below. Apply them only to prose "
            "(explanations, answers, summaries, procedures). Never change code, commands, "
            "file contents, commit messages, identifiers, paths, URLs, numbers, error text or "
            "quoted text. Do not say that the style is on. If the user sets /ste100 level or "
            "asks you to stop STE, do that for the rest of the session.")
    card = style_card(level)
    terms, room = [], MAX_CONTEXT - len(head) - len(card) - 120
    for term in cfg["allow"]:          # the allow list must never push the card over the cap
        if len(", ".join(terms + [term])) > room:
            break
        terms.append(term)
    if terms:
        more = len(cfg["allow"]) - len(terms)
        head += (" Project terms (use them as technical nouns): " + ", ".join(terms)
                 + (f", and {more} more (see /ste100 status)." if more else "."))
    return f"{head}\n\n{card}"


def hook() -> int:
    """SessionStart hook. Never reads stdin, never fails, never prints a traceback."""
    try:
        context = hook_context(project_dir())
        if context:
            sys.stdout.write(json.dumps({"hookSpecificOutput": {
                "hookEventName": "SessionStart", "additionalContext": context}}))
            sys.stdout.flush()
    except BaseException:  # noqa: BLE001  a broken setting must never break a session start
        pass
    return 0
