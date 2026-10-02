---
type: regex
target: trace
pattern: "\"file_path\"\\s*:\\s*\"[^\"]*notes\\.md\"[^}]*\"(?:old_string|content)\""
match: not_contains
---

The audited file is not edited.
