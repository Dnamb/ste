---
name: ste100
description: >-
  ASD-STE100 Simplified Technical English (STE): explain, write, rewrite and audit
  text with the STE rules. Use when the user asks to explain or write something "in
  STE", "in ASD-STE100", "in Simplified Technical English" or "80% of the way to
  ASD-STE100"; to rewrite or simplify documentation, procedures, instructions,
  warnings or cautions in controlled English; to audit, check or score a file, a
  folder, pasted text or the last reply for STE (this gives a scored HTML report); or
  to set the STE level (lite 60, standard 80, strict 100), make STE the default reply
  style, or manage project terms. Do not use it for ordinary coding tasks.
argument-hint: "[explain|rewrite|audit|level|default|status|allow|dict] ..."
allowed-tools:
  - Read
  - Write
  - Glob
  - Bash(uv run *)
  - PowerShell(uv run *)
---

# STE100

Write and audit text in ASD-STE100 Simplified Technical English (Issue 9). The
rules in this skill are our own paraphrase. The official specification is free
from https://www.asd-ste100.org.

## The command

In this skill, `STE` means this command line:

```
uv run --no-project --quiet "${CLAUDE_SKILL_DIR}/scripts/ste.py"
```

- Run it from the project folder. The same line works in Bash and in PowerShell.
- Put each path or glob in double quotes. Do not use `&&`, pipes, heredocs or
  redirects.
- If `uv` is not installed, use `python` (3.11 or later) in place of
  `uv run --no-project --quiet`, and tell the user that `uv` is recommended.
- Exit codes: 0 = pass, 1 = fail (a normal result, not an error), 2 = input error
  (read the message, then fix the input).

## Route on the arguments

The arguments are: `$ARGUMENTS`

| First word | Mode |
|---|---|
| `explain`, `write` or any other text | Explain |
| `rewrite` | Rewrite |
| `audit`, `check`, `score` | Audit |
| `level` | Level mode |
| `default`, `status`, `allow` | Settings |
| `dict` | Dictionary |

If the arguments are empty, use the mode that the request asks for (for example,
"explain this in STE" is Explain). If the user typed only `/ste100`, run
`STE status`, then show the settings and a one-line list of the modes.

## Rules for all modes

- Apply STE only to prose. Never change code, commands, file contents that are
  not prose, identifiers, paths, URLs, numbers with units, error text or quoted
  text. Keep them byte for byte.
- Keep the meaning. If STE cannot give the meaning, keep the sentence and say why.
- Write your own replies in this skill in STE too.
- The score is this tool's own measure. ASD-STE100 defines no score. Do not say
  that text is "STE compliant" or "certified". Say that it "passes the STE100
  check at level N".
- Change the settings files only when the user asks for it.

## Level

The level is a number from 0 to 100, or `lite` (60), `standard` (80) or `strict`
(100). Use the first level that you find:

1. a level in the current request;
2. a level that the user set in this session with `/ste100 level`;
3. the level in the "STE100 default style" context, if a hook added it;
4. 80 (standard).

At level N, use each card rule with a tag at or below the next preset at or above
N. In an audit, the level is also the pass mark. If you got the level from step 1
or 2, give it to `STE check` with `--level N`. Otherwise the CLI reads the saved
level itself.

## Explain

1. Find the level.
2. Write the answer with the style card rules for that level. Use the technical
   nouns of the subject (for example, "server", "API key", "torque wrench").
   Explain an uncommon technical noun one time.
3. For a procedure, use numbered steps, one command in each step. Put a WARNING
   or a CAUTION immediately before a dangerous step.
4. Before you send the answer, read it again and correct each sentence that does
   not obey the card. Do not run the checker for an answer, unless the user asks
   for a score.
5. Do not start with a sentence about STE. Give the answer.

## Rewrite

1. Read the input. For pasted text, first use Write to save it word for word to
   `ste-reports/input/<slug>.md` (`<slug>` is 1 to 4 words from the topic, with
   hyphens).
2. Write the STE version to `<stem>.ste.md` in the same folder as the original.
   Edit the original only if the user asks. Keep the markdown structure, the code
   blocks, the links and the tables.
3. Run `STE check "<original>" "<stem>.ste.md"`. The output gives a score for each
   file.
4. If the score of the STE version is less than the level, correct its counted
   findings and run the check again. Do this no more than 2 more times.
5. Reply with the path, the score before and after, and the findings that remain.
   For pasted text, also show the rewrite.

## Audit

1. Read `${CLAUDE_SKILL_DIR}/references/audit.md`. It gives the review procedure,
   the review JSON format and the limits for long documents.
2. Find the targets:
   - files, folders or globs: give them to the check as they are (a glob in quotes);
   - `last`: use Write to save your reply before this request, word for word, to
     `ste-reports/input/last-reply.md`;
   - pasted text: use Write to save it word for word to `ste-reports/input/<slug>.md`.
3. Run `STE check "<target>" ...`. Add `--level N` (see Level) and
   `--threshold N` if the user gives a different pass mark for this audit. The
   output gives the path of the result JSON ("Saved: ...").
4. Review the findings as `audit.md` tells you. Use Write to save the review to
   `<result stem>.review.json` in the same folder as the result JSON.
5. Run `STE report "<result>.json" --review "<result stem>.review.json" --open`.
6. Reply in chat:
   - the score, the pass mark and PASS or FAIL;
   - the 3 rules with the most counted findings, each with one example and its fix;
   - the coverage (what you reviewed, and what you did not);
   - the path of the HTML report.

If you cannot run commands, do the audit in chat: give each finding with its rule
id, the quote and a fix, then the rewrites. Say that there is no score and no HTML
report, because the checker did not run.

## Level mode

- `/ste100 level <N|lite|standard|strict>`: use this level for the rest of the
  session. Do not save it. Say that `/ste100 level <N> save` saves it.
- `/ste100 level <N> save [--project]`: run `STE level <N>` (add `--project` if
  the user gives it).

## Settings

Run the command, then show its output.

| Request | Command |
|---|---|
| `status` | `STE status` |
| `default on [level] [--project]` | `STE default on [level] [--project]` |
| `default off [--project]` | `STE default off [--project]` |
| `allow add <term> ... [--project]` | `STE allow add "<term>" ... [--project]` |
| `allow rm <term> ... [--project]` | `STE allow rm "<term>" ... [--project]` |

- Without `--project`, the change goes to the global settings for all projects.
  With `--project`, it goes to `.claude/ste100.json` in this project, and it has
  priority over the global settings.
- A change to the default style starts at the next session start or `/clear`.
- Allow terms are the technical nouns and verbs of the project. The checker never
  flags them.

## Dictionary

`/ste100 dict import <pdf>` reads the user's own copy of the ASD-STE100
specification and saves the dictionary for this user only:

```
uv run --no-project --quiet --with pdfplumber "${CLAUDE_SKILL_DIR}/scripts/ste.py" dict import "<pdf>"
```

The dictionary is copyright material. Do not show its entries, copy them to the
project or commit them.

## Style card

The tag [60], [80] or [100] is the lowest level that uses the rule. At level N,
use every rule with a tag at or below the next preset at or above N (70 uses [60] and [80]).

<!-- card:start -->
Words
- [60] Use "make sure that" (not ensure), "before" (not prior to), "start" (not commence), "use" (not utilize), "to" (not in order to). For numbers, use "approximately" ("about" means "concerned with").
- [60] Do not use contractions ("do not", "it is"), semicolons or Latin abbreviations (write "for example", "that is").
- [80] Use only these verb forms: imperative, infinitive, simple present, simple past, future with "will", and the past participle as an adjective.
- [80] Do not use "has been", "is being", "would", "should", "might", "may" or "shall". Use "must" or "can".
- [80] Do not use -ing forms as verbs: "before you remove the cover", not "before removing the cover".
- [80] Use a verb for an action: "examine the pump", not "do an examination of the pump".
- [80] Use one verb, not a phrasal verb: "install", not "set up".
- [80] Keep "that" after "make sure". Use "this" with a noun ("this valve").
- [80] Do not use regional words, slang or jargon.
- [100] Use only approved STE words in their approved meaning and part of speech, and the technical nouns and verbs of the subject. "Test" is a noun: "do a test of the unit", not "test the unit".
- [100] Use one name for one item. Do not change to a synonym.
- [100] Use the past participle only as an adjective ("the damaged part") or after "is" or "are".
- [100] Use American spelling. Use hyphens to join related words. Use parentheses only for references, labels and short additions.
- [100] Use a pronoun only when its noun is clear. Be careful with "with". Do not use "he" or "she" for a person in general.

Sentences
- [60] Procedures: 20 words or fewer in each sentence. Descriptions: 25 words or fewer.
- [60] Write each step as a command. Write one instruction in each sentence, unless the actions occur at the same time.
- [60] Put a condition first, then a comma: "If the light comes on, stop the engine."
- [60] Use the active voice in procedures. In descriptions, use the passive only when you do not know who or what does the action.
- [60] Use no more than 3 words in a noun cluster. Use prepositions or hyphens.
- [80] Write short sentences with one topic. Use connecting words ("then", "but", "because") between related sentences. Use "a", "the" or "this" before a noun when you can.
- [80] A note gives information, not instructions.
- [80] If a word change does not give good STE, write the sentence again with a different structure.

Paragraphs
- [60] Each paragraph has one topic and no more than 6 sentences.
- [80] Give information gradually, one fact after the other. Use paragraphs and vertical lists to show the structure.
- [100] Use key words and headings to show the structure. Keep a consistent style.

Safety
- [60] Before a dangerous step, give a WARNING (risk of injury or death) or a CAUTION (risk of damage). Start with a clear command or condition, then give the risk: "WARNING: Do not touch the brake unit until it is cool. Hot parts can cause injury."
<!-- card:end -->
