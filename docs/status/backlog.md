---
title: Backlog
parent: Status
nav_order: 1
---

# Feature Backlog

Auto-regenerated from [`llm/features/BACKLOG.md`](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/BACKLOG.md) on every push.

{% assign active = site.data.backlog.items | where: "resolved", false %}
{% assign done = site.data.backlog.items | where: "resolved", true %}

**{{ active | size }}** active, **{{ done | size }}** resolved.

## Active

{% include backlog-table.html items=active %}

<details>
<summary><strong>Resolved ({{ done | size }})</strong></summary>

{% include backlog-table.html items=done %}

</details>
