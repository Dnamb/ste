# Audit procedure

Use this procedure after `STE check`. The rule text, tier and method of each rule
are in `rules.md` in this folder.

## 1. Read the check output

- The first lines give the score, the pass mark, PASS or FAIL and the share of
  clean sentences in each tier.
- Each finding line has this form:
  `id file:line rule [conf, status] 'quote' -> fix`.
- The output shows no more than 50 findings. For all of them, read the result
  JSON:
  - `findings`: `id`, `rule`, `tier`, `conf` (high or low), `status`, `file`,
    `line`, `quote`, `message`, `fix`, `seg` (the sentence id), `source`.
  - `segments` (the sentences): `id`, `file`, `line`, `kind`, `text`, `words`.
    The kinds are `proc` (procedure), `desc` (description), `note`, `warning`,
    `caution`, `title` and `cell` (a table cell).
- `enforced_tier` is the highest tier that the level enforces. Findings in a
  tier above it cannot change PASS or FAIL.

## 2. Decide each open finding

- A `low` finding does not count until you confirm it. Confirm it if the text
  breaks the rule. Reject it if it does not, and give a short reason.
- A `high` finding counts now. Reject it only if it is clearly wrong, and give
  the reason. For example:
  - the word is a technical noun or a technical verb of the subject;
  - the word is a name, a label on a screen or quoted text;
  - the checker read the sentence incorrectly.
- If the same technical noun is rejected many times, tell the user that
  `/ste allow add "<term>"` stops these findings.
- A `house` finding is house style. It is never scored. Do not review it.
- When `dictionary` is `imported`, findings with source `dict` come from the
  user's own copy of the ASD-STE100 dictionary:
  - "is not an approved STE word": the dictionary lists the word as not
    approved. Use the fix. Reject it only for a technical noun or verb.
  - "is not in the STE dictionary" (`low`): one finding for each word. Most of
    these words are technical nouns or verbs. Reject those, and confirm the
    others.
  - "is not a form of ... that the dictionary lists" (`low`): rule 1.4.
  - Do not show or copy dictionary entries in the report, the notes or the chat.
    Give only the approved word and its part of speech.

## 3. Add the findings that the checker cannot find

Read the sentences and add findings for the rules below. Do not add a finding
that the checker already gives for the same words. Do the tiers at or below the
enforced tier first.

Tier 60:
- 5.2: two or more instructions in one sentence (not done at the same time).
- 5.3: a step that is not a command.
- 5.4: a condition after the instruction.
- 3.6: the passive voice in a procedure.
- 2.1: a noun cluster of more than 3 words.
- 7.1 to 7.3: a dangerous step without a WARNING or a CAUTION, the wrong signal
  word, no command or condition at the start, or no explanation of the risk.

Tier 80:
- 4.1 and 6.5: a sentence or a paragraph with more than one topic.
- 4.3: a complex sentence that must be a vertical list.
- 4.4: related sentences without connecting words.
- 4.5: a noun without an article or a demonstrative where one is possible.
- 5.5: a note that gives an instruction.
- 6.1 and 6.4: facts in an unclear order, or no paragraphs where they are
  necessary.
- 3.2, 3.4, 3.7 and 9.3: verb forms, nouns for actions and phrasal verbs that
  the checker did not find.
- 1.10: regional words, slang or jargon.
- GR-4: "this" without a clear noun.
- 9.1: a sentence that keeps a bad structure around a changed word.

Tier 100:
- 1.1 to 1.3: a word that is not approved, or an approved word with a different
  meaning or part of speech. The curated word list is short. Without an imported
  dictionary, most of these findings come from you.
- 1.7 and 1.13: a technical noun used as a verb, or a technical verb used as a
  noun.
- 1.11: two names for one item.
- 2.2 and 3.3: a new short form for a long name, or a past participle that is
  not an adjective.
- 6.2, 8.2, 8.3, 9.2 and 9.4: unclear structure words, missing hyphens, wrong
  use of parentheses, wrong context of a word, an inconsistent style.
- GR-2, GR-3 and GR-5: an unclear "with", an unclear pronoun, a false friend.

You cannot add the counting rules 8.4 to 8.7.

## 4. Write rewrites

Write an STE rewrite for the worst sentences: the sentences with the most
counted findings. Write no more than 8 for a short document. Keep the meaning,
the code, the names, the numbers and the quoted text. The report checks each
rewrite again and shows the findings that remain. Try to get no findings.

## 5. The review JSON

Write one JSON object (UTF-8). All keys are optional.

```json
{
  "confirm": ["F3", {"id": "F7", "note": "two instructions"}],
  "reject": [{"id": "F5", "reason": "\"Logging\" is a feature name (a technical noun)"}],
  "add": [
    {"rule": "7.1", "file": "docs/setup.md", "line": 14,
     "quote": "Disconnect the battery cable",
     "message": "Dangerous step without a WARNING",
     "fix": "Put a WARNING before this step"}
  ],
  "rewrites": [
    {"file": "docs/setup.md", "line": 3,
     "original": "Prior to commencing, ensure the unit is powered off.",
     "rewrite": "Before you start, make sure that the unit is off."}
  ],
  "notes": "Reviewed all 12 open findings and all 42 sentences."
}
```

- `confirm` and `reject` take finding ids, or objects with `id` and a `note` or
  `reason`. An unknown id gives a warning.
- `add` items need `rule`, `file`, `line` and `quote`. `message`, `fix` and
  `note` are optional.
  - Use `file` exactly as the result JSON gives it.
  - `quote` must be words from the sentence, in the same sequence (1 to 8 words
    is best). Case, spaces and the type of quote marks can be different.
  - The report finds the quote within 2 lines of `line`, then in all of the
    file. With no quote, it uses the sentence nearest to the line.
  - If the quote is not found, the finding becomes a document note. It is not
    scored, and the report output gives a warning. Correct the quote, then run
    the report again.
- `rewrites` items need `file`, `line` and `rewrite`. Give `original` (the
  sentence text) to help find the sentence.
- `notes` is one string. Put the coverage in it.

## 6. Limits for long documents

- 4,000 words or fewer: review every open finding and read every sentence.
- More than 4,000 words:
  - review no more than 60 open findings (the lowest tiers first, then in file
    order);
  - read no more than the 40 worst sentences for step 3;
  - write the coverage in `notes` and in the chat summary, for example:
    "Reviewed 60 of 214 open findings and 40 of 380 sentences."
- An open finding that you did not review does not count. Tell the user that
  the score can be too high because of this.

## 7. After the report

Give the chat summary that SKILL.md describes. Do not change the audited files.
If the result is FAIL, offer a rewrite.
