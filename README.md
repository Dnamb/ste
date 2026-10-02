# ste100

A Claude Code plugin for ASD-STE100 Simplified Technical English (STE).

Andrej Karpathy suggested that you ask an LLM to explain a subject "in
ASD-STE100", or "80% of the way to ASD-STE100": the rules of STE make the
answer shorter and easier to read. This plugin makes that a tool:

- **Explain and write** in STE, at a level that you select.
- **Rewrite** a file or pasted text in STE. Code, commands, URLs, numbers and
  error text stay byte for byte.
- **Audit** files, folders, globs, pasted text or Claude's last reply. You get a
  score, PASS or FAIL against a pass mark, and an HTML report.
- **Make STE the default** reply style, for all projects or for one project.

The plugin is not endorsed by ASD or the STEMG, and the score is not a
compliance certificate. See [Legal](#legal).

## Requirements

- Claude Code.
- [uv](https://docs.astral.sh/uv/) (recommended), or Python 3.11 or later on
  `PATH`. The scripts use only the Python standard library.
- `dict import` only: `pdfplumber`. `uv` installs it for the one command.

## Install

From a local clone:

```
/plugin marketplace add C:\Projects\ASD-STE100_Skill
/plugin install ste100@ste100
```

Start a new session, then run `/ste100 status` to see the settings.

To try the plugin without installing it, start Claude Code from any folder with
`claude --plugin-dir "C:\Projects\ASD-STE100_Skill"`.

## Usage

| Command | What it does |
|---|---|
| `/ste100 explain <question>` | Answer in STE. Free text after `/ste100` is also explain. |
| `/ste100 rewrite <file or text>` | Write `<stem>.ste.md` next to the file, check it, and show the score before and after. |
| `/ste100 audit <targets> [--level N] [--threshold N]` | Check, review and write the HTML report. Targets: files, folders, a quoted glob (`"docs/**/*.md"`), `last` (the last reply) or pasted text. |
| `/ste100 level <N\|lite\|standard\|strict>` | Use this level for the rest of the session. Add `save` (and `--project`) to keep it. |
| `/ste100 default on [level] [--project]` | Make STE the default reply style from the next session start or `/clear`. |
| `/ste100 default off [--project]` | Stop the default style. |
| `/ste100 status` | Show the settings and the file that each one comes from. |
| `/ste100 allow add\|rm <term> ... [--project]` | Project terms (technical nouns and verbs) that the checker never flags. |
| `/ste100 dict import <pdf>` | Read the dictionary from your own copy of the specification. See [Dictionary](#dictionary). |

You do not have to use the slash command. "Explain how a heat pump works in
STE", "rewrite this in Simplified Technical English" or "audit docs/setup.md
for STE" also load the skill.

## Levels and the score

One number from 0 to 100 sets two things: the rules that Claude obeys when it
writes, and the pass mark of an audit.

| Level | Name | Rules that Claude obeys |
|---|---|---|
| 60 | lite | Core rules: sentence length (20 words in a procedure, 25 in a description, 6 sentences in a paragraph), one instruction in each step, commands, conditions first, active voice, noun clusters of 3 words or fewer, no contractions, semicolons or Latin abbreviations, WARNING and CAUTION format, and the most frequent word changes. |
| 80 | standard | Lite, plus verb forms and tenses, no -ing verbs, no modal verbs other than "must", "can" and "will", no phrasal verbs, verbs for actions, articles, notes without instructions, and one topic in each sentence and paragraph. |
| 100 | strict | Standard, plus approved words in their approved meaning and part of speech, one name for one item, US spelling, hyphens, pronouns and "with". |

A level between two presets uses the rules of the next preset above it (70
writes like 80), but the pass mark stays 70. `references/rules.md` gives the
tier and the check method of each rule.

### The score formula

ASD-STE100 does not define a score. This is the plugin's own measure:

```
score = 60 * c60 + 20 * c80 + 20 * c100
```

`c60`, `c80` and `c100` are the shares of sentences that have no counted
finding in that tier or a lower tier. So:

- text that obeys all rules of tiers 60 and 80 gets 80 or more, and only text
  with no counted finding gets 100. "Passes at level 80" means "obeys the
  standard rules";
- an audit always scores all tiers, so you can compare scores between levels
  and before and after a rewrite;
- the report also gives the counted findings for each 100 words, to compare
  with other tools.

A finding counts if:

- it has high confidence and the review did not reject it, or
- the review confirmed or added it.

A low-confidence finding (passive voice, noun clusters, a step that is not a
command, and more) counts only when the review confirms it. House-style
findings (wordy phrases such as "in order to") are advice and never change the
score.

## How an audit works

1. The checker (`skills/ste100/scripts/checker.py`) reads the text. It skips
   front matter, code blocks, inline code and URLs. It divides the text into
   sentences of the kinds procedure, description, note, warning, caution,
   title and table cell, and it counts words as section 8 of the
   specification tells you (a number with its unit is 1 word).
2. 26 detectors give findings with high or low confidence.
3. Claude reviews each open finding, adds the findings that need judgment (for
   example a dangerous step without a WARNING) and writes STE rewrites for the
   worst sentences.
4. The report command merges the review, scores the text again and writes the
   HTML report.

The output goes to `./ste-reports/` (add it to your `.gitignore`):

| File | Contents |
|---|---|
| `<name>-<time>.json` | The check result: sentences, findings, score. |
| `<name>-<time>.review.json` | Claude's review. |
| `<name>-<time>.reviewed.json` | The result after the review. |
| `<name>-<time>.html` | The report: summary, annotated sentences with rewrites, findings by rule, word substitutions, limits and score bands, files and notes. It has light and dark themes and prints on A3. It runs no scripts. |

## Command line and CI

You can run the checker without Claude:

```
uv run --no-project --quiet skills/ste100/scripts/ste.py check "docs/**/*.md" --threshold 80
```

Example output:

```
STE100 check: 1 file(s), 3 sentences, 30 words
Score 33.3 / pass mark 80: FAIL
Clean sentences: tier 60 33%, tier 80 33%, tier 100 33%
Findings: 8 counted, 3 need review, 1 house style (26.7 counted per 100 words)
Saved: ste-reports\setup-20261002-172635.json

id   location  rule [conf, status] text -> fix
F4 setup.md:3 1.1 [high, counted] 'utilize' -> USE
F5 setup.md:3 8.1 [high, counted] ';' -> Write two sentences, or use a vertical list.
...
```

Exit codes: 0 = pass, 1 = fail, 2 = input or usage error. Use exit code 1 as
a CI gate. Without Claude's review, low-confidence findings do not count, so a
CI score can be higher than an audit score.

| Subcommand | Use |
|---|---|
| `check <inputs...> [--level N] [--threshold N] [--type auto\|proc\|desc] [--json-out P] [--out-dir D]` | Files, folders (`.md` and `.txt`), globs (the script expands them, also in PowerShell) or `-` for stdin. |
| `report <result.json> [--review R] [--out P] [--open]` | Merge a review and write the HTML. `STE100_NO_OPEN=1` stops `--open`. |
| `status`, `default`, `level`, `allow` | The settings, as in the slash commands. |
| `dict import <pdf>` | Import your dictionary. |
| `hook` | The SessionStart hook. It prints the style card only when the default style is on. |

## Settings

| File | Scope |
|---|---|
| `.claude/ste100.json` in the project | This project. It has priority. |
| `~/.config/ste100/config.json` (or `$STE100_CONFIG_DIR/config.json`) | All projects. |

Keys: `default` (true or false), `level` (0 to 100) and `allow` (a list of
terms). Without a file, the default style is off and the level is 80.

When `default` is on, a SessionStart hook adds a short STE style card to each
session. The card tells Claude to use STE for prose only, never for code,
commands, commit messages, file contents or quoted text.

## Dictionary

Without a dictionary, the checker uses a short curated word list
(`skills/ste100/assets/words.tsv`, written for this project), and Claude does
most of the checks for approved words.

For full word checks, request the ASD-STE100 specification (free) from
[asd-ste100.org](https://www.asd-ste100.org), then import your copy:

```
/ste100 dict import "C:\path\to\ASD-STE100-Issue-9.pdf"
```

The import saves `dictionary.json` in your user settings folder, not in the
project. Then each check also finds the words that the dictionary does not
approve, and the words that it does not contain. `status` shows the source and
the date of the import.

The dictionary is copyright material. Do not share or commit the PDF or
`dictionary.json`. This repo ignores `*.pdf` and `dictionary*.json`.

## Tests

Offline tests (fast, no cost):

```
uv run --no-project --with pytest pytest -q tests
```

To test the PDF import with your own copy of the specification, set
`STE100_DICT_PDF` to its path and add `--with pdfplumber`. The test checks only
counts and three facts.

Headless end-to-end sessions (real `claude -p` runs, approximately $2 for all 8):

```
STE100_E2E=1 uv run --no-project --with pytest --with pytest-xdist pytest -q -rP -n 4 tests/e2e
```

Plugin evals, with and without the plugin (the difference shows if the skill
helps):

```
claude plugin eval . --tag win --scaffold --trust-plugin --runs 2 --max-cost-usd 5 --no-publish --judge-model sonnet
```

Use a Sonnet judge. The default Haiku judge gave FAIL votes on replies that
were correct STE.

Result on 2 Oct 2026 (7 cases, 2 runs for each arm, $2.87): the skill fired in
all the runs where it must, and did not fire for the coding task. With the
plugin, 6 of 7 cases passed (score 0.96). The mean difference from no plugin is
+0.08. The largest gains are the default style from the hook (+0.50) and the
safety procedure (+0.17). When a prompt asks for STE by name, Claude without
the plugin is already good, so the difference is small there. The value of the
plugin is the default style, the checker score and the audit report. The
`strict-style` LLM grader is noisy: when the same judge was called directly, it
gave PASS to the replies that failed in the eval.

The `audit-report` case needs Bash, which native Windows eval runs cannot get.
Run it under WSL2 with `--tag wsl --allow-tools Bash Write`.

Validate the manifests:

```
claude plugin validate --strict .
```

## Known limits

- The checker has no part-of-speech tagger. Passive voice, noun clusters and
  commands are found with word lists and patterns, so these findings have low
  confidence and need the review.
- Without an imported dictionary, the strict tier (100) is mostly Claude's
  judgment. The report shows which dictionary the check used.
- A long document (more than 4,000 words) gets a partial review: no more than
  60 open findings and the 40 worst sentences. The report and the chat give
  this coverage. Open findings that the review did not read do not count, so
  the score can be too high.
- The default style changes Claude's prose replies only. Over a very long
  session, the style can drift. Start a new session or `/clear` to load the
  card again.

## Legal

- ASD-STE100 is a specification of ASD (the AeroSpace, Security and Defence Industries
  Association of Europe), maintained by the Simplified Technical English
  Maintenance Group (STEMG). This plugin is not endorsed by ASD or the STEMG.
- The rules in `references/rules.md` and in the style card are our own short
  paraphrases. The examples are our own. The plugin does not contain or
  redistribute the ASD-STE100 dictionary.
- A score from this plugin is not proof of compliance with ASD-STE100. A
  trained STE writer must do the final check of documents that must comply.

## License

MIT. See [LICENSE](LICENSE).
