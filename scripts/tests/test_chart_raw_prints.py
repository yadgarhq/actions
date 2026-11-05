"""YAADGAAR 557 (ADR-1136, ratifying ADR-1129). A chart prints no values-derived scalar raw.

`hooks/chart_raw_prints.py` reads every template under each chart's `templates/`
and judges each OUTPUT action once, by its last pipeline stage. A print whose
operands reach a values-derived scalar (`.Values`, `$.Values`, a variable bound
from one, `.`/`.field` under a `with`/`range` over one, `.`/`.field` in a define
body) must end in an approved encoder, or sit in `chart/ci/raw-prints-allowlist.txt`
with a reason. An action the gate cannot parse is NOT_MODELLED, red.

EVERY GREEN HERE HAS A RED ON THE SAME SHAPE, and every case asserts the exit
code (the `test_chart_baseline.py` discipline).

Tests: python3 -m pytest scripts/tests/test_chart_raw_prints.py -q
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
GATE = REPO / "hooks" / "chart_raw_prints.py"
HOOK_ID = "chart-raw-prints"


def chart(root: Path, templates: dict, allowlist=None) -> Path:
    directory = root / "chart"
    (directory / "templates").mkdir(parents=True, exist_ok=True)
    (directory / "Chart.yaml").write_text("apiVersion: v2\nname: c\nversion: 0.1.0\n")
    (directory / "values.yaml").write_text("{}\n")
    for name, text in templates.items():
        path = directory / "templates" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text)
    if allowlist is not None:
        (directory / "ci").mkdir(exist_ok=True)
        (directory / "ci" / "raw-prints-allowlist.txt").write_text(allowlist)
    return directory


def run(root: Path):
    return subprocess.run(
        [sys.executable, str(GATE), "--root", str(root)],
        capture_output=True,
        text=True,
        cwd=root,
    )


def judged(tmp_path: Path, source: str, name: str = "t.yaml", allowlist=None):
    chart(tmp_path, {name: source}, allowlist)
    return run(tmp_path)


# ------------------------------------------------------------ the raw print


def test_a_raw_values_print_is_red_and_names_file_and_line(tmp_path):
    result = judged(tmp_path, "kind: ConfigMap\nmetadata:\n  name: {{ .Values.x }}\n")
    assert result.returncode == 1, result.stdout
    assert "RAW_PRINT chart/templates/t.yaml:3: {{ .Values.x }}" in result.stdout, result.stdout


@pytest.mark.parametrize(
    "source",
    [
        "  name: {{ .Values.x | quote }}\n",
        "  name: {{ quote .Values.x }}\n",
        "  spec: {{ .Values.x | toJson }}\n",
        "  spec: {{ toJson .Values.x }}\n",
        "  port: {{ .Values.x | int64 }}\n",
        "  port: {{ int64 .Values.x }}\n",
        "  port: {{ .Values.x | int }}\n",
        "  spec:\n    {{- .Values.x | toYaml | nindent 4 }}\n",
        "  spec:\n    {{- toYaml .Values.x | nindent 4 }}\n",
        "  spec:\n{{ .Values.x | toYaml | indent 4 }}\n",
        "  name: {{ .Values.x | default \"a\" | quote }}\n",
        "  name: {{ required \"x is required\" .Values.x | quote }}\n",
    ],
    ids=["quote", "quote-call", "toJson", "toJson-call", "int64", "int64-call", "int",
         "toYaml-nindent", "toYaml-call-nindent", "toYaml-indent-alone", "default-then-quote", "required-then-quote"],
)
def test_an_encoded_print_is_green(tmp_path, source):
    result = judged(tmp_path, source)
    assert result.returncode == 0, result.stdout
    assert "green" in result.stdout


@pytest.mark.parametrize(
    ("source", "named"),
    [
        ("  name: {{ .Values.x | toYaml }}\n", ".Values.x | toYaml"),
        ("  name: {{ .Values.x | toYaml | indent 4 }}\n", ".Values.x | toYaml | indent 4"),
        ("  name: {{ .Values.x | squote }}\n", ".Values.x | squote"),
        ("  name: {{ .Values.x | b64enc }}\n", ".Values.x | b64enc"),
        ("  name: {{ printf \"%s-x\" .Values.x }}\n", 'printf "%s-x" .Values.x'),
        ("  name: {{ .Values.x | quote | printf \"%s\" }}\n", '.Values.x | quote | printf "%s"'),
        ("  name: {{ .Values.x | nindent 2 }}\n", ".Values.x | nindent 2"),
        ("  name: {{ (.Values.x | quote) | trim }}\n", "(.Values.x | quote) | trim"),
        ("  name: {{ $.Values.x }}\n", "$.Values.x"),
        ("  name: {{ index .Values \"x\" }}\n", 'index .Values "x"'),
        ("{{- $v := .Values.x }}\n  name: {{ $v }}\n", "$v"),
        ("{{- $v := .Values }}\n  name: {{ $v.x }}\n", "$v.x"),
        ("{{- range $k, $v := .Values.m }}\n  {{ $k }}: x\n{{- end }}\n", "$k"),
        ("{{- with .Values.x }}\n  name: {{ .name }}\n{{- end }}\n", ".name"),
        ("{{- range .Values.list }}\n  - {{ . }}\n{{- end }}\n", "."),
        ("  name: {{ $unbound }}\n", "$unbound"),
        ("  name: {{ tpl .Values.x . }}\n", "tpl .Values.x ."),
        ("{{- define \"c.x\" }}\nname: {{ .name }}\n{{- end }}\n", ".name"),
        ("  name: {{ .Values.x | int | printf \"%d\" }}\n", '.Values.x | int | printf "%d"'),
    ],
    ids=["inline-toYaml", "toYaml-indent-inline", "squote", "b64enc", "printf", "encoder-not-last", "nindent-only",
         "encoder-in-parens", "dollar-values", "index", "var-from-values", "var-path", "range-key",
         "with-field", "range-dot", "unbound-var", "tpl", "define-field", "int-then-printf"],
)
def test_a_values_derived_print_without_a_final_encoder_is_red(tmp_path, source, named):
    result = judged(tmp_path, source)
    assert result.returncode == 1, result.stdout
    assert f"RAW_PRINT chart/templates/t.yaml:" in result.stdout and f"{{{{ {named} }}}}" in result.stdout, result.stdout


# ------------------------------------------------- not a values-derived print

HELPERS = '{{- define "c.labels" }}app: x{{- end }}\n{{- define "c.x" }}y: z{{- end }}\n'


@pytest.mark.parametrize(
    "source",
    [
        "{{/* a comment {{ .Values.x }} */}}\nkind: ConfigMap\n",
        "{{- /* name: {{ .Values.x }} */ -}}\n",
        "{{- if .Values.x }}\nkind: ConfigMap\n{{- end }}\n",
        "{{- if eq .Values.x \"a\" }}\nkind: A\n{{- else if .Values.y }}\nkind: B\n{{- else }}\nkind: C\n{{- end }}\n",
        "{{- with .Values.x }}\nkind: ConfigMap\n{{- end }}\n",
        HELPERS + "  labels: {{ include \"c.labels\" (dict \"v\" .Values.x) | nindent 4 }}\n",
        HELPERS + "  labels: {{ include \"c.labels\" . | nindent 4 }}\n",
        HELPERS + "{{ template \"c.x\" .Values.x }}\n",
        "{{- $n := printf \"%s-x\" .Values.x }}\n",
        "{{- $_ := set .Values \"a\" 1 }}\n",
        "{{- fail (printf \"bad %s\" .Values.x) }}\n",
        "  name: {{ .Release.Name }}\n  chart: {{ .Chart.Name }}-{{ .Chart.Version }}\n",
        "  ns: {{ $.Release.Namespace }}\n",
        "  data: {{ $.Files.Get \"config/a.yaml\" | indent 4 }}\n",
        "{{- range tuple \"a\" \"b\" }}\n  - {{ . }}\n{{- end }}\n",
        "{{- $lit := \"fixed\" }}\n  name: {{ $lit }}\n",
        "{{- define \"c.labels\" }}\napp: {{ .Release.Name }}\n{{- end }}\n",
        "{{- with .Values.x }}\n  ns: {{ $.Release.Namespace }}\n{{- end }}\n",
        "  name: fixed\n",
    ],
    ids=["comment", "trimmed-comment", "if", "if-else-if", "with-condition", "include-args", "include-root",
         "template", "assignment", "set", "fail", "release-and-chart", "dollar-release", "files-get",
         "range-literal", "literal-var", "define-release", "root-in-with", "no-action"],
)
def test_a_print_that_is_not_values_derived_is_not_flagged(tmp_path, source):
    result = judged(tmp_path, source)
    assert result.returncode == 0, result.stdout
    assert "RAW_PRINT" not in result.stdout


@pytest.mark.parametrize(
    ("source", "named"),
    [
        ('  v: {{ include "common.render" (dict "value" .Values.x "context" $) }}\n', 'include "common.render"'),
        ("{{- $name := .Values.helper }}\n  v: {{ include $name . }}\n", "include $name ."),
        ('{{ template "sub.thing" .Values.x }}\n', 'template "sub.thing" .Values.x'),
    ],
    ids=["helper-not-in-chart", "dynamic-name", "template-not-in-chart"],
)
def test_an_include_the_chart_does_not_define_carries_its_operands_taint(tmp_path, source, named):
    result = judged(tmp_path, source)
    assert result.returncode == 1 and named in result.stdout, result.stdout


def test_a_helper_defined_in_another_file_of_the_chart_is_opaque(tmp_path):
    chart(tmp_path, {"_h.tpl": '{{- define "c.h" }}name: fixed{{- end }}\n', "t.yaml": '{{ include "c.h" .Values.x }}\n'})
    result = run(tmp_path)
    assert result.returncode == 0, result.stdout


def test_a_literal_var_reassigned_from_values_is_red(tmp_path):
    source = "{{- $n := \"fixed\" }}\n{{- if .Values.o }}{{- $n = .Values.o }}{{- end }}\n  name: {{ $n }}\n"
    result = judged(tmp_path, source)
    assert result.returncode == 1 and "{{ $n }}" in result.stdout, result.stdout


# ------------------------------------------------------------------ not modelled


@pytest.mark.parametrize(
    "source",
    [
        "  name: {{ (.Values.x | quote }}\n",
        "  name: {{ .Values.x | quote\n",
        "{{- if .Values.x }}\nkind: A\n",
        "kind: A\n{{- end }}\n",
        "{{/* never closed\n",
    ],
    ids=["unbalanced-paren", "unclosed-action", "unclosed-block", "stray-end", "unclosed-comment"],
)
def test_an_action_the_gate_cannot_parse_is_red_not_green(tmp_path, source):
    result = judged(tmp_path, source)
    assert result.returncode == 1, result.stdout
    assert "NOT_MODELLED chart/templates/t.yaml" in result.stdout, result.stdout


def test_a_template_that_is_not_utf8_is_not_modelled(tmp_path):
    result = judged(tmp_path, b"name: \xff\xfe {{ .Values.x }}\n")
    assert result.returncode == 1 and "NOT_MODELLED chart/templates/t.yaml" in result.stdout, result.stdout


def test_a_not_modelled_action_cannot_be_allowlisted(tmp_path):
    allowlist = "t.yaml: (.Values.x | quote  # ADR-1136: tried\n"
    result = judged(tmp_path, "  name: {{ (.Values.x | quote }}\n", allowlist=allowlist)
    assert result.returncode == 1 and "NOT_MODELLED" in result.stdout, result.stdout


# --------------------------------------------------------------- which files


@pytest.mark.parametrize("name", ["_helpers.tpl", "sub/t.yaml", "t.yml", "t.json"])
def test_every_template_file_is_scanned_whatever_its_suffix(tmp_path, name):
    result = judged(tmp_path, "name: {{ .Values.x }}\n", name=name)
    assert result.returncode == 1 and f"RAW_PRINT chart/templates/{name}:1" in result.stdout, result.stdout


def test_notes_and_vendored_charts_are_not_scanned(tmp_path):
    chart(tmp_path, {"NOTES.txt": "Release {{ .Values.x }}\n", "t.yaml": "kind: A\n"})
    vendored = tmp_path / "chart" / "charts" / "sub"
    (vendored / "templates").mkdir(parents=True)
    (vendored / "Chart.yaml").write_text("apiVersion: v2\nname: sub\nversion: 0.1.0\n")
    (vendored / "templates" / "t.yaml").write_text("name: {{ .Values.x }}\n")
    result = run(tmp_path)
    assert result.returncode == 0, result.stdout


def test_a_tree_with_no_chart_is_red(tmp_path):
    result = run(tmp_path)
    assert result.returncode == 1 and "NO_CHART" in result.stdout, result.stdout


def test_the_success_line_counts_what_was_judged(tmp_path):
    result = judged(tmp_path, "a: {{ .Values.a | quote }}\nb: {{ .Release.Name }}\n")
    assert result.returncode == 0, result.stdout
    assert "1 chart(s), 1 template file(s), 2 output print(s), 1 values-derived" in result.stdout, result.stdout


# -------------------------------------------------------------------- allowlist


RAW = "  cmd: {{ $probe.name }}\n"


def test_an_allowlisted_print_is_green(tmp_path):
    allowlist = "# comment line\n\nt.yaml: $probe.name  # ADR-1136: the render check refuses a control character in it\n"
    result = judged(tmp_path, RAW, allowlist=allowlist)
    assert result.returncode == 0, result.stdout
    assert "1 allowlisted" in result.stdout


@pytest.mark.parametrize(
    "line",
    ["t.yaml: $probe.name\n", "t.yaml: $probe.name  # ADR-1136:\n", "t.yaml: $probe.name  # ADR-1136:   \n"],
    ids=["no-marker", "empty-reason", "blank-reason"],
)
def test_an_allowlist_entry_with_no_reason_is_refused(tmp_path, line):
    result = judged(tmp_path, RAW, allowlist=line)
    assert result.returncode == 1, result.stdout
    assert "ALLOWLIST_NO_REASON" in result.stdout, result.stdout


def test_a_malformed_allowlist_line_is_refused(tmp_path):
    result = judged(tmp_path, RAW, allowlist="just some words  # ADR-1136: why\n")
    assert result.returncode == 1 and "ALLOWLIST_MALFORMED" in result.stdout, result.stdout


def test_a_stale_allowlist_entry_is_refused(tmp_path):
    allowlist = "t.yaml: $gone  # ADR-1136: was safe\n"
    result = judged(tmp_path, "a: {{ .Values.a | quote }}\n", allowlist=allowlist)
    assert result.returncode == 1 and "STALE_ALLOWLIST" in result.stdout, result.stdout


def test_an_allowlist_entry_excuses_only_its_own_file(tmp_path):
    chart(tmp_path, {"t.yaml": RAW, "sub/t.yaml": RAW}, "t.yaml: $probe.name  # ADR-1136: safe\n")
    result = run(tmp_path)
    assert result.returncode == 1, result.stdout
    assert "RAW_PRINT chart/templates/sub/t.yaml:1" in result.stdout
    assert "RAW_PRINT chart/templates/t.yaml:" not in result.stdout


# ------------------------------------------- review of actions#200: mutants that survived


@pytest.mark.parametrize(
    ("source", "named"),
    [
        # R5: an `else` restores the OUTER dot, not the `with` target's.
        ("{{- with .Chart }}\n  a: b\n{{- else }}\n  name: {{ .foo }}\n{{- end }}\n", ".foo"),
        # R6: the root `.` printed alone in a define is the define's argument.
        ('{{- define "c.n" }}{{ . | trunc 63 }}{{- end }}\n', ". | trunc 63"),
        # R7: `Subcharts` carries the subcharts' values; it is not a clean built-in.
        ("  name: {{ .Subcharts.x.Values.y }}\n", ".Subcharts.x.Values.y"),
        ("  name: {{ $.Subcharts.x.Values.y }}\n", "$.Subcharts.x.Values.y"),
        # R8: `$` is the root and holds `.Values`.
        ('  name: {{ index $ "Values" "x" }}\n', 'index $ "Values" "x"'),
        ('  name: {{ index . "Values" "x" }}\n', 'index . "Values" "x"'),
        # R10: `else with` over values taints the new dot.
        ("{{- if .Values.a }}\n  a: b\n{{- else with .Values.x }}\n  name: {{ .name }}\n{{- end }}\n", ".name"),
    ],
    ids=["R5-else-restores-outer-dot", "R6-define-dot-alone", "R7-subcharts", "R7-dollar-subcharts",
         "R8-index-dollar-values", "R8-index-dot-values", "R10-else-with-values"],
)
def test_a_review_mutant_shape_is_red(tmp_path, source, named):
    result = judged(tmp_path, source)
    assert result.returncode == 1, result.stdout
    assert f"{{{{ {named} }}}}" in result.stdout and "RAW_PRINT" in result.stdout, result.stdout


@pytest.mark.parametrize("name", ["extra.txt", "sub/NOTES.txt", "sub/x.txt"])
def test_r11_only_the_top_level_notes_file_is_skipped(tmp_path, name):
    result = judged(tmp_path, "name: {{ .Values.x }}\n", name=name)
    assert result.returncode == 1 and f"RAW_PRINT chart/templates/{name}:1" in result.stdout, result.stdout


# -------------------------------------- escape 1: `set` and friends on the root or a built-in


@pytest.mark.parametrize(
    "source",
    [
        '{{- $_ := set .Release "Name" .Values.evil }}\n  name: {{ .Release.Name }}\n',
        '{{- $_ := set . "Release" (dict "Name" .Values.evil) }}\n  name: {{ .Release.Name }}\n',
        '{{- $_ := set .Template "Name" .Values.evil }}\n  name: {{ .Template.Name }}\n',
        '{{- $_ := set $.Chart "Name" .Values.evil }}\n  name: {{ .Chart.Name }}\n',
        '{{- $_ := set $ "Chart" (dict "Name" .Values.evil) }}\n  name: {{ .Chart.Name }}\n',
        '{{- $_ := unset .Capabilities "KubeVersion" }}\n',
        '{{- $_ := merge .Release (dict "Name" .Values.evil) }}\n',
        '{{- $_ := mergeOverwrite .Chart (dict "Name" .Values.evil) }}\n',
        '{{- $_ := mustMergeOverwrite .Files (dict) }}\n',
        "{{- $c := deepCopy . }}\n",
        '{{- $r := .Release }}\n{{- $_ := set $r "Name" .Values.evil }}\n  name: {{ .Release.Name }}\n',
        '{{- $root := . }}\n{{- $_ := set $root "Release" (dict "Name" .Values.evil) }}\n',
        '{{- $_ := set (index $ "Release") "Name" .Values.evil }}\n',
        '{{- with .Release }}{{- $_ := set . "Name" $.Values.evil }}{{- end }}\n',
        '{{- if set .Release "Name" .Values.evil }}{{- end }}\n',
        '{{- $ctx := dict "root" $ }}\n{{- $_ := set $ctx.root "Release" (dict "Name" .Values.evil) }}\n',
        '{{- $_ := .Values.evil | set .Release "Name" }}\n',
    ],
    ids=["set-release", "set-dot", "set-template", "set-dollar-chart", "set-dollar", "unset-capabilities",
         "merge-release", "mergeOverwrite-chart", "mustMergeOverwrite-files", "deepCopy-dot",
         "alias-var", "root-var", "index-dollar", "with-release-dot", "in-a-condition", "dict-holding-root",
         "piped-value"],
)
def test_a_mutation_of_the_root_or_a_builtin_is_not_modelled(tmp_path, source):
    result = judged(tmp_path, source)
    assert result.returncode == 1, result.stdout
    assert "NOT_MODELLED chart/templates/t.yaml" in result.stdout, result.stdout


def test_set_on_a_fresh_dict_is_modelled_and_taints_the_dict(tmp_path):
    green = judged(tmp_path, '{{- $d := dict }}\n{{- $_ := set $d "k" "lit" }}\n  name: {{ $d.k }}\n')
    assert green.returncode == 0, green.stdout


def test_set_of_a_values_string_into_a_fresh_dict_taints_it(tmp_path):
    result = judged(tmp_path, '{{- $d := dict }}\n{{- $_ := set $d "k" .Values.x }}\n  name: {{ $d.k }}\n')
    assert result.returncode == 1 and "RAW_PRINT" in result.stdout and "{{ $d.k }}" in result.stdout, result.stdout


# ------------------------- escape 2: a define whose body reads its argument is not opaque


def test_an_allowlisted_define_body_does_not_excuse_a_values_caller(tmp_path):
    chart(
        tmp_path,
        {"_h.tpl": '{{- define "own.label" }}{{ . | trunc 63 }}{{- end }}\n',
         "t.yaml": '  label: {{ include "own.label" .Values.evil }}\n'},
        "_h.tpl: . | trunc 63  # ADR-1136: every caller passes a literal\n",
    )
    result = run(tmp_path)
    assert result.returncode == 1, result.stdout
    assert 'RAW_PRINT chart/templates/t.yaml:1: {{ include "own.label" .Values.evil }}' in result.stdout, result.stdout


def test_an_allowlisted_define_body_called_with_a_literal_is_green(tmp_path):
    chart(
        tmp_path,
        {"_h.tpl": '{{- define "own.label" }}{{ . | trunc 63 }}{{- end }}\n',
         "t.yaml": '  label: {{ include "own.label" "fixed" }}\n'},
        "_h.tpl: . | trunc 63  # ADR-1136: every caller passes a literal\n",
    )
    result = run(tmp_path)
    assert result.returncode == 0, result.stdout


def test_a_raw_define_body_also_reddens_its_values_caller(tmp_path):
    chart(tmp_path, {"_h.tpl": '{{- define "c.v" }}{{ .name }}{{- end }}\n',
                     "t.yaml": '  v: {{ include "c.v" .Values.x }}\n'})
    result = run(tmp_path)
    assert result.returncode == 1, result.stdout
    assert "RAW_PRINT chart/templates/_h.tpl:1: {{ .name }}" in result.stdout
    assert 'RAW_PRINT chart/templates/t.yaml:1: {{ include "c.v" .Values.x }}' in result.stdout, result.stdout


RELEASE_HELPER = '{{- define "c.rel" }}app: {{ .Release.Name }}{{- end }}\n{{- define "c.outer" }}{{ include "c.rel" . }}{{- end }}\n'


@pytest.mark.parametrize(
    ("caller", "red"),
    [
        ('  {{- include "c.rel" . | nindent 2 }}\n', False),
        ('  {{- include "c.rel" $ | nindent 2 }}\n', False),
        ('{{- range .Values.list }}\n  {{- include "c.rel" $ | nindent 2 }}\n{{- end }}\n', False),
        ('  {{- include "c.outer" . | nindent 2 }}\n', False),
        ('  {{- include "c.rel" (dict "Release" (dict "Name" .Values.evil)) | nindent 2 }}\n', True),
        ('  {{- include "c.outer" (dict "Release" (dict "Name" .Values.evil)) | nindent 2 }}\n', True),
        ('{{- with .Values.x }}\n  {{- include "c.rel" . | nindent 2 }}\n{{- end }}\n', True),
    ],
    ids=["root-dot", "root-dollar", "dollar-in-range", "through-another-define", "dict-release",
         "dict-release-through-another-define", "values-dot"],
)
def test_a_define_reading_a_builtin_off_its_argument_is_judged_at_the_call_site(tmp_path, caller, red):
    chart(tmp_path, {"_h.tpl": RELEASE_HELPER, "t.yaml": caller})
    result = run(tmp_path)
    assert result.returncode == (1 if red else 0), result.stdout
    assert ("RAW_PRINT chart/templates/t.yaml" in result.stdout) == red, result.stdout


def test_sensitivity_reaches_a_define_scanned_before_the_one_it_calls(tmp_path):
    # Files are read in sorted order: `0.yaml`, `_a.tpl`, `_b.tpl`. `c.outer` learns
    # that `c.rel` is sensitive only after `_b.tpl` is read, so without the fixed
    # point `0.yaml`'s call would find `c.outer` opaque.
    chart(tmp_path, {
        "_a.tpl": '{{- define "c.outer" }}{{ include "c.rel" . }}{{- end }}\n',
        "_b.tpl": '{{- define "c.rel" }}app: {{ .Release.Name }}{{- end }}\n',
        "0.yaml": '  {{- include "c.outer" (dict "Release" (dict "Name" .Values.evil)) | nindent 2 }}\n',
    })
    result = run(tmp_path)
    assert result.returncode == 1 and "RAW_PRINT chart/templates/0.yaml:1" in result.stdout, result.stdout


def test_a_define_whose_argument_reads_are_all_encoded_stays_opaque(tmp_path):
    chart(tmp_path, {"_h.tpl": '{{- define "c.q" }}name: {{ .name | quote }}{{- end }}\n',
                     "t.yaml": '{{ include "c.q" .Values.x }}\n'})
    result = run(tmp_path)
    assert result.returncode == 0, result.stdout


def test_a_block_action_is_not_modelled(tmp_path):
    result = judged(tmp_path, '{{- block "c.b" . }}x{{- end }}\n')
    assert result.returncode == 1 and "NOT_MODELLED" in result.stdout, result.stdout


# ------------------------------------------------------------ publication facts


def test_the_hook_is_published_and_judges_the_whole_tree():
    hooks = {h["id"]: h for h in yaml.safe_load((REPO / ".pre-commit-hooks.yaml").read_text())}
    hook = hooks[HOOK_ID]
    assert hook["entry"] == "hooks/chart_raw_prints.py"
    assert hook["language"] == "script"
    assert hook["always_run"] is True and hook["pass_filenames"] is False


def test_no_class_adopts_the_hook_yet():
    # ADR-1136's sweep lands each chart's allowlist first; adoption follows.
    classes = json.loads((REPO / "classes.json").read_text())
    for name, cls in classes["classes"].items():
        assert HOOK_ID not in [h["id"] for h in cls["hooks"]], name


def test_the_hook_is_standard_library_only():
    assert "import yaml" not in GATE.read_text()
