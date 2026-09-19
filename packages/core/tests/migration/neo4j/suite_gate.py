"""CI gate: did the *shared conformance suite* actually run, and pass?

    python packages/core/tests/migration/neo4j/suite_gate.py <junit.xml>

Why this exists in this shape
-----------------------------
The first version of this gate asserted only ``tests > 0 and skipped == 0``
over the junit XML. A reviewer defeated it by **deleting
``test_contract_conformance.py`` outright**: the remaining tests still reported
"53 tests, 0 skipped", so the gate passed while the seven shared contract tests
were gone. Renaming the subclass so pytest stops collecting it does the same.

That gate proved *something* ran. It did not prove the thing it was named for
ran — the seventh instance of that pattern in this programme, and the first one
inside a defence built against it. Counting is not identification.

So the gate now identifies the tests by name:

* the expected names are **derived at runtime from
  ``kg_contracts.testing.contract.GraphMutationStoreContract``**, not
  transcribed here. A transcript would be a local restatement of an upstream
  rule (§9.0 obligation 3) and would drift silently when upstream adds a test;
  deriving them means an added upstream test becomes a *requirement* here the
  moment the pin moves.
* every one of those names must appear in the XML under a single classname, and
  every one must have passed. Deleting the module removes them all; renaming the
  class out of pytest's collection pattern removes them all; overriding one with
  a stub that skips is a skip and is rejected.

``test_suite_gate.py`` points this function at doctored XML — deleted module,
renamed class, skipped test, failed test — and asserts each is rejected, so the
gate is itself falsifiable (§9.0 obligation 5).
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path


class SuiteGateError(AssertionError):
    """The junit report does not show the shared conformance suite passing."""


def expected_contract_tests() -> frozenset[str]:
    """The shared suite's test method names, read from upstream at runtime."""
    from kg_contracts.testing.contract import GraphMutationStoreContract

    names = frozenset(name for name in dir(GraphMutationStoreContract) if name.startswith("test_"))
    if not names:
        raise SuiteGateError(
            "GraphMutationStoreContract exposes no test_* methods - the gate "
            "cannot verify an empty suite (and would otherwise pass vacuously)"
        )
    return names


def check(junit_path: Path, expected: frozenset[str] | None = None) -> str:
    """Raise :class:`SuiteGateError` unless the shared suite ran and passed.

    Returns a one-line summary on success.
    """
    expected = expected_contract_tests() if expected is None else expected
    root = ET.parse(junit_path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root)

    total = skipped = failed = 0
    # name -> outcome, grouped by the class that ran it.
    by_class: dict[str, dict[str, str]] = defaultdict(dict)
    for suite in suites:
        for case in suite.iter("testcase"):
            total += 1
            outcome = "passed"
            for child in case:
                if child.tag == "skipped":
                    outcome = "skipped"
                    skipped += 1
                elif child.tag in ("failure", "error"):
                    outcome = "failed"
                    failed += 1
            classname = case.get("classname") or ""
            name = case.get("name") or ""
            by_class[classname][name] = outcome

    if total == 0:
        raise SuiteGateError(f"{junit_path}: no tests at all")
    if skipped:
        raise SuiteGateError(
            f"{junit_path}: {skipped} of {total} tests skipped - the adapter "
            f"suite must run in full (a missing Docker or a missing "
            f"'migration' extra skips it and still reports success)"
        )
    if failed:
        raise SuiteGateError(f"{junit_path}: {failed} of {total} tests failed")

    # The identification step: some one class must have run ALL of them.
    for classname, outcomes in sorted(by_class.items()):
        present = expected & outcomes.keys()
        if present != expected:
            continue
        not_passed = sorted(n for n in expected if outcomes[n] != "passed")
        if not_passed:
            raise SuiteGateError(f"{junit_path}: {classname} did not pass {not_passed}")
        return (
            f"{total} tests, 0 skipped, 0 failed; "
            f"{len(expected)}/{len(expected)} shared contract tests passed "
            f"under {classname}"
        )

    best = max((len(expected & outcomes.keys()) for outcomes in by_class.values()), default=0)
    raise SuiteGateError(
        f"{junit_path}: no class ran the shared GraphMutationStoreContract "
        f"suite. Expected all {len(expected)} of {sorted(expected)}; the "
        f"closest class ran {best}. The conformance module was deleted, "
        f"renamed out of pytest's collection pattern, or never imported - "
        f"{total} other tests passing does not substitute for it."
    )


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <junit.xml>", file=sys.stderr)
        return 2
    try:
        print(check(Path(argv[1])))
    except SuiteGateError as exc:
        print(f"SUITE GATE FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
