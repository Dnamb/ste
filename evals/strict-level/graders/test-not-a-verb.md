---
type: regex
pattern: "\\b(?:test|tests|tested|testing)\\s+(?:the|a|an|your|it|this)\\b"
flags: i
match: not_contains
---

In STE, "test" is a noun ("do a test of the battery"), not a verb.
