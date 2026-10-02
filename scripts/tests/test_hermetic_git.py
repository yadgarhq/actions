"""LEDGER 1131. The gate that stops a THIRD test file from shelling out to
real `git` with no isolated `env=` -- the class that already cost
`test_next_version.py` and `test_service_immutable.py` a red suite each,
both for the identical reason: `git commit` exports `GIT_DIR` and
`GIT_INDEX_FILE` to everything it spawns, so a nested `git init` under
`tmp_path` obeys the OUTER repository this very suite is run FROM, during a
real commit, instead of the fixture it just built.

TWO LAYERS, the same split `hooks/no_test_skips.py` uses and for the same
reason: the AST-level functions prove the DETECTION is right on inputs no
real git repository is needed to construct, and the subprocess layer proves
the real CLI -- `git ls-files` scoping included -- agrees.

THIS FILE'S OWN `git()` HELPER DOGFOODS THE GATE IT TESTS: it carries
`env=_git_env()`, so this suite is itself one of the files
`hermetic_git.py` would refuse the moment that line is dropped.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

GATE = Path(__file__).resolve().parents[2] / "hooks" / "hermetic_git.py"

_spec = importlib.util.spec_from_file_location("hermetic_git", GATE)
hermetic_git = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hermetic_git)


# ---------------------------------------------------------------------------
# Layer 1 — the AST-level detection, no git repository needed.
# ---------------------------------------------------------------------------


def _violations(source: str, path: str = "tests/test_fixture.py") -> list[str]:
    tree = ast.parse(source, filename=path)
    return hermetic_git.violations(Path(path), tree)


def test_a_bare_subprocess_git_call_with_no_env_is_refused():
    found = _violations(
        "import subprocess\n"
        "def git(cwd, *a):\n"
        "    subprocess.run(['git', *a], cwd=cwd, check=True)\n"
    )
    assert len(found) == 1
    assert "test_fixture.py:3" in found[0]
    assert "no `env=`" in found[0]


def test_env_isolated_via_a_named_helper_is_accepted():
    """THE CONTROL. Without this the refusal above would prove nothing — a
    gate that reddens every subprocess `git` call, isolated or not, is not
    testing isolation at all."""
    found = _violations(
        "import subprocess\n"
        "def git(cwd, *a):\n"
        "    subprocess.run(['git', *a], cwd=cwd, env=_git_env())\n"
    )
    assert found == []


def test_env_isolated_via_a_dict_literal_is_accepted():
    found = _violations(
        "import subprocess, os\n"
        "def git(cwd, *a):\n"
        "    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}\n"
        "    subprocess.run(['git', *a], cwd=cwd, env=env)\n"
    )
    assert found == []


def test_env_os_environ_spelled_out_is_refused_the_same_as_missing():
    """THE LEAK WRITTEN OUT LOUD. `env=os.environ` passes the ambient
    environment through exactly as completely as no `env=` at all — the
    THIRD shape this gate must not treat as isolation."""
    found = _violations(
        "import subprocess, os\n"
        "def git(cwd, *a):\n"
        "    subprocess.run(['git', *a], cwd=cwd, env=os.environ)\n"
    )
    assert len(found) == 1
    assert "ambient environment" in found[0]


def test_env_os_environ_copy_is_refused_the_same_as_the_bare_attribute():
    """`.copy()` makes a new dict, but every `GIT_*` key is still in it."""
    found = _violations(
        "import subprocess, os\n"
        "def git(cwd, *a):\n"
        "    subprocess.run(['git', *a], cwd=cwd, env=os.environ.copy())\n"
    )
    assert len(found) == 1


def test_env_dict_of_os_environ_is_refused_the_same_as_the_bare_attribute():
    found = _violations(
        "import subprocess, os\n"
        "def git(cwd, *a):\n"
        "    subprocess.run(['git', *a], cwd=cwd, env=dict(os.environ))\n"
    )
    assert len(found) == 1


def test_env_unpacking_os_environ_in_a_dict_literal_is_refused():
    """`{**os.environ, 'FOO': 'bar'}` only ADDS a key; it filters nothing
    out, so every `GIT_*` key rides along unchanged."""
    found = _violations(
        "import subprocess, os\n"
        "def git(cwd, *a):\n"
        "    subprocess.run(['git', *a], cwd=cwd, env={**os.environ, 'FOO': 'bar'})\n"
    )
    assert len(found) == 1


def test_env_comprehension_over_os_environ_items_is_still_accepted():
    """THE CONTROL for the three refusals above: a dict COMPREHENSION that
    filters `os.environ.items()` — the real `_git_env()` shape — is a
    different AST node (`DictComp`, not `Dict`/`Call`) and must stay green."""
    found = _violations(
        "import subprocess, os\n"
        "def git(cwd, *a):\n"
        "    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}\n"
        "    subprocess.run(['git', *a], cwd=cwd, env=env)\n"
    )
    assert found == []


def test_env_none_is_refused_the_same_as_missing():
    """`env=None` means "inherit the ambient environment" to `subprocess`
    itself -- the identical leak to omitting `env=` altogether, spelled a
    different way."""
    found = _violations(
        "import subprocess\n"
        "def git(cwd, *a):\n"
        "    subprocess.run(['git', *a], cwd=cwd, env=None)\n"
    )
    assert len(found) == 1


def test_git_argv_passed_as_the_args_keyword_is_still_detected():
    """`subprocess.run(args=[...])` is the keyword form of the same call --
    the argv is not `node.args[0]` here, it is the `args=` keyword."""
    found = _violations(
        "import subprocess\n"
        "def git(cwd, *a):\n"
        "    subprocess.run(args=['git', *a], cwd=cwd)\n"
    )
    assert len(found) == 1


def test_env_os_environ_merged_with_bitor_is_refused():
    """`os.environ | {...}` (PEP 584) merges the ambient environment into a
    new dict and ADDS keys -- it filters nothing out, the same leak as the
    dict-unpack shape, through the `|` operator instead of `**`."""
    found = _violations(
        "import subprocess, os\n"
        "def git(cwd, *a):\n"
        "    subprocess.run(['git', *a], cwd=cwd, env=os.environ | {'FOO': 'bar'})\n"
    )
    assert len(found) == 1


def test_an_aliased_from_import_is_still_a_subprocess_call():
    """LEDGER 1241. `subprocess_bindings()` already maps `check_output as co`
    to the LOCAL name `co` in `imported_call_names` -- but `subprocess_call_name`
    then returns that same local alias `co`, which `violations()` checks
    against `SUBPROCESS_CALLS` (`{"run", "check_call", "check_output", ...}`).
    `"co"` is not in that set, so an aliased import slips past unjudged even
    though it is exactly the `check_output` call this gate exists to catch.
    """
    found = _violations(
        "from subprocess import check_output as co\n"
        "def git(cwd, *a):\n"
        "    co(['git', *a], cwd=cwd)\n"
    )
    assert len(found) == 1
    assert "test_fixture.py:3" in found[0]
    assert "no `env=`" in found[0]


def test_a_chained_bitor_merge_is_still_refused():
    """LEDGER 1241. `os.environ | {"A": "1"} | {"B": "2"}` parses as
    `BinOp(BinOp(os.environ, |, {"A": "1"}), |, {"B": "2"})` -- `os.environ`
    is the LEFT child of the INNER BinOp, never a direct operand of the outer
    one. `is_ambient_environ`'s BitOr branch checked only
    `_is_os_environ(node.left) or _is_os_environ(node.right)` at the top
    level, so a second `|` in the chain hid the leak from a check that never
    recursed into its own operands.
    """
    found = _violations(
        "import subprocess, os\n"
        "def git(cwd, *a):\n"
        "    subprocess.run(['git', *a], cwd=cwd, "
        "env=os.environ | {'A': '1'} | {'B': '2'})\n"
    )
    assert len(found) == 1
    assert "ambient environment" in found[0]


def test_a_local_function_named_run_is_not_treated_as_subprocess():
    """THE FALSE-POSITIVE CONTROL. A local function or test fixture helper
    that happens to be named `run`/`call`/`Popen` and takes a `["git", ...]`
    argument for reasons that have nothing to do with `subprocess` must not
    be refused -- only `subprocess.<name>(...)`, or a name actually imported
    `from subprocess import <name>`, is a subprocess call.
    """
    found = _violations(
        "def run(argv):\n"
        "    return argv\n"
        "x = run(['git', 'init'])\n"
    )
    assert found == []


def test_check_call_check_output_and_popen_are_all_covered():
    for call in ("check_call", "check_output", "call", "Popen"):
        found = _violations(
            f"import subprocess\n"
            f"def git(cwd, *a):\n"
            f"    subprocess.{call}(['git', *a], cwd=cwd)\n"
        )
        assert len(found) == 1, call


def test_a_non_git_subprocess_call_is_not_judged():
    """THE SCAN IS FOR `git` SPECIFICALLY, never every subprocess call —
    a test shelling out to `helm` or `cargo` with the ambient environment
    has no nested repository to be confused about."""
    found = _violations(
        "import subprocess\n"
        "def helm(*a):\n"
        "    subprocess.run(['helm', *a])\n"
    )
    assert found == []


def test_an_argv_built_in_a_variable_is_the_named_blind_spot():
    """WHAT THIS GATE DOES NOT SEE, pinned rather than merely claimed. Every
    real git-shelling test in this estate writes the list inline; the day
    one does not, this is the case that would need widening."""
    found = _violations(
        "import subprocess\n"
        "def git(cwd, *a):\n"
        "    argv = ['git', *a]\n"
        "    subprocess.run(argv, cwd=cwd)\n"
    )
    assert found == []


def test_production_code_shelling_to_git_is_not_in_scope():
    """`scripts/service_immutable.py` and `scripts/repin.py` shell to `git`
    with no `env=` override and that is CORRECT: they read the real
    checkout the process already runs in, with no competing fixture
    repository. `is_test_file` is what draws that line."""
    assert not hermetic_git.is_test_file(Path("scripts/service_immutable.py"))
    assert not hermetic_git.is_test_file(Path("scripts/repin.py"))
    assert hermetic_git.is_test_file(Path("scripts/tests/test_repin.py"))
    assert hermetic_git.is_test_file(Path("scripts/tests/fixtures/helper.py"))


# ---------------------------------------------------------------------------
# Layer 2 — the real CLI, a real repository, `git ls-files` scoping included.
# ---------------------------------------------------------------------------


def _git_env() -> dict[str, str]:
    """The environment a fixture repository is built in, with the caller's
    own `GIT_*` stripped.

    HERMETIC BECAUSE IT HAD TO BE — this is the file whose own subject is the
    gate that would refuse this helper's absence. `test_next_version.py` and
    `test_service_immutable.py` each hit the leak this guards against before
    this gate existed to name it.
    """
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=cwd, check=True, capture_output=True, text=True, env=_git_env(),
    )


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    return root


def _run(root: Path):
    return subprocess.run(
        [sys.executable, str(GATE)],
        cwd=root, capture_output=True, text=True, check=False, env=_git_env(),
    )


BAD = (
    "import subprocess\n"
    "def git(cwd, *a):\n"
    "    subprocess.run(['git', *a], cwd=cwd)\n"
)
GOOD = (
    "import subprocess, os\n"
    "def _git_env():\n"
    "    return {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}\n"
    "def git(cwd, *a):\n"
    "    subprocess.run(['git', *a], cwd=cwd, env=_git_env())\n"
)


def test_a_tracked_violating_test_file_is_refused(tmp_path):
    root = _repo(tmp_path, {"scripts/tests/test_x.py": BAD})
    result = _run(root)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "scripts/tests/test_x.py" in result.stderr
    assert "no `env=`" in result.stderr


def test_a_tracked_isolated_test_file_passes(tmp_path):
    root = _repo(tmp_path, {"scripts/tests/test_x.py": GOOD})
    result = _run(root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 test file(s) examined" in result.stdout


def test_an_untracked_violating_file_is_not_walked(tmp_path):
    """LEDGER 845's lesson, reused: `git ls-files` scoping means a file nobody
    `git add`-ed — a stray worktree's, a scratch file, build output — is not
    this repository's source regardless of its content."""
    root = _repo(tmp_path, {"scripts/tests/test_clean.py": GOOD})
    stray = root / "scripts" / "tests" / "test_stray.py"
    stray.write_text(BAD)  # deliberately not `git add`-ed
    result = _run(root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 test file(s) examined" in result.stdout


def test_a_violation_outside_tests_is_not_flagged(tmp_path):
    """Production code shelling to `git` with the ambient environment is the
    correct shape — see `test_production_code_shelling_to_git_is_not_in_scope`
    for why — and this is the same fact proved end-to-end."""
    root = _repo(tmp_path, {"scripts/service_immutable.py": BAD})
    result = _run(root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 test file(s) examined" in result.stdout


def test_a_non_git_tree_is_refused(tmp_path):
    root = tmp_path / "plain"
    root.mkdir()
    (root / "scripts").mkdir()
    (root / "scripts" / "tests").mkdir()
    (root / "scripts" / "tests" / "test_x.py").write_text(GOOD)
    result = _run(root)
    assert result.returncode == 1
    assert "not a git repository" in result.stderr


def test_this_repositorys_own_tests_pass_the_gate_they_dogfood():
    """RUNNING IT HERE IS PROVING IT. This repository's `scripts/tests/`
    already carries two git-shelling files (`test_next_version.py`,
    `test_service_immutable.py`) that established the `_git_env()`
    convention this gate checks for, plus this file's own `_git()` helper.
    """
    result = subprocess.run(
        [sys.executable, str(GATE)],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True, text=True, check=False, env=_git_env(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
