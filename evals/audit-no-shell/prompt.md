---
name: audit-no-shell
description: Without a shell, the audit is done in chat with rule ids
tags: [win, audit]
max_turns: 8
allowed_tools: [Read, Glob, Grep, Skill]
---

Audit this text against ASD-STE100 Simplified Technical English. List each problem with its rule number and a fix.

# Backup notes

Backups should be verified on a weekly basis, i.e. every Monday, so that data loss can't go unnoticed. The operator, having checked the logs, must then rotate the keys; this is done via the admin console.

1. Utilize the restore wizard and select the snapshot.
2. The restore is started by the operator after the volume has been mounted.
