"""Persistence and human-decision service for nightly review items.

The storage label is ``:Canon__ReviewItem`` under the canonical ``ns``
namespace (ADR-0004/0005): one staging database, a disjoint label space, and
every read and write scoped by namespace. A review item is workflow state, not
canonical graph content — no canonical ``Identity``/``Assertion`` reader selects
this label — but it lives on the same isolated path so the queue cannot leak
into the legacy graph either.

Human decisions are **append-only**: :meth:`ReviewItemRepository.record_decision`
updates the item's current status and creates a ``:Canon__ReviewDecision`` node
linked with ``[:HAS_DECISION]``. A decision record is never updated or deleted,
so a later status change still leaves the decision history intact.

The commit itself is delegated to
:func:`agentic_kg.migration.curation.commit.commit_candidate`, imported lazily so
this module (and the API router that uses it) imports on a default install
without the opt-in ``migration`` extra.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from neo4j import ManagedTransaction

from agentic_kg.knowledge_graph.models.review_item import (
    VERDICT_ADMIT,
    ReviewAgreement,
    ReviewDecisionRecord,
    ReviewItem,
    is_merge_verdict,
)
from agentic_kg.knowledge_graph.repository import (
    Neo4jRepository,
    RepositoryError,
    get_repository,
)

#: Default page size for ``/api/review``.
DEFAULT_LIMIT = 50
MAX_LIMIT = 200

#: Node labels. Both carry the canonical prefix; neither is canonical content.
LABEL_REVIEW_ITEM = "Canon__ReviewItem"
LABEL_REVIEW_DECISION = "Canon__ReviewDecision"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ReviewItemNotFoundError(RepositoryError):
    """The requested review item does not exist in this namespace."""


class ReviewItemNotPendingError(RepositoryError):
    """The item already has a human decision; decisions are not overwritten."""


class ReviewItemBlockedError(RepositoryError):
    """The item cannot be approved because its candidate is not committable."""


@dataclass(frozen=True)
class ReviewCommitOutcome:
    """The facts a successful commit adds to the decision record."""

    epoch: Optional[int]
    identity_ids: tuple[str, ...] = ()


#: The seam the API uses to commit. Tests inject a fake that records calls.
Committer = Callable[[ReviewItem], ReviewCommitOutcome]


def commit_via_kgcs(item: ReviewItem) -> ReviewCommitOutcome:
    """Commit one approved item through KGCS (lazy optional import)."""
    from agentic_kg.migration.curation.commit import commit_candidate

    result = commit_candidate(
        item.candidate_payload,
        namespace=item.namespace,
    )
    return ReviewCommitOutcome(
        epoch=result.epoch,
        identity_ids=result.identity_ids,
    )


class ReviewItemRepository:
    """Read/write access to ``:Canon__ReviewItem`` nodes for one namespace."""

    def __init__(
        self,
        repository: Neo4jRepository,
        *,
        namespace: Optional[str] = None,
    ) -> None:
        self._repo = repository
        self.namespace = (
            namespace
            or os.getenv("KGCS_CANONICAL_NAMESPACE")
            or "canon"
        )

    @classmethod
    def from_uri(
        cls,
        uri: str,
        *,
        username: str = "neo4j",
        password: str = "",
        database: str = "neo4j",
        namespace: Optional[str] = None,
    ) -> "ReviewItemRepository":
        """Build a store against an explicit target.

        Kept here so integration tests do not construct a repository directly;
        the session's database-ownership guard still wraps the constructor, and
        only a database the test session started/declared is accepted.
        """
        from agentic_kg.config import Neo4jConfig

        return cls(
            Neo4jRepository(
                Neo4jConfig(
                    uri=uri,
                    username=username,
                    password=password,
                    database=database,
                )
            ),
            namespace=namespace,
        )

    def close(self) -> None:
        """Release the underlying repository's driver."""
        self._repo.close()

    # -- writes ------------------------------------------------------------

    def save(self, item: ReviewItem) -> ReviewItem:
        """Upsert an item by ``uid`` without overwriting a human decision.

        ``MERGE`` on ``(ns, run_id, candidate_id)`` makes the write idempotent.
        The advisory fields are refreshed only while the item is still
        ``pending``; once a human has decided, a retried adviser stage cannot
        silently replace the grade the human saw.
        """
        if item.namespace != self.namespace:
            raise RepositoryError(
                f"review item namespace {item.namespace!r} does not match this "
                f"store's namespace {self.namespace!r}"
            )
        props = item.to_neo4j_properties()
        now = item.updated_at or item.created_at or _now_iso()

        def _save(tx: ManagedTransaction) -> None:
            tx.run(
                """
                MERGE (r:Canon__ReviewItem {uid: $uid})
                ON CREATE SET r = $props
                WITH r
                WHERE r.status = 'pending'
                SET r.adviser_verdict = $adviser_verdict,
                    r.confidence = $confidence,
                    r.rationale = $rationale,
                    r.adviser_abstained = $adviser_abstained,
                    r.adviser_type = $adviser_type,
                    r.adviser_version = $adviser_version,
                    r.adviser_model_id = $adviser_model_id,
                    r.adviser_model_version = $adviser_model_version,
                    r.prompt_version = $prompt_version,
                    r.candidate_summary = $candidate_summary,
                    r.candidate_payload = $candidate_payload,
                    r.evidence_refs = $evidence_refs,
                    r.committable = $committable,
                    r.commit_blocker = $commit_blocker,
                    r.updated_at = $now
                """,
                uid=item.uid,
                props=props,
                adviser_verdict=item.adviser_verdict,
                confidence=item.confidence,
                rationale=item.rationale,
                adviser_abstained=item.adviser_abstained,
                adviser_type=item.adviser_type,
                adviser_version=item.adviser_version,
                adviser_model_id=item.adviser_model_id,
                adviser_model_version=item.adviser_model_version,
                prompt_version=item.prompt_version,
                candidate_summary=item.candidate_summary,
                candidate_payload=props["candidate_payload"],
                evidence_refs=props["evidence_refs"],
                committable=item.committable,
                commit_blocker=item.commit_blocker,
                now=now,
            )

        with self._repo.session() as session:
            session.execute_write(_save)
        return item

    def record_decision(
        self,
        item: ReviewItem,
        *,
        decision: str,
        user_id: str,
        note: Optional[str] = None,
        committed_epoch: Optional[int] = None,
        committed_identity_ids: tuple[str, ...] = (),
    ) -> ReviewItem:
        """Record a human decision and append an immutable decision node."""
        if decision not in ("approve", "reject"):
            raise ValueError(f"invalid decision: {decision!r}")
        status = "approved" if decision == "approve" else "rejected"
        now = _now_iso()
        record = ReviewDecisionRecord(
            uid=f"review_decision_{uuid.uuid4().hex}",
            review_uid=item.uid,
            namespace=item.namespace,
            decision=decision,  # type: ignore[arg-type]
            decided_by=user_id,
            decided_at=now,
            note=note,
            committed_epoch=committed_epoch,
            committed_identity_ids=list(committed_identity_ids),
        )

        def _decide(tx: ManagedTransaction) -> Optional[dict]:
            record_result = tx.run(
                """
                MATCH (r:Canon__ReviewItem {uid: $uid, ns: $ns})
                WITH r
                WHERE r.status = 'pending'
                SET r.status = $status,
                    r.decided_by = $user_id,
                    r.decided_at = $now,
                    r.decision_note = $note,
                    r.committed_epoch = $committed_epoch,
                    r.committed_identity_ids = $committed_identity_ids,
                    r.updated_at = $now
                WITH r
                CREATE (d:Canon__ReviewDecision $decision_props)
                CREATE (r)-[:HAS_DECISION]->(d)
                RETURN r
                """,
                uid=item.uid,
                ns=item.namespace,
                status=status,
                user_id=user_id,
                now=now,
                note=note,
                committed_epoch=committed_epoch,
                committed_identity_ids=json.dumps(list(committed_identity_ids)),
                decision_props=record.to_neo4j_properties(),
            ).single()
            return dict(record_result["r"]) if record_result else None

        with self._repo.session() as session:
            props = session.execute_write(_decide)
        if props is None:
            raise ReviewItemNotPendingError(
                f"review item {item.id} is not pending; decisions are append-only"
            )
        return ReviewItem.from_neo4j_properties(props)

    # -- reads -------------------------------------------------------------

    def get(self, item_id: str) -> ReviewItem:
        def _get(tx: ManagedTransaction) -> Optional[dict]:
            record = tx.run(
                """
                MATCH (r:Canon__ReviewItem {id: $id, ns: $ns})
                RETURN r LIMIT 1
                """,
                id=item_id,
                ns=self.namespace,
            ).single()
            return dict(record["r"]) if record else None

        with self._repo.session() as session:
            props = session.execute_read(_get)
        if props is None:
            raise ReviewItemNotFoundError(f"review item not found: {item_id}")
        return ReviewItem.from_neo4j_properties(props)

    def list(
        self,
        *,
        status: Optional[str] = None,
        candidate_type: Optional[str] = None,
        run_id: Optional[str] = None,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> tuple[list[ReviewItem], int]:
        """Return a page of items plus the total matching the same filters."""
        params = {
            "ns": self.namespace,
            "status": status,
            "candidate_type": candidate_type,
            "run_id": run_id,
        }
        where = """
            WHERE ($status IS NULL OR r.status = $status)
              AND ($candidate_type IS NULL
                   OR r.candidate_kind = $candidate_type
                   OR r.entity_type = $candidate_type)
              AND ($run_id IS NULL OR r.run_id = $run_id)
        """

        def _list(tx: ManagedTransaction) -> list[dict]:
            result = tx.run(
                f"""
                MATCH (r:Canon__ReviewItem {{ns: $ns}})
                {where}
                RETURN r
                ORDER BY r.created_at DESC, r.id ASC
                SKIP $offset LIMIT $limit
                """,
                **params,
                offset=offset,
                limit=limit,
            )
            return [dict(record["r"]) for record in result]

        def _count(tx: ManagedTransaction) -> int:
            record = tx.run(
                f"""
                MATCH (r:Canon__ReviewItem {{ns: $ns}})
                {where}
                RETURN count(r) AS count
                """,
                **params,
            ).single()
            return int(record["count"]) if record else 0

        with self._repo.session() as session:
            props_list = session.execute_read(_list)
            total = session.execute_read(_count)
        return [ReviewItem.from_neo4j_properties(p) for p in props_list], total

    def candidate_ids_for_run(self, run_id: str) -> set[str]:
        """The candidate ids already queued for a run (idempotency check)."""

        def _ids(tx: ManagedTransaction) -> set[str]:
            result = tx.run(
                """
                MATCH (r:Canon__ReviewItem {ns: $ns, run_id: $run_id})
                RETURN r.candidate_id AS candidate_id
                """,
                ns=self.namespace,
                run_id=run_id,
            )
            return {record["candidate_id"] for record in result}

        with self._repo.session() as session:
            return session.execute_read(_ids)

    def count_items(
        self,
        *,
        status: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> int:
        def _count(tx: ManagedTransaction) -> int:
            record = tx.run(
                """
                MATCH (r:Canon__ReviewItem {ns: $ns})
                WHERE ($status IS NULL OR r.status = $status)
                  AND ($run_id IS NULL OR r.run_id = $run_id)
                RETURN count(r) AS count
                """,
                ns=self.namespace,
                status=status,
                run_id=run_id,
            ).single()
            return int(record["count"]) if record else 0

        with self._repo.session() as session:
            return session.execute_read(_count)

    def verdict_counts(self, run_id: str) -> dict[str, int]:
        """Adviser verdict mix for one run, keyed by the stored verdict."""

        def _counts(tx: ManagedTransaction) -> dict[str, int]:
            result = tx.run(
                """
                MATCH (r:Canon__ReviewItem {ns: $ns, run_id: $run_id})
                RETURN r.adviser_verdict AS verdict, count(r) AS count
                """,
                ns=self.namespace,
                run_id=run_id,
            )
            return {
                record["verdict"]: int(record["count"])
                for record in result
                if record["verdict"] is not None
            }

        with self._repo.session() as session:
            counts = session.execute_read(_counts)
        return dict(sorted(counts.items()))

    def agreement_by_entity_type(self) -> dict[str, ReviewAgreement]:
        """Adviser-vs-human agreement to date, per entity type.

        The adviser's expectation is derived from its verdict: ``admit`` and
        ``merge-into:<id>`` both expect approval; ``reject`` expects rejection.
        A merge recommendation is counted against approval because it is a
        non-reject recommendation, but merge is not executable in this slice;
        the item's ``committable`` flag carries that separate fact.
        """

        def _decisions(tx: ManagedTransaction) -> list[dict]:
            result = tx.run(
                """
                MATCH (r:Canon__ReviewItem {ns: $ns})
                WHERE r.status IN ['approved', 'rejected']
                RETURN r.entity_type AS entity_type,
                       r.candidate_kind AS candidate_kind,
                       r.adviser_verdict AS adviser_verdict,
                       r.status AS status
                """,
                ns=self.namespace,
            )
            return [dict(record) for record in result]

        with self._repo.session() as session:
            rows = session.execute_read(_decisions)

        buckets: dict[str, ReviewAgreement] = {}
        for row in rows:
            entity_type = row.get("entity_type") or row.get("candidate_kind") or "unknown"
            bucket = buckets.setdefault(
                entity_type, ReviewAgreement(entity_type=entity_type)
            )
            verdict = row.get("adviser_verdict") or ""
            expects_approval = verdict == VERDICT_ADMIT or is_merge_verdict(verdict)
            actual_approval = row.get("status") == "approved"
            bucket.decided += 1
            if expects_approval == actual_approval:
                bucket.agreed += 1

        for bucket in buckets.values():
            bucket.agreement_rate = (
                round(bucket.agreed / bucket.decided, 4) if bucket.decided else None
            )
        return dict(sorted(buckets.items()))


class CandidateReviewService:
    """Approve/reject a review item, committing only on approval.

    The committer is injected so the API test can prove "approve commits,
    reject does not" without a database or the optional KGCS extra. The
    production default is :func:`commit_via_kgcs`.
    """

    def __init__(
        self,
        repository: ReviewItemRepository,
        *,
        committer: Optional[Committer] = None,
    ) -> None:
        self._repo = repository
        self._committer = committer or commit_via_kgcs

    @property
    def repository(self) -> ReviewItemRepository:
        return self._repo

    def get(self, item_id: str) -> ReviewItem:
        return self._repo.get(item_id)

    def list(self, **kwargs: Any) -> tuple[list[ReviewItem], int]:
        return self._repo.list(**kwargs)

    def agreement_by_entity_type(self) -> dict[str, ReviewAgreement]:
        return self._repo.agreement_by_entity_type()

    def approve(
        self, item_id: str, *, user_id: str, note: Optional[str] = None
    ) -> ReviewItem:
        item = self._repo.get(item_id)
        if item.status != "pending":
            raise ReviewItemNotPendingError(
                f"review item {item_id} is already {item.status}"
            )
        if not item.committable:
            raise ReviewItemBlockedError(
                item.commit_blocker
                or "this candidate cannot be committed until entity resolution is wired"
            )
        if is_merge_verdict(item.adviser_verdict):
            raise ReviewItemBlockedError(
                "the adviser recommended a merge; merge decisions are not "
                "executable in this slice"
            )
        outcome = self._committer(item)
        return self._repo.record_decision(
            item,
            decision="approve",
            user_id=user_id,
            note=note,
            committed_epoch=outcome.epoch,
            committed_identity_ids=outcome.identity_ids,
        )

    def reject(
        self, item_id: str, *, user_id: str, note: Optional[str] = None
    ) -> ReviewItem:
        item = self._repo.get(item_id)
        if item.status != "pending":
            raise ReviewItemNotPendingError(
                f"review item {item_id} is already {item.status}"
            )
        return self._repo.record_decision(
            item,
            decision="reject",
            user_id=user_id,
            note=note,
        )


def get_review_item_repository(
    repository: Optional[Neo4jRepository] = None,
    *,
    namespace: Optional[str] = None,
) -> ReviewItemRepository:
    """Build a repository bound to the process default Neo4j (if none given)."""
    return ReviewItemRepository(
        repository or get_repository(),
        namespace=namespace,
    )


__all__ = [
    "DEFAULT_LIMIT",
    "LABEL_REVIEW_DECISION",
    "LABEL_REVIEW_ITEM",
    "MAX_LIMIT",
    "CandidateReviewService",
    "Committer",
    "ReviewCommitOutcome",
    "ReviewItemBlockedError",
    "ReviewItemNotFoundError",
    "ReviewItemNotPendingError",
    "ReviewItemRepository",
    "commit_via_kgcs",
    "get_review_item_repository",
]
