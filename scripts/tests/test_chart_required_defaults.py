"""YAADGAAR 332 (ADR-0705, ADR-1143). A gated chart key carries no default.

`hooks/chart_required_defaults.py` derives the GATED keys of a chart (ruling 1 of
yaadgaar 327, narrowed by ADR-1143): (a) every key a template passes to `required`
that the REQUIRED list does not classify DEFAULT-OK, (b) every key a
`values.schema.json` block lists in `required`, (c) every key under a path segment
named `tls`. A gated key must be absent from `chart/values.yaml`, or null. Today's
violations live in `chart/ci/required-defaults-baseline.txt`, and that file only
shrinks.

EVERY GREEN HERE HAS A RED ON THE SAME SHAPE, and every case asserts the exit code
(the `test_chart_baseline.py` discipline). The parser cases carry the estate's own
false-positive traps: `(list "off" "optional" "required")` in the parent chart's
`_validate.tpl`, and `required: {{ .Values.database.instance.tls.required }}` in
the three -db `mariadb.yaml` files.

Tests: python3 -m pytest scripts/tests/test_chart_required_defaults.py -q
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
GATE = REPO / "hooks" / "chart_required_defaults.py"
HOOK_ID = "chart-required-defaults"

sys.path.insert(0, str(GATE.parent))
import chart_required_defaults as gate  # noqa: E402

sys.path.pop(0)


# ------------------------------------------------------------------ fixtures


def chart(root: Path, values: str, schema=None, templates=None, baseline=None, name="c") -> Path:
    # `name: c` is in no REQUIRED list, so arm (a) fails closed there: every
    # template-required key is gated, as before ADR-1143. A test of the narrowed arm
    # names a real chart.
    directory = root / "chart"
    (directory / "templates").mkdir(parents=True, exist_ok=True)
    (directory / "Chart.yaml").write_text(f"apiVersion: v2\nname: {name}\nversion: 0.1.0\n")
    (directory / "values.yaml").write_text(values)
    if schema is not None:
        (directory / "values.schema.json").write_text(json.dumps(schema))
    for name, text in (templates or {}).items():
        (directory / "templates" / name).write_text(text)
    if baseline is not None:
        (directory / "ci").mkdir(exist_ok=True)
        (directory / "ci" / "required-defaults-baseline.txt").write_text(baseline)
    return directory


def clean_env(**extra):
    # Strip `GIT_*` (hermetic-git, ledger 1131): under `git commit` the outer
    # GIT_INDEX_FILE would aim these fixture repositories at the real index.
    # Strip `GITHUB_*` so a CI runner's event never decides a local fixture's base.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") and not k.startswith("GITHUB_")}
    env.update(extra)
    return env


def run(root: Path, *arguments, env=None):
    return subprocess.run(
        [sys.executable, str(GATE), "--root", str(root), *arguments],
        capture_output=True,
        text=True,
        env=env or clean_env(),
        cwd=root,
    )


def git(root: Path, *arguments) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
        env=clean_env(
            GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
            GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null",
        ),
    ).stdout.strip()


def commit(root: Path, message: str = "c") -> str:
    git(root, "add", "-A")
    git(root, "commit", "-q", "--no-gpg-sign", "-m", message)
    return git(root, "rev-parse", "HEAD")


ADOPTED_CONFIG = (
    "repos:\n  - repo: https://github.com/yadgarhq/actions\n    rev: v9.9.9\n"
    "    hooks:\n      - id: chart-required-defaults\n"
)
UNADOPTED_CONFIG = (
    "repos:\n  - repo: https://github.com/yadgarhq/actions\n    rev: v9.9.9\n"
    "    hooks:\n      - id: helm-lint\n"
)

TLS_VALUES = "tls:\n  enabled: true\n  caSecret: ca\n"
TLS_BASELINE = (
    "tls.caSecret  # ADR-1140 R-a: trust anchor, consumers set it first\n"
    "tls.enabled  # ADR-1140 OK: ADR-0845 switch, default removed after consumers\n"
)


# ------------------------------------------------------- (a) template parser


def paths(source: str):
    found, refused = gate.template_required(source, "t.yaml")
    return sorted(found), refused


@pytest.mark.parametrize(
    "source, expected",
    [
        ('{{ required "m" .Values.a.b }}', ["a.b"]),
        ('{{ required "m" $.Values.a.b | quote }}', ["a.b"]),
        ('{{ required "m" (index .Values "a" "b") }}', ["a.b"]),
        ('{{ required "m" (index $.Values "a" "b-c") }}', ["a.b-c"]),
        ('{{ .Values.a.b | required "m" | quote }}', ["a.b"]),
        ('{{- dict "name" (required "m" .Values.x.y) | toYaml }}', ["x.y"]),
        ('{{ required "a }} b \\" c" .Values.z }}', ["z"]),
        ('{{- $t := .Values.tls }}\n{{ required "m" $t.caSecret }}', ["tls.caSecret"]),
        ('{{- $t := $.Values.tls }}\n{{ required "m" (get $t "caSecret") }}', ["tls.caSecret"]),
        ('{{- $t := .Values.a }}\n{{ required "m" (index $t "b" "c") }}', ["a.b.c"]),
        ('{{- if (required "m" .Values.on) }}x{{ end }}', ["on"]),
        ('{{- $v := required "m" .Values.k }}', ["k"]),
        ('{{ required (printf "%s" "m") .Values.p }}', ["p"]),
    ],
)
def test_a_required_call_resolves_to_its_dotted_key(source, expected):
    found, refused = paths(source)
    assert refused == []
    assert found == expected


@pytest.mark.parametrize(
    "source",
    [
        # The estate's own traps: a string literal, a field named `required`, a comment.
        '{{- if (has $tls.clientAuth (list "off" "optional" "required")) }}x{{ end }}',
        "    required: {{ .Values.database.instance.tls.required }}",
        '{{/* required "m" .Values.hidden */}}',
        '{{- /* a comment naming required "m" .Values.hidden */ -}}',
        '{{ quote "required \\"m\\" .Values.hidden" }}',
        "# required .Values.hidden, a YAML comment outside any action",
    ],
)
def test_no_call_to_required_finds_nothing(source):
    assert paths(source) == ([], [])


@pytest.mark.parametrize(
    "source, why",
    [
        ('{{ with .Values.a }}{{ required "m" .b }}{{ end }}', "relative"),
        ('{{- $t := .Values.a }}{{- $t = .Values.b }}{{ required "m" $t.c }}', "assigned 2 times"),
        ('{{- $l := .Values.a | default dict }}{{ required "m" $l.c }}', "not bound"),
        ('{{ required "m" (dig "a" "" .Values.b) }}', "dig"),
        ('{{ required $message $node }}', "not bound"),
        ('{{ required "only a message" }}', "1 argument"),
        ('{{ required "m" .Values }}', "the whole values tree"),
        ('{{ required "m" (index .Values $k) }}', "not a string literal"),
    ],
)
def test_a_form_the_parser_cannot_resolve_is_refused_by_name(source, why):
    found, refused = paths(source)
    assert found == []
    assert len(refused) == 1
    # The reason, not the echoed form: the form itself would contain the word.
    assert why in refused[0].split(" in `")[0]


def test_an_unclosed_action_is_refused_not_skipped():
    found, refused = paths('{{ required "m" .Values.a ')
    assert found == [] and refused and "unclosed" in refused[0]


# ---------------------------------------------------------- (b) schema walk


def schema_paths(schema):
    found, refused = gate.schema_required(schema)
    return sorted(found), refused


def test_schema_required_lists_are_dotted_from_the_root():
    schema = {
        "required": ["server"],
        "properties": {
            "server": {"required": ["limits"], "properties": {"limits": {"required": ["a", "b"]}}},
            # A PROPERTY named `required` is a key, not a keyword.
            "tls": {"properties": {"required": {"description": "x"}}},
        },
    }
    assert schema_paths(schema) == (["server", "server.limits", "server.limits.a", "server.limits.b"], [])


def test_required_under_if_items_or_contains_gates_no_values_key():
    # seeder's registry shape: the `if` arm is a condition, and `contains` and
    # `items` speak of array elements, which have no dotted path.
    schema = {
        "properties": {
            "registry": {
                "if": {"required": ["teams"]},
                "then": {"properties": {"teams": {"contains": {"required": ["name"]}}}},
                "properties": {"roots": {"items": {"required": ["path"]}}},
            }
        }
    }
    assert schema_paths(schema) == ([], [])


@pytest.mark.parametrize("keyword", ["allOf", "anyOf", "oneOf"])
def test_required_under_a_combinator_is_refused(keyword):
    schema = {"properties": {"a": {keyword: [{"required": ["b"]}]}}}
    found, refused = schema_paths(schema)
    assert found == [] and len(refused) == 1 and keyword in refused[0]


def test_required_directly_under_then_is_refused():
    schema = {"properties": {"a": {"if": {}, "then": {"required": ["b"]}}}}
    found, refused = schema_paths(schema)
    assert found == [] and "then" in refused[0]


def test_a_combinator_with_no_required_inside_is_not_refused():
    # gateway's `sourceAddress.trustedProxyHops` anyOf of types.
    schema = {"properties": {"h": {"anyOf": [{"type": "null"}, {"type": "integer"}]}}}
    assert schema_paths(schema) == ([], [])


def test_a_ref_is_refused():
    found, refused = schema_paths({"properties": {"a": {"$ref": "#/$defs/x"}}})
    assert found == [] and "$ref" in refused[0]


# ---------------------------------------------------- the rule, on real trees


def test_a_template_required_key_with_a_default_is_red(tmp_path):
    chart(tmp_path, "a:\n  b: x\n", templates={"t.yaml": '{{ required "m" .Values.a.b }}\n'})
    result = run(tmp_path)
    assert result.returncode == 1, result.stdout
    assert "NEW_DEFAULT" in result.stdout and "a.b" in result.stdout and "(a)" in result.stdout


@pytest.mark.parametrize("values", ["a:\n  c: 1\n", "a:\n  b:\n", "a:\n  b: null\n", "a:\n  b: ~\n", 'a:\n  b: ""\n'])
def test_a_template_required_key_absent_null_or_empty_is_green(tmp_path, values):
    # Measured on helm 4.3.0: `required` refuses nil and "", and passes 0 and false.
    chart(tmp_path, values, templates={"t.yaml": '{{ required "m" .Values.a.b }}\n'})
    result = run(tmp_path)
    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize("value", ["0", "false"])
def test_a_template_required_key_defaulted_to_zero_or_false_is_red(tmp_path, value):
    chart(tmp_path, f"a:\n  b: {value}\n", templates={"t.yaml": '{{ required "m" .Values.a.b }}\n'})
    assert run(tmp_path).returncode == 1


def test_a_schema_required_key_with_a_default_is_red(tmp_path):
    schema = {"properties": {"a": {"required": ["b"]}}}
    chart(tmp_path, "a:\n  b: 16\n", schema=schema)
    result = run(tmp_path)
    assert result.returncode == 1 and "a.b" in result.stdout and "(b)" in result.stdout


def test_a_schema_required_key_absent_is_green(tmp_path):
    chart(tmp_path, "a:\n  c: 16\n", schema={"properties": {"a": {"required": ["b"]}}})
    assert run(tmp_path).returncode == 0


def test_an_empty_string_satisfies_the_schema_so_it_is_a_default_there(tmp_path):
    chart(tmp_path, 'a:\n  b: ""\n', schema={"properties": {"a": {"required": ["b"]}}})
    assert run(tmp_path).returncode == 1


@pytest.mark.parametrize("values, key", [("tls:\n  caSecret: ca\n", "tls.caSecret"), ("db:\n  tls:\n    mode: x\n", "db.tls.mode")])
def test_a_key_under_tls_with_a_default_is_red(tmp_path, values, key):
    chart(tmp_path, values)
    result = run(tmp_path)
    assert result.returncode == 1 and key in result.stdout and "(c)" in result.stdout


@pytest.mark.parametrize(
    "values",
    [
        "tls:\n  caSecret:\n",  # null
        "tlsMode: strict\n",  # a segment that merely starts with tls
        "db:\n  notls: 1\n",
        "tls: false\n",  # the tls key itself is not under tls
    ],
)
def test_a_key_not_under_tls_or_null_under_it_is_green(tmp_path, values):
    chart(tmp_path, values)
    assert run(tmp_path).returncode == 0, run(tmp_path).stdout


def test_an_empty_string_under_tls_is_a_default(tmp_path):
    chart(tmp_path, 'tls:\n  domain: ""\n')
    assert run(tmp_path).returncode == 1


def test_a_gated_mapping_is_a_default_when_a_leaf_under_it_is(tmp_path):
    schema = {"required": ["server"]}
    chart(tmp_path, "server:\n  limits:\n    a: 1\n", schema=schema)
    result = run(tmp_path)
    assert result.returncode == 1 and "server" in result.stdout


def test_a_gated_mapping_of_null_leaves_is_a_default(tmp_path):
    # yaadgaar 574. Measured on helm 4.3.0 and 3.18.4: a present mapping satisfies a
    # schema `required` and a template `required` even when every leaf under it is
    # null (it renders `{"limits":{}}`), so the mapping itself is the default.
    chart(tmp_path, "server:\n  limits:\n    a:\n", schema={"required": ["server"]})
    result = run(tmp_path)
    assert result.returncode == 1 and "NEW_DEFAULT: `server` (b)" in result.stdout, result.stdout


def test_a_gated_sequence_is_a_default(tmp_path):
    chart(tmp_path, "a:\n  b:\n    - x\n", schema={"properties": {"a": {"required": ["b"]}}})
    assert run(tmp_path).returncode == 1


def test_a_gated_key_below_an_unread_flow_collection_is_refused(tmp_path):
    chart(tmp_path, "a: {b: 1}\n", schema={"properties": {"a": {"required": ["b"]}}})
    result = run(tmp_path)
    assert result.returncode == 1 and "UNMODELLED_VALUES" in result.stdout


def test_an_empty_values_document_is_read_as_an_empty_mapping(tmp_path):
    # yadgarhq/config ships `values.yaml` as literally `{}`.
    chart(tmp_path, "# comment\n{}\n")
    assert run(tmp_path).returncode == 0


def test_a_values_file_the_scanner_cannot_read_is_refused(tmp_path):
    chart(tmp_path, "a: &x 1\nb: *x\n%TAG\n")
    result = run(tmp_path)
    assert result.returncode == 1 and "UNMODELLED_VALUES" in result.stdout


def test_an_unresolvable_required_form_reddens_the_chart(tmp_path):
    chart(tmp_path, "a: {}\n", templates={"t.yaml": '{{ with .Values.a }}{{ required "m" .b }}{{ end }}\n'})
    result = run(tmp_path)
    assert result.returncode == 1 and "UNRESOLVED_REQUIRED" in result.stdout and "t.yaml:1" in result.stdout


def test_an_unmodelled_schema_reddens_the_chart(tmp_path):
    chart(tmp_path, "a: 1\n", schema={"allOf": [{"required": ["a"]}]})
    result = run(tmp_path)
    assert result.returncode == 1 and "UNMODELLED_SCHEMA" in result.stdout


def test_a_tree_with_no_chart_is_red(tmp_path):
    result = run(tmp_path)
    assert result.returncode == 1 and "NO_CHART" in result.stdout


def test_a_vendored_subchart_is_not_judged(tmp_path):
    chart(tmp_path, "a: 1\n")
    sub = tmp_path / "chart" / "charts" / "sub"
    sub.mkdir(parents=True)
    (sub / "Chart.yaml").write_text("apiVersion: v2\nname: s\nversion: 0.1.0\n")
    (sub / "values.yaml").write_text("tls:\n  x: 1\n")
    assert run(tmp_path).returncode == 0


def test_the_success_line_counts_what_it_judged(tmp_path):
    chart(tmp_path, TLS_VALUES, templates={"t.yaml": '{{ required "m" .Values.tls.caSecret }}\n'}, baseline=TLS_BASELINE)
    result = run(tmp_path)
    assert result.returncode == 0, result.stdout
    assert "1 chart" in result.stdout and "2 gated keys" in result.stdout and "2 baselined" in result.stdout


# ----------------------------------------------------------------- the ratchet


def test_a_baselined_violation_is_green(tmp_path):
    chart(tmp_path, TLS_VALUES, baseline=TLS_BASELINE)
    result = run(tmp_path)
    assert result.returncode == 0, result.stdout


def test_a_violation_missing_from_the_baseline_is_red(tmp_path):
    chart(tmp_path, TLS_VALUES + "  domain: d\n", baseline=TLS_BASELINE)
    result = run(tmp_path)
    assert result.returncode == 1 and "NEW_DEFAULT" in result.stdout and "tls.domain" in result.stdout


def test_a_stale_baseline_entry_is_red(tmp_path):
    chart(tmp_path, "tls:\n  enabled: true\n", baseline=TLS_BASELINE)
    result = run(tmp_path)
    assert result.returncode == 1 and "STALE_BASELINE" in result.stdout and "tls.caSecret" in result.stdout


@pytest.mark.parametrize(
    "text, why",
    [
        ("tls.caSecret\n", "ADR-1140"),
        ("tls.caSecret  # ADR-1140 R-a:\n", "reason"),
        ("tls.caSecret  # ADR-1140 R-a: x\ntls.caSecret  # ADR-1140 R-a: y\n", "twice"),
        ("tls caSecret  # ADR-1140 R-a: x\n", "dotted key"),
    ],
)
def test_a_malformed_baseline_is_refused_by_name(tmp_path, text, why):
    chart(tmp_path, "tls:\n  caSecret: ca\n", baseline=text)
    result = run(tmp_path)
    assert result.returncode == 1 and "BASELINE_MALFORMED" in result.stdout and why in result.stdout


def adopted_repo(tmp_path, base_baseline, config=ADOPTED_CONFIG):
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / ".pre-commit-config.yaml").write_text(config)
    chart(tmp_path, TLS_VALUES, baseline=base_baseline)
    return commit(tmp_path, "base")


def test_a_baseline_that_grows_against_an_adopted_base_is_red(tmp_path):
    base = adopted_repo(tmp_path, "tls.caSecret  # ADR-1140 R-a: x\n")
    (tmp_path / "chart" / "ci" / "required-defaults-baseline.txt").write_text(TLS_BASELINE)
    commit(tmp_path, "head")
    result = run(tmp_path, "--base", base)
    assert result.returncode == 1 and "BASELINE_GREW" in result.stdout and "tls.enabled" in result.stdout


def test_a_baseline_that_shrinks_against_an_adopted_base_is_green(tmp_path):
    base = adopted_repo(tmp_path, TLS_BASELINE)
    chart(tmp_path, "tls:\n  enabled: true\n", baseline="tls.enabled  # ADR-1140 OK: x\n")
    commit(tmp_path, "head")
    assert run(tmp_path, "--base", base).returncode == 0


def test_the_adopting_change_may_add_the_whole_baseline(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / ".pre-commit-config.yaml").write_text(UNADOPTED_CONFIG)
    chart(tmp_path, TLS_VALUES)
    base = commit(tmp_path, "base")
    (tmp_path / ".pre-commit-config.yaml").write_text(ADOPTED_CONFIG)
    chart(tmp_path, TLS_VALUES, baseline=TLS_BASELINE)
    commit(tmp_path, "adopt")
    result = run(tmp_path, "--base", base)
    assert result.returncode == 0, result.stdout
    assert "adopts" in result.stdout


def test_an_unreadable_base_is_refused_not_skipped(tmp_path):
    adopted_repo(tmp_path, TLS_BASELINE)
    result = run(tmp_path, "--base", "0" * 40)
    assert result.returncode == 1 and "BASE_UNREADABLE" in result.stdout


def test_ci_reads_the_base_from_the_pull_request_event(tmp_path):
    base = adopted_repo(tmp_path, "tls.caSecret  # ADR-1140 R-a: x\n")
    (tmp_path / "chart" / "ci" / "required-defaults-baseline.txt").write_text(TLS_BASELINE)
    commit(tmp_path, "head")
    event = tmp_path.parent / f"{tmp_path.name}-event.json"
    event.write_text(json.dumps({"pull_request": {"base": {"sha": base}}}))
    env = clean_env(GITHUB_ACTIONS="true", GITHUB_EVENT_PATH=str(event))
    result = run(tmp_path, env=env)
    assert result.returncode == 1 and "BASELINE_GREW" in result.stdout


def test_ci_with_no_pull_request_and_no_parent_is_refused(tmp_path):
    adopted_repo(tmp_path, TLS_BASELINE)
    event = tmp_path.parent / f"{tmp_path.name}-event.json"
    event.write_text(json.dumps({}))
    env = clean_env(GITHUB_ACTIONS="true", GITHUB_EVENT_PATH=str(event))
    result = run(tmp_path, env=env)
    assert result.returncode == 1 and "BASE_UNREADABLE" in result.stdout


def test_a_local_run_compares_the_working_tree_against_head(tmp_path):
    adopted_repo(tmp_path, "tls.caSecret  # ADR-1140 R-a: x\n")
    (tmp_path / "chart" / "ci" / "required-defaults-baseline.txt").write_text(TLS_BASELINE)
    result = run(tmp_path)
    assert result.returncode == 1 and "BASELINE_GREW" in result.stdout


def test_outside_a_git_repository_there_is_no_base_and_no_growth_check(tmp_path):
    # Every tree test above runs here; the growth half needs history, and a tree
    # with none has no base to grow from. The success line says the half was skipped.
    chart(tmp_path, TLS_VALUES, baseline=TLS_BASELINE)
    result = run(tmp_path)
    assert result.returncode == 0 and "no git history" in result.stdout


def test_report_prints_a_baseline_line_per_violation_and_exits_zero(tmp_path):
    chart(tmp_path, TLS_VALUES, templates={"t.yaml": '{{ required "m" .Values.x }}\n'})
    result = run(tmp_path, "--report")
    assert result.returncode == 0, result.stdout
    assert "tls.caSecret\t(c)" in result.stdout and "tls.enabled\t(c)" in result.stdout


# ---------------------------------------------------------- publication facts


def test_the_hook_is_published_and_judges_the_whole_tree():
    hooks = {h["id"]: h for h in yaml.safe_load((REPO / ".pre-commit-hooks.yaml").read_text())}
    hook = hooks[HOOK_ID]
    assert hook["entry"] == "hooks/chart_required_defaults.py"
    assert hook["language"] == "script"
    assert hook["always_run"] is True and hook["pass_filenames"] is False


def test_no_class_adopts_the_hook_yet():
    # Merging the gate must not redden a consumer: adoption lands with each
    # consumer's baseline, in that consumer's own pull request.
    classes = json.loads((REPO / "classes.json").read_text())
    for name, cls in classes["classes"].items():
        assert HOOK_ID not in [h["id"] for h in cls["hooks"]], name


def test_the_hook_is_standard_library_only():
    source = GATE.read_text()
    assert "import yaml" not in source


# ------------------------------------------- review of actions#175: closing holes


@pytest.mark.parametrize("values", ["mtls:\n  ca: x\n", "tlsFoo:\n  ca: x\n", "a:\n  mtls:\n    ca: x\n"])
def test_a_segment_that_only_contains_tls_is_not_gated(tmp_path, values):
    chart(tmp_path, values)
    result = run(tmp_path)
    assert result.returncode == 0, result.stdout


def test_tls_at_depth_is_still_gated(tmp_path):
    chart(tmp_path, "a:\n  b:\n    tls:\n      ca: x\n")
    result = run(tmp_path)
    assert result.returncode == 1 and "a.b.tls.ca" in result.stdout


def test_ci_with_an_event_base_missing_from_the_checkout_is_refused(tmp_path):
    adopted_repo(tmp_path, TLS_BASELINE)
    event = tmp_path.parent / f"{tmp_path.name}-event.json"
    event.write_text(json.dumps({"pull_request": {"base": {"sha": "f" * 40}}}))
    env = clean_env(GITHUB_ACTIONS="true", GITHUB_EVENT_PATH=str(event))
    result = run(tmp_path, env=env)
    assert result.returncode == 1 and "BASE_UNREADABLE" in result.stdout and "f" * 40 in result.stdout


@pytest.mark.parametrize("values", ["server:\n  a:\n  b: 1\n", "server:\n  a: null\n  b: 1\n"])
def test_a_gated_mapping_with_one_null_and_one_defaulted_leaf_is_red(tmp_path, values):
    chart(tmp_path, values, schema={"required": ["server"]})
    result = run(tmp_path)
    assert result.returncode == 1 and "`server`" in result.stdout


@pytest.mark.parametrize(
    "schema, word",
    [
        ({"properties": {"a": {"dependencies": {"b": {"required": ["c"]}}}}}, "dependencies"),
        ({"properties": {"a": {"dependencies": {"b": ["c"]}}}}, "dependencies"),
        ({"properties": {"a": {"$dynamicRef": "#x"}}}, "$dynamicRef"),
        ({"properties": {"a": {"$recursiveRef": "#"}}}, "$recursiveRef"),
        ({"properties": {"a": {"x-unknown": {"required": ["b"]}}}}, "x-unknown"),
        ({"properties": {"a": {"required": True}}}, "required"),
    ],
)
def test_a_schema_keyword_the_walk_does_not_model_is_refused(schema, word):
    found, refused = schema_paths(schema)
    assert found == [] and len(refused) == 1 and word in refused[0]


def test_the_annotation_and_validation_keywords_are_modelled():
    schema = {
        "$schema": "x", "$id": "x", "$comment": "x", "title": "t", "description": "d",
        "type": "object", "additionalProperties": False,
        "properties": {"a": {"type": "integer", "minimum": 0, "maximum": 9, "default": 1, "enum": [1],
                             "const": 1, "examples": [1], "pattern": "x", "format": "x", "minLength": 1,
                             "maxLength": 2, "multipleOf": 1, "exclusiveMinimum": 0, "exclusiveMaximum": 9,
                             "deprecated": False, "readOnly": False, "writeOnly": False},
                       "l": {"type": "array", "minItems": 1, "maxItems": 2, "uniqueItems": True, "items": {"type": "string"}}},
    }
    assert schema_paths(schema) == ([], [])


HELPER = '{{- define "h" -}}{{ required "m" .Values.a }}{{- end -}}\n'


@pytest.mark.parametrize(
    "call", ['{{ include "h" . }}', '{{ include "h" $ }}', '{{ template "h" . }}', "{{ include $name $ }}"]
)
def test_a_helper_that_calls_required_is_resolved_under_the_root_context(tmp_path, call):
    chart(tmp_path, "b: 1\n", templates={"_h.tpl": HELPER, "t.yaml": call + "\n"})
    result = run(tmp_path)
    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize(
    "call",
    [
        '{{ include "h" (dict "context" $) }}',
        '{{ template "h" .Values }}',
        '{{ range .Values.list }}{{ include "h" . }}{{ end }}',
        '{{ include "h" }}',
        '{{ include $name (dict) }}',
        '{{ block "h" .Values }}{{ end }}',
    ],
)
def test_a_helper_that_calls_required_under_another_context_is_refused(tmp_path, call):
    chart(tmp_path, "b: 1\n", templates={"_h.tpl": HELPER, "t.yaml": call + "\n"})
    result = run(tmp_path)
    assert result.returncode == 1 and "UNRESOLVED_REQUIRED" in result.stdout and "t.yaml:1" in result.stdout


def test_a_helper_that_reaches_required_through_another_is_refused_too(tmp_path):
    outer = '{{- define "g" -}}{{ include "h" . }}{{- end -}}\n'
    chart(tmp_path, "b: 1\n", templates={"_h.tpl": HELPER + outer, "t.yaml": '{{ include "g" (dict) }}\n'})
    result = run(tmp_path)
    assert result.returncode == 1 and "UNRESOLVED_REQUIRED" in result.stdout and '"g"' in result.stdout


def test_a_helper_with_no_required_takes_any_context(tmp_path):
    helper = '{{- define "n" -}}{{ .x }}{{- end -}}\n'
    chart(tmp_path, "b: 1\n", templates={"_h.tpl": helper, "t.yaml": '{{ include "n" (dict "x" 1) }}\n'})
    assert run(tmp_path).returncode == 0


def test_tpl_of_a_literal_that_calls_required_is_refused():
    found, refused = paths('{{ tpl "{{ required \\"m\\" .Values.a }}" . }}')
    assert found == [] and len(refused) == 1 and "tpl" in refused[0].split(" in `")[0]


def test_tpl_of_a_values_string_is_not_a_required_call():
    assert paths("{{ tpl .Values.t . }}") == ([], [])


def test_a_template_file_of_any_suffix_is_scanned(tmp_path):
    chart(tmp_path, "a: 1\n", templates={"x.json": '{{ required "m" .Values.a }}\n'})
    result = run(tmp_path)
    assert result.returncode == 1 and "NEW_DEFAULT" in result.stdout and "`a`" in result.stdout


def test_a_template_file_that_is_not_text_is_refused_not_skipped(tmp_path):
    directory = chart(tmp_path, "b: 1\n")
    (directory / "templates" / "blob.bin").write_bytes(b"\xff\xfe\x00{{")
    result = run(tmp_path)
    assert result.returncode == 1 and "UNSCANNED_TEMPLATE" in result.stdout and "blob.bin" in result.stdout


# ---------------------------------------- delta review of actions#175: fail-opens


X_HELPER = '{{- define "x" -}}{{ required "m" .Values.a }}{{- end -}}\n'


def test_a_dynamic_include_inside_a_define_rides_on_the_callers_context(tmp_path):
    # The reviewer's probe: `.` inside "d" is d's argument, not the chart root.
    d = '{{- define "d" -}}{{ include (printf "%s" "x") . }}{{- end -}}\n'
    chart(tmp_path, "b: 1\n", templates={"_h.tpl": X_HELPER + d, "t.yaml": '{{ include "d" .Values.sub }}\n'})
    result = run(tmp_path)
    assert result.returncode == 1 and "UNRESOLVED_REQUIRED" in result.stdout and '"d"' in result.stdout


def test_a_dollar_inside_a_define_is_the_defines_argument(tmp_path):
    d = '{{- define "d" -}}{{ include "x" $ }}{{- end -}}\n'
    chart(tmp_path, "b: 1\n", templates={"_h.tpl": X_HELPER + d, "t.yaml": '{{ include "d" (dict) }}\n'})
    result = run(tmp_path)
    assert result.returncode == 1 and '"d"' in result.stdout


def test_a_define_handing_its_own_context_on_is_green_when_called_from_the_root(tmp_path):
    d = '{{- define "d" -}}{{ include (printf "%s" "x") . }}{{ include "x" $ }}{{- end -}}\n'
    chart(tmp_path, "b: 1\n", templates={"_h.tpl": X_HELPER + d, "t.yaml": '{{ include "d" . }}\n'})
    result = run(tmp_path)
    assert result.returncode == 0, result.stdout


def test_a_top_level_dynamic_include_under_the_root_stays_accepted(tmp_path):
    # platform `render-checks.yaml:1929`: `include (index $source 1) $`.
    chart(tmp_path, "b: 1\n", templates={"_h.tpl": X_HELPER, "t.yaml": '{{ include (index $s 1) $ }}\n'})
    assert run(tmp_path).returncode == 0


@pytest.mark.parametrize("branch", ["else with", "else range"])
def test_else_with_rebinds_dot(tmp_path, branch):
    call = f'{{{{ if .Values.q }}}}{{{{ {branch} .Values.sub }}}}{{{{ include "x" . }}}}{{{{ end }}}}\n'
    chart(tmp_path, "b: 1\n", templates={"_h.tpl": X_HELPER, "t.yaml": call})
    result = run(tmp_path)
    assert result.returncode == 1 and "UNRESOLVED_REQUIRED" in result.stdout


def test_a_plain_else_after_if_keeps_dot_at_the_root(tmp_path):
    call = '{{ if .Values.q }}{{ else }}{{ include "x" . }}{{ end }}\n'
    chart(tmp_path, "b: 1\n", templates={"_h.tpl": X_HELPER, "t.yaml": call})
    assert run(tmp_path).returncode == 0


@pytest.mark.parametrize(
    "source",
    [
        '{{ "{{ required \\"m\\" .Values.a }}" | tpl . }}',
        '{{ tpl (printf "%s" "x") . }}',
        "{{ tpl $t . }}",
        "{{ $t | tpl . }}",
        "{{ tpl }}",
    ],
)
def test_a_tpl_whose_template_is_not_a_readable_literal_is_refused(source):
    found, refused = paths(source)
    assert found == [] and len(refused) == 1 and "tpl" in refused[0].split(" in `")[0]


def test_a_tpl_of_a_literal_with_no_required_is_accepted():
    assert paths('{{ tpl "hello {{ .Values.x }}" . }}') == ([], [])


@pytest.mark.parametrize(
    "values, red",
    [
        ('t: "{{ required \\"m\\" .Values.a }}"\n', True),
        ("t: |\n  {{ .Values.a }}\n", True),  # a block scalar the scan does not read
        ("t: hello\n", False),
        ("other: 1\n", False),
    ],
)
def test_tpl_of_a_values_string_is_judged_against_values_yaml(tmp_path, values, red):
    chart(tmp_path, values, templates={"t.yaml": "{{ tpl .Values.t . }}\n"})
    result = run(tmp_path)
    assert (result.returncode == 1) is red, result.stdout
    if red:
        assert "UNRESOLVED_REQUIRED" in result.stdout and "`t`" in result.stdout


# ------------------------- ADR-1143 (yaadgaar 571): arm (a) reads the REQUIRED list
#
# Arm (a) refuses a default only when the key is on the ADR-0705 REQUIRED list in
# `hooks/data/required-knobs.json`. A template `required` on a key the list
# classifies DEFAULT-OK is the refuse-empty idiom, and its default is allowed. Every
# key the list does not classify DEFAULT-OK by exact match counts as REQUIRED: an
# unknown chart, an unclassified key, a malformed or missing list (fail closed).
# Arms (b) and (c) do not read the list.


def required_on(key: str) -> str:
    return '{{ required "m" .Values.' + key + " }}\n"


def test_a_template_required_default_ok_key_keeps_its_default(tmp_path):
    # gateway `nats.user` is DEFAULT-OK in the list.
    chart(tmp_path, "nats:\n  user: gateway\n", templates={"t.yaml": required_on("nats.user")}, name="gateway")
    result = run(tmp_path)
    assert result.returncode == 0, result.stdout
    assert "1 template-required key exempt as DEFAULT-OK" in result.stdout


def test_a_template_required_key_on_the_required_list_with_a_default_is_red(tmp_path):
    # gateway `projectValidation.mode` is R-a.
    chart(tmp_path, "projectValidation:\n  mode: counting\n", templates={"t.yaml": required_on("projectValidation.mode")}, name="gateway")
    result = run(tmp_path)
    assert result.returncode == 1, result.stdout
    assert "NEW_DEFAULT: `projectValidation.mode` (a)" in result.stdout


def test_a_template_required_key_the_list_does_not_classify_counts_as_required(tmp_path):
    chart(tmp_path, "brandNew:\n  knob: x\n", templates={"t.yaml": required_on("brandNew.knob")}, name="gateway")
    result = run(tmp_path)
    assert result.returncode == 1, result.stdout
    assert "NEW_DEFAULT: `brandNew.knob` (a)" in result.stdout


@pytest.mark.parametrize("key, values", [("nats", "nats:\n  user: gateway\n"), ("nats.user.x", "nats:\n  user:\n    x: 1\n")])
def test_an_ancestor_or_descendant_of_a_default_ok_row_counts_as_required(tmp_path, key, values):
    chart(tmp_path, values, templates={"t.yaml": required_on(key)}, name="gateway")
    result = run(tmp_path)
    assert result.returncode == 1, result.stdout
    assert f"`{key}` (a)" in result.stdout


def test_a_chart_the_list_does_not_name_counts_every_key_as_required(tmp_path):
    chart(tmp_path, "nats:\n  user: gateway\n", templates={"t.yaml": required_on("nats.user")}, name="not-in-the-list")
    result = run(tmp_path)
    assert result.returncode == 1 and "`nats.user` (a)" in result.stdout, result.stdout


@pytest.mark.parametrize("operand", ['(index .Values "nats.user")', '(index .Values "nats" "user.x")', '(get .Values "nats.user")'])
def test_an_index_literal_that_is_not_a_plain_name_is_never_exempt(tmp_path, operand):
    # `index .Values "nats.user"` reads a top-level key literally named `nats.user`,
    # not the DEFAULT-OK knob `nats` -> `user`. The joined path cannot be matched to
    # the list, so it counts as REQUIRED.
    key = "nats.user" if operand.endswith('"nats.user")') else "nats.user.x"
    values = "nats:\n  user: gateway\n" if key == "nats.user" else "nats:\n  user:\n    x: 1\n"
    chart(tmp_path, values, templates={"t.yaml": '{{ required "m" ' + operand + " }}\n"}, name="gateway")
    result = run(tmp_path)
    assert result.returncode == 1, result.stdout
    assert f"`{key}` (a)" in result.stdout, result.stdout


def test_an_index_of_plain_names_matches_the_list(tmp_path):
    chart(tmp_path, "nats:\n  user: gateway\n", templates={"t.yaml": '{{ required "m" (index .Values "nats" "user") }}\n'}, name="gateway")
    assert run(tmp_path).returncode == 0


def test_a_quoted_chart_name_is_read(tmp_path):
    directory = chart(tmp_path, "nats:\n  user: gateway\n", templates={"t.yaml": required_on("nats.user")})
    (directory / "Chart.yaml").write_text('apiVersion: v2\nname: "gateway"  # the gateway\nversion: 0.1.0\n')
    assert run(tmp_path).returncode == 0


def test_a_chart_yaml_with_two_top_level_names_is_an_unknown_chart(tmp_path):
    # Two `name:` lines are not one chart identity: neither borrows its exemptions.
    directory = chart(tmp_path, "nats:\n  user: gateway\n", templates={"t.yaml": required_on("nats.user")})
    (directory / "Chart.yaml").write_text("apiVersion: v2\nname: gateway\nversion: 0.1.0\nname: config\n")
    result = run(tmp_path)
    assert result.returncode == 1, result.stdout
    assert "NEW_DEFAULT: `nats.user` (a)" in result.stdout


def test_a_chart_name_differing_only_in_case_is_an_unknown_chart(tmp_path):
    chart(tmp_path, "nats:\n  user: gateway\n", templates={"t.yaml": required_on("nats.user")}, name="Gateway")
    result = run(tmp_path)
    assert result.returncode == 1, result.stdout
    assert "NEW_DEFAULT: `nats.user` (a)" in result.stdout


def test_a_default_ok_key_under_tls_is_still_refused_by_arm_c(tmp_path):
    # gateway `task.tls.caSecretKey` is DEFAULT-OK, template-required and under tls.
    chart(tmp_path, "task:\n  tls:\n    caSecretKey: ca.crt\n", templates={"t.yaml": required_on("task.tls.caSecretKey")}, name="gateway")
    result = run(tmp_path)
    assert result.returncode == 1, result.stdout
    assert "NEW_DEFAULT: `task.tls.caSecretKey` (c)" in result.stdout


def test_a_default_ok_key_the_schema_requires_is_still_refused_by_arm_b(tmp_path):
    schema = {"properties": {"task": {"required": ["requestTimeoutSeconds"]}}}
    chart(tmp_path, "task:\n  requestTimeoutSeconds: 16\n", schema=schema, templates={"t.yaml": required_on("task.requestTimeoutSeconds")}, name="gateway")
    result = run(tmp_path)
    assert result.returncode == 1, result.stdout
    assert "NEW_DEFAULT: `task.requestTimeoutSeconds` (b)" in result.stdout


def test_an_empty_string_on_a_template_required_key_stays_no_default_under_tls(tmp_path):
    # `required` refuses "": that is a fact about helm, not about the list. The shipped
    # gate held such a key green, and narrowing arm (a) must not turn it red under (c).
    chart(tmp_path, 'task:\n  tls:\n    caSecretKey: ""\n', templates={"t.yaml": required_on("task.tls.caSecretKey")}, name="gateway")
    result = run(tmp_path)
    assert result.returncode == 0, result.stdout


def in_process(tmp_path, monkeypatch, capsys, knobs_text):
    path = tmp_path / "knobs.json"
    if knobs_text is not None:
        path.write_text(knobs_text)
    monkeypatch.setattr(gate, "KNOBS", path)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    status = gate.main(["--root", str(tmp_path / "tree")])
    return status, capsys.readouterr().out


GOOD_KNOBS = {"source": {}, "charts": {"gateway": {"required": ["a.b"], "default_ok": ["nats.user"]}}}


@pytest.mark.parametrize(
    "text, why",
    [
        (None, "cannot be read"),
        ("{not json", "not JSON"),
        ("[]", "`charts`"),
        (json.dumps({"charts": []}), "`charts`"),
        (json.dumps({"charts": {"gateway": ["nats.user"]}}), "gateway"),
        (json.dumps({"charts": {"gateway": {"required": [], "default_ok": "nats.user"}}}), "default_ok"),
        (json.dumps({"charts": {"gateway": {"required": [], "default_ok": [1]}}}), "default_ok"),
        (json.dumps({"charts": {"gateway": {"required": [], "default_ok": ["nats.*"]}}}), "nats.*"),
        (json.dumps({"charts": {"gateway": {"required": [], "default_ok": ["a[0]"]}}}), "a[0]"),
        (json.dumps({"charts": {"gateway": {"required": [], "default_ok": ["a", "a"]}}}), "twice"),
        (json.dumps({"charts": {"gateway": {"required": ["a"], "default_ok": ["a"]}}}), "both"),
        (json.dumps({"charts": {"gateway": {"required": []}}}), "default_ok"),
        (json.dumps({"charts": {"gateway": {"required": [], "default_ok": [], "x": []}}}), "x"),
    ],
)
def test_a_malformed_list_is_refused_by_name_and_fails_closed(tmp_path, monkeypatch, capsys, text, why):
    chart(tmp_path / "tree", "nats:\n  user: gateway\n", templates={"t.yaml": required_on("nats.user")}, name="gateway")
    status, out = in_process(tmp_path, monkeypatch, capsys, text)
    assert status == 1, out
    assert "REQUIRED_LIST_MALFORMED" in out and why in out, out
    assert "NEW_DEFAULT: `nats.user` (a)" in out, out


def test_a_malformed_list_is_red_even_with_nothing_template_required(tmp_path, monkeypatch, capsys):
    chart(tmp_path / "tree", "a: 1\n", name="gateway")
    status, out = in_process(tmp_path, monkeypatch, capsys, "{not json")
    assert status == 1 and "REQUIRED_LIST_MALFORMED" in out, out


def test_one_malformed_chart_entry_voids_the_whole_list(tmp_path, monkeypatch, capsys):
    # The gateway entry alone is well formed; the list is not, so nothing is exempt.
    knobs = {"charts": {"gateway": {"required": [], "default_ok": ["nats.user"]}, "iam": {"required": ["x.*"], "default_ok": []}}}
    chart(tmp_path / "tree", "nats:\n  user: gateway\n", templates={"t.yaml": required_on("nats.user")}, name="gateway")
    status, out = in_process(tmp_path, monkeypatch, capsys, json.dumps(knobs))
    assert status == 1 and "REQUIRED_LIST_MALFORMED" in out, out
    assert "NEW_DEFAULT: `nats.user` (a)" in out, out


def test_a_well_formed_list_is_read(tmp_path, monkeypatch, capsys):
    chart(tmp_path / "tree", "nats:\n  user: gateway\n", templates={"t.yaml": required_on("nats.user")}, name="gateway")
    status, out = in_process(tmp_path, monkeypatch, capsys, json.dumps(GOOD_KNOBS))
    assert status == 0, out


def test_report_marks_a_malformed_list(tmp_path, monkeypatch, capsys):
    chart(tmp_path / "tree", "a: 1\n", name="gateway")
    path = tmp_path / "knobs.json"
    path.write_text("{not json")
    monkeypatch.setattr(gate, "KNOBS", path)
    status = gate.main(["--root", str(tmp_path / "tree"), "--report"])
    out = capsys.readouterr().out
    assert status == 0 and "! REQUIRED_LIST_MALFORMED" in out, out


def test_the_list_has_no_command_line_override():
    # A consumer must not be able to aim the gate at a permissive list.
    source = GATE.read_text()
    assert "--required-list" not in source and "os.environ" not in source.split("def load_knobs", 1)[1].split("\ndef ", 1)[0]


def test_the_shipped_list_is_well_formed():
    knobs, errors = gate.load_knobs(gate.KNOBS)
    assert errors == [] and "gateway" in knobs


# ------------------- yaadgaar 574: fail-open spots closed before any class adopts


@pytest.mark.parametrize("values", ["server:\n  limits:\n    a:\n", 'server:\n  limits:\n    a: ""\n', "server:\n  a: null\n  b: ~\n"])
def test_a_template_required_mapping_of_null_or_empty_leaves_is_a_default(tmp_path, values):
    # helm's `required` refuses only nil and "": a mapping present is neither.
    chart(tmp_path, values, templates={"t.yaml": required_on("server")})
    result = run(tmp_path)
    assert result.returncode == 1 and "NEW_DEFAULT: `server` (a)" in result.stdout, result.stdout
    assert "gives it `a mapping`" in result.stdout


def test_a_gated_key_written_null_is_still_no_default(tmp_path):
    # The mapping rule does not touch a key written with nothing under it.
    chart(tmp_path, "server:\n", schema={"required": ["server"]}, templates={"t.yaml": required_on("server")})
    assert run(tmp_path).returncode == 0, run(tmp_path).stdout


INGRESS_TLS = [
    "ingress:\n  tls:\n    - secretName: s\n      hosts:\n        - a.example\n",
    "ingress:\n  tls:\n  - secretName: s\n",
    "ingress:\n  tls: []\n",
    "ingress:\n  tls: [{secretName: s}]  # flow\n",
    "tls:\n  - secretName: s\n",
]


@pytest.mark.parametrize("values", INGRESS_TLS)
def test_a_sequence_at_a_tls_key_is_a_gated_default_not_unmodelled(tmp_path, values):
    # A standard ingress `tls:` list. It is a default at the `tls` path itself (arm
    # (c)), so it can be baselined with a reason; it is never silently green.
    chart(tmp_path, values)
    key = "tls" if values.startswith("tls") else "ingress.tls"
    result = run(tmp_path)
    assert result.returncode == 1, result.stdout
    assert f"NEW_DEFAULT: `{key}` (c)" in result.stdout and "UNMODELLED_VALUES" not in result.stdout, result.stdout


@pytest.mark.parametrize("values", INGRESS_TLS)
def test_a_baselined_sequence_at_a_tls_key_is_green(tmp_path, values):
    key = "tls" if values.startswith("tls") else "ingress.tls"
    chart(tmp_path, values, baseline=f"{key}  # ADR-1140 OK: the ingress terminates no TLS by default\n")
    result = run(tmp_path)
    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize(
    "values",
    [
        "ingress:\n  tls: {secretName: s}\n",
        "ingress:\n  tls: &t x\n",
        "ingress:\n  tls: *t\n",
        "ingress:\n  tls: !!map {}\n",
        "ingress:\n  tls: |\n    x\n",
    ],
)
def test_any_other_unread_value_at_a_tls_key_stays_refused(tmp_path, values):
    chart(tmp_path, values)
    result = run(tmp_path)
    assert result.returncode == 1 and "UNMODELLED_VALUES" in result.stdout, result.stdout


FLOW_CONFIGS = [
    "repos:\n  - repo: https://github.com/yadgarhq/actions\n    rev: v9.9.9\n    hooks: [{id: chart-required-defaults}]\n",
    "repos:\n  - repo: https://github.com/yadgarhq/actions\n    rev: v9.9.9\n    hooks:\n      - {id: helm-lint}\n      - {id: 'chart-required-defaults', exclude: x}\n",
    '{"repos": [{"repo": "x", "rev": "v1", "hooks": [{"id": "chart-required-defaults"}]}]}\n',
]


@pytest.mark.parametrize("config", FLOW_CONFIGS)
def test_a_flow_style_adoption_still_judges_growth(tmp_path, config):
    # The base has NO baseline, so only its adoption makes growth judged here.
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / ".pre-commit-config.yaml").write_text(config)
    chart(tmp_path, TLS_VALUES)
    base = commit(tmp_path, "base")
    chart(tmp_path, TLS_VALUES, baseline=TLS_BASELINE)
    commit(tmp_path, "head")
    result = run(tmp_path, "--base", base)
    assert result.returncode == 1 and "BASELINE_GREW" in result.stdout and "tls.enabled" in result.stdout, result.stdout


def test_a_comment_naming_the_hook_is_not_adoption(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / ".pre-commit-config.yaml").write_text("# chart-required-defaults lands later\n" + UNADOPTED_CONFIG)
    chart(tmp_path, TLS_VALUES)
    base = commit(tmp_path, "base")
    (tmp_path / ".pre-commit-config.yaml").write_text(ADOPTED_CONFIG)
    chart(tmp_path, TLS_VALUES, baseline=TLS_BASELINE)
    commit(tmp_path, "adopt")
    result = run(tmp_path, "--base", base)
    assert result.returncode == 0 and "adopts" in result.stdout, result.stdout


def test_a_baseline_that_grows_before_adoption_is_red_once_the_base_has_one(tmp_path):
    # The base has not adopted the hook but already carries a baseline: growth is
    # judged against it, so the window between a baseline landing and its class
    # naming the hook cannot grow the list unseen by any run of the gate.
    base = adopted_repo(tmp_path, "tls.caSecret  # ADR-1140 R-a: x\n", config=UNADOPTED_CONFIG)
    (tmp_path / "chart" / "ci" / "required-defaults-baseline.txt").write_text(TLS_BASELINE)
    commit(tmp_path, "head")
    result = run(tmp_path, "--base", base)
    assert result.returncode == 1 and "BASELINE_GREW" in result.stdout and "tls.enabled" in result.stdout, result.stdout


@pytest.mark.parametrize("arm, extra", [("b", {"schema": {"properties": {"a": {"required": ["b"]}}}}), ("a", {"templates": {"t.yaml": '{{ required "m" .Values.a.b }}\n'}})])
def test_a_gated_sequence_at_the_same_indent_as_its_key_is_a_default(tmp_path, arm, extra):
    # `b:` then `- x` at b's own indent is a YAML sequence. The values scan reads it
    # as a null `b`, which the shipped gate took for "no default" (yaadgaar 574).
    chart(tmp_path, "a:\n  b:\n  - x\n", **extra)
    result = run(tmp_path)
    assert result.returncode == 1 and f"NEW_DEFAULT: `a.b` ({arm})" in result.stdout, result.stdout


def test_a_dash_shallower_than_a_null_key_does_not_make_it_a_sequence(tmp_path):
    # `b:` is null; the `- x` belongs to `list`, two levels out.
    values = "list:\n  - y\nother:\n  a:\n    b:\n  c: 1\n"
    assert gate.sequence_paths(values) == {"list"}
    assert gate.sequence_paths("a:\n  b:\n- x\n") == set()


def test_a_mapping_of_null_leaves_under_tls_is_judged_by_its_leaves(tmp_path):
    chart(tmp_path, "tls:\n  a:\n    b:\n")
    assert run(tmp_path).returncode == 0, run(tmp_path).stdout


def test_a_gated_key_below_a_same_indent_sequence_is_refused(tmp_path):
    chart(tmp_path, "a:\n- b: 1\n", schema={"properties": {"a": {"required": ["b"]}}})
    result = run(tmp_path)
    assert result.returncode == 1 and "UNMODELLED_VALUES" in result.stdout and "`a.b`" in result.stdout, result.stdout
