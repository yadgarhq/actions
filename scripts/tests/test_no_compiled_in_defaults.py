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

import re
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


def test_absent_as_off_lookup_key_is_refused(tmp_path):
    """`lookup(TRUST_HEADERS).as_deref() == Some("1")` — gateway attest.rs:219."""
    write(
        tmp_path,
        "src/main.rs",
        "fn main() {\n"
        "    let on = lookup(TRUST_HEADERS).as_deref() == Some(\"1\");\n"
        "}\n",
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "tested against Some(" in result.stderr


def test_absent_as_off_lowercase_field_is_not_refused(tmp_path):
    """The same P3 narrowing as P1: a lowercase field is not a config key."""
    write(
        tmp_path,
        "src/main.rs",
        "fn main() {\n"
        '    let is_stdio = v.get("type").as_deref() == Some("stdio");\n'
        "}\n",
    )
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr


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


def test_none_arm_bare_constant_scoped_by_bare_env_call_is_refused(tmp_path):
    """P5's scope widened from `env::var(`/`lookup(` to also match `env(`.

    A bare `env(KEY)` call with no `.unwrap_or` chain is not itself a P1 hit,
    but it still marks the file as one that reads the environment.
    """
    write(
        tmp_path,
        "src/main.rs",
        "fn f(x: Option<&str>, env: impl Fn(&str) -> Option<String>) -> &str {\n"
        "    let _ = env(SOME_KEY);\n"
        "    match x {\n"
        "        Some(v) => v,\n"
        "        None => DEFAULT_LEVEL,\n"
        "    }\n"
        "}\n",
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "None arm beside an env read" in result.stderr


# LEDGER 965, UNIT C-A1 FOLLOW-UP. Comment-awareness: a doc comment that
# merely QUOTES a forbidden shape in prose configures nothing. Measured
# against gateway src/invalidate.rs:95 at origin/main, a `//!` module doc
# line quoting `EnvFilter::new("info")` was a false positive under the first
# version of this gate.


def test_envfilter_new_in_doc_comment_is_not_refused(tmp_path):
    write(
        tmp_path,
        "src/main.rs",
        '//! main builds `EnvFilter::new("info")` with no override.\n'
        "fn main() {}\n",
    )
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr


def test_default_const_in_doc_comment_is_not_refused(tmp_path):
    write(
        tmp_path,
        "src/main.rs",
        "/// See also const DEFAULT_X: u32 in the sibling module.\n"
        "fn main() {}\n",
    )
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr


def test_default_const_with_trailing_comment_marker_is_still_honoured(tmp_path):
    """Comment-stripping must drop WHOLE-LINE comments only.

    The ADR-0569-EXCEPTION marker itself lives in a trailing comment, so a
    trailing comment on a real code line must still be read.
    """
    write(
        tmp_path,
        "src/main.rs",
        "const DEFAULT_TTL_SECONDS: u64 = 30; // ADR-0569-EXCEPTION(CC): fine\n",
    )
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr


# YAADGAAR 338 (ledger 1345). The grep-blind shapes of the P0/P1/P3 patterns,
# swept against the consumers' origin/main (no real instance found, so the
# widening is preventive) and then closed. Each refusal has a pristine
# companion that must stay clean: a wider pattern is worth only what it does
# not newly refuse.


def _refused(tmp_path, body, title="ADR-0569 VIOLATION"):
    write(tmp_path, "src/main.rs", body)
    result = run(tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert title in result.stderr
    assert "src/main.rs:" in result.stderr
    return result


def _clean(tmp_path, body):
    write(tmp_path, "src/main.rs", body)
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "searching 1 Rust file(s)" in result.stdout


def test_grpc_header_constant_with_nested_chain_stays_clean(tmp_path):
    """WHY NESTED CHAIN LINKS ARE NOT WIDENED. `yadgarhq/project`
    src/service/request_id.rs:52 at origin/main reads a gRPC header constant
    through a nested-paren chain; a nested-link pattern newly reds it and finds
    no real knob anywhere in the consumers (swept 2026-11-01). If this test
    ever needs to flip, re-sweep the consumers first."""
    _clean(
        tmp_path,
        "fn id(req: &Request<()>) -> String {\n"
        "    req.metadata()\n        .get(REQUEST_ID_HEADER)\n"
        "        .and_then(|v| v.to_str().ok())\n"
        "        .map(|v| capped(v).into_owned())\n"
        "        .unwrap_or_default()\n}\n",
    )


def test_borrowed_key_is_refused(tmp_path):
    _refused(
        tmp_path, "fn f() { let n = lookup(&POLL).unwrap_or_default(); }\n"
    )


def test_grpc_header_path_constant_stays_clean(tmp_path):
    """WHY A PATH-QUALIFIED KEY IS NOT WIDENED IN P1. `yadgarhq/iam`
    src/key_identity/tests.rs:73 and src/service/tests.rs:211 at origin/main read
    `metadata.get(yadgar_dial::GRPC_TIMEOUT)` through a chain ending in
    `.unwrap_or_default()`; a path-qualified P1 key newly reds both (swept
    2026-11-01). Re-sweep the consumers before flipping this."""
    _clean(
        tmp_path,
        "fn t(m: &MetadataMap) {\n"
        "    let told = m.get(yadgar_dial::GRPC_TIMEOUT)"
        ".map(|v| v.to_str().unwrap_or_default().to_string());\n}\n",
    )


def test_path_qualified_key_in_a_presence_test_is_refused(tmp_path):
    _refused(
        tmp_path, 'fn f() { let on = get(keys::TRUST_HEADERS) == Some("1"); }\n'
    )


def test_path_qualified_key_in_a_presence_test_needs_an_uppercase_last_segment(tmp_path):
    _clean(
        tmp_path, 'fn f(m: &Map) { let on = m.get(keys::index) == Some("1"); }\n'
    )


def test_rustfmt_split_env_var_arguments_are_refused(tmp_path):
    _refused(
        tmp_path,
        "fn f() {\n    let v = std::env::var(\n        \"YADGAR_ADDR\",\n    )\n"
        '    .unwrap_or_else(|_| "d".into());\n}\n',
    )


def test_rustfmt_split_lookup_arguments_are_refused(tmp_path):
    result = _refused(
        tmp_path,
        "fn f() {\n    let v = lookup(\n        URL,\n    )\n    .unwrap_or_default();\n}\n",
    )
    # The line reported is the one the call starts on, not the join's tail.
    assert re.search(r"src/main\.rs:\s+2\t", result.stderr), result.stderr


def test_split_arguments_with_a_marker_are_not_refused(tmp_path):
    """The marker rides on the chain's last line, which the join keeps."""
    _clean(
        tmp_path,
        "fn f() {\n    let v = lookup(\n        URL,\n    )\n"
        "    .unwrap_or_default(); // ADR-0569-EXCEPTION(ABS): off when empty\n}\n",
    )


def test_aliased_var_import_is_refused(tmp_path):
    _refused(
        tmp_path,
        "use std::env::var as fetch;\n"
        'fn f() { let v = fetch("YADGAR_ADDR").unwrap_or_default(); }\n',
    )


def test_braced_aliased_var_import_is_refused(tmp_path):
    _refused(
        tmp_path,
        "use std::env::{self, var_os as fetch};\n"
        'fn f() { let v = fetch("YADGAR_ADDR").unwrap_or_default(); }\n',
    )


def test_bare_imported_var_is_refused(tmp_path):
    _refused(
        tmp_path,
        "use std::env::var;\n"
        'fn f() { let v = var("YADGAR_ADDR").unwrap_or_default(); }\n',
    )


def test_option_env_macro_fallback_is_refused(tmp_path):
    _refused(
        tmp_path, 'fn f() -> &\'static str { option_env!("BUILD_ID").unwrap_or("dev") }\n'
    )


# Companions: shapes the widened patterns must keep clean.


def test_unimported_lookalike_alias_is_not_refused(tmp_path):
    """`fetch` is an env alias only because an `env::var` import says so."""
    _clean(
        tmp_path,
        'fn f() { let v = fetch("YADGAR_ADDR").unwrap_or_default(); }\n',
    )


def test_lowercase_key_with_nested_chain_is_not_refused(tmp_path):
    _clean(
        tmp_path,
        'fn f(v: &Value) { let n = v.get("retries").map(|x| x.parse().ok()).unwrap_or(5); }\n',
    )


def test_lowercase_path_value_is_not_a_config_key(tmp_path):
    _clean(
        tmp_path, "fn f(m: &Map) { let n = m.get(keys::index).copied().unwrap_or(0); }\n"
    )


def test_split_arguments_of_an_unrelated_call_are_not_refused(tmp_path):
    _clean(
        tmp_path,
        "fn f() {\n    let n = compute(\n        first,\n        second,\n    )\n"
        "    .unwrap_or(0);\n}\n",
    )


def test_env_read_and_fallback_in_separate_split_calls_are_not_refused(tmp_path):
    """The join is per call: it must not fuse neighbouring statements."""
    _clean(
        tmp_path,
        "fn f() {\n    let a = require(\n        \"YADGAR_ADDR\",\n    );\n"
        "    let b = other.unwrap_or(1);\n}\n",
    )


def test_unaliased_var_from_another_module_is_not_refused(tmp_path):
    _clean(
        tmp_path,
        "use crate::cfg::var;\n"
        'fn f() { let v = var("x").unwrap_or_default(); }\n',
    )


# ADR-0645: the bracket joins must not WEAKEN the gate. Each shape below was
# refused by the chain-only gate before yaadgaar 338 and must stay refused.


def test_marker_on_an_argument_line_does_not_cover_the_read(tmp_path):
    _refused(
        tmp_path,
        'fn f() {\n    let v = env::var("K").unwrap_or(\n'
        '        "d".into(), // ADR-0569-EXCEPTION(ABS): about the argument\n    );\n}\n',
    )


def test_marker_on_the_closing_line_does_not_cover_the_inner_read(tmp_path):
    _refused(
        tmp_path,
        'fn f() {\n    let v = env::var("K").unwrap_or(\n        "d".into(),\n'
        "    ); // ADR-0569-EXCEPTION(ABS): about the closing line\n}\n",
    )


def test_marker_on_an_outer_calls_closing_line_does_not_cover_the_read(tmp_path):
    _refused(
        tmp_path,
        'fn f() {\n    foo(\n        env::var("K").unwrap_or_default(),\n'
        "    ); // ADR-0569-EXCEPTION(ABS): about foo\n}\n",
    )


def test_a_comment_ending_in_an_open_paren_does_not_hide_the_next_read(tmp_path):
    _refused(
        tmp_path,
        'fn f() {\n    // see foo(\n    let v = env::var("K").unwrap_or_default();\n}\n',
    )
