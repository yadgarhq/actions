#!/usr/bin/env python3
"""LEDGER 1131. A test file shells a throwaway git repository into existence
under `tmp_path`, and that `git` call must not obey this process's OWN git
repository instead of the one it just built.

THE CLASS HAS BITTEN TWICE ALREADY, by the SAME MECHANISM BOTH TIMES.
`pre-commit-scripts` runs `pytest scripts/tests/` as a hook DURING `git
commit`, and `git commit` exports `GIT_DIR` and `GIT_INDEX_FILE` to every
process it spawns. A nested `git init`/`git commit` in a fixture directory
then reads those variables and addresses the OUTER repository being
committed to instead of its own -- `test_next_version.py`'s `_git_env` names
the symptom exactly: "the fixture's commits were being written into the
repository being committed to". `test_service_immutable.py` hit the same
thing first. Both carry the identical fix: strip every `GIT_*` variable
before the nested call.

NOTHING STOPPED A THIRD FILE FROM MISSING IT, which is what this gate is for.
`pre-commit run --all-files` -- how this suite is normally exercised -- sets
neither variable, so a file missing the isolation is GREEN on every ordinary
run and red only on the one path that matters: a real `git commit` invoking
`pytest-scripts`. A gate whose failure mode is invisible on the common path
is exactly ADR-0645's class, restated for environment leakage rather than an
empty scan.

WHAT THIS CHECKS, MECHANICALLY. Every `subprocess.run`/`check_call`/
`check_output`/`call`/`Popen` call whose first positional argument is a
list or tuple literal opening with the string `"git"` -- the shape every
git-shelling test in this estate already uses -- must carry an `env=`
keyword. `env=os.environ`, `env=os.environ.copy()`, `env=dict(os.environ)` and
`env={**os.environ, ...}` (the leak written out loud, or wrapped in just
enough code to look deliberate) are each refused the same way a missing
`env=` is; passing it through any OTHER expression -- a call to a helper, a
dict comprehension that filters `os.environ.items()` -- is accepted. THIS IS
NOT A PROOF the helper actually filters `GIT_*`: it is the same shape of
check `boot_reads_watched.py`
makes about its own markers, a MARKER'S PRESENCE rather than a semantic
verification of what it does, matched against the one convention this
estate's own fixtures already settled on (`_git_env()` in `test_next_version.py`
and `test_service_immutable.py`), because actually interpreting an arbitrary
`env=` expression needs more than a parser.

WHAT THIS DOES NOT SEE. A `git` argv built into a variable before the call
(`argv = ["git", ...]; subprocess.run(argv)`) is invisible to this scan,
which reads the call's own argument list rather than tracing data flow.
Every real `git`-shelling test in this estate today calls `subprocess.run`
with the list written inline, so this is a real gap and not a theoretical
one being pre-empted.

A SECOND, NAMED BLIND SPOT: a test that runs a SCRIPT which itself shells to
`git` -- `subprocess.run([sys.executable, str(GATE), ...])` -- with no `env=`
of its own. The nested `git` call is inside the gate under test, not inside
this test file, so the AST scan here finds no `["git", ...]` literal to flag
at all. That call still needs isolating, for the identical reason: an
inherited `GIT_DIR` reaches the gate's own `git` invocations exactly as it
would a bare one. `test_next_version.py`'s and `test_no_test_skips.py`'s own
`run()` helpers already pass `env=_git_env()` to that outer call for this
reason, but this gate cannot verify it -- the leak would surface one process
boundary away from anything written here.

SCOPED TO TEST FILES, NEVER PRODUCTION HOOK CODE. `scripts/service_immutable.py`
and `scripts/repin.py` also shell out to `git` with no `env=` override, and
that is CORRECT there: those calls read the real checkout the process is
already running in, with no competing fixture repository to be confused
with. Isolation matters only where a test builds a SECOND repository nested
inside the first. A file counts as a test here when its path contains a
`tests` directory component or its name starts with `test_`.

TRACKED FILES ONLY, via `git ls-files` rather than a filesystem walk --
ledger 845's own fix, applied to the gate written immediately after it: a
walk pruned by directory NAME would miss this repository's own stray
worktree at any other name, the exact class that ledger closed.

`language: script`, NOT python, for the reason every other hook in this
manifest already carries: `language: python` makes pre-commit pip-install
this repository, which is not a package. Stdlib only, including `ast` --
this reads PYTHON source, where a real parser is free, unlike the Rust
`boot_reads_watched.py` hand-rolls a scanner for.

Tests: `python3 -m pytest scripts/tests/test_hermetic_git.py -q`.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

SUBPROCESS_CALLS = {"run", "check_call", "check_output", "call", "Popen"}


def _git_env() -> dict[str, str]:
    """This process's own environment, `GIT_*` stripped.

    Used ONLY for this gate's OWN read of the real repository it runs in --
    `tracked_python_files` below -- never for judging a test file's content.
    """
    import os

    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def tracked_python_files() -> list[Path] | None:
    """Every git-tracked `.py` file in this checkout. `None` if not a git repo.

    LEDGER 845's fix, reused: `git ls-files` rather than a filesystem walk, so
    a stray worktree at any name is excluded by what it is rather than by a
    name somebody thought to list.
    """
    env = _git_env()
    probe = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True, env=env,
    )
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        return None
    listed = subprocess.run(
        ["git", "ls-files", "-z"], capture_output=True, text=True, env=env, check=True,
    )
    return [Path(p) for p in listed.stdout.split("\0") if p and p.endswith(".py")]


def is_test_file(path: Path) -> bool:
    """Whether `path` is test code rather than production hook/script code.

    See the module docstring's SCOPED TO TEST FILES section for why
    production code shelling to `git` with no `env=` is correct, not missed.
    """
    return "tests" in path.parts or path.name.startswith("test_")


def is_git_argv(node: ast.AST | None) -> bool:
    """Whether `node` is a list/tuple literal opening with the string `"git"`."""
    if not isinstance(node, (ast.List, ast.Tuple)) or not node.elts:
        return False
    first = node.elts[0]
    return isinstance(first, ast.Constant) and first.value == "git"


def env_keyword(call: ast.Call) -> ast.AST | None:
    for kw in call.keywords:
        if kw.arg == "env":
            return kw.value
    return None


def _is_os_environ(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def is_ambient_environ(node: ast.AST) -> bool:
    """Whether `node` passes `os.environ` through with nothing that could
    filter `GIT_*` out of it -- the leak spelled out loud, or wrapped in just
    enough code to look deliberate.

    CAUGHT: `os.environ`, `os.environ.copy()`, `dict(os.environ)`, and a dict
    literal that unpacks `**os.environ` -- `{**os.environ, "FOO": "bar"}` only
    ADDS a key, it does not filter one out, so it carries the leak too. NOT
    caught: a dict COMPREHENSION over `os.environ.items()` -- `{k: v for k, v
    in os.environ.items() if not k.startswith("GIT_")}`, the `_git_env()`
    convention this gate exists to require -- a `ast.DictComp`, a different
    node entirely from the `ast.Dict` literal checked here.
    """
    if _is_os_environ(node):
        return True
    if isinstance(node, ast.Call):
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "copy"
            and _is_os_environ(func.value)
        ):
            return True
        if (
            isinstance(func, ast.Name)
            and func.id == "dict"
            and len(node.args) == 1
            and _is_os_environ(node.args[0])
        ):
            return True
    if isinstance(node, ast.Dict):
        for key, value in zip(node.keys, node.values):
            if key is None and _is_os_environ(value):
                return True
    return False


def subprocess_call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def violations(path: Path, tree: ast.AST) -> list[str]:
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if subprocess_call_name(node) not in SUBPROCESS_CALLS:
            continue
        if not node.args or not is_git_argv(node.args[0]):
            continue
        env = env_keyword(node)
        if env is not None and not is_ambient_environ(env):
            continue
        found.append(
            f"{path}:{node.lineno}: a subprocess `git` call carries "
            f"{'an unfiltered copy of the ambient environment' if env is not None else 'no `env=`'}"
            f", so this process's own GIT_* leaks into it. A nested `git init`/`git "
            f"commit` under `tmp_path` then addresses the OUTER repository "
            f"this gate itself runs in rather than the fixture it just "
            f"built -- ledger 1131, and it has cost two files already. Pass "
            f"`env=` built from a `GIT_*`-stripped copy of `os.environ` (the "
            f"`_git_env()` convention in `test_next_version.py` and "
            f"`test_service_immutable.py`)."
        )
    return found


def main() -> int:
    files = tracked_python_files()
    if files is None:
        print(
            "hermetic-git gate: this checkout is not a git repository, so "
            "there is no tracked-file list to read. This is a failure and "
            "not a pass: a gate that examined nothing proves nothing.",
            file=sys.stderr,
        )
        return 1

    problems: list[str] = []
    examined = 0
    for path in files:
        if not is_test_file(path):
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as error:
            problems.append(f"{path}: could not parse as Python ({error})")
            continue
        examined += 1
        problems.extend(violations(path, tree))

    if problems:
        print(
            "A test file shells out to `git` with no isolated `env=`:",
            file=sys.stderr,
        )
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(f"\nexamined: {examined} test file(s)", file=sys.stderr)
        return 1
    print(
        f"hermetic-git gate: {examined} test file(s) examined, every "
        f"subprocess `git` call isolates its environment."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
