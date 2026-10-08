"""LEDGER 726. The ADR-0523 gate: a boot read is watched, or declared unwatched.

WHAT IS ASSERTED IS THE REFUSAL, not only the pass. A gate fed conforming trees
alone proves it returns 0, which `true` also does — the rule
`test_no_compiled_in_defaults.py` records above its own subject, and the reason
this file spends most of its length on inputs the gate must reject.

THREE OF THESE CASES ARE REGRESSIONS OF DEFECTS THE FIRST DRAFT SHIPPED, each
found by running the gate against the six real repositories rather than by
reading it:

  * `yadgar-lifecycle` exports its OWN `rotate::File`, whose constructors are
    `File::read` and `File::certificate`, and every service's `src/rotate.rs`
    calls them. A `File::NAME` classifier that refused unknown names reddened all
    six repositories over a name collision with nothing wrong.
  * `re.match(pattern, text[i:])` copies the tail of the file on every character.
    Against `yadgarhq/gateway` the scan did not finish inside two minutes.
  * Braces inside format strings move a `#[cfg(test)]` module's end. Every one of
    these repositories logs with `tracing::info!("{}", x)`.

The gate is run as a SUBPROCESS against a temporary tree, because its subject is
the working directory it is invoked in — which is how pre-commit invokes it in a
consumer. The blanking pass is exercised directly, because its failures are
silent: it does not raise, it moves a line.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

GATE = Path(__file__).resolve().parents[2] / "hooks" / "boot_reads_watched.py"

_spec = importlib.util.spec_from_file_location("boot_reads_watched", GATE)
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

# A watch set with two materials to name, written the way the real ones are.
ROTATE = """\
use crate::serve::ServerTls;

impl Material for ServerTls {
    fn files(&self) -> Vec<File<'_>> {
        vec![
            File::certificate(Presented::Serving, self.cert_file()),
            File::read(self.key_file()),
        ]
    }
}

pub fn watch_set(listener: Option<&ServerTls>, config: &Configuration) -> Inputs {
    Inputs::of(SERVICE, &[&listener, config])
}
"""

REASON = "ruling reserved, ledger 730 — see the module documentation"


def write(tree: Path, relative: str, body: str) -> None:
    path = tree / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def run(tree: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE)], cwd=tree, capture_output=True, text=True
    )


def tree_with(tmp_path: Path, body: str, rotate: str = ROTATE) -> Path:
    write(tmp_path, "src/rotate.rs", rotate)
    write(tmp_path, "src/serve.rs", body)
    return tmp_path


# --------------------------------------------------------------------------
# The pass, and the two markers that produce it.
# --------------------------------------------------------------------------


def test_a_watched_read_naming_a_real_material_passes(tmp_path):
    run_result = run(
        tree_with(
            tmp_path,
            "fn load(p: &Path) {\n"
            "    // ADR-0523-WATCHED: ServerTls\n"
            "    let _ = std::fs::read(p);\n"
            "}\n",
        )
    )
    assert run_result.returncode == 0, run_result.stderr
    assert "1 filesystem read(s) judged" in run_result.stdout


def test_a_declared_exclusion_passes_and_is_the_iam_case(tmp_path):
    """`iam` reads two crypto keys at boot that the watch set does not carry.

    A gate with no exclusion is disabled the first time it fires on a file
    somebody deliberately does not watch, so this arm is load-bearing rather
    than a convenience.
    """
    run_result = run(
        tree_with(
            tmp_path,
            "fn read_key(p: &str) {\n"
            f"    let _ = std::fs::read(p); // ADR-0523-UNWATCHED: {REASON}\n"
            "}\n",
        )
    )
    assert run_result.returncode == 0, run_result.stderr


def test_a_marker_may_sit_on_the_reads_own_line(tmp_path):
    run_result = run(
        tree_with(
            tmp_path,
            "fn load(p: &Path) {\n"
            "    let _ = std::fs::read(p); // ADR-0523-WATCHED: ServerTls\n"
            "}\n",
        )
    )
    assert run_result.returncode == 0, run_result.stderr


# --------------------------------------------------------------------------
# The refusals. This is the half that makes the gate a gate.
# --------------------------------------------------------------------------


def test_an_unmarked_read_is_refused_and_named(tmp_path):
    """THE WHOLE POINT: adding a boot read and forgetting the watch set is RED.

    A detector whose forgetting-case is green is the defect, not the fix.
    """
    run_result = run(tree_with(tmp_path, "fn load(p: &Path) {\n    std::fs::read(p);\n}\n"))
    assert run_result.returncode == 1
    assert "src/serve.rs:2" in run_result.stderr
    assert "no ADR-0523 marker" in run_result.stderr


def test_a_watched_marker_naming_nothing_in_rotate_is_refused(tmp_path):
    """SIDE B LIVES IN src/rotate.rs, so a marker cannot satisfy itself.

    Without this the two sides of the comparison are both the read site's own
    file, which is the certifying-fixture class (ADR-0599).
    """
    run_result = run(
        tree_with(
            tmp_path,
            "fn load(p: &Path) {\n"
            "    // ADR-0523-WATCHED: Invented\n"
            "    std::fs::read(p);\n"
            "}\n",
        )
    )
    assert run_result.returncode == 1
    assert "`Invented`" in run_result.stderr


def test_a_token_reason_is_refused(tmp_path):
    run_result = run(
        tree_with(
            tmp_path,
            "fn load(p: &Path) {\n"
            "    // ADR-0523-UNWATCHED: todo\n"
            "    std::fs::read(p);\n"
            "}\n",
        )
    )
    assert run_result.returncode == 1
    assert "character reason" in run_result.stderr


def test_one_marker_cannot_cover_two_reads(tmp_path):
    """A cert and its key are two reads, and each needs its own decision."""
    run_result = run(
        tree_with(
            tmp_path,
            "fn identity(c: &Path, k: &Path) {\n"
            "    // ADR-0523-WATCHED: ServerTls\n"
            "    let _ = std::fs::read(c);\n"
            "    let _ = std::fs::read(k);\n"
            "}\n",
        )
    )
    assert run_result.returncode == 1
    assert "src/serve.rs:4" in run_result.stderr
    assert "src/serve.rs:3" not in run_result.stderr


def test_a_marker_further_than_the_lookback_does_not_reach(tmp_path):
    body = (
        "// ADR-0523-WATCHED: ServerTls\n"
        + "".join(f"// filler {i}\n" for i in range(gate.LOOKBACK + 2))
        + "fn load(p: &Path) { std::fs::read(p); }\n"
    )
    run_result = run(tree_with(tmp_path, body))
    assert run_result.returncode == 1
    assert "no ADR-0523 marker" in run_result.stderr


def test_an_unclassified_fs_call_refuses_the_run(tmp_path):
    """FAIL CLOSED ON A SHAPE IT CANNOT READ.

    A repository that starts reading through a call this scan does not know
    would otherwise pass having inspected everything except the thing that
    changed.
    """
    run_result = run(
        tree_with(
            tmp_path,
            "fn load(p: &Path) {\n"
            "    // ADR-0523-WATCHED: ServerTls\n"
            "    let _ = std::fs::read(p);\n"
            "    let _ = std::fs::slurp_everything(p);\n"
            "}\n",
        )
    )
    assert run_result.returncode == 1
    assert "UNCLASSIFIED FILESYSTEM ACCESS" in run_result.stderr
    assert "fs::slurp_everything" in run_result.stderr


def test_importing_the_read_function_is_refused(tmp_path):
    """`use std::fs::read;` would let `read(p)` do the work with no `fs::`.

    A bare `read(` is far too common a name to match on, so the import is what
    gets refused.
    """
    run_result = run(
        tree_with(
            tmp_path,
            "use std::fs::{read, write};\n"
            "fn load(p: &Path) {\n"
            "    // ADR-0523-WATCHED: ServerTls\n"
            "    let _ = read(p);\n"
            "}\n",
        )
    )
    assert run_result.returncode == 1
    assert "import the module, not the function" in run_result.stderr


# --------------------------------------------------------------------------
# The three floors. A gate that searched nothing proves nothing.
# --------------------------------------------------------------------------


def test_no_source_root_is_a_failure_not_a_pass(tmp_path):
    assert run(tmp_path).returncode == 1
    assert "NO RUST SOURCE ROOT" in run(tmp_path).stderr


def test_a_source_root_holding_no_rust_file_is_a_failure(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "README.md").write_text("no rust here\n")
    run_result = run(tmp_path)
    assert run_result.returncode == 1
    assert "MATCHED 0 NON-TEST RUST FILES" in run_result.stderr


def test_a_repository_without_a_watch_set_file_is_refused(tmp_path):
    write(tmp_path, "src/serve.rs", "fn main() {}\n")
    run_result = run(tmp_path)
    assert run_result.returncode == 1
    assert "NO `.rs` FILE UNDER" in run_result.stderr


def test_a_rotate_directory_holding_only_test_named_files_is_not_reported_as_absent(
    tmp_path,
):
    """A REVIEW FINDING. `src/rotate/` EXISTS here -- it just holds nothing
    `rotate_files()` accepts, because its only file is excluded as test code.
    `NEITHER ... EXISTS` would be a false claim about the tree; the refusal
    must say there is no ACCEPTED `.rs` file there, not that the directory is
    absent.
    """
    tmp = tmp_path / "repo"
    write(tmp, "src/rotate/tests.rs", "fn f() {}\n")
    write(tmp, "src/serve.rs", "fn main() {}\n")
    run_result = run(tmp)
    assert run_result.returncode == 1
    assert "NEITHER" not in run_result.stderr
    assert "NO `.rs` FILE UNDER" in run_result.stderr


def tree_with_split_rotate(tmp_path: Path, body: str) -> Path:
    """LEDGER 813, LEDGER 719: `yadgar-lifecycle` split a 1536-line
    `src/rotate.rs` into `src/rotate/{mod,inputs,schedule,schedule_error}.rs`
    to clear this estate's 500-line file ceiling. A consumer's own
    `src/rotate.rs` crosses that ceiling the same way eventually, so this
    fixture puts the `impl Material` in one sibling and `fn watch_set` in
    another -- matching lifecycle's real split, where `Configuration`'s impl
    lives in `schedule.rs` rather than `mod.rs`.
    """
    write(
        tmp_path,
        "src/rotate/mod.rs",
        "mod schedule;\n\n"
        "use crate::serve::ServerTls;\n\n"
        "impl Material for ServerTls {\n"
        "    fn files(&self) -> Vec<File<'_>> {\n"
        "        vec![\n"
        "            File::certificate(Presented::Serving, self.cert_file()),\n"
        "            File::read(self.key_file()),\n"
        "        ]\n"
        "    }\n"
        "}\n",
    )
    write(
        tmp_path,
        "src/rotate/schedule.rs",
        "pub fn watch_set(listener: Option<&ServerTls>, config: &Configuration) -> Inputs {\n"
        "    Inputs::of(SERVICE, &[&listener, config])\n"
        "}\n",
    )
    write(tmp_path, "src/serve.rs", body)
    return tmp_path


def test_a_split_rotate_directory_is_accepted(tmp_path):
    """THE WHOLE POINT OF 813: the directory form must not hard-fail.

    Before the fix, `ROTATE.is_file()` is False for a directory and the gate
    refuses with "DOES NOT EXIST" even though the watch set is fully declared.
    """
    run_result = run(
        tree_with_split_rotate(
            tmp_path,
            "fn load(p: &Path) {\n"
            "    // ADR-0523-WATCHED: ServerTls\n"
            "    let _ = std::fs::read(p);\n"
            "}\n",
        )
    )
    assert run_result.returncode == 0, run_result.stderr
    assert "1 filesystem read(s) judged" in run_result.stdout


def test_a_split_rotate_directory_still_refuses_an_unmarked_read(tmp_path):
    """The directory form is accepted, not exempted -- it still judges reads."""
    run_result = run(
        tree_with_split_rotate(tmp_path, "fn load(p: &Path) {\n    std::fs::read(p);\n}\n")
    )
    assert run_result.returncode == 1
    assert "no ADR-0523 marker" in run_result.stderr


def test_a_flat_rotate_file_and_a_sibling_directory_are_both_read(tmp_path):
    """REAL RUST 2018, NOT AN ODD TREE, AND NOT `tree_with_split_rotate`'s OWN
    FIXTURE. That helper writes `src/rotate/mod.rs`, and a `src/rotate.rs`
    ALONGSIDE a `src/rotate/mod.rs` is two files claiming the same module --
    E0761, a compile error -- so this builds its own tree instead.

    `src/rotate.rs` declaring `mod schedule;` with no `src/rotate/mod.rs` at
    all is valid, idiomatic Rust -- `schedule` then lives at
    `src/rotate/schedule.rs`, a PARTIAL split that leaves the flat file in
    place holding only the `mod` declaration. A marker naming a material
    declared only in that sibling must be accepted, not refused as unknown:
    an earlier revision treated the flat file and the directory as mutually
    exclusive and picked the flat file alone, which refuses exactly this
    tree. THE FLAT FILE MUST NOT ALSO CONTAIN `fn watch_set` OR `impl
    Material` of its own -- if it did, the either-or revision would read
    Side B from the flat file alone and pass by accident, proving nothing
    about the sibling ever being read.
    """
    tmp = tmp_path / "repo"
    write(tmp, "src/rotate.rs", "mod schedule;\n")
    write(
        tmp,
        "src/rotate/schedule.rs",
        "use crate::serve::ServerTls;\n\n"
        "impl Material for ServerTls {\n"
        "    fn files(&self) -> Vec<File<'_>> {\n"
        "        vec![\n"
        "            File::certificate(Presented::Serving, self.cert_file()),\n"
        "            File::read(self.key_file()),\n"
        "        ]\n"
        "    }\n"
        "}\n\n"
        "pub fn watch_set(listener: Option<&ServerTls>, config: &Configuration) -> Inputs {\n"
        "    Inputs::of(SERVICE, &[&listener, config])\n"
        "}\n",
    )
    write(
        tmp,
        "src/serve.rs",
        "fn load(p: &Path) {\n"
        "    // ADR-0523-WATCHED: ServerTls\n"
        "    let _ = std::fs::read(p);\n"
        "}\n",
    )
    run_result = run(tmp)
    assert run_result.returncode == 0, run_result.stderr
    assert "1 filesystem read(s) judged" in run_result.stdout


def test_a_fake_material_in_src_rotate_tests_rs_does_not_validate_a_marker(tmp_path):
    """A REVIEW FINDING. `rotate_files()`'s `rglob("*.rs")` read `src/rotate/
    tests.rs` the same as any sibling, so a `FakeMaterial` planted there --
    test fixture code, never compiled into the service -- validated a WATCHED
    marker naming it. `source_files()` already excludes `tests.rs` by name
    from SIDE A; Side B must exclude it the same way, or a marker can be
    satisfied by a name that names nothing real.
    """
    tmp = tmp_path / "repo"
    write(
        tmp,
        "src/rotate/mod.rs",
        "mod tests;\n\n"
        "impl Material for ServerTls {\n"
        "    fn files(&self) -> Vec<File<'_>> { vec![] }\n"
        "}\n",
    )
    write(
        tmp,
        "src/rotate/tests.rs",
        "impl Material for FakeMaterial {\n"
        "    fn files(&self) -> Vec<File<'_>> { vec![] }\n"
        "}\n",
    )
    write(
        tmp,
        "src/serve.rs",
        "fn load(p: &Path) {\n"
        "    // ADR-0523-WATCHED: FakeMaterial\n"
        "    let _ = std::fs::read(p);\n"
        "}\n",
    )
    run_result = run(tmp)
    assert run_result.returncode == 1, run_result.stdout
    assert "`FakeMaterial`" in run_result.stderr
    assert "does not declare" in run_result.stderr


def test_a_fake_material_in_a_rotate_tests_directory_does_not_validate_a_marker(tmp_path):
    """THE OTHER SHAPE `source_files()` excludes: a `tests/` DIRECTORY
    component, not only a `tests.rs` FILE -- `src/rotate/tests/fixtures.rs`.
    """
    tmp = tmp_path / "repo"
    write(
        tmp,
        "src/rotate/mod.rs",
        "mod tests;\n\n"
        "impl Material for ServerTls {\n"
        "    fn files(&self) -> Vec<File<'_>> { vec![] }\n"
        "}\n",
    )
    write(
        tmp,
        "src/rotate/tests/fixtures.rs",
        "impl Material for FakeMaterial {\n"
        "    fn files(&self) -> Vec<File<'_>> { vec![] }\n"
        "}\n",
    )
    write(
        tmp,
        "src/serve.rs",
        "fn load(p: &Path) {\n"
        "    // ADR-0523-WATCHED: FakeMaterial\n"
        "    let _ = std::fs::read(p);\n"
        "}\n",
    )
    run_result = run(tmp)
    assert run_result.returncode == 1, run_result.stdout
    assert "`FakeMaterial`" in run_result.stderr
    assert "does not declare" in run_result.stderr


def test_a_cfg_test_impl_inside_a_real_rotate_sibling_does_not_validate_a_marker(tmp_path):
    """THE NARROWER GAP: not every fake material lives in a file named for
    tests -- an INLINE `#[cfg(test)] mod tests { impl Material for Fake ... }`
    inside an otherwise-real sibling is invisible to a filename check alone.
    `watch_set_names()` must blank `#[cfg(test)]` spans the same way `classify()`
    already does for Side A.
    """
    tmp = tmp_path / "repo"
    write(
        tmp,
        "src/rotate/mod.rs",
        "use crate::serve::ServerTls;\n\n"
        "impl Material for ServerTls {\n"
        "    fn files(&self) -> Vec<File<'_>> { vec![] }\n"
        "}\n\n"
        "#[cfg(test)]\n"
        "mod tests {\n"
        "    impl Material for FakeMaterial {\n"
        "        fn files(&self) -> Vec<File<'_>> { vec![] }\n"
        "    }\n"
        "}\n",
    )
    write(
        tmp,
        "src/serve.rs",
        "fn load(p: &Path) {\n"
        "    // ADR-0523-WATCHED: FakeMaterial\n"
        "    let _ = std::fs::read(p);\n"
        "}\n",
    )
    run_result = run(tmp)
    assert run_result.returncode == 1, run_result.stdout
    assert "`FakeMaterial`" in run_result.stderr
    assert "does not declare" in run_result.stderr


def test_a_rotate_file_declaring_no_material_is_refused(tmp_path):
    """Side B empty means every WATCHED marker fails and every other read passes.

    That verdict says nothing about the watch set, so it is refused rather than
    reported.
    """
    run_result = run(
        tree_with(tmp_path, "fn main() {}\n", rotate="// nothing here yet\n")
    )
    assert run_result.returncode == 1
    assert "DECLARES NO MATERIAL" in run_result.stderr


def test_judging_zero_reads_is_a_failure(tmp_path):
    """Every service here reads a credential or a listener key at boot.

    A run that judged none means the classifier no longer recognises the shape
    this code is written in — the blindness mode a file count cannot see.
    """
    run_result = run(tree_with(tmp_path, "pub fn nothing() -> u8 { 7 }\n"))
    assert run_result.returncode == 1
    assert "JUDGED 0 READS" in run_result.stderr


# --------------------------------------------------------------------------
# LEDGER 925 B-U5: every boot read lives in a library, declared in src/rotate.rs.
#
# `yadgar-lifecycle`'s `serve-tls` feature moves the listener's certificate, key
# and client-CA reads into the crate, beside the crate's own
# `impl Material for ServerTls`. A repository whose only boot reads were those
# then judges zero reads, and the floor above refuses it. The declaration below
# is the one way through that floor, and every arm of it is a refusal first.
# --------------------------------------------------------------------------

LIBRARY_MARKER = "// ADR-0523-LIBRARY-WATCHED: yadgar_lifecycle::serve_tls::ServerTls\n"

# A watch set whose listener material is the crate's, the shape task,
# task-db, project, project-db and iam-db take after B-U5.
ROTATE_LIBRARY = (
    "pub use yadgar_lifecycle::rotate::{Configuration, Inputs, Material};\n"
    "use crate::serve::ServerTls;\n"
    "\n"
    + LIBRARY_MARKER
    + "pub fn watch_set(listener: Option<&ServerTls>, config: &Configuration) -> Inputs {\n"
    "    Inputs::of(SERVICE, &[&listener, config])\n"
    "}\n"
)

CARGO_SERVE_TLS = """\
[package]
name = "task"

[dependencies]
yadgar-lifecycle = { git = "https://github.com/yadgarhq/lifecycle.git", tag = "v0.2.20", features = [
  "serve-tls",
] }
"""

NO_READS = "pub use yadgar_lifecycle::serve_tls::ServerTls;\npub fn nothing() -> u8 { 7 }\n"

MARKED_READ = (
    "pub use yadgar_lifecycle::serve_tls::ServerTls;\n"
    "fn read_key(p: &str) {\n"
    f"    let _ = std::fs::read(p); // ADR-0523-UNWATCHED: {REASON}\n"
    "}\n"
)


def library_tree(
    tmp_path: Path,
    body: str = NO_READS,
    rotate: str = ROTATE_LIBRARY,
    cargo: str | None = CARGO_SERVE_TLS,
) -> Path:
    tree_with(tmp_path, body, rotate=rotate)
    if cargo is not None:
        write(tmp_path, "Cargo.toml", cargo)
    return tmp_path


def test_zero_reads_with_a_library_declaration_passes(tmp_path):
    """THE B-U5 SHAPE: no `fs::` read left in src/, the listener's in the crate."""
    run_result = run(library_tree(tmp_path))
    assert run_result.returncode == 0, run_result.stderr
    assert "0 filesystem read(s) judged" in run_result.stdout
    assert "yadgar_lifecycle::serve_tls::ServerTls" in run_result.stdout
    assert "the zero-read floor is answered by declared library material(s)" in run_result.stdout
    assert "Reads inside other libraries are outside this scan." in run_result.stdout


def test_zero_reads_without_a_declaration_names_the_way_through(tmp_path):
    """The floor still refuses, and now says what an honest zero looks like."""
    rotate = ROTATE_LIBRARY.replace(LIBRARY_MARKER, "")
    run_result = run(library_tree(tmp_path, rotate=rotate))
    assert run_result.returncode == 1
    assert "JUDGED 0 READS" in run_result.stderr
    assert "ADR-0523-LIBRARY-WATCHED" in run_result.stderr


def test_a_declaration_does_not_excuse_an_unmarked_read(tmp_path):
    """REGRESSION GUARD: the declaration speaks for the library, never for src/."""
    run_result = run(library_tree(tmp_path, body="fn load(p: &Path) {\n    std::fs::read(p);\n}\n"))
    assert run_result.returncode == 1
    assert "src/serve.rs:2" in run_result.stderr
    assert "no ADR-0523 marker" in run_result.stderr


def test_a_declaration_beside_a_declared_unwatched_read_passes(tmp_path):
    """The iam shape: library listener, plus crypto keys declared unwatched."""
    run_result = run(library_tree(tmp_path, body=MARKED_READ))
    assert run_result.returncode == 0, run_result.stderr


def test_a_declaration_without_the_crate_is_refused(tmp_path):
    """A MARKED read keeps the zero floor out of it, so only the check can fire."""
    cargo = '[package]\nname = "task"\n\n[dependencies]\ntokio = "1"\n'
    run_result = run(library_tree(tmp_path, body=MARKED_READ, cargo=cargo))
    assert run_result.returncode == 1
    assert "src/rotate.rs:4" in run_result.stderr
    assert "does not depend on `yadgar-lifecycle`" in run_result.stderr


def test_a_declaration_without_the_feature_is_refused(tmp_path):
    cargo = CARGO_SERVE_TLS.replace('"serve-tls",\n', "")
    run_result = run(library_tree(tmp_path, body=MARKED_READ, cargo=cargo))
    assert run_result.returncode == 1
    assert "feature `serve-tls`" in run_result.stderr


def test_a_dev_dependency_does_not_satisfy_a_declaration(tmp_path):
    """Boot is the production binary; a test-only dependency carries nothing."""
    cargo = CARGO_SERVE_TLS.replace("[dependencies]", "[dev-dependencies]")
    run_result = run(library_tree(tmp_path, body=MARKED_READ, cargo=cargo))
    assert run_result.returncode == 1
    assert "does not depend on `yadgar-lifecycle`" in run_result.stderr


def test_an_optional_dependency_does_not_satisfy_a_declaration(tmp_path):
    cargo = CARGO_SERVE_TLS.replace('tag = "v0.2.20",', 'tag = "v0.2.20", optional = true,')
    run_result = run(library_tree(tmp_path, body=MARKED_READ, cargo=cargo))
    assert run_result.returncode == 1
    assert "optional" in run_result.stderr


def test_a_renamed_package_does_not_satisfy_a_declaration(tmp_path):
    cargo = CARGO_SERVE_TLS.replace('tag = "v0.2.20",', 'tag = "v0.2.20", package = "evil",')
    run_result = run(library_tree(tmp_path, body=MARKED_READ, cargo=cargo))
    assert run_result.returncode == 1
    assert "package" in run_result.stderr


def test_a_missing_manifest_refuses_a_declaration(tmp_path):
    run_result = run(library_tree(tmp_path, body=MARKED_READ, cargo=None))
    assert run_result.returncode == 1
    assert "there is no Cargo.toml beside src/" in run_result.stderr
    assert "Traceback" not in run_result.stderr


def test_an_unknown_library_material_is_refused(tmp_path):
    """The table is measured, not inferred: a path nobody verified is refused."""
    rotate = ROTATE_LIBRARY.replace("serve_tls::ServerTls\n", "serve_tls::Invented\n", 1)
    run_result = run(library_tree(tmp_path, body=MARKED_READ, rotate=rotate))
    assert run_result.returncode == 1
    assert "`yadgar_lifecycle::serve_tls::Invented`" in run_result.stderr
    assert "not a library material this gate knows" in run_result.stderr


def test_a_declared_material_absent_from_the_watch_set_is_refused(tmp_path):
    """SIDE B AGAIN: a library material the watch set never folds in is a lie."""
    rotate = ROTATE_LIBRARY.replace("listener: Option<&ServerTls>, ", "")
    run_result = run(library_tree(tmp_path, body=MARKED_READ, rotate=rotate))
    assert run_result.returncode == 1
    assert "`ServerTls`" in run_result.stderr
    assert "watch_set" in run_result.stderr


def test_a_local_material_impl_contradicts_a_library_declaration(tmp_path):
    """Rust forbids implementing lifecycle's trait for lifecycle's type outside
    lifecycle, so a local `impl Material for ServerTls` means a LOCAL type --
    whose reads the crate's impl does not carry. Path-qualified spelling too."""
    for spelling in ("impl Material for ServerTls", "impl crate::rotate::Material for ServerTls"):
        rotate = ROTATE_LIBRARY + f"{spelling} {{\n    fn files(&self) {{}}\n}}\n"
        run_result = run(library_tree(tmp_path, body=MARKED_READ, rotate=rotate))
        assert run_result.returncode == 1, spelling
        assert "implements `Material` for `ServerTls` locally" in run_result.stderr


def test_a_declaration_inside_cfg_test_does_not_count(tmp_path):
    rotate = ROTATE_LIBRARY.replace(LIBRARY_MARKER, "") + (
        "#[cfg(test)]\nmod tests {\n    " + LIBRARY_MARKER + "}\n"
    )
    run_result = run(library_tree(tmp_path, rotate=rotate))
    assert run_result.returncode == 1
    assert "JUDGED 0 READS" in run_result.stderr


def test_a_declaration_outside_the_watch_set_files_does_not_count(tmp_path):
    """It is a Side B statement, so it lives where Side B is read."""
    rotate = ROTATE_LIBRARY.replace(LIBRARY_MARKER, "")
    run_result = run(library_tree(tmp_path, body=LIBRARY_MARKER + NO_READS, rotate=rotate))
    assert run_result.returncode == 1
    assert "JUDGED 0 READS" in run_result.stderr


def test_a_wrapped_local_material_impl_still_contradicts_a_declaration(tmp_path):
    """REVIEW FINDING: a line-at-a-time scan let both wrapped headers through."""
    for spelling in ("impl\n    Material for ServerTls", "impl Material\n    for ServerTls"):
        rotate = ROTATE_LIBRARY + f"{spelling} {{}}\n"
        run_result = run(library_tree(tmp_path, rotate=rotate))
        assert run_result.returncode == 1, spelling
        assert "src/rotate.rs:8 implements `Material` for `ServerTls` locally" in run_result.stderr


def test_a_material_impl_inside_cfg_test_does_not_contradict_a_declaration(tmp_path):
    """Test-only fixtures never reach the binary, so they shadow nothing."""
    rotate = ROTATE_LIBRARY + (
        "#[cfg(test)]\nmod tests {\n    impl Material for ServerTls {}\n}\n"
    )
    run_result = run(library_tree(tmp_path, rotate=rotate))
    assert run_result.returncode == 0, run_result.stderr


def test_a_foreign_path_ending_in_the_material_name_is_refused(tmp_path):
    """The table key is matched EXACTLY, never by its last segment."""
    rotate = ROTATE_LIBRARY.replace("yadgar_lifecycle::serve_tls::ServerTls", "evil::x::ServerTls")
    run_result = run(library_tree(tmp_path, body=MARKED_READ, rotate=rotate))
    assert run_result.returncode == 1
    assert "`evil::x::ServerTls` is not a library material this gate knows" in run_result.stderr


def test_a_declaration_without_a_library_import_is_refused(tmp_path):
    """POSITIVE EVIDENCE: something must import the name out of the library."""
    body = "pub fn nothing() -> u8 { 7 }\n"
    run_result = run(library_tree(tmp_path, body=body))
    assert run_result.returncode == 1
    assert "no production source file imports `ServerTls`" in run_result.stderr


def test_a_library_import_only_in_a_comment_or_test_is_not_evidence(tmp_path):
    for body in (
        "// use yadgar_lifecycle::serve_tls::ServerTls;\npub fn nothing() {}\n",
        "#[cfg(test)]\nmod tests {\n    use yadgar_lifecycle::serve_tls::ServerTls;\n}\n",
    ):
        run_result = run(library_tree(tmp_path, body=body))
        assert run_result.returncode == 1, body
        assert "no production source file imports `ServerTls`" in run_result.stderr


def test_a_braced_library_import_is_evidence(tmp_path):
    """The project-db shape: `pub use yadgar_lifecycle::serve_tls::{ClientAuth, ServerTls};`."""
    body = "pub use yadgar_lifecycle::serve_tls::{\n    ClientAuth,\n    ServerTls,\n};\n"
    run_result = run(library_tree(tmp_path, body=body))
    assert run_result.returncode == 0, run_result.stderr


def test_an_alias_borrowing_the_material_name_is_refused(tmp_path):
    """`use crate::tls::MyTls as ServerTls` hands the name to a local type."""
    body = NO_READS + "use crate::tls::MyTls as ServerTls;\n"
    run_result = run(library_tree(tmp_path, body=body))
    assert run_result.returncode == 1
    assert "src/serve.rs:3 aliases something `as ServerTls`" in run_result.stderr


# --------------------------------------------------------------------------
# Test code is not the subject, and the estate's own `File` is not std's.
# --------------------------------------------------------------------------


def test_reads_inside_a_cfg_test_module_are_not_judged(tmp_path):
    body = (
        "fn load(p: &Path) {\n"
        "    // ADR-0523-WATCHED: ServerTls\n"
        "    let _ = std::fs::read(p);\n"
        "}\n"
        "\n"
        "#[cfg(test)]\n"
        "mod tests {\n"
        "    #[test]\n"
        "    fn fixture() {\n"
        "        let _ = std::fs::read(\"fixture.pem\");\n"
        "        std::fs::write(\"out\", b\"x\").unwrap();\n"
        "    }\n"
        "}\n"
    )
    run_result = run(tree_with(tmp_path, body))
    assert run_result.returncode == 0, run_result.stderr
    assert "1 filesystem read(s) judged" in run_result.stdout


def test_a_braced_format_string_does_not_move_the_test_module_end(tmp_path):
    """THE TRAP, and it is in every one of these repositories.

    Counting braces over raw text lets `tracing::info!("{}", x)` inside the test
    module drive the depth negative early. The module then "ends" before the
    fixture read, which is reported as a violation in test code — and, run the
    other way, hides a real read below a test module.
    """
    body = (
        "#[cfg(test)]\n"
        "mod tests {\n"
        "    #[test]\n"
        "    fn logs() {\n"
        '        tracing::info!("{} {} }}}}", 1, 2);\n'
        '        let _ = std::fs::read("fixture.pem");\n'
        "    }\n"
        "}\n"
        "\n"
        "pub fn after(p: &Path) {\n"
        "    // ADR-0523-WATCHED: ServerTls\n"
        "    let _ = std::fs::read(p);\n"
        "}\n"
    )
    run_result = run(tree_with(tmp_path, body))
    assert run_result.returncode == 0, run_result.stderr
    assert "1 filesystem read(s) judged" in run_result.stdout


def test_a_tests_rs_file_is_not_scanned(tmp_path):
    write(tmp_path, "src/rotate.rs", ROTATE)
    write(
        tmp_path,
        "src/serve.rs",
        "fn load(p: &Path) {\n"
        "    // ADR-0523-WATCHED: ServerTls\n"
        "    let _ = std::fs::read(p);\n"
        "}\n",
    )
    write(tmp_path, "src/crypto/tests.rs", 'fn f() { std::fs::read("k"); }\n')
    run_result = run(tmp_path)
    assert run_result.returncode == 0, run_result.stderr


def test_the_estates_own_File_type_is_not_a_filesystem_read(tmp_path):
    """`yadgar_lifecycle::rotate::File::read(path)` builds a watch-set entry.

    It opens nothing. Refusing unknown `File::NAME` reddened all six real
    repositories on this collision, which is why the `File::` table ignores what
    it does not know while the `fs::` table refuses.
    """
    rotate = ROTATE + (
        "\nimpl Material for UpstreamTls {\n"
        "    fn files(&self) -> Vec<File<'_>> {\n"
        "        vec![File::read(self.ca_file())]\n"
        "    }\n"
        "}\n"
    )
    body = (
        "pub fn entry(p: &Path) -> File<'_> { File::read(p) }\n"
        "pub fn load(p: &Path) {\n"
        "    // ADR-0523-WATCHED: ServerTls\n"
        "    let _ = std::fs::read(p);\n"
        "}\n"
    )
    run_result = run(tree_with(tmp_path, body, rotate))
    # A REAL READ SITS BESIDE THE COLLISION ON PURPOSE. Asserting only that the
    # run judged nothing would also pass if the classifier had stopped seeing
    # `fs::read` altogether -- the blindness the second floor exists to catch,
    # admitted by a test named for something else.
    assert run_result.returncode == 0, run_result.stderr
    assert "1 filesystem read(s) judged" in run_result.stdout
    assert "UNCLASSIFIED" not in run_result.stderr


def test_an_unmarked_read_inside_cfg_not_test_is_refused_not_swallowed(tmp_path):
    r"""LEDGER 1240. `#[cfg(not(test))]` is PRODUCTION code -- it compiles into
    the binary in every build EXCEPT a test build. The old `test_spans()`
    regex `#\[cfg\((?:[^)]*\b)?test\b` matches the literal substring `test`
    inside `not(test)` too, because `[^)]*` happily consumes `not(` (it has no
    `)`), so a `#[cfg(not(test))] fn` was blanked out of Side A the same way a
    real `#[cfg(test)]` module is. An unmarked boot read inside it was never
    judged -- a detector whose forgetting-case is green is the defect.
    """
    run_result = run(
        tree_with(
            tmp_path,
            "fn load(p: &Path) {\n"
            "    // ADR-0523-WATCHED: ServerTls\n"
            "    let _ = std::fs::read(p);\n"
            "}\n"
            "\n"
            "#[cfg(not(test))]\n"
            "fn load_prod(q: &Path) {\n"
            "    let _ = std::fs::read(q);\n"
            "}\n",
        )
    )
    assert run_result.returncode == 1, run_result.stdout
    assert "src/serve.rs:8" in run_result.stderr
    assert "no ADR-0523 marker" in run_result.stderr


def test_a_real_test_module_beside_cfg_not_test_is_still_excluded(tmp_path):
    """The fix must not overcorrect: a GENUINE `#[cfg(test)]` module is still
    test code and is still not judged, even sitting right next to a
    `#[cfg(not(test))]` one.
    """
    run_result = run(
        tree_with(
            tmp_path,
            "#[cfg(not(test))]\n"
            "fn load_prod(q: &Path) {\n"
            "    // ADR-0523-WATCHED: ServerTls\n"
            "    let _ = std::fs::read(q);\n"
            "}\n"
            "\n"
            "#[cfg(test)]\n"
            "mod tests {\n"
            "    #[test]\n"
            "    fn fixture() {\n"
            '        let _ = std::fs::read("fixture.pem");\n'
            "    }\n"
            "}\n",
        )
    )
    assert run_result.returncode == 0, run_result.stderr
    assert "1 filesystem read(s) judged" in run_result.stdout


def test_a_material_impl_inside_cfg_not_test_is_not_hidden_from_side_b(tmp_path):
    """LEDGER 1240, THE OTHER DIRECTION: the same false match inside
    `watch_set_names()` blanks a REAL `impl Material` sitting under
    `#[cfg(not(test))]` in `src/rotate.rs`, which falsely refuses a correct
    WATCHED marker naming it -- Side B false refusal on legitimate code.

    `UpstreamTls` names a material found ONLY inside the blanked impl block,
    never in the `watch_set` signature itself -- `ServerTls` would pass even
    under the bug, because `watch_set`'s own parameter list already mentions
    it, which would make this a false green for a reason unrelated to the fix.
    """
    rotate = (
        "#[cfg(not(test))]\n"
        "impl Material for UpstreamTls {\n"
        "    fn files(&self) -> Vec<File<'_>> {\n"
        "        vec![File::read(self.key_file())]\n"
        "    }\n"
        "}\n"
        "\n"
        "pub fn watch_set(config: &Configuration) -> Inputs {\n"
        "    Inputs::of(SERVICE, &[config])\n"
        "}\n"
    )
    run_result = run(
        tree_with(
            tmp_path,
            "fn load(p: &Path) {\n"
            "    // ADR-0523-WATCHED: UpstreamTls\n"
            "    let _ = std::fs::read(p);\n"
            "}\n",
            rotate=rotate,
        )
    )
    assert run_result.returncode == 0, run_result.stderr


def test_test_not_first_inside_all_is_still_test_code(tmp_path):
    """LEDGER 1240, SECOND PASS. `cfg(all(feature = "x", test))` puts `test`
    SECOND rather than first, which the first pass's regex
    (`(?:all\\(\\s*)?test\\b`, requiring `test` immediately after `all(`) did
    not match -- a false NEGATIVE the other direction: this read should have
    been excluded as test code and was not, which also means Side B
    (`watch_set_names()`) would wrongly blank a real `impl Material` behind
    the identical predicate.
    """
    run_result = run(
        tree_with(
            tmp_path,
            "fn load(p: &Path) {\n"
            "    // ADR-0523-WATCHED: ServerTls\n"
            "    let _ = std::fs::read(p);\n"
            "}\n"
            "\n"
            '#[cfg(all(feature = "x", test))]\n'
            "mod fixture_only {\n"
            "    fn load_fixture(q: &Path) {\n"
            "        let _ = std::fs::read(q);\n"
            "    }\n"
            "}\n",
        )
    )
    assert run_result.returncode == 0, run_result.stderr
    assert "1 filesystem read(s) judged" in run_result.stdout


def test_cfg_any_containing_test_is_production_code(tmp_path):
    """LEDGER 1240, THIRD PASS -- INVERTED FROM AN EARLIER REVISION OF THIS
    TEST, which asserted the opposite and was wrong. `cfg(any(test, foo))`
    compiles whenever `test` OR `foo` holds -- so it compiles into a
    PRODUCTION build whenever `foo` is set, regardless of `test`. Treating it
    as test code (excluding it from Side A, as the second pass's
    not-nested-under-`not(` rule did) is a false acceptance: an unmarked read
    behind it would never be judged. `any(...)` implies `test` only when
    EVERY alternative does, and `foo` does not.
    """
    run_result = run(
        tree_with(
            tmp_path,
            "fn load(p: &Path) {\n"
            "    // ADR-0523-WATCHED: ServerTls\n"
            "    let _ = std::fs::read(p);\n"
            "}\n"
            "\n"
            "#[cfg(any(test, foo))]\n"
            "mod fixture_only {\n"
            "    fn load_fixture(q: &Path) {\n"
            "        let _ = std::fs::read(q);\n"
            "    }\n"
            "}\n",
        )
    )
    assert run_result.returncode == 1, run_result.stdout
    assert "src/serve.rs:9" in run_result.stderr
    assert "no ADR-0523 marker" in run_result.stderr


def test_test_inside_all_not_is_still_production_code(tmp_path):
    """`cfg(all(not(test), x))` negates `test` -- the whole predicate is TRUE
    only when NOT testing (and `x` holds), so this stays production code and
    an unmarked read inside it must still be refused, not swallowed.
    """
    run_result = run(
        tree_with(
            tmp_path,
            "fn load(p: &Path) {\n"
            "    // ADR-0523-WATCHED: ServerTls\n"
            "    let _ = std::fs::read(p);\n"
            "}\n"
            "\n"
            '#[cfg(all(not(test), x))]\n'
            "fn load_prod(q: &Path) {\n"
            "    let _ = std::fs::read(q);\n"
            "}\n",
        )
    )
    assert run_result.returncode == 1, run_result.stdout
    assert "src/serve.rs:8" in run_result.stderr
    assert "no ADR-0523 marker" in run_result.stderr


def test_cfg_any_with_a_negated_child_and_test_is_still_production_code(tmp_path):
    """B1, THE BLOCKER FROM ADVERSARIAL RE-REVIEW. `cfg(any(not(x), test))`
    compiles whenever `not(x)` holds -- i.e. whenever `x` is UNSET -- entirely
    independent of `test`. ADR-0645: the shipped gate on `origin/main`
    correctly refuses an unmarked read behind this predicate; a candidate
    that accepts it is a newly-introduced false acceptance, blocking under
    ADR-0645 regardless of any other improvement in the same change. The
    earlier `not`-nesting-only rule excluded this as test code because its
    literal `test` atom sits outside every `not(` frame; the implies-test
    rule does not, because `any(...)` only implies `test` when EVERY
    alternative does, and `not(x)` never does.
    """
    run_result = run(
        tree_with(
            tmp_path,
            "fn load(p: &Path) {\n"
            "    // ADR-0523-WATCHED: ServerTls\n"
            "    let _ = std::fs::read(p);\n"
            "}\n"
            "\n"
            "#[cfg(any(not(x), test))]\n"
            "fn load_prod(q: &Path) {\n"
            "    let _ = std::fs::read(q);\n"
            "}\n",
        )
    )
    assert run_result.returncode == 1, run_result.stdout
    assert "src/serve.rs:8" in run_result.stderr
    assert "no ADR-0523 marker" in run_result.stderr


def test_all_conjuncts_that_force_test_are_accepted_as_test_only(tmp_path):
    """ADR-0839 (amends ADR-0645): `all(not(foo), test)` and
    `all(any(a, b), test)` each carry `test` as a DIRECT conjunct of an
    `all(...)`, so the whole predicate can only be true when `test` is --
    under every possible assignment of `foo`/`a`/`b`. The shipped gate on
    `origin/main` refused both (a false refusal: neither can ever compile
    outside a test build), and ADR-0839 rules that correcting a PROVEN false
    refusal is not the newly-accepted-input ADR-0645 forbids. Both read as
    test code here and their reads are excluded from Side A.
    """
    for predicate in ('#[cfg(all(not(foo), test))]', "#[cfg(all(any(a, b), test))]"):
        run_result = run(
            tree_with(
                tmp_path,
                "fn load(p: &Path) {\n"
                "    // ADR-0523-WATCHED: ServerTls\n"
                "    let _ = std::fs::read(p);\n"
                "}\n"
                "\n"
                f"{predicate}\n"
                "mod fixture_only {\n"
                "    fn load_fixture(q: &Path) {\n"
                "        let _ = std::fs::read(q);\n"
                "    }\n"
                "}\n",
            )
        )
        assert run_result.returncode == 0, (predicate, run_result.stderr)
        assert "1 filesystem read(s) judged" in run_result.stdout, predicate


def test_not_followed_by_a_comment_then_the_paren_is_still_not(tmp_path):
    """B2, A REVIEW FINDING. `cfg(not /*c*/ (test))` -- `blank_noncode`
    already turns the comment into spaces by the time this gate sees it, so
    the predicate reads as `not          (test)`. An earlier revision
    recomputed "was the identifier right before this `(` the word `not`" by
    re-slicing raw text at the `(` itself, and the blanked comment had
    already cleared that slice to empty -- so this predicate was wrongly read
    as test code (false acceptance). Tokens are emitted independently of
    what separates them, so `ident("not")` followed by `"("` is the same
    token pair whether they are adjacent in the source or a comment apart.
    """
    run_result = run(
        tree_with(
            tmp_path,
            "fn load(p: &Path) {\n"
            "    // ADR-0523-WATCHED: ServerTls\n"
            "    let _ = std::fs::read(p);\n"
            "}\n"
            "\n"
            "#[cfg(not /*c*/ (test))]\n"
            "fn load_prod(q: &Path) {\n"
            "    let _ = std::fs::read(q);\n"
            "}\n",
        )
    )
    assert run_result.returncode == 1, run_result.stdout
    assert "src/serve.rs:8" in run_result.stderr
    assert "no ADR-0523 marker" in run_result.stderr


def test_File_open_is_still_a_read(tmp_path):
    run_result = run(
        tree_with(tmp_path, "fn f(p: &Path) { let _ = std::fs::File::open(p); }\n")
    )
    assert run_result.returncode == 1
    assert "no ADR-0523 marker" in run_result.stderr


# --------------------------------------------------------------------------
# The blanking pass, exercised directly: its failures move a line rather than
# raising, so a behavioural test alone would not localise them.
# --------------------------------------------------------------------------


def test_blanking_preserves_every_line_number():
    source = 'fn f() {\n    let s = "a\\nb";\n    /* one\n       two */\n    let c = \'x\';\n}\n'
    assert gate.blank_noncode(source).count("\n") == source.count("\n")


def test_blanking_removes_braces_inside_strings():
    assert "{" not in gate.blank_noncode('let s = "{}{{}}";')


def test_blanking_keeps_braces_that_are_braces():
    assert gate.blank_noncode("fn f() { g(); }").count("{") == 1


def test_a_lifetime_is_not_an_unterminated_char_literal():
    """`&'a str` opens a quote that never closes; a naive scan eats the file."""
    source = "fn f<'a>(s: &'a str) -> &'a str { s }\nfn g(p: &Path) { std::fs::read(p); }\n"
    assert "fs::read" in gate.blank_noncode(source)


def test_a_raw_string_holding_a_quote_is_blanked_whole():
    assert '"' not in gate.blank_noncode('let s = r#"a " b"#;')


def test_a_nested_block_comment_closes_at_the_outer_pair():
    """Rust nests `/* */`, unlike C. Stopping at the first `*/` blanks code."""
    source = "/* a /* b */ c */\nfn f(p: &Path) { std::fs::read(p); }\n"
    assert "fs::read" in gate.blank_noncode(source)


def test_a_read_written_inside_a_comment_is_not_a_read(tmp_path):
    run_result = run(
        tree_with(tmp_path, "// once called std::fs::read(p) here\npub fn f() -> u8 { 1 }\n")
    )
    assert run_result.returncode == 1
    assert "JUDGED 0 READS" in run_result.stderr
