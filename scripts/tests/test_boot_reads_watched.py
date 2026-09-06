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
    assert "DOES NOT EXIST" in run_result.stderr


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
