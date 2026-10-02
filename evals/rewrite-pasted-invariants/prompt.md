---
name: rewrite-pasted-invariants
description: A pasted rewrite keeps code, URLs, units and error codes byte for byte
tags: [win, rewrite]
max_turns: 12
allowed_tools: [Read, Glob, Grep, Skill]
---

Rewrite this in ASD-STE100 Simplified Technical English. Give only the rewritten text, with no notes or list of changes.

# Restart the gateway

In order to restart the gateway, you should utilize the following command, and it is imperative that you ensure the pod has been drained prior to commencing.

```bash
kubectl rollout restart deployment/gateway -n edge
```

The documentation can be found at https://example.com/docs/gateway, and the maximum payload weight for the rack is 20 kg.

If the client reports ERR_CONN_RESET, the connection was dropped by the load balancer; wait approximately 30 seconds and retry.
