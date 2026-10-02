---
type: regex
pattern: "\\b[A-Za-z]+n['\u2019]t\\b|\\b(?:it|that|there|you|we|they|what|here)['\u2019](?:s|re|ll|ve|d)\\b|;|\\b(?:e\\.g\\.|i\\.e\\.|etc\\.|viz\\.)"
flags: i
match: not_contains
---

Tier 60 mechanics: no contractions, no semicolons, no Latin abbreviations.
