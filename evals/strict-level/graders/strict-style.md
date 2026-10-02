---
type: llm
---

PASS if the reply follows strict ASD-STE100 Simplified Technical English:
- numbered steps written as commands, 20 words or fewer in each sentence;
- one instruction in each sentence, unless the actions occur at the same time
  (a step can have two or three short sentences, for example "Remove the black
  probe. Then remove the red probe.");
- no -ing forms used as verbs, and past participles only as adjectives or
  after "is" or "are";
- no multi-word verbs such as "set up", "hook up" or "plug in";
- simple, common words, with one name for each item (it does not change
  between "multimeter", "meter" and "tester");
- a WARNING or a CAUTION before a step that has a risk (for example a short
  circuit across the battery terminals).
Technical nouns of the subject ("test lead", "COM socket", "terminal") are
correct STE. Count only clear breaks of the points above. Headings, table
cells and equipment lists need not be sentences.

PASS if there is no more than one clear break.

FAIL if there are two or more clear breaks.
