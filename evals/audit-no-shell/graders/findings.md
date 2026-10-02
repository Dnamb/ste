---
type: llm
---

PASS if the reply lists specific problems in the text, each with a quote or a
clear location and a fix, and it finds at least five of these:
- "should" (a modal verb);
- the semicolon;
- the contraction "can't";
- the Latin abbreviation "i.e.";
- "Utilize" (use "use");
- the passive voice in step 2 ("is started by the operator");
- the -ing clause "having checked the logs";
- "has been mounted" (a perfect tense).

FAIL if it finds fewer than five, or if it gives no fixes.
