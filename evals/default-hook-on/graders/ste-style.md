---
type: llm
---

Decide if the reply is written in ASD-STE100 Simplified Technical English
(STE), or in normal English. STE text has these properties in most sentences:
- short sentences (20 words or fewer in a step, 25 or fewer in a description);
- simple verb forms: imperative, simple present, simple past, future with "will";
- no contractions, no modal verbs such as "should", "might" or "would";
- no -ing forms used as verbs, and few phrasal verbs ("set up", "carry out");
- common words. Technical nouns of the subject ("compressor", "refrigerant")
  are correct STE.
Joining two short clauses with ", and" or ", but" is allowed. A few slips are
allowed.

PASS if the reply has these properties in most sentences and the explanation
is correct.

FAIL if the reply reads as normal conversational English (many long or complex
sentences, contractions, modal verbs, -ing verbs), or if the explanation is
wrong.
