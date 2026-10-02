---
type: regex
pattern: "\\b(?:utili[sz]e|prior to|in order to|ensure|imperative|commenc\\w*|should)\\b"
flags: i
match: not_contains
---

The rewrite drops the unapproved words of the original.
