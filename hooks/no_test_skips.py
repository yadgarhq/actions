#!/usr/bin/env python3
"""No test silently stops running, in two layers that see different facts.

Ledger 837, and the operator's words are the specification: "i need a hard check
in the validation that prevents tests skips. agents usually do this to force a
green test. no test bending is allowed", applied to all repositories.

WHY TWO LAYERS RATHER THAN ONE LIST OF FORBIDDEN MARKERS. A source-marker check
cannot deliver the property on its own. A skip that leaves no marker — an early
return, a filter in a CI invocation, a renamed test directory, a binary that
stopped being built — removes the test just as completely and matches no pattern.
So the marker scan is the cheap layer that runs on every commit, and the
execution audit is the layer that reads what actually ran. Each catches what the
other cannot, and the pull request body that introduced this file says which is
which rather than implying the pair is airtight.

    LAYER 1, the default mode: a source and invocation scan of the whole tree.
    Refuses a marker or an invocation that removes a test from the run.

    LAYER 2, `--audit-cargo FILE`: reads the captured output of a `cargo test`
    run and asserts on the `test result:` lines libtest prints.

    LAYER 2, `--run-pytest ARGS...`: runs pytest, re-emits its output verbatim,
    and asserts on the tally line it prints.

AN AUDIT OVER NOTHING FAILS (ADR-0645). A gate that examined no test binary and
exited 0 is indistinguishable from a green one, and that exact shape produced a
wrong conclusion in the session this file was written in. So Layer 2 refuses when
it finds no summary line at all, and both layers print the number of files,
binaries and summary lines they examined beside every verdict — pass included.

WHAT LAYER 1 DOES *NOT* REFUSE, said here rather than discovered later:

  - `continue-on-error: true` on a step that runs tests. Such a step still RUNS
    the test and still prints its result, so nothing stops running and nothing is
    silent; what stops is the failure blocking the merge. That is a different
    defect, it belongs to ADR-0561/0562, and `estate`'s dated C-18 row is a
    decision about it rather than an accident. Refusing it here would hard-fail a
    known and deliberate configuration, which ADR-0584 forbids.
  - A guard placed at the SECOND statement of a test body or later. See the
    first-statement rule below for why the line is drawn there and what escapes.
  - A positional path or filename argument to pytest. `pytest scripts/tests/ -q`
    is how this repository runs its own suite, and no scan can tell a directory
    that IS the suite from one that is a slice of it.

THE EXEMPTION IS A FACT, NEVER A REASON STRING. This estate has twice established
that a gate must key on the fact and not on a marker or on a consequence of it
(ledger 701, and ledger 729 where keying on `detect.outputs.image == 'false'` beat
keying on a job's `skipped` result). The two legitimate `#[ignore]` sites in the
estate are legitimate BECAUSE SOMETHING RUNS THEM, not because somebody wrote a
justification, so that is what is checked — see `ignored_is_covered`. The
predicate is falsified the moment the thing stops running.

AND IT DOES NOT DRIFT ONTO UNRELATED CODE. `hooks/observe_coverage.py`'s
in-code exemption walk skips blank lines rather than stopping at them, so its
markers reattach to whatever follows once the original target is edited away
(ledger 838). Nothing here reads an in-code marker at all, so there is no marker
to reattach.

THIS FILE IS SCANNED BY ITSELF, and that shapes how the patterns are written.
Layer 1 walks every `.py` file in the tree, this one included, so a pattern
spelled out in full inside its own source would make the gate refuse itself. The
escaped forms below cannot match their own source text — a regex needing a
literal `.` does not match the two characters that write it — and the prose in
this file names the markers without the leading `@` or the trailing `(` for the
same reason. This is a real constraint rather than a curiosity: a gate that
cannot survive its own rule is a gate somebody switches off.

Tests: `python3 -m pytest scripts/tests/test_no_test_skips.py -q`.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# Layer 2 — the summary lines, which are the only thing that says what ran.
# --------------------------------------------------------------------------

# libtest, measured rather than assumed. From `yadgarhq/store` job 102994206633:
#   test result: ok. 6 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 1.10s
CARGO_RESULT = re.compile(
    r"^test result: (?P<verdict>\S+)\. "
    r"(?P<passed>\d+) passed; (?P<failed>\d+) failed; "
    r"(?P<ignored>\d+) ignored; (?P<measured>\d+) measured; "
    r"(?P<filtered>\d+) filtered out"
)

# libtest names each ignored test as it declines to run it, with or without the
# reason from the attribute. The names are what makes the ignored total
# reviewable instead of a bare number.
CARGO_IGNORED_NAME = re.compile(r"^test (?P<name>\S+) \.\.\. ignored")

# pytest's tally, e.g. `===== 41 passed, 1 warning in 0.63s =====`. Read after
# ANSI stripping: pytest colours the tally, and the escape sequence sits
# BETWEEN the number and the word, so an unstripped line reads as no tally at
# all. Measured — that is exactly how the first run of the wrapper failed.
PYTEST_TALLY = re.compile(
    r"\b(?P<n>\d+) (?P<what>passed|failed|skipped|xfailed|xpassed|deselected|errors?)\b"
)

# The tallies that mean a collected test did not produce a real verdict.
# `xfailed` and `xpassed` are here for accuracy rather than by analogy: such a
# test IS collected and IS executed, and what is suppressed is its verdict. That
# still hides a broken test behind a green run, which is the thing being refused.
PYTEST_SUPPRESSED = ("skipped", "xfailed", "xpassed", "deselected")


def audit_cargo(text: str):
    """Findings and counts for one captured `cargo test` run.

    Deliberately NOT asserting `ignored == 0`. Measured on the estate: `store`'s
    own `cargo test --all-features` reports `0 passed; 0 failed; 3 ignored` for
    its `engine_tls` binary in every green run, and those three are executed
    later in the same job against an engine a step stands up. An `ignored` total
    is a question for Layer 1, which can see whether something runs them; this
    layer reports the names so the number is never merely a number.

    `filtered out` and `measured` ARE asserted, and they have no legitimate
    non-zero in an unfiltered run: `filtered out` counts tests a filter removed,
    and `measured` counts benchmark-mode entries that report no pass. The floor
    on executed tests is an AGGREGATE rather than per-line, because a crate with
    no doc-tests still prints a `0 passed` line for them.
    """
    matches = [m for m in (CARGO_RESULT.match(ln) for ln in text.splitlines()) if m]
    ignored_names = [
        m.group("name") for m in (CARGO_IGNORED_NAME.match(ln) for ln in text.splitlines()) if m
    ]
    totals = {k: 0 for k in ("passed", "failed", "ignored", "measured", "filtered")}
    for m in matches:
        for key in totals:
            totals[key] += int(m.group(key))

    problems = []
    if not matches:
        problems.append(
            "no `test result:` line in the captured output, so this audit examined "
            "no test binary at all. A gate that measured nothing must not report "
            "green (ADR-0645)"
        )
    if totals["filtered"]:
        problems.append(
            f"{totals['filtered']} test(s) were FILTERED OUT of this run. An "
            "unfiltered run filters nothing; a filter here is a test that stopped "
            "running with no marker to find"
        )
    if totals["measured"]:
        problems.append(
            f"{totals['measured']} test(s) were reported as `measured` rather than "
            "passed, which is benchmark mode and produces no pass/fail verdict"
        )
    if matches and totals["passed"] == 0:
        problems.append(
            "every binary reported zero passing tests, so nothing here says a test "
            "executed"
        )
    if totals["failed"]:
        problems.append(
            f"{totals['failed']} test(s) FAILED. A `test result:` line with a "
            "non-zero `failed` count is not a green audit"
        )
    return problems, matches, totals, ignored_names


ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def audit_pytest_tally(text: str):
    """Findings and counts for one captured pytest run.

    The tally is read from the LAST line that carries one, because pytest prints
    a per-section tally for the short summary before the final line.
    """
    tallies: dict[str, int] = {}
    for line in ANSI.sub("", text).splitlines():
        found = dict(
            (m.group("what"), int(m.group("n"))) for m in PYTEST_TALLY.finditer(line)
        )
        if found:
            tallies = found

    problems = []
    if not tallies:
        problems.append(
            "pytest printed no tally this audit could read, so nothing here says a "
            "test executed (ADR-0645)"
        )
        return problems, tallies

    for what in PYTEST_SUPPRESSED:
        n = tallies.get(what, 0)
        if n:
            problems.append(f"{n} test(s) reported as `{what}`, which is a test bent green")
    if not tallies.get("passed", 0):
        problems.append("pytest reported zero passing tests, so nothing here says a test ran")
    return problems, tallies


# --------------------------------------------------------------------------
# Layer 1 — walking the tree.
# --------------------------------------------------------------------------

# Directories that hold build output, vendored code or a second checkout, none of
# which is this repository's source. `.ci-actions` is the path `ci-pr.yaml` uses
# for its second checkout of yadgarhq/actions, and scanning it would judge one
# repository's tree by another's contents.
#
# LEDGER 860 ADDED `.claude`, AND IT IS THE SAME CLASS AS `.ci-actions` rather
# than a noise filter. `.claude/worktrees/<name>` is where an agent's git
# worktree lives, so its files are ANOTHER BRANCH's files, and a stale one
# blocked every local commit in `yadgarhq/iam-db` while CI stayed green — CI
# clones afresh and never has the directory. A gate that refuses locally and
# passes remotely is the shape that gets a gate switched off.
#
# THE RESIDUAL, stated rather than discovered later: this is a NAME, so a
# worktree the operator puts anywhere else (`<repo>/wt-foo`) is still walked.
# Only reading the index — `git ls-files` — closes the class, and ledger 845
# holds that question. It is deferred because it would change this gate's
# interface from "any tree" to "a git repository", which both the constructed
# fixtures in `scripts/tests/test_no_test_skips.py` and the `--root <clone>`
# sweep across nineteen repositories depend on.
PRUNE = {
    ".git",
    ".ci-actions",
    ".claude",
    "target",
    "node_modules",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "vendor",
}


def walk(root: Path, suffixes: tuple[str, ...]):
    """Every file under `root` with one of `suffixes`, build output pruned.

    PRUNE IS MATCHED INSIDE THE TREE, NEVER AGAINST THE PATH THAT LEADS TO IT,
    and until ledger 860 it was matched against the whole absolute path. MEASURED
    against the shipped v1.20.0 gate: the same `yadgarhq/store` checkout examined
    18 Rust files at `/tmp/clean/store` and ZERO at `/tmp/target/store`, exiting 0
    in both cases. So a repository living anywhere under a directory named
    `target`, `vendor`, `venv`, `node_modules`, `.venv` or any cache in the set
    had this gate examine nothing and report green — the check-that-cannot-fail
    class (ADR-0645), reached through the operator's choice of directory rather
    than through anything in the tree.

    That is also why `.claude` could not simply be added to the set. This
    estate's agents work in `~/.claude/worktrees/<name>`, so the absolute match
    would have turned the gate into a no-op in exactly the checkouts ledger 860
    is about, trading a false red for a false green.
    """
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in PRUNE for part in path.relative_to(root).parts):
            continue
        if path.suffix in suffixes:
            yield path


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# --------------------------------------------------------------------------
# Rust — `#[ignore]`, and the fact that decides whether it is a skip.
# --------------------------------------------------------------------------

IGNORE_ATTR = re.compile(r"^\s*#\[\s*ignore\b")
TEST_ATTR = re.compile(r"^\s*#\[\s*(tokio::|async_std::|actix_web::|rstest\b|serial_test::)?test\b")
ATTR_LINE = re.compile(r"^\s*#\[")
CFG_ATTR = re.compile(r"^\s*#\[\s*cfg\s*\((?P<pred>.*)\)\s*\]\s*$")
FN_LINE = re.compile(r"^\s*(pub\s+)?(async\s+)?(unsafe\s+)?fn\s+(?P<name>\w+)")
MOD_TESTS = re.compile(r"^\s*(pub\s+)?mod\s+(?P<name>\w*test\w*)\b")

# A `cargo test` invocation, and the tail of the line it sits on. The tail is
# what carries `--ignored`, `--test <target>` and any suppression, so the scan
# reads a command rather than a file name.
CARGO_TEST_CALL = re.compile(r"\bcargo\s+(\+\S+\s+)?(test|nextest\s+run)\b(?P<tail>[^\n]*)")
PYTEST_CALL = re.compile(r"\b(?:python3?\s+-m\s+)?pytest\b(?P<tail>[^\n]*)")

# The reusable workflow whose private-CA step stands an engine up and runs the
# ignored suites. A repository that does not call it gets no exemption from it.
SHARED_WORKFLOW = "yadgarhq/actions/.github/workflows/ci-pr.yaml"
# The one contract between that step and a suite. The step's own predicate is
# "which test SOURCES name this", so keying the exemption on the same name means
# the exemption and the execution cannot come apart: delete the name and the step
# stops selecting the suite AND the exemption evaporates in the same edit.
TLS_CONTRACT = "YADGAR_TEST_TLS_DSN"


def target_of(path: Path) -> str | None:
    """The cargo test target a source file belongs to, or None for a unit test.

    An integration test is a file directly inside a `tests/` directory, and its
    target name is the file stem — `crates/front/tests/smoke.rs` is `smoke`.
    Anything else is compiled into a lib or bin target, which an unfiltered
    `--ignored` run covers without naming.
    """
    if path.parent.name == "tests":
        return path.stem
    return None


def ignored_execution(root: Path):
    """What this repository does to execute ignored tests, read from its workflows.

    Returns (named_targets, unfiltered) — the `--test <name>` targets some
    workflow runs with `--ignored`, and whether any `--ignored` run selects no
    target at all and therefore covers the unit tests too.

    `continue-on-error` is deliberately not consulted: such a step still runs the
    test. See this file's header.
    """
    named: set[str] = set()
    unfiltered = False
    for path in walk(root / ".github" / "workflows", (".yaml", ".yml")):
        for m in CARGO_TEST_CALL.finditer(read(path)):
            tail = m.group("tail")
            if "--ignored" not in tail:
                continue
            selectors = re.findall(r"--test[= ]+\"?\$?\{?(?P<t>[\w.-]+)\}?\"?", tail)
            if selectors:
                named.update(selectors)
            else:
                unfiltered = True
    return named, unfiltered


def ignored_is_covered(path: Path, named: set[str], unfiltered: bool, calls_shared: bool) -> str | None:
    """Why an `#[ignore]` in `path` still executes, or None when nothing runs it.

    Two facts, and neither is a marker anybody writes for this gate's benefit.

    THE TLS CLAUSE BELOW IS GATED ON `target_of(path) is not None` because the
    private-CA step it describes selects targets by `cargo metadata` kind
    `test` — see `ci-pr.yaml`'s `cargo test --all-features --test "$t"`, where
    `$t` comes only from targets whose kind is `test`. A file compiled into a
    lib or bin target can never be selected by `--test <name>`, so naming
    `TLS_CONTRACT` there grants no real coverage. `target_of(path) is not
    None` is exactly the same test this function already uses for the named-
    target branch below, applied here first.

    TWO STATED LIMITS of the TLS clause, left open rather than closed here:
      - `TLS_CONTRACT in read(path)` is a whole-file substring match, not a
        parse of code versus comment. A test gutted to a stub keeps the
        exemption as long as the old comment naming the contract survives.
      - The step that actually executes the named suite lives in
        `yadgarhq/actions`, not in the repository this function walks.
        Deleting that step leaves every consumer's exemption standing with
        nothing running it. This repository's own falsification tests cover
        only a consumer-side rename of the variable, not a deletion of the
        step; that stronger gap is not closed by anything here.
    """
    target = target_of(path)
    if calls_shared and target is not None and TLS_CONTRACT in read(path):
        return (
            f"names {TLS_CONTRACT}, so the private-CA step in {SHARED_WORKFLOW} "
            "selects this suite, runs it with --ignored and refuses unless at "
            "least one test passed with none left ignored"
        )
    if target is not None and target in named:
        return f"a workflow in this repository runs `cargo test --test {target} -- --ignored`"
    if target is None and unfiltered:
        return "a workflow in this repository runs `cargo test -- --ignored` with no target filter"
    return None


# cfg predicates CI actually satisfies, or which `--all-features` /
# `--no-default-features` between them cover. A name outside this set is a cfg
# nothing sets, so a test behind it never runs and never says so.
KNOWN_CFG = {
    "unix",
    "windows",
    "test",
    "doc",
    "doctest",
    "debug_assertions",
    "miri",
    "proc_macro",
    "rustfmt",
    "clippy",
    "panic",
    "target_os",
    "target_arch",
    "target_env",
    "target_family",
    "target_endian",
    "target_pointer_width",
    "target_vendor",
    "target_feature",
    "target_has_atomic",
    "feature",
    # Not in the estate today. Admitted anyway, because each is a cfg a future
    # adopter plausibly sets from RUSTFLAGS or a harness, and refusing one would
    # red a correct repository over a name rather than over a skip.
    "loom",
    "coverage",
    "coverage_nightly",
    "tarpaulin",
    "tarpaulin_include",
    "kani",
    "fuzzing",
}


def declared_features(root: Path) -> set[str]:
    """Every feature name a manifest in this tree declares, plus dependency names.

    Deliberately an OVER-approximation. An optional dependency becomes an
    implicit feature, `dep:` and `pkg/feat` syntax spell dependencies inside
    feature lists, and a workspace can declare features in several manifests.
    Over-approximating costs a missed narrow case; under-approximating reds a
    correct repository, and there are nineteen of those.
    """
    names: set[str] = set()
    for path in walk(root, (".toml",)):
        if path.name != "Cargo.toml":
            continue
        section = None
        for line in read(path).splitlines():
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                section = stripped[1:-1]
                continue
            if section is None or "=" not in stripped or stripped.startswith("#"):
                continue
            key = stripped.split("=", 1)[0].strip().strip('"')
            if section == "features":
                names.add(key)
                for token in re.findall(r'"([^"]+)"', stripped.split("=", 1)[1]):
                    names.add(token.split("/")[0].removeprefix("dep:"))
            elif "dependencies" in section:
                names.add(key)
                names.add(key.replace("-", "_"))
    return names


def unsatisfiable_cfg(pred: str, features: set[str]) -> str | None:
    """Why a cfg predicate can never be true here, or None when it can be.

    Only the shapes that are unsatisfiable BY CONSTRUCTION are refused, and the
    reason is the false-positive budget rather than modesty. `--all-features` and
    `--no-default-features` between them build both states of every declared
    feature, and the estate's only cfg on a test is `#[cfg(unix)]`, so reasoning
    about target triples would add risk over nineteen repositories and catch
    nothing that exists.
    """
    if re.search(r"\bany\s*\(\s*\)", pred):
        return "`any()` is false by definition, so nothing behind it ever runs"
    for feat in re.findall(r'feature\s*=\s*"([^"]+)"', pred):
        if feat not in features:
            return (
                f'no manifest in this tree declares the feature "{feat}", so '
                "`--all-features` cannot turn it on and nothing behind it ever runs"
            )
    for name in re.findall(r"(?<![\w\"])([a-z_][a-z0-9_]*)\s*(?=[,)=]|$)", pred):
        if name in ("all", "any", "not"):
            continue
        if name not in KNOWN_CFG:
            return (
                f"`{name}` is not a cfg this estate's builds set, so a test behind "
                "it never runs and never reports that it did not"
            )
    return None


def rust_items(text: str):
    """Every test item in a Rust source, with the attribute block above it.

    Yields (line_number, kind, name, attributes) where attributes are the
    contiguous `#[...]` lines immediately above the item. Contiguous, and it
    STOPS at a blank line rather than stepping over one — the correction ledger
    838 records against the exemption walk in `observe_coverage.py`.
    """
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        fn = FN_LINE.match(line)
        mod = MOD_TESTS.match(line)
        if not fn and not mod:
            continue
        attrs = []
        back = idx - 1
        while back >= 0:
            above = lines[back]
            if not above.strip():
                break
            if ATTR_LINE.match(above) or above.strip().startswith("]"):
                attrs.append(above)
                back -= 1
                continue
            if above.strip().startswith("//"):
                back -= 1
                continue
            break
        attrs.reverse()
        if fn and not any(TEST_ATTR.match(a) for a in attrs):
            continue
        yield idx + 1, ("fn" if fn else "mod"), (fn or mod).group("name"), attrs


# A guard as the FIRST statement of a test body: an `if` whose block does nothing
# but leave the test. Everything after the first statement is out of scope on
# purpose — `yadgarhq/yadgar`'s two root-detection guards sit six statements in,
# after the setup whose result they test, and they protect a real environmental
# impossibility rather than bending a result.
ENV_PROBE = re.compile(
    r"\benv::var|\bstd::env\b|\bvar_os\b|\bexists\s*\(\s*\)|\bread_to_string\b|"
    r"\bmetadata\s*\(|\bwhich\b|\bCommand::new\b|\bTcpStream::connect\b"
)
GUARD_RETURN = re.compile(r"^\s*return\b|^\s*Ok\s*\(\s*\)\s*$")


def first_statement_guard(text: str, fn_line: int) -> str | None:
    """The bending guard at the top of a test body, or None.

    Refused only when the FIRST statement is an `if` whose block's only effect is
    to leave the function, and whose condition probes the environment. A test that
    does real work first and only then bails is not this shape, and the line is
    drawn where it is so that the estate's two legitimate guards stay legitimate.
    """
    lines = text.splitlines()
    idx = fn_line - 1
    while idx < len(lines) and "{" not in lines[idx]:
        idx += 1
    idx += 1
    while idx < len(lines) and (not lines[idx].strip() or lines[idx].strip().startswith("//")):
        idx += 1
    if idx >= len(lines):
        return None
    head = lines[idx].strip()
    if not head.startswith("if "):
        return None
    if not ENV_PROBE.search(head):
        return None
    body: list[str] = []
    depth = head.count("{") - head.count("}")
    scan = idx + 1
    while scan < len(lines) and depth > 0:
        body.append(lines[scan])
        depth += lines[scan].count("{") - lines[scan].count("}")
        scan += 1
    effective = [
        b.strip()
        for b in body
        if b.strip() and not b.strip().startswith("//") and b.strip() not in ("}", "};")
    ]
    if effective and all(GUARD_RETURN.match(b) for b in effective):
        return (
            "the first statement of this test is an environment probe whose only "
            "effect is to leave the test. A test that returns before it asserts "
            "anything is a skip that reports as a pass"
        )
    return None


# --------------------------------------------------------------------------
# Python — the skip family, written so that this file does not match itself.
# --------------------------------------------------------------------------
#
# Each pattern's own source text cannot satisfy it, because a regex asking for a
# literal `.` does not match the backslash-and-dot that writes one. The prose
# around here names the markers without their leading `@` or trailing `(` for the
# same reason. See this file's header.
PY_SKIPS = [
    (re.compile(r"@\s*pytest\.mark\.skip\b"), "the pytest mark that skips a test outright"),
    (re.compile(r"@\s*pytest\.mark\.skipif\b"), "the conditional pytest skip mark"),
    (re.compile(r"@\s*pytest\.mark\.xfail\b"), "the pytest mark that tolerates a failure"),
    (re.compile(r"\bpytest\.skip\s*\("), "an imperative pytest skip"),
    (re.compile(r"\bpytest\.xfail\s*\("), "an imperative pytest expected-failure"),
    (re.compile(r"\bpytest\.importorskip\s*\("), "a pytest import guard that skips on absence"),
    (re.compile(r"@\s*unittest\.skip"), "a unittest skip decorator"),
    (re.compile(r"@\s*skipIf\b|@\s*skipUnless\b|@\s*skip\s*\("), "a bare unittest skip decorator"),
    (re.compile(r"@\s*unittest\.expectedFailure\b"), "a unittest expected-failure decorator"),
    (re.compile(r"@\s*expectedFailure\b"), "a bare unittest expected-failure decorator"),
    (re.compile(r"\.skipTest\s*\("), "an imperative unittest skip"),
    (re.compile(r"\braise\s+(unittest\.)?SkipTest\b"), "a raised unittest skip"),
    (re.compile(r"^\s*pytestmark\s*="), "a module-wide pytest mark, which can skip a whole file"),
]

# Narrowing that removes tests from a run. Kept to explicit flags: a positional
# path cannot be told apart from the suite itself, and this repository's own hook
# runs `pytest scripts/tests/ -q`.
CARGO_NARROWING = {"--skip", "--exact"}
PYTEST_NARROWING = {"-k", "-m", "--deselect", "--ignore-glob"}
PYTEST_NARROWING_PREFIX = ("--ignore=", "--deselect=", "--ignore-glob=", "-k=", "-m=")

# Suppression of a test command's own exit code. `|| rc=$?` is NOT this: it
# CAPTURES the status, and `ci-pr.yaml`'s private-CA step then checks it. Nor is a
# `|| true` on a neighbouring `grep` or `docker` command — the tail read here
# starts at the test invocation, so only suppression attached to it is seen.
SUPPRESSION = re.compile(r"\|\|\s*(true|:|exit\s+0|echo\b)|;\s*exit\s+0\b")

# Test-runner configuration that narrows collection before any flag is passed.
CONFIG_FILES = ("pytest.ini", "setup.cfg", "tox.ini", "pyproject.toml")
ADDOPTS_NARROWING = re.compile(
    r"addopts\s*=.*(?:\s-k\b|\s-m\b|--deselect|--ignore=|--ignore-glob)"
)
COLLECT_IGNORE = re.compile(r"^\s*collect_ignore(_glob)?\s*=")


def scan_invocations(path: Path, text: str):
    """Findings for every test invocation in a command file."""
    for m in CARGO_TEST_CALL.finditer(text):
        tail = m.group("tail")
        line = text[: m.start()].count("\n") + 1
        tokens = tail.split()
        bad = [t for t in tokens if t.split("=")[0] in CARGO_NARROWING]
        if bad:
            yield (
                f"{path}:{line}: `cargo test` is narrowed by {' '.join(bad)}, which "
                "removes tests from the run"
            )
        if SUPPRESSION.search(tail):
            yield (
                f"{path}:{line}: the exit code of this `cargo test` is suppressed, so "
                "a failing suite reports as a passing one"
            )
    for m in PYTEST_CALL.finditer(text):
        tail = m.group("tail")
        line = text[: m.start()].count("\n") + 1
        tokens = tail.split()
        bad = [
            t
            for t in tokens
            if t in PYTEST_NARROWING or t.startswith(PYTEST_NARROWING_PREFIX)
        ]
        if bad:
            yield (
                f"{path}:{line}: `pytest` is narrowed by {' '.join(bad)}, which removes "
                "tests from the run"
            )
        if SUPPRESSION.search(tail):
            yield (
                f"{path}:{line}: the exit code of this `pytest` is suppressed, so a "
                "failing suite reports as a passing one"
            )


def layer_one(root: Path):
    """Every way this scan can see a test being removed from the run.

    Returns (problems, examined) — the second is what makes a green verdict
    readable, per ADR-0645.
    """
    problems: list[str] = []
    examined = {
        "rust files": 0,
        "test items": 0,
        "ignored attributes": 0,
        "python files": 0,
        "command files": 0,
        "workflows": 0,
    }

    workflow_dir = root / ".github" / "workflows"
    workflows = list(walk(workflow_dir, (".yaml", ".yml")))
    examined["workflows"] = len(workflows)
    calls_shared = any(SHARED_WORKFLOW in read(w) for w in workflows)
    named, unfiltered = ignored_execution(root)
    features = declared_features(root)

    for path in walk(root, (".rs",)):
        text = read(path)
        examined["rust files"] += 1
        rel = path.relative_to(root)
        for line, kind, name, attrs in rust_items(text):
            examined["test items"] += 1
            if any(IGNORE_ATTR.match(a) for a in attrs):
                examined["ignored attributes"] += 1
                why = ignored_is_covered(path, named, unfiltered, calls_shared)
                if why is None:
                    problems.append(
                        f"{rel}:{line}: `{name}` is `#[ignore]` and nothing in this "
                        "repository executes it. An ignored test is a skip unless "
                        "something runs it with `--ignored` and refuses when it "
                        "measured nothing"
                    )
            for attr in attrs:
                cfg = CFG_ATTR.match(attr)
                if not cfg:
                    continue
                why = unsatisfiable_cfg(cfg.group("pred"), features)
                if why:
                    problems.append(f"{rel}:{line}: `{name}` is behind `{attr.strip()}` — {why}")
            if kind == "fn":
                guard = first_statement_guard(text, line)
                if guard:
                    problems.append(f"{rel}:{line}: `{name}` — {guard}")

    for path in walk(root, (".py",)):
        text = read(path)
        examined["python files"] += 1
        rel = path.relative_to(root)
        for idx, line in enumerate(text.splitlines(), start=1):
            for pattern, label in PY_SKIPS:
                if pattern.search(line):
                    problems.append(f"{rel}:{idx}: {label} — `{line.strip()}`")
        if path.name == "conftest.py":
            for idx, line in enumerate(text.splitlines(), start=1):
                if COLLECT_IGNORE.match(line):
                    problems.append(
                        f"{rel}:{idx}: `collect_ignore` removes files from collection "
                        "before any test is named"
                    )
        problems.extend(scan_invocations(rel, text))

    for path in walk(root, (".sh", ".bash", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".mk", "")):
        if path.name == "Makefile" or path.suffix in (".sh", ".bash", ".yaml", ".yml", ".mk"):
            examined["command files"] += 1
            problems.extend(scan_invocations(path.relative_to(root), read(path)))
        if path.name in CONFIG_FILES:
            text = read(path)
            for idx, line in enumerate(text.splitlines(), start=1):
                if ADDOPTS_NARROWING.search(line):
                    problems.append(
                        f"{path.relative_to(root)}:{idx}: `addopts` narrows collection "
                        f"for every pytest run in this repository — `{line.strip()}`"
                    )
    return problems, examined


# --------------------------------------------------------------------------
# Entry points.
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--audit-cargo", metavar="FILE", help="captured `cargo test` output")
    parser.add_argument("--root", default=".", help="the tree Layer 1 scans")
    parser.add_argument(
        "--run-pytest",
        nargs=argparse.REMAINDER,
        help="run pytest with these arguments and audit its tally",
    )
    args = parser.parse_args(argv)

    if args.run_pytest is not None:
        return run_pytest(args.run_pytest)
    if args.audit_cargo:
        return report_cargo(Path(args.audit_cargo))
    return report_layer_one(Path(args.root))


def report_layer_one(root: Path) -> int:
    problems, examined = layer_one(root)
    counted = ", ".join(f"{v} {k}" for k, v in examined.items())
    if problems:
        print("A test would stop running, and nothing would say so:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(f"\nexamined: {counted}", file=sys.stderr)
        return 1
    # A FINDING, never a bare pass. The counts are what separates "clean" from
    # "this walked an empty tree" — the distinction ADR-0645 exists for.
    print(f"no test skip markers or narrowed invocations. examined: {counted}")
    return 0


def report_cargo(path: Path) -> int:
    if not path.is_file():
        print(f"::error::there is no captured test output at {path}", file=sys.stderr)
        return 1
    problems, matches, totals, ignored_names = audit_cargo(read(path))
    counts = (
        f"{len(matches)} `test result:` line(s), {totals['passed']} passed, "
        f"{totals['failed']} failed, {totals['ignored']} ignored, "
        f"{totals['measured']} measured, {totals['filtered']} filtered out"
    )
    if ignored_names:
        counts += f"; ignored: {', '.join(sorted(ignored_names))}"
    if problems:
        for problem in problems:
            print(f"::error::{problem}", file=sys.stderr)
        print(f"examined: {counts}", file=sys.stderr)
        return 1
    print(f"the run executed tests and nothing was filtered out. examined: {counts}")
    return 0


def run_pytest(argv: list[str]) -> int:
    """Run pytest, re-emit every line, then refuse a tally that hides a test.

    The output is re-emitted rather than swallowed, because a wrapper that eats
    the failure report replaces a readable red with an unreadable one.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *argv],
        capture_output=True,
        text=True,
        check=False,
    )
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    problems, tallies = audit_pytest_tally(proc.stdout + proc.stderr)
    counts = ", ".join(f"{v} {k}" for k, v in sorted(tallies.items())) or "no tally"
    if proc.returncode != 0:
        print(f"pytest itself failed (exit {proc.returncode}). examined: {counts}", file=sys.stderr)
        return proc.returncode
    if problems:
        for problem in problems:
            print(f"pytest ran, and a test did not: {problem}", file=sys.stderr)
        print(f"examined: {counts}", file=sys.stderr)
        return 1
    print(f"every collected test produced a real verdict. examined: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
