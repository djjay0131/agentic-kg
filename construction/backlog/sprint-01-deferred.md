# Sprint 01 Deferred Items

**Created:** 2026-01-07
**Sprint:** 01 - Knowledge Graph Foundation
**Status:** Documented for future work

---

## Deferred Functional Requirements

### 1. Multi-hop Graph Traversal (FR-2.3.4)

**Requirement:** Support configurable depth traversal (e.g., "problems that extend problems that extend P")

**Current State:** `get_related_problems()` returns single-hop relations only.

**Suggested Implementation:**
```python
def get_related_problems(
    self,
    problem_id: str,
    relation_type: Optional[RelationType] = None,
    direction: str = "both",
    max_depth: int = 1,  # Add this parameter
) -> list[tuple[Problem, ProblemRelation, int]]:  # Add depth to return
```

**Priority:** Medium - Useful for agent-based exploration in Phase 3

**Target Sprint:** Sprint 03 (Agent Orchestration) or as needed

---

### 2. Referential Integrity on Paper Delete (FR-2.5.2)

**Requirement:** Cannot delete a Paper if Problems reference it via EXTRACTED_FROM

**Current State:** `delete_paper()` does not check for EXTRACTED_FROM relations.

**Suggested Implementation:**
```python
def delete_paper(self, doi: str, force: bool = False) -> bool:
    # Check for EXTRACTED_FROM relations first
    if not force:
        count = self._count_extracted_problems(doi)
        if count > 0:
            raise IntegrityError(
                f"Cannot delete paper {doi}: {count} problems reference it"
            )
```

**Priority:** Low - Soft delete is preferred pattern anyway

**Target Sprint:** As needed (low priority)

---

## Deferred Documentation

### 3. Neo4j Aura Production Setup

**Location:** Task 2 in sprint-01-knowledge-graph.md

**Scope:**
- Document Neo4j Aura account setup
- Connection string format for Aura
- Secret management in GCP
- Backup and restore procedures

**Priority:** High - Needed before production deployment

**Target Sprint:** Sprint 02 or pre-production checklist

---

### 4. Sample Data Schema Documentation

**Location:** Task 10 in sprint-01-knowledge-graph.md

**Scope:**
- Document the structure of sample problems
- Explain relationships between sample entities
- Provide guidance for adding new sample data

**Priority:** Low - Developer convenience

**Target Sprint:** As needed

---

### 5. Update techContext.md with Neo4j Details

**Location:** Task 11 in sprint-01-knowledge-graph.md

**Scope:**
- Add Neo4j architecture to techContext.md
- Document graph schema
- Add Cypher query patterns

**Priority:** Medium - Documentation hygiene

**Target Sprint:** Before Sprint 02 starts

---

## Notes

- All core user stories (US-01 through US-05) are implemented
- All high-priority functional requirements are complete
- Deferred items are low-to-medium priority and do not block Sprint 01 merge
- Multi-hop traversal will be more important when agents need to explore the graph
