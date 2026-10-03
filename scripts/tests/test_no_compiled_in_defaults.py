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


# LEDGER 965, UNIT C-A1. The widened ADR-0569 gate, covering the census shapes
# plan-C.md names beyond the three the gate already forbade: a lookup/env/get
# call keyed by a config knob falling back inline (chained through rustfmt's
# split), a `DEFAULT_*` const/static, a presence test against `Some("...")`,
# `EnvFilter::new(...)`, and a `None` arm beside an env read that answers with
# a bare constant.
#
# P1 IS ANCHORED TO AN UPPERCASE KEY, narrower than the card's first draft.
# The unnarrowed shape (`(lookup|env|get)\(...)...unwrap_or`) matches ANY
# map/JSON lookup chained into unwrap_or, config key or not — measured
# 2026-10-03 against yadgarhq/yadgar @ origin/main, where it false-positives
# on seven lines that read an untyped JSON/slice value by a lowercase field
# name or a variable, never a config knob: src/hook/guard.rs:60 (`payload
# .get("tool_name").and_then(Value::as_str).unwrap_or("")`) among them. A
# config key in this estate is always ALL_CAPS, whether passed as a bare
# constant (gateway broker.rs: `lookup(URL)`) or a literal
# (`env("YADGAR_CREDENTIAL_TTL_SECONDS")`), so the key is required to match
# `[A-Z][A-Z0-9_]*`, quoted or bare.


def test_lookup_bare_constant_key_chain_is_refused(tmp_path):
    """`lookup(URL).unwrap_or_default()` — the real gateway broker.rs shape."""
    write(
        tmp_path,
        "src/main.rs",
        "fn from_lookup(lookup: impl Fn(&str) -> Option<String>) {\n"
        "    let url = lookup(URL).unwrap_or_default();\n"
        "}\n",
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "lookup/env/get call on a CONFIG KEY" in result.stderr
    assert "src/main.rs:" in result.stderr


def test_env_quoted_uppercase_key_chain_is_refused(tmp_path):
    """A quoted ALL-CAPS literal key is the same shape as a bare constant."""
    write(
        tmp_path,
        "src/main.rs",
        "fn main() {\n"
        '    let ttl = env("YADGAR_CREDENTIAL_TTL_SECONDS")\n'
        '        .unwrap_or_else(|| "30".into());\n'
        "}\n",
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "lookup/env/get call on a CONFIG KEY" in result.stderr


def test_json_field_lookup_by_lowercase_key_is_not_refused(tmp_path):
    """The false-positive this narrowing exists to close.

    Measured against yadgar-client src/hook/guard.rs:60 at origin/main: a
    serde_json chain reading a lowercase field is not a config knob.
    """
    write(
        tmp_path,
        "src/main.rs",
        "fn pre_tool_guard(payload: &Value) -> Decision {\n"
        '    let tool = payload\n'
        '        .get("tool_name")\n'
        "        .and_then(Value::as_str)\n"
        '        .unwrap_or("");\n'
        "}\n",
    )
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr


def test_lookup_chain_marked_is_not_refused(tmp_path):
    write(
        tmp_path,
        "src/main.rs",
        "fn from_lookup(lookup: impl Fn(&str) -> Option<String>) {\n"
        "    let url = lookup(URL).unwrap_or_default(); "
        "// ADR-0569-EXCEPTION(ABS): off when empty\n"
        "}\n",
    )
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr


def test_default_const_is_refused(tmp_path):
    write(tmp_path, "src/main.rs", "const DEFAULT_TTL_SECONDS: u64 = 30;\n")
    result = run(tmp_path)
    assert result.returncode == 1
    assert "named DEFAULT_" in result.stderr


def test_default_const_marked_is_not_refused(tmp_path):
    write(
        tmp_path,
        "src/main.rs",
        "const DEFAULT_TTL_SECONDS: u64 = 30; "
        "// ADR-0569-EXCEPTION(CC): tied to iam's credential lifetime\n",
    )
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr


def test_absent_as_off_literal_is_refused(tmp_path):
    """`get("TLS_ENABLED").as_deref() != Some("1")` — gateway cache.rs shape."""
    write(
        tmp_path,
        "src/main.rs",
        "fn main() {\n"
        '    let on = get("TLS_ENABLED").as_deref() != Some("1");\n'
        "}\n",
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "tested against Some(" in result.stderr


def test_envfilter_new_is_refused(tmp_path):
    write(
        tmp_path,
        "src/main.rs",
        'fn main() { let filter = EnvFilter::new("info"); }\n',
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "EnvFilter::new" in result.stderr


def test_envfilter_new_marked_is_not_refused(tmp_path):
    write(
        tmp_path,
        "src/main.rs",
        'fn main() { let filter = EnvFilter::new("info"); '
        "// ADR-0569-EXCEPTION(LIB): observability, not behaviour\n}\n",
    )
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr


def test_none_arm_bare_constant_beside_env_read_is_refused(tmp_path):
    """Scoped to files that read the environment, per the card."""
    write(
        tmp_path,
        "src/main.rs",
        "fn f(x: Option<&str>) -> &str {\n"
        '    let _ = std::env::var("K");\n'
        "    match x {\n"
        "        Some(v) => v,\n"
        "        None => DEFAULT_LEVEL,\n"
        "    }\n"
        "}\n",
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "None arm beside an env read" in result.stderr


def test_none_arm_bare_constant_outside_env_scope_is_not_refused(tmp_path):
    """The same `None => CONST` shape, in a file that never reads the env."""
    write(
        tmp_path,
        "src/main.rs",
        "fn f(x: Option<&str>) -> &str {\n"
        "    match x {\n"
        "        Some(v) => v,\n"
        "        None => SOME_CONST,\n"
        "    }\n"
        "}\n",
    )
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr


def test_none_arm_bare_constant_marked_is_not_refused(tmp_path):
    write(
        tmp_path,
        "src/main.rs",
        "fn f(x: Option<&str>) -> &str {\n"
        '    let _ = std::env::var("K");\n'
        "    match x {\n"
        "        Some(v) => v,\n"
        "        None => DEFAULT_LEVEL, "
        "// ADR-0569-EXCEPTION(ABS): log level default\n"
        "    }\n"
        "}\n",
    )
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr
