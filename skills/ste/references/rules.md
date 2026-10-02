# ASD-STE100 Issue 9 rules (paraphrased)

The rule text below is our own short paraphrase. The official rules, the
dictionary and the examples are in ASD-STE100 Issue 9, which you can request
free of charge from https://www.asd-ste100.org. This plugin is not endorsed by
ASD or the STEMG.

The checker reads this table. It is the single source of truth for each
rule's tier and method.

- **tier** is the lowest level that enforces the rule: 60 = lite (core),
  80 = standard, 100 = strict. `-` = the rule only defines how words are
  counted.
- **method**: `auto` = a high-confidence detector exists (it can also have
  low-confidence parts); `heuristic` = only low-confidence detectors, which
  need a review; `judgment` = the reviewer checks it; `counting` = it changes
  the word count only.
- Examples are our own. They are not taken from the specification.

| id | rule (paraphrase) | tier | method | example (not STE -> STE) |
|---|---|---|---|---|
| 1.1 | Use only approved words, technical nouns (TN) or technical verbs (TV). | 100 | auto | "Utilize the clamp." -> "Use the clamp." |
| 1.2 | Use an approved word only as its approved part of speech. | 100 | auto | "Test the circuit." -> "Do a test of the circuit." |
| 1.3 | Use an approved word only with its approved meaning. | 100 | auto | "Wait about 10 minutes." -> "Wait approximately 10 minutes." |
| 1.4 | Use only the approved forms of verbs and adjectives. | 100 | judgment | Use a comparative form only if the dictionary lists it. |
| 1.5 | Technical nouns are words in the technical categories (names, parts, tools, materials, software items, units). | 100 | judgment | "server", "torque wrench", "log file" are TNs. |
| 1.6 | Use an unapproved word only as a TN or as part of a TN. | 100 | judgment | "fan blade" is a TN; "blade" alone in its everyday sense is not. |
| 1.7 | Do not use a TN as a verb. | 100 | judgment | "Grease the bearing." -> "Apply grease to the bearing." |
| 1.8 | Use the TNs that your company or field approves. | 100 | judgment | Use the part names from the parts list. |
| 1.9 | Select short, easy TNs. | 100 | judgment | "fixing device for the cable" -> "cable clamp" |
| 1.10 | Do not use regional words, slang or jargon as TNs. | 80 | judgment | "the gizmo" -> "the adapter" |
| 1.11 | Use one TN for one item. Do not change to a synonym. | 100 | judgment | "the lid ... the cover" -> "the cover ... the cover" |
| 1.12 | Technical verbs are verbs in the technical categories (manufacturing processes, computer processes, some operational verbs). | 100 | judgment | "compile", "drill", "ream" are TVs. |
| 1.13 | Do not use a TV as a noun. | 100 | judgment | "Do a compile." -> "Compile the code." |
| 1.14 | Use American English spelling. | 100 | auto | "colour" -> "color" |
| 2.1 | Do not use more than 3 words in a multi-word noun. Reword it or use hyphens. | 60 | heuristic | "pump motor bracket bolt torque" -> "torque of the bolt on the pump motor bracket" |
| 2.2 | When a TN has more than 3 words, write it in full. Do not make a new short form. | 100 | judgment | Write the full name the first time and keep it the same. |
| 3.1 | Use only the verb forms that the dictionary gives. | 100 | judgment | Use only the listed forms of an approved verb. |
| 3.2 | Use only the infinitive, the imperative, the simple present, past and future, and the past participle as an adjective. | 80 | auto | "The valve has opened." -> "The valve opened." |
| 3.3 | Use the past participle only as an adjective, with a noun or after a form of BE. | 100 | judgment | "a damaged seal", "the seal is damaged" |
| 3.4 | Do not use complex verb structures (has been, will have, is being, used to). | 80 | auto | "The motor had been running." -> "The motor operated." |
| 3.5 | Use an -ing form only as a TN or as a modifier in a TN. Do not use it as a verb. | 80 | auto | "before removing the cover" -> "before you remove the cover" |
| 3.6 | Use the active voice. In descriptive text, use the passive only when you do not know who or what does the action. In procedures, always use the active voice. | 60 | auto | "The cover is removed by the operator." -> "The operator removes the cover." |
| 3.7 | Use a verb, not a noun, to show an action. | 80 | heuristic | "Do an inspection of the seal." -> "Examine the seal." |
| 4.1 | Write short, clear sentences with one topic. | 80 | judgment | Split a sentence that gives two unrelated facts. |
| 4.2 | Do not leave out words to make the text shorter. Do not use contractions. | 60 | auto | "Don't open the valve." -> "Do not open the valve." |
| 4.3 | Use a vertical list to make complex text clear. | 80 | judgment | Put 4 conditions in a list, not in one long sentence. |
| 4.4 | Use connecting words and phrases between related sentences. | 80 | judgment | Use "thus", "but", "then" to show how sentences relate. |
| 4.5 | Use an article or a demonstrative before a noun when possible. | 80 | heuristic | "Remove cover." -> "Remove the cover." |
| 5.1 | A procedural sentence has no more than 20 words. A note has no more than 25 words. | 60 | auto | Split a 28-word instruction into two instructions. |
| 5.2 | Write one instruction in each sentence, unless you must do the actions at the same time. | 60 | heuristic | "Remove the cover and clean the filter." -> "Remove the cover. Clean the filter." |
| 5.3 | Write instructions in the imperative (command) form. | 60 | heuristic | "You must close the valve." -> "Close the valve." |
| 5.4 | If an instruction has a condition, write the condition first, then a comma. | 60 | heuristic | "Close the valve if the pressure increases." -> "If the pressure increases, close the valve." |
| 5.5 | A note gives information. It does not give instructions. | 80 | heuristic | "NOTE: Close the valve first." -> put the step in the procedure. |
| 6.1 | Give information gradually, one fact after the other. | 80 | judgment | Put the general fact first, then the details. |
| 6.2 | Use key words and phrases to make the structure of the text clear. | 100 | judgment | Start each paragraph with its topic. |
| 6.3 | A descriptive sentence has no more than 25 words. | 60 | auto | Split a 31-word description into two sentences. |
| 6.4 | Use paragraphs to show the logical structure of the text. | 80 | judgment | One paragraph for the function, one for the parts. |
| 6.5 | Each paragraph has only one topic. | 80 | judgment | Move the safety fact to its own paragraph. |
| 6.6 | A paragraph has no more than 6 sentences. | 60 | auto | Divide an 8-sentence paragraph into two paragraphs. |
| 7.1 | Start a safety instruction with a signal word: WARNING for a risk of injury or death, CAUTION for a risk of damage. | 60 | heuristic | "CAUTION: High voltage can kill you." -> "WARNING: ..." |
| 7.2 | Start a safety instruction with a clear, simple command or condition. | 60 | auto | "WARNING: The surface is hot." -> "WARNING: Do not touch the surface. It is hot." |
| 7.3 | Give a short explanation of the risk. | 60 | heuristic | Add "The fluid can burn your skin." after the command. |
| 8.1 | Do not use semicolons. | 60 | auto | "The pump stops; the light comes on." -> "The pump stops. The light comes on." |
| 8.2 | Use hyphens to join words that are related. | 100 | judgment | "high pressure line" -> "high-pressure line" |
| 8.3 | Use parentheses only for the permitted uses (for example, references, labels, a short added word). | 100 | judgment | Do not put a full sentence in parentheses. |
| 8.4 | In a vertical list, a colon ends a sentence for the word count. | - | counting | "Do these steps:" counts as one sentence. |
| 8.5 | Text in parentheses counts as 1 word. | - | counting | "(refer to step 4)" = 1 word |
| 8.6 | A number, a number with its unit, an abbreviation, an identifier, quoted text, a title, a label and a proper noun each count as 1 word. | - | counting | "20 kg", "ERR_CONN_RESET", "Claude Code" = 1 word each |
| 8.7 | A hyphenated word counts as 1 word. | - | counting | "high-pressure" = 1 word |
| 9.1 | When a word change does not give good STE, write the sentence again with a different construction. | 80 | judgment | Do not keep the old sentence shape around a new word. |
| 9.2 | Use each approved word correctly, in its approved sense and context. | 100 | judgment | Use "follow" only for "come after". |
| 9.3 | Do not use phrasal verbs (a verb and a particle that together have a new meaning). | 80 | auto | "Find out the cause." -> "Find the cause." |
| 9.4 | Use a consistent style. | 100 | judgment | Write all steps in the same form. |
| GR-1 | Keep "that" after "make sure" and similar verbs. | 80 | auto | "Make sure the valve is closed." -> "Make sure that the valve is closed." |
| GR-2 | Be careful with "with". It can have many meanings. | 100 | judgment | "Clean the filter with the brush." is clear; "a pump with a leak" is not as clear. |
| GR-3 | Use only approved pronouns, and make sure that each pronoun has a clear referent. | 100 | judgment | "it" must point to one noun only. |
| GR-4 | Make sure that "this" has a clear referent. Add a noun if necessary. | 80 | heuristic | "This is dangerous." -> "This procedure is dangerous." |
| GR-5 | Be careful with false friends: words that look like a word in your language but have a different meaning. | 100 | judgment | Check words that translators often confuse. |
| GR-6 | Do not use Latin abbreviations (e.g., i.e., etc.). | 60 | auto | "tools, e.g. a wrench" -> "tools, for example a wrench" |
| GR-7 | Use inclusive language. Do not use "he" or "she" for a person in general. | 100 | auto | "The operator must lock his panel." -> "The operator must lock the panel." |
| GR-8 | You can use the possessive ('s), but use it carefully. | 100 | judgment | "the pump's housing" -> "the pump housing" |
| HS | House style (advisory, never scored): wordy phrases that add words but no meaning. | 80 | auto | "in order to" -> "to" |
