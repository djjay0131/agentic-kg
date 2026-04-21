---
title: Status
nav_order: 3
has_children: true
permalink: /status/
---

# Status

Live project-state dashboard, auto-regenerated from `llm/memory_bank/` and `llm/features/` on every push to `master`.

{% assign s = site.data.status %}

## Graph snapshot

| Metric | Value |
|---|---|
| Last updated | **{{ s.last_updated }}** |
| Graph nodes | {{ s.graph_nodes }} |
| Graph edges | {{ s.graph_edges }} |
| Problem mentions | {{ s.problem_mentions }} |
| Problem concepts | {{ s.problem_concepts }} |
| Sanity checks | {{ s.sanity_checks }} |
| Completed sprints | {{ s.completed_sprints }} |
| Tests passing | {{ s.tests_passing }} |

## Drill-down

- [Backlog →]({% link status/backlog.md %}) — active and resolved features, auto-regenerated from `llm/features/BACKLOG.md`
- [Sprints →]({% link status/sprints.md %}) — all {{ s.completed_sprints }} completed sprints with links to source docs
- [Changelog →]({% link status/changelog.md %}) — curated highlights
- [Service inventory →]({% link status/service-inventory.md %}) — deployed endpoints and infra components
