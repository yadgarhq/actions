"""LEDGER 648. The ADR-0569 gate's source-root walk, which this car widened.

The gate moved from five byte-identical per-repository copies into this
repository's published manifest, and the move changed one thing: the walk now
covers `crates/*/src` as well as root-level `src/`. That change is the reason
this file exists. Against a workspace layout the old walk found no root and
exited 1 reading "a gate that searches nothing proves nothing" — a refusal
naming a real rule while checking no file, which under ADR-0577 made
`yadgarhq/estate` a permanent non-adopter of a gate it needs.

WHAT IS ASSERTED IS THE REFUSAL, not only the pass. A gate is worth testing for
the inputs it must reject; feeding it conforming trees only would prove it
returns 0, which a `true` also does. The same reasoning `yadgarhq/config`'s
suite records above its own gate.

The gate is run as a SUBPROCESS against a temporary tree rather than imported,
because it is shell and its subject is the working directory it is invoked in —
which is also how pre-commit invokes it in a consumer.
"""

import subprocess
from pathlib import Path

GATE = Path(__file__).resolve().parents[2] / "hooks" / "no_compiled_in_defaults.sh"

# A file with no compiled-in default. Kept minimal on purpose: this suite is
# about which files the gate FINDS, not about which patterns it forbids.
CLEAN = 'fn main() { let _ = env_required("YADGAR_ADDR"); }\n'

# The inline fallback form, the third thing the gate forbids.
DIRTY = 'fn main() { let a = std::env::var("K").unwrap_or_else(|_| "d".into()); }\n'


def run(tree: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(GATE)], cwd=tree, capture_output=True, text=True
    )


def write(tree: Path, relative: str, body: str) -> None:
    path = tree / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def test_single_crate_layout_is_unchanged(tmp_path):
    """The five repositories that carried a copy are all root-level `src/`."""
    write(tmp_path, "src/main.rs", CLEAN)
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "searching 1 Rust file(s)" in result.stdout


def test_workspace_layout_is_searched(tmp_path):
    """The widening. Without it this tree is the 'no source root' refusal."""
    write(tmp_path, "crates/front/src/lib.rs", CLEAN)
    write(tmp_path, "crates/harness/src/lib.rs", CLEAN)
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "searching 2 Rust file(s)" in result.stdout
    assert "crates/front/src" in result.stdout


def test_workspace_violation_is_refused(tmp_path):
    """Finding the files is worthless unless the verdict still lands on them."""
    write(tmp_path, "crates/front/src/lib.rs", DIRTY)
    result = run(tmp_path)
    assert result.returncode == 1
    assert "ADR-0569 VIOLATION" in result.stderr
    assert "crates/front/src/lib.rs" in result.stderr


def test_no_rust_source_root_still_refuses(tmp_path):
    """The widened walk must not become a walk that accepts anything.

    A tree with neither layout is still a gate that searched nothing, and the
    refusal that says so is the property most easily lost when a glob is added.
    """
    write(tmp_path, "README.md", "no rust here\n")
    result = run(tmp_path)
    assert result.returncode == 1
    assert "NO RUST SOURCE ROOT FOUND" in result.stderr


def test_source_root_present_but_empty_still_refuses(tmp_path):
    """A directory-exists check is not a non-empty-glob check."""
    (tmp_path / "crates" / "front" / "src").mkdir(parents=True)
    result = run(tmp_path)
    assert result.returncode == 1
    assert "MATCHED 0 RUST FILES" in result.stderr
