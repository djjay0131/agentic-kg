"""Assert the KGIS/KGCS pins actually resolved to the commits they name.

The sibling of :mod:`suite_gate`, one layer down, and for the same reason.
``suite_gate`` exists because *counting is not identification*: a gate that
proved 53 tests ran proved nothing about whether the seven named ones did. The
CI step this module backs had the same shape — it was called *"Assert the extra
actually resolved"* and ran::

    python -c "import kg_contracts, kgcs; print(kg_contracts.__file__)"

which proves the packages **import**, not which commit they are.

That gap is not theoretical here. ``agentic-kgcs`` shipped a three-way breaking
change (agentic-kgcs#34: ``compensate()`` signature, ``CompensationResult``
shape, ``reversal_data`` nesting) while leaving ``version = "1.0.0"`` untouched
— releases go in a separate PR upstream. So **two mutually incompatible
packages self-report the same version**, and the SHA pin is the only thing that
distinguishes them. A check keyed on the version, or on importability, cannot
tell them apart; one keyed on the resolved commit can.

Four ways to fail, each of which has a real failure mode behind it:

* the two pyprojects name different commits for the same package — the root
  file's own comment requires them to be byte-identical, and nothing enforced
  it;
* a pin is not a full 40-hex commit — a branch or tag pin is mutable and pins
  nothing reproducible, which is the stated reason these are SHAs at all;
* the installed distribution has no ``direct_url.json`` — it came from an index
  rather than the git pin, so the version number is all you would have;
* it has one, and the commit disagrees with the pyprojects.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from importlib import metadata
from pathlib import Path

#: Requirement lines look like ``name @ git+https://host/org/repo@<sha>``.
_PIN = re.compile(r"^\s*(?P<name>[A-Za-z0-9._-]+)\s*@\s*git\+[^@]+@(?P<rev>\S+?)\s*$")

_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")

#: The packages this gate is responsible for.
TRACKED = ("agentic-kgis", "agentic-kgcs")


class PinGateError(AssertionError):
    """A pin is unverifiable, inconsistent, or not what got installed."""


def read_pins(pyproject: Path) -> dict[str, str]:
    """Every ``name @ git+…@rev`` requirement in the file, as ``{name: rev}``.

    Reads the parsed TOML rather than grepping, so a requirement that has been
    moved between dependency groups is still found and a commented-out one is
    not.
    """
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    project = data.get("project", {})
    groups: list[list[str]] = [project.get("dependencies", []) or []]
    groups.extend((project.get("optional-dependencies", {}) or {}).values())

    pins: dict[str, str] = {}
    for group in groups:
        for requirement in group:
            matched = _PIN.match(str(requirement))
            if matched is None:
                continue
            name = matched.group("name").lower()
            rev = matched.group("rev")
            if name in pins and pins[name] != rev:
                raise PinGateError(
                    f"{pyproject}: {name} is pinned to two different revisions "
                    f"({pins[name]} and {rev}) within the same file"
                )
            pins[name] = rev
    return pins


def installed_commit(distribution: str) -> str | None:
    """The commit a distribution was installed from, or ``None`` if not a VCS install."""
    try:
        raw = metadata.distribution(distribution).read_text("direct_url.json")
    except metadata.PackageNotFoundError as exc:
        raise PinGateError(f"{distribution} is not installed at all") from exc
    if not raw:
        return None
    return json.loads(raw).get("vcs_info", {}).get("commit_id")


def check(pyprojects: list[Path], tracked: tuple[str, ...] = TRACKED) -> str:
    """Raise :class:`PinGateError` unless every tracked pin resolved as named."""
    if not pyprojects:
        raise PinGateError("no pyproject files given; nothing was verified")

    per_file = {path: read_pins(path) for path in pyprojects}
    lines: list[str] = []

    for name in tracked:
        revisions = {path: pins[name] for path, pins in per_file.items() if name in pins}
        if not revisions:
            raise PinGateError(
                f"{name} is not pinned by git URL in any of "
                f"{[str(p) for p in pyprojects]}; a version specifier pins nothing "
                f"reproducible for these packages"
            )
        distinct = set(revisions.values())
        if len(distinct) > 1:
            detail = ", ".join(f"{path}={rev}" for path, rev in revisions.items())
            raise PinGateError(f"{name} is pinned inconsistently across files: {detail}")

        pinned = distinct.pop()
        if not _FULL_SHA.match(pinned):
            raise PinGateError(
                f"{name} is pinned to {pinned!r}, which is not a full 40-character "
                f"commit SHA; tags and branches are mutable and pin nothing"
            )

        actual = installed_commit(name)
        if actual is None:
            raise PinGateError(
                f"{name} was not installed from its git pin (no direct_url.json). "
                f"It resolved from an index, so its version number is the only "
                f"identity available - and these packages ship breaking changes "
                f"without bumping it."
            )
        if actual != pinned:
            raise PinGateError(f"{name} resolved to {actual}, but the pyprojects pin {pinned}")
        lines.append(f"{name} @ {pinned} (verified against direct_url.json)")

    return "; ".join(lines)


def main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv[1:]]
    if not paths:
        print("usage: pin_gate.py <pyproject.toml> [<pyproject.toml> ...]", file=sys.stderr)
        return 2
    try:
        print(check(paths))
    except PinGateError as exc:
        print(f"PIN GATE FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
