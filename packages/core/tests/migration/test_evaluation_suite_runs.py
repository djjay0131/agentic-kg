"""The evaluation suite must actually run somewhere, and never skip silently.

**History, because the guard's shape changed for a reason.** This file used to
assert the opposite of what it asserts now. When the evaluation subpackage
landed, no CI job installed the ``migration`` extra, so every test under
``evaluation/`` was skipped in CI — reported green having verified nothing. The
guard asserted that gap existed and told whoever closed it to delete the test.

PR #74 closed it. Its ``migration-canonical-adapter`` job installs
``./packages/core[migration]`` and runs ``pytest packages/core/tests/migration``,
which is this directory. The evaluation tests now really execute in CI, the old
assertion fired exactly as designed, and it is gone.

What replaces it is the durable half. There are two CI contexts for this
directory and they are *supposed* to differ:

* ``test.yml`` -> ``test (3.12)`` installs no extras and runs
  ``packages/core/tests``. The evaluation tests skip there, correctly and by
  design.
* ``integration-tests.yml`` -> ``migration-canonical-adapter`` installs the
  extra and runs ``packages/core/tests/migration``. They must really run there.

So "the extra is absent" is no longer a fact worth asserting — it depends on
which job you are in. Two things are still worth asserting, and each would
silently void this entire suite:

1. **A skip that should have been a run.** If ``kg_eval`` is importable and the
   tests skip anyway, the suite is a no-op wearing a green check. This is the
   failure this repo keeps rediscovering in new places, so it is checked
   directly rather than inferred.
2. **No job runs them at all.** If the one job that installs the extra is
   deleted or stops running this path, the tests go back to skipping everywhere
   and nothing else notices. The workflow check is a supporting leg here, not
   the only one — an earlier version of this guard relied on workflow-grepping
   alone and was defeated twice by install spellings it had not anticipated.

   **And a third time, by a path prefix.** This check used to be the regex
   ``pytest\\s+packages/core/tests/migration\\b``. ``\\b`` is a word boundary,
   and
   it matches at the ``/`` in ``packages/core/tests/migration/neo4j`` — so a job
   narrowed to the ``neo4j`` subdirectory satisfied the assertion while every
   test under ``evaluation/`` stopped running. PR #83 did exactly that, for an
   unrelated and locally reasonable reason, and this guard stayed green through
   it; #83 reverted its own change and reported the defect rather than patching
   this file. The assertion and the fact it names had come apart: the assertion
   said "some job runs this directory", the regex actually tested "some job runs
   a path that *starts with* this directory's name", and a subdirectory
   satisfies the second without satisfying the first.

   The check is therefore no longer textual. It parses the workflow, and for
   **each job** asks two questions together: does this job install the
   ``migration`` extra, and does it run a ``pytest`` target that *contains* the
   evaluation directory — that directory itself or an ancestor of it, with any
   path excluded by ``--ignore``/``--deselect`` on the same invocation
   discounted? A subdirectory is not an ancestor, so the narrowing that defeated
   the regex now fails the assertion.

   **Both questions, of one job.** Fixing the containment check alone was not
   enough, and the way it failed is worth recording because it is the same
   defect wearing different clothes. The old assertion asked "does any line in
   this file install the extra?" and "does any line in this file run this path?"
   as two independent searches over one blob of text — and ``unit-tests`` runs
   ``pytest packages/core/tests/``, a genuine *ancestor* of the evaluation
   directory, while installing no extras. So a containment check that ignored
   job boundaries was satisfied by a job in which these tests provably skip,
   and it stayed green against the very narrowing it was written to catch.
   Two true facts about two different jobs do not compose into the fact the
   assertion names. The unit of the claim is a job, so the unit of the check is
   a job. ``test_an_ancestor_path_in_a_job_without_the_extra_does_not_count``
   pins that specific hole.

   ``test_the_guard_rejects_*`` drives the rejection for real, per §9.0
   obligation 5: a criterion that cannot fail for the reason it names is not a
   criterion.

This module lives one directory **above** ``evaluation/`` on purpose: a
module-level ``pytest.importorskip`` in a ``conftest.py`` skips the whole
directory it governs at collection time, so a copy of this file inside
``evaluation/`` would itself be skipped without ``kg_eval`` — a test about an
invisible skip, made invisible by that same skip. Nothing here imports
``kg_eval``.
"""

from __future__ import annotations

import fnmatch
import importlib.util
import os
import re
from pathlib import Path, PurePosixPath

import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
TEST_WORKFLOW = REPO_ROOT / ".github/workflows/test.yml"
INTEGRATION_WORKFLOW = REPO_ROOT / ".github/workflows/integration-tests.yml"
EVALUATION_DIR = Path(__file__).parent / "evaluation"

#: The same directory, repo-relative and POSIX-spelled — the form a workflow
#: writes and the form the containment check compares against. Derived from
#: :data:`EVALUATION_DIR` rather than typed twice, so moving the directory
#: cannot leave this guard silently asserting over a path that is gone.
EVALUATION_DIR_REL = PurePosixPath(EVALUATION_DIR.relative_to(REPO_ROOT).as_posix())

#: Modules the evaluation subpackage needs. Both ship in ``agentic-kgis``.
REQUIRED_MODULES = ("kg_eval", "kg_contracts")

#: Values that count as "this is CI". GitHub Actions sets ``CI=true``, but other
#: runners spell it ``1``, ``yes`` or ``on``, and arming only on the literal
#: ``"true"`` silently disarms anything keyed on it everywhere else. Matches the
#: truthiness convention already used by ``agentic_kg.migration.config``.
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def in_ci(value: str | None) -> bool:
    """Does ``value`` (a raw ``CI`` env var) mean "this is CI"?

    A named function rather than a module-level expression so the rule can be
    tested by *calling* it with each spelling. An earlier version asserted on the
    module's own source text and was vacuous: the assertion quoted the pattern it
    was searching for, so ``inspect.getsource`` always found it.
    """
    return (value or "").strip().lower() in _TRUTHY


IN_CI = in_ci(os.environ.get("CI"))


def _importable(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _evaluation_stack_importable() -> bool:
    return all(_importable(name) for name in REQUIRED_MODULES)


def _install_lines_in(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if re.search(r"\b(pip|uv)\b.*\binstall\b", line)
    ]


def _install_lines(workflow: Path) -> list[str]:
    return _install_lines_in(workflow.read_text(encoding="utf-8"))


def _extra_install_lines(lines: list[str]) -> list[str]:
    return [
        line
        for line in lines
        if re.search(r"\[[^\]]*migration", line) or re.search(r"agentic[-_]kgis", line)
    ]


def _installs_the_extra(workflow: Path) -> list[str]:
    return _extra_install_lines(_install_lines(workflow))


#: A ``pytest`` invocation, anchored so it is a command and not a substring of
#: one (``pytest-asyncio`` in an install line must not match).
_PYTEST_CALL = re.compile(r"(?:^|[\s;|&])pytest(?=\s)(?P<args>[^;|&]*)")

#: Options that remove a path from a run that would otherwise have included it.
_EXCLUDING_OPTS = ("--ignore", "--ignore-glob", "--deselect")


#: Characters that make an ``--ignore-glob`` value a pattern rather than a path.
_GLOB_CHARS = "*?["


def _strip_shell_comment(line: str) -> str:
    """Drop everything from an unquoted ``#`` to end of line.

    ``_run_scripts`` gets YAML-comment-free text for free, because the parser
    strips those. **Shell** comments inside a ``run:`` block survive it, and a
    commented-out command is not a command. Leaving them in let a leftover
    ``# pytest .../migration`` sitting above an active
    ``pytest .../migration/neo4j`` satisfy this guard — review of this PR
    measured exactly that. Comment-out-the-old-line-and-write-the-narrowed-one
    is the single most common way an invocation gets scoped down, and the step
    this guard reads is the most comment-heavy step in the workflow, so this is
    the realistic form of the defect, not a contrived one. It is the same gap as
    the ``\\b`` bug wearing a ``#``: the check and the fact had stopped
    referring to the same thing.

    A ``#`` only opens a comment at the start of a token, and never inside
    quotes, so ``--ignore=a#b`` and ``echo "# not a comment"`` are left alone.
    """
    quote = ""
    for index, char in enumerate(line):
        if quote:
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "#" and (index == 0 or line[index - 1].isspace()):
            return line[:index]
    return line


def _logical_lines(text: str) -> list[str]:
    """Workflow lines, comments removed and backslash-continuations joined.

    A ``run: |`` block writes one command across several lines. Reading the file
    line-by-line would see ``pytest packages/core/tests/migration \\`` and the
    flags beneath it as unrelated lines, and would miss a target written on a
    continuation line entirely.

    Comments are stripped **before** continuations are joined, which is also
    what a shell does: a comment ends at the newline, and a trailing backslash
    inside one continues nothing.
    """
    without_comments = "\n".join(_strip_shell_comment(line) for line in text.splitlines())
    return re.sub(r"\\\s*\n\s*", " ", without_comments).splitlines()


def _excludes_evaluation(option: str, value: str) -> bool:
    """Would this exclusion remove the evaluation directory from the run?

    ``--ignore`` and ``--deselect`` take a path, so containment answers it.
    ``--ignore-glob`` takes a pattern: the literal prefix before the first
    wildcard is tested for containment (which settles the common
    ``<dir>/*`` form), and the pattern is then matched against the directory
    and a representative file inside it. Ambiguity resolves toward *excluded* —
    that direction makes the guard fail red, and a false red is a question,
    while a false green is the bug this whole module exists to prevent.
    """
    if option == "--ignore-glob":
        cuts = [value.find(char) for char in _GLOB_CHARS if char in value]
        prefix = (value if not cuts else value[: min(cuts)]).rstrip("/")
        if prefix and _covers_evaluation(prefix):
            return True
        return any(
            fnmatch.fnmatch(candidate, value)
            for candidate in (
                str(EVALUATION_DIR_REL),
                str(EVALUATION_DIR_REL / "test_runner.py"),
            )
        )
    return _covers_evaluation(value)


def _pytest_invocations(text: str) -> list[tuple[list[str], list[tuple[str, str]]]]:
    """Every ``pytest`` call in ``text`` as ``(targets, [(option, value), ...])``.

    Path-shaped positional arguments are targets; paths given to ``--ignore``,
    ``--ignore-glob`` or ``--deselect`` are exclusions. Both are needed: a job
    that names this directory and then ignores the evaluation subdirectory is
    not running the evaluation suite, and counting it would reintroduce exactly
    the gap this guard exists to close, one option further along.

    **Both spellings.** pytest accepts ``--ignore=PATH`` and ``--ignore PATH``,
    and an earlier version of this function understood only the first. The
    space form was not merely missed: because the value is a bare path-shaped
    token, it was counted as a **target**, so the diagnostic positively claimed
    the evaluation directory was being run by the very invocation excluding it.
    The value token is therefore consumed here, which is what makes it
    impossible for an excluded path to be read back as a target.
    """
    invocations: list[tuple[list[str], list[tuple[str, str]]]] = []
    for line in _logical_lines(text):
        for call in _PYTEST_CALL.finditer(line):
            targets: list[str] = []
            excluded: list[tuple[str, str]] = []
            tokens = call.group("args").split()
            index = 0
            while index < len(tokens):
                token = tokens[index]
                index += 1
                if token.startswith("-"):
                    option, separator, value = token.partition("=")
                    if option in _EXCLUDING_OPTS:
                        if not separator and index < len(tokens):
                            value = tokens[index]
                            index += 1  # consume: never reachable as a target
                        if value:
                            excluded.append((option, value.rstrip("/")))
                    continue
                if "/" in token:
                    targets.append(token.rstrip("/"))
            if targets:
                invocations.append((targets, excluded))
    return invocations


def _covers_evaluation(target: str) -> bool:
    """Does running ``target`` run the evaluation directory?

    True only when ``target`` **is** that directory or an **ancestor** of it.
    This is the whole correction: the old regex asked whether the workflow text
    started with the migration directory's name, which a *sub*directory also
    satisfies. Containment is the fact the assertion names, so containment is
    what is tested.
    """
    try:
        candidate = PurePosixPath(target)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return False
    return EVALUATION_DIR_REL == candidate or EVALUATION_DIR_REL.is_relative_to(candidate)


def _targets_running_the_evaluation_suite(text: str) -> list[str]:
    """Targets of a ``pytest`` call that really would run ``evaluation/``."""
    running: list[str] = []
    for targets, excluded in _pytest_invocations(text):
        if any(_excludes_evaluation(option, value) for option, value in excluded):
            continue
        running.extend(target for target in targets if _covers_evaluation(target))
    return running


def _run_scripts(text: str) -> dict[str, str]:
    """``job id -> that job's shell, and only that job's shell``.

    Parsed, not grepped. The whole point of this function is the boundary: a
    ``run:`` block belongs to exactly one job, and an assertion about "a job
    that installs the extra and runs this path" is only true if one job does
    both. Flattening the file to text loses precisely the distinction the claim
    depends on.
    """
    document = yaml.safe_load(text) or {}
    jobs = document.get("jobs") or {}
    scripts: dict[str, str] = {}
    for job_id, job in jobs.items():
        steps = (job or {}).get("steps") or []
        scripts[str(job_id)] = "\n".join(
            step["run"]
            for step in steps
            if isinstance(step, dict) and isinstance(step.get("run"), str)
        )
    return scripts


def _jobs_running_the_evaluation_suite(text: str) -> dict[str, list[str]]:
    """Jobs that install the ``migration`` extra **and** run ``evaluation/``.

    Both conditions, of the same job. A job with the extra that runs a narrower
    path does not run these tests; a job that runs a containing path without the
    extra skips them. Only their conjunction is the fact worth asserting.
    """
    qualifying: dict[str, list[str]] = {}
    for job_id, script in _run_scripts(text).items():
        if not _extra_install_lines(_install_lines_in(script)):
            continue
        targets = _targets_running_the_evaluation_suite(script)
        if targets:
            qualifying[job_id] = targets
    return qualifying


# --------------------------------------------------------------------------


def test_workflows_exist() -> None:
    assert TEST_WORKFLOW.is_file(), f"expected {TEST_WORKFLOW}"
    assert INTEGRATION_WORKFLOW.is_file(), f"expected {INTEGRATION_WORKFLOW}"


def test_the_evaluation_tests_really_run_when_the_stack_is_importable() -> None:
    """A skip that should have been a run is this suite's worst failure mode.

    It is indistinguishable from a pass in a green check, and it is precisely how
    the evaluation tests spent their first CI runs verifying nothing. If
    ``kg_eval`` resolves, the modules must import and the suite must be live.
    """
    if not _evaluation_stack_importable():
        return  # correctly skipping; the other tests cover that context

    import kg_eval  # noqa: F401  — proves find_spec was not lying
    from agentic_kg.migration.evaluation import runner

    assert runner.run_evaluation is not None


def test_a_ci_job_installs_the_extra_and_runs_this_directory() -> None:
    """Deleting that job would send the suite back to skipping everywhere.

    Nothing else would notice: every other job installs no extras, so the tests
    would collect, skip, and report green. This is the supporting leg to the
    importability check above, never the only one — an earlier guard relied on
    workflow text alone and was defeated twice by install spellings it had not
    anticipated.
    """
    text = INTEGRATION_WORKFLOW.read_text(encoding="utf-8")
    assert _installs_the_extra(INTEGRATION_WORKFLOW), (
        "no job in integration-tests.yml installs the 'migration' extra. Without "
        "one, every test under tests/migration/evaluation/ skips in CI and the "
        "suite verifies nothing while reporting green."
    )
    qualifying = _jobs_running_the_evaluation_suite(text)
    assert qualifying, (
        "no job in integration-tests.yml both installs the 'migration' extra "
        f"and runs a pytest target containing {EVALUATION_DIR_REL}, so the "
        "evaluation suite is not executed by CI at all — it collects, skips, "
        "and reports green.\n"
        f"Per job (installs extra, pytest targets): {_diagnose(text)}\n"
        "Two things this deliberately does NOT accept: a path *under* the "
        "migration directory (e.g. packages/core/tests/migration/neo4j), which "
        "does not run this suite; and a containing path run by a job that "
        "installs no extras (e.g. unit-tests), where these tests skip."
    )


def _diagnose(text: str) -> dict[str, tuple[bool, list[str]]]:
    """Per-job (installs the extra, pytest targets) — for the failure message."""
    return {
        job_id: (
            bool(_extra_install_lines(_install_lines_in(script))),
            [t for targets, _ in _pytest_invocations(script) for t in targets],
        )
        for job_id, script in _run_scripts(text).items()
    }


#: A minimal one-job workflow: installs the extra, then runs whatever is given.
#: Synthetic on purpose — these tests must be able to express a workflow the
#: repo does not have, which is the only way to drive the rejection paths.
_WORKFLOW_TEMPLATE = """name: t
jobs:
  migration-canonical-adapter:
    steps:
      - name: Install
        run: |
          pip install -e "./packages/core[migration]"
      - name: Run
        run: |
{run}
"""


def _workflow(run_block: str) -> str:
    return _WORKFLOW_TEMPLATE.format(run=run_block)


def test_the_guard_rejects_a_path_narrowed_to_a_subdirectory() -> None:
    """§9.0 obligation 5: the criterion must fail for the reason it names.

    This is PR #83's change, reconstructed: the extra is installed, the job
    runs, and the path is scoped one level down. The superseded regex
    ``pytest\\s+packages/core/tests/migration\\b`` matched it, because ``\\b``
    matches at the ``/`` — the assertion stayed green while the evaluation suite
    stopped running. If this test ever passes vacuously, the guard has gone back
    to verifying nothing.
    """
    narrowed = _workflow("          pytest packages/core/tests/migration/neo4j -v\n")

    assert re.search(r"pytest\s+packages/core/tests/migration\b", narrowed), (
        "the superseded regex no longer matches the narrowed spelling, so this "
        "test is no longer reconstructing the defect it documents"
    )
    assert _jobs_running_the_evaluation_suite(narrowed) == {}, (
        "a job scoped to migration/neo4j does not run migration/evaluation, but "
        "the guard counted it as one that does"
    )


def test_the_guard_accepts_the_directory_itself_and_its_ancestors() -> None:
    """The other half: the spellings that really do run it must be recognised.

    A check that rejected everything would also pass the test above.
    """
    for target in (
        "packages/core/tests/migration/evaluation",
        "packages/core/tests/migration",
        "packages/core/tests/migration/",
        "packages/core/tests",
        "packages/core",
    ):
        text = _workflow(f"          pytest {target} -v --tb=short\n")
        assert _jobs_running_the_evaluation_suite(text) == {
            "migration-canonical-adapter": [target.rstrip("/")]
        }, f"{target} contains the evaluation directory but was not recognised"


def test_an_ancestor_path_in_a_job_without_the_extra_does_not_count() -> None:
    """The hole that a job-blind containment check left open.

    ``unit-tests`` really does run ``packages/core/tests/`` — an ancestor of the
    evaluation directory — and really does install no extras, so these tests
    skip there. Reading the workflow as one blob, "something installs the extra"
    and "something runs a containing path" are both true while no job does both,
    and the guard goes green against the exact narrowing it exists to catch.
    """
    text = """name: t
jobs:
  unit-tests:
    steps:
      - run: |
          pip install -e ./packages/core
      - run: |
          pytest packages/core/tests/ -m "not integration"
  migration-canonical-adapter:
    steps:
      - run: |
          pip install -e "./packages/core[migration]"
      - run: |
          pytest packages/core/tests/migration/neo4j -v
"""
    assert _extra_install_lines(_install_lines_in(text)), "precondition: extra installed somewhere"
    assert _targets_running_the_evaluation_suite(text) == ["packages/core/tests"], (
        "precondition: a containing path is run somewhere in this workflow"
    )
    assert _jobs_running_the_evaluation_suite(text) == {}, (
        "no single job both installs the extra and runs the evaluation "
        "directory, but the guard accepted the workflow anyway"
    )


def test_a_commented_out_pytest_line_does_not_count() -> None:
    """A commented-out command is not a command.

    Both shapes measured in review of this PR, both of which kept the guard
    green through the exact narrowing it names. The first is the realistic one:
    comment out the old invocation, write the narrowed one underneath.
    """
    commented_then_narrowed = _workflow(
        "          # pytest packages/core/tests/migration -v\n"
        "          pytest packages/core/tests/migration/neo4j -v\n"
    )
    assert _jobs_running_the_evaluation_suite(commented_then_narrowed) == {}, (
        "a commented-out full-tree line satisfied the guard while the live "
        "invocation was scoped to a subdirectory"
    )

    wholly_commented = _workflow(
        "          echo 'temporarily disabled'\n"
        "          # pytest packages/core/tests/migration -v --tb=short\n"
    )
    assert _jobs_running_the_evaluation_suite(wholly_commented) == {}, (
        "the only pytest line was commented out, so nothing runs the suite"
    )


def test_a_hash_inside_quotes_or_mid_token_is_not_a_comment() -> None:
    """The comment strip must not eat live command text.

    Over-stripping would fail red on a correct workflow, which is safe but
    wrong; this pins the boundary so the fix for the bypass does not become a
    different defect.
    """
    assert _strip_shell_comment('echo "# not a comment" ') == 'echo "# not a comment" '
    assert _strip_shell_comment("pytest a/b --opt=x#y") == "pytest a/b --opt=x#y"
    assert _strip_shell_comment("pytest a/b  # trailing") == "pytest a/b  "
    assert _strip_shell_comment("# whole line") == ""
    assert _jobs_running_the_evaluation_suite(
        _workflow("          pytest packages/core/tests/migration -v  # the whole tree\n")
    ) == {"migration-canonical-adapter": ["packages/core/tests/migration"]}


def test_a_backslash_inside_a_comment_does_not_swallow_the_next_command() -> None:
    """Comments are stripped *before* continuations are joined, as a shell does.

    A comment ends at the newline, so a trailing backslash inside one continues
    nothing. Joining first would splice the live command below into the comment
    text and then delete both, reporting "no job runs the suite" for a workflow
    that does. That direction fails red rather than green, so it is the safe
    kind of wrong — but ``_logical_lines`` states that the order matters, and a
    stated design decision with no test behind it is the shape this module
    exists to object to. Mutation testing found this exact ordering swap
    surviving; this is the test that kills it.
    """
    text = _workflow(
        "          echo 'note' # disabled for now \\\n"
        "          pytest packages/core/tests/migration -v\n"
    )
    assert _jobs_running_the_evaluation_suite(text) == {
        "migration-canonical-adapter": ["packages/core/tests/migration"]
    }, "a backslash inside a comment swallowed the live pytest line below it"


def test_every_exclusion_spelling_is_honoured() -> None:
    """pytest accepts ``--ignore=PATH`` and ``--ignore PATH``. So must this.

    Only the ``=`` spelling was handled before. Review of this PR measured the
    other four as ACCEPTED — each one a job that names the migration tree and
    then excludes the evaluation directory from it, counted as a job that runs
    the evaluation directory.
    """
    evaluation = "packages/core/tests/migration/evaluation"
    for exclusion in (
        f"--ignore={evaluation}",
        f"--ignore {evaluation}",
        f"--ignore={evaluation}/",
        f"--deselect={evaluation}",
        f"--deselect {evaluation}",
        f"--ignore-glob={evaluation}/*",
        f"--ignore-glob {evaluation}/*",
        "--ignore-glob=packages/core/tests/migration/*",
    ):
        text = _workflow(f"          pytest packages/core/tests/migration {exclusion} -v\n")
        assert _jobs_running_the_evaluation_suite(text) == {}, (
            f"{exclusion!r} removes the evaluation directory from the run, but "
            "the guard still counted this job as running it"
        )


def test_an_exclusion_that_spares_the_evaluation_dir_still_counts() -> None:
    """The counterweight: not every exclusion disqualifies the job.

    Without this, making ``_excludes_evaluation`` return ``True`` unconditionally
    would pass the test above, and the guard would reject every real workflow.
    """
    for exclusion in (
        "--ignore=packages/core/tests/migration/neo4j",
        "--ignore packages/core/tests/migration/neo4j",
        "--ignore-glob=*/neo4j/*",
        "--deselect packages/core/tests/migration/neo4j/test_read_surface.py::test_x",
    ):
        text = _workflow(f"          pytest packages/core/tests/migration {exclusion} -v\n")
        assert _jobs_running_the_evaluation_suite(text) == {
            "migration-canonical-adapter": ["packages/core/tests/migration"]
        }, f"{exclusion!r} does not exclude the evaluation directory"


def test_an_excluded_path_is_never_counted_as_a_target() -> None:
    """The space form was worse than a miss.

    Because ``--ignore PATH`` puts a bare path-shaped token in the argument
    list, the excluded path was itself collected as a target — so the guard's
    diagnostic would have positively reported the evaluation directory as one of
    the paths being run by the invocation that excludes it.
    """
    text = _workflow(
        "          pytest packages/core/tests/migration "
        "--ignore packages/core/tests/migration/evaluation -v\n"
    )
    targets = [target for targets, _ in _pytest_invocations(text) for target in targets]
    assert targets == ["packages/core/tests/migration"], (
        f"the ignored path leaked into the target list: {targets}"
    )


def test_a_target_whose_evaluation_dir_is_ignored_does_not_count() -> None:
    """Same defect class, one option further along.

    Naming the directory and then excluding it runs none of these tests. A check
    that looked only at positional targets would call that green.
    """
    text = _workflow(
        "          pytest packages/core/tests/migration "
        "--ignore=packages/core/tests/migration/evaluation -v\n"
    )
    assert _jobs_running_the_evaluation_suite(text) == {}


def test_pytest_is_matched_as_a_command_not_as_a_substring() -> None:
    """``pip install pytest-asyncio`` is not a pytest invocation."""
    assert _pytest_invocations("          pip install pytest-asyncio ./packages/core\n") == []


def test_a_target_on_a_continuation_line_is_still_seen() -> None:
    """Backslash-continuations are joined before parsing.

    A line-by-line reader would miss this target entirely and report "no job
    runs the suite" for a workflow that does.
    """
    text = _workflow(
        "          pytest \\\n            packages/core/tests/migration \\\n            -v\n"
    )
    assert _jobs_running_the_evaluation_suite(text) == {
        "migration-canonical-adapter": ["packages/core/tests/migration"]
    }


def test_the_real_workflow_job_is_the_one_we_think_it_is() -> None:
    """Name the job, so a rename is a decision rather than a silent drift."""
    qualifying = _jobs_running_the_evaluation_suite(
        INTEGRATION_WORKFLOW.read_text(encoding="utf-8")
    )
    assert "migration-canonical-adapter" in qualifying, (
        f"expected migration-canonical-adapter to run the suite; got {qualifying}"
    )


def test_the_no_extras_job_is_still_expected_to_skip() -> None:
    """``test (3.12)`` installs no extras, and that is correct, not a defect.

    Pinned so the two contexts stay deliberately different. If this job ever
    starts installing the extra, the evaluation tests run twice per CI run —
    harmless, but it should be a decision rather than a surprise.
    """
    assert _install_lines(TEST_WORKFLOW), "no install commands found in test.yml"
    with_extra = _installs_the_extra(TEST_WORKFLOW)
    assert not with_extra, (
        f"test.yml now installs the migration extra ({with_extra}). That is not "
        "wrong, but the evaluation suite will now run in two jobs; update this "
        "test deliberately rather than leaving it stale."
    )


def test_the_conftest_guard_is_still_in_place() -> None:
    """The skip must stay an ``importorskip``, not become a bare import.

    A bare import would turn a no-extras run into a collection *error* for the
    whole directory rather than a clean skip — and ``test (3.12)`` is a no-extras
    run.
    """
    conftest = (EVALUATION_DIR / "conftest.py").read_text(encoding="utf-8")
    assert 'pytest.importorskip(\n    "kg_eval"' in conftest, (
        "evaluation/conftest.py no longer guards on kg_eval; a no-extras job "
        "would error on collection instead of skipping cleanly"
    )


def test_in_ci_accepts_every_truthy_spelling_and_rejects_the_rest() -> None:
    """Behavioural, not textual.

    Replacing the predicate with ``== "true"`` makes ``in_ci("1")`` False and
    fails this test. An earlier version asserted that the module's source
    contained a particular string — and since the assertion itself contained that
    string, it could never fail. A test that quotes its own subject is not a test.
    """
    for spelling in ("true", "TRUE", " True ", "1", "yes", "YES", "on", "ON"):
        assert in_ci(spelling) is True, spelling
    for spelling in ("", "   ", "false", "0", "no", "off", "maybe", None):
        assert in_ci(spelling) is False, spelling


def test_in_ci_drives_the_module_level_flag() -> None:
    assert IN_CI == in_ci(os.environ.get("CI"))


def test_evaluation_package_is_not_imported_by_default_install_paths() -> None:
    """Nothing outside this subpackage may import it.

    ``agentic_kg.migration.evaluation`` imports ``kg_eval`` unguarded, which is
    correct for a tool whose whole purpose is ``kg_eval`` — and fatal if some
    module on the default import path reaches for it. This asserts the blast
    radius stays zero, and it is the check that matters most now that the extra
    really is installed in one CI job: a stray import would break every job that
    does *not* install it.
    """
    src = REPO_ROOT / "packages/core/src/agentic_kg"
    offenders = []
    for path in src.rglob("*.py"):
        if "migration/evaluation" in path.as_posix():
            continue
        text = path.read_text(encoding="utf-8")
        if "migration.evaluation" in text or re.search(r"^\s*import kg_eval", text, re.M):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == [], (
        "these modules reach into the evaluation subpackage (or kg_eval) from the "
        f"default import path, which breaks a no-extras install: {offenders}"
    )
