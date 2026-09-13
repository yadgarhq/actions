"""LEDGER 511, STAGE 5. The chart-baseline gate, and in particular its two-file predicate.

`hooks/chart_baseline.py` is `language: script` and therefore stdlib-only — a
`language: python` hook makes pre-commit pip-install this repository, which is
not a package, and the failure lands in the CONSUMING repository where nothing
explains it. So its YAML scan is hand-written, and a hand parser standing between
a deployment baseline and its subject is worth more evidence than "it looked
right".

THE CENTRAL TEST HERE IS THE EQUIVALENCE ONE, the shape
`test_certificate_usages.py` records for the same reason. It runs the hand scan
and `yaml.safe_load` over one corpus and asserts they agree on EVERY scalar leaf.
PyYAML is available to this suite (the `pytest-scripts` hook installs it) and not
to the hook, which is the asymmetry that makes the comparison possible. A
disagreement means the scan is not a safe stand-in for a YAML parser and the
gate's design fails, whatever its verdicts happen to be.

EVERY GREEN IS PAIRED WITH A RED ON THE SAME TREE, and every case asserts the
EXIT CODE rather than only the message. A suite of conforming inputs proves the
gate returns 0, and so does `true`; asserting the code is also what makes the
mutation proof meaningful — neutering the refusal must redden the whole suite
rather than half of it.

WHY THE PAIRS ARE TIGHT AT THE BOUNDARY. The rule is
`grace >= DRAIN_BUDGET + EXIT_MARGIN + preStop sleep`, so the interesting inputs
are 35/34 with a 5s sleep and 30/29 without one — and 32-with-a-5s-sleep, which a
flat `>= 30` would pass while leaving 27s for a 25s drain plus a 5s exit. A suite
that only tried 35 and 10 would not tell the two rules apart.

THE TRAPS IN THE CORPUS ARE THE REAL ONES, measured against `origin/main` of the
seven module repositories on 2026-09-13:

  * `- maxSkew: {{ .Values.topologySpread.maxSkew }}` is a key on a SEQUENCE ITEM,
    and the leading dash is the only thing between it and a mapping-key pattern.
    The gate's first revision skipped it and reported "sets no maxSkew" against
    all seven charts — a false refusal, the worst failure available here, because
    it reddens honest work and gets the gate removed.
  * A COMMENT BLOCK NAMING THE FORBIDDEN VALUE. Every module's deployment
    template explains at length why `DoNotSchedule` would hang a roll, so a scan
    that reads comments refuses a correct chart.
  * A COMMENT THAT LOOKS LIKE THE KEY. `# serviceAccountName: ...` must not
    satisfy the requirement that the pod spec set it.
  * `{{ . }}` UNDER A `with`, which is how all seven spell the grace period. Its
    value is legible only through the enclosing block.

The gate is run as a SUBPROCESS in every verdict test, because its subject is a
tree rather than a function — which is how pre-commit invokes it in a consumer.
"""

import subprocess
import sys
from pathlib import Path

import yaml

GATE = Path(__file__).resolve().parents[2] / "hooks" / "chart_baseline.py"


# ---------------------------------------------------------------- the fixtures

CHART_YAML = """\
apiVersion: v2
name: iam
version: 0.1.0
"""

# The grace period and the spread, spelled the way all seven module charts spell
# them: a `with` block binding `.`, an `if` guard on the spread, and the three
# spread scalars reaching `values.yaml` through `.Values.topologySpread.*`. The
# comment naming `DoNotSchedule` is one of the traps this corpus carries.
DEPLOYMENT = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .Chart.Name }}
spec:
  replicas: {{ .Values.replicaCount }}
  template:
    spec:
      # A commented-out key must not satisfy anything:
      # serviceAccountName: someone-elses-account
      serviceAccountName: {{ .Chart.Name }}
      automountServiceAccountToken: false
      {{- with .Values.terminationGracePeriodSeconds }}
      # 35 = 5 + 25 + 5, and every term is somebody else's number.
      terminationGracePeriodSeconds: {{ . }}
      {{- end }}
      {{- if .Values.topologySpread.enabled }}
      # `DoNotSchedule` WOULD HANG EVERY ROLL, and that is the whole reason this
      # says otherwise. A scan that reads comments finds the forbidden value here.
      topologySpreadConstraints:
        - maxSkew: {{ .Values.topologySpread.maxSkew }}
          topologyKey: {{ .Values.topologySpread.topologyKey }}
          whenUnsatisfiable: {{ .Values.topologySpread.whenUnsatisfiable }}
          labelSelector:
            matchLabels:
              app: {{ .Chart.Name }}
      {{- end }}
      containers:
        - name: {{ .Chart.Name }}
          image: example
          lifecycle:
            {{- with .Values.preStopSleepSeconds }}
            preStop:
              sleep:
                seconds: {{ . }}
            {{- end }}
"""

SERVICE_ACCOUNT = """\
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ .Chart.Name }}
automountServiceAccountToken: false
"""

NETWORK_POLICY = """\
{{- if .Values.networkPolicy.enabled }}
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {{ .Chart.Name }}
spec:
  podSelector:
    matchLabels:
      app: {{ .Chart.Name }}
  policyTypes: [Ingress]
{{- end }}
"""

VALUES = """\
replicaCount: 2

# The grace period, with its arithmetic in the template beside the field.
terminationGracePeriodSeconds: 35
preStopSleepSeconds: 5

topologySpread:
  enabled: true
  maxSkew: 1
  topologyKey: kubernetes.io/hostname
  whenUnsatisfiable: ScheduleAnyway

networkPolicy:
  # OFF by default, and correct: a module chart must stay installable on a bare
  # cluster, so switching this on is the reference deployment's business (D80).
  enabled: false
  clients: [gateway]
  scrapeFrom: {}
"""


def write_chart(root: Path, name: str = "chart", **replace) -> Path:
    """A conforming chart, with named files replaced or deleted (`None`)."""
    files = {
        "Chart.yaml": CHART_YAML,
        "values.yaml": VALUES,
        "templates/deployment.yaml": DEPLOYMENT,
        "templates/serviceaccount.yaml": SERVICE_ACCOUNT,
        "templates/networkpolicy.yaml": NETWORK_POLICY,
    }
    for key, value in replace.items():
        files[key.replace("__", "/").replace("_yaml", ".yaml")] = value
    chart = root / name
    for relative, text in files.items():
        if text is None:
            continue
        path = chart / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return chart


def run(root: Path, *arguments):
    return subprocess.run(
        [sys.executable, str(GATE), "--root", str(root), *arguments],
        capture_output=True,
        text=True,
    )


def swap(text: str, old: str, new: str) -> str:
    """Replace `old` once, refusing silently to replace nothing."""
    assert old in text, f"the fixture no longer contains {old!r}"
    return text.replace(old, new, 1)


# ----------------------------------------------------------- the green baseline


def test_a_conforming_chart_passes_and_reports_both_counts(tmp_path):
    write_chart(tmp_path)
    result = run(tmp_path)
    assert result.returncode == 0, result.stdout
    assert "1 charts, 1 judged" in result.stdout
    assert "assertions evaluated" in result.stdout
    assert "0 findings" in result.stdout


def test_the_report_names_every_chart_and_its_fields(tmp_path):
    write_chart(tmp_path, name="one")
    write_chart(tmp_path, name="two", templates__serviceaccount_yaml=None)
    result = run(tmp_path, "--report")
    assert result.returncode == 1
    assert "one: PASS" in result.stdout
    assert "two: FAIL service-account" in result.stdout


def test_the_default_root_is_the_working_directory(tmp_path):
    write_chart(tmp_path)
    result = subprocess.run(
        [sys.executable, str(GATE)], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout


def test_a_root_that_is_not_a_directory_is_refused(tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("", encoding="utf-8")
    result = run(target)
    assert result.returncode == 1
    assert "is not a directory" in result.stdout


# ------------------------------------------------------------- the grace period


def test_the_floor_is_tight_at_thirty_five_with_a_five_second_sleep(tmp_path):
    write_chart(tmp_path, name="green")
    write_chart(
        tmp_path,
        name="red",
        values_yaml=swap(
            VALUES, "terminationGracePeriodSeconds: 35", "terminationGracePeriodSeconds: 34"
        ),
    )
    result = run(tmp_path, "--report")
    assert result.returncode == 1
    assert "green: PASS" in result.stdout
    assert "red: FAIL grace-period" in result.stdout
    assert "below the floor of 35s" in result.stdout
    assert "5 preStop sleep" in result.stdout


def test_thirty_two_with_a_five_second_sleep_is_refused(tmp_path):
    """The case a flat `>= 30` passes: 32 - 5 leaves 27s for a 25s drain + 5s exit."""
    write_chart(
        tmp_path,
        values_yaml=swap(
            VALUES, "terminationGracePeriodSeconds: 35", "terminationGracePeriodSeconds: 32"
        ),
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "below the floor of 35s" in result.stdout


def test_thirty_passes_and_twenty_nine_fails_where_there_is_no_sleep(tmp_path):
    without_sleep = swap(DEPLOYMENT, "{{- with .Values.preStopSleepSeconds }}", "{{- with .Values.absent }}")
    without_sleep = swap(without_sleep, "seconds: {{ . }}", "seconds: 0")
    for grace, expected in ((30, 0), (29, 1)):
        root = tmp_path / f"tree{grace}"
        root.mkdir()
        write_chart(
            root,
            templates__deployment_yaml=without_sleep,
            values_yaml=swap(
                VALUES,
                "terminationGracePeriodSeconds: 35",
                f"terminationGracePeriodSeconds: {grace}",
            ),
        )
        result = run(root)
        assert result.returncode == expected, result.stdout
        if expected:
            assert "below the floor of 30s" in result.stdout


def test_a_template_that_sets_no_grace_period_is_refused(tmp_path):
    write_chart(
        tmp_path,
        templates__deployment_yaml=swap(
            DEPLOYMENT, "terminationGracePeriodSeconds: {{ . }}", "# nothing here"
        ),
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "sets no `terminationGracePeriodSeconds`" in result.stdout


def test_a_values_file_that_switches_the_grace_period_off_is_refused(tmp_path):
    """Every template line present, and `{{- with }}` renders none of them."""
    write_chart(
        tmp_path,
        values_yaml=swap(VALUES, "terminationGracePeriodSeconds: 35", "terminationGracePeriodSeconds: 0"),
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "renders `terminationGracePeriodSeconds` only when" in result.stdout


def test_a_grace_period_absent_from_values_is_refused(tmp_path):
    write_chart(
        tmp_path,
        values_yaml=swap(VALUES, "terminationGracePeriodSeconds: 35\n", ""),
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "renders `terminationGracePeriodSeconds` only when" in result.stdout


def test_a_grace_period_that_is_not_a_number_is_refused(tmp_path):
    write_chart(
        tmp_path,
        values_yaml=swap(
            VALUES, "terminationGracePeriodSeconds: 35", "terminationGracePeriodSeconds: soon"
        ),
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "not an integer number of seconds" in result.stdout


def test_a_sleep_that_is_not_a_number_is_refused(tmp_path):
    write_chart(
        tmp_path,
        values_yaml=swap(VALUES, "preStopSleepSeconds: 5", "preStopSleepSeconds: briefly"),
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "the grace-period floor cannot be computed" in result.stdout


def test_a_literal_grace_period_in_the_template_is_judged_too(tmp_path):
    """A chart need not route the field through values; the rule is the same."""
    for grace, expected in ((35, 0), (34, 1)):
        literal = swap(
            DEPLOYMENT,
            "{{- with .Values.terminationGracePeriodSeconds }}",
            "{{- if true }}",
        )
        literal = swap(literal, "terminationGracePeriodSeconds: {{ . }}", f"terminationGracePeriodSeconds: {grace}")
        root = tmp_path / f"tree{grace}"
        root.mkdir()
        write_chart(root, templates__deployment_yaml=literal)
        result = run(root)
        assert result.returncode == expected, result.stdout


def test_an_expression_the_gate_cannot_model_is_refused_not_skipped(tmp_path):
    write_chart(
        tmp_path,
        templates__deployment_yaml=swap(
            DEPLOYMENT,
            "terminationGracePeriodSeconds: {{ . }}",
            'terminationGracePeriodSeconds: {{ include "grace" . }}',
        ),
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "an expression this gate does not model" in result.stdout


# ---------------------------------------------------------- the ServiceAccount


def test_a_chart_with_no_service_account_template_is_refused(tmp_path):
    write_chart(tmp_path, templates__serviceaccount_yaml=None)
    result = run(tmp_path)
    assert result.returncode == 1
    assert "declares `kind: ServiceAccount`" in result.stdout


def test_the_service_account_is_found_by_kind_not_by_filename(tmp_path):
    write_chart(
        tmp_path,
        templates__serviceaccount_yaml=None,
        templates__identity_yaml=SERVICE_ACCOUNT,
    )
    result = run(tmp_path)
    assert result.returncode == 0, result.stdout


def test_a_pod_that_names_no_service_account_is_refused(tmp_path):
    write_chart(
        tmp_path,
        templates__deployment_yaml=swap(
            DEPLOYMENT, "      serviceAccountName: {{ .Chart.Name }}\n", ""
        ),
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "sets no `serviceAccountName`" in result.stdout


def test_a_commented_out_key_does_not_satisfy_the_requirement(tmp_path):
    """The fixture carries `# serviceAccountName: someone-elses-account`."""
    stripped = swap(DEPLOYMENT, "      serviceAccountName: {{ .Chart.Name }}\n", "")
    assert "# serviceAccountName:" in stripped
    write_chart(tmp_path, templates__deployment_yaml=stripped)
    result = run(tmp_path)
    assert result.returncode == 1
    assert "sets no `serviceAccountName`" in result.stdout


def test_a_pod_that_mounts_an_api_token_is_refused(tmp_path):
    write_chart(
        tmp_path,
        templates__deployment_yaml=swap(
            DEPLOYMENT,
            "automountServiceAccountToken: false",
            "automountServiceAccountToken: true",
        ),
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "not `false`" in result.stdout


def test_a_pod_that_sets_no_automount_key_is_refused(tmp_path):
    write_chart(
        tmp_path,
        templates__deployment_yaml=swap(
            DEPLOYMENT, "      automountServiceAccountToken: false\n", ""
        ),
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "sets no `automountServiceAccountToken`" in result.stdout


def test_the_automount_key_is_judged_through_values_too(tmp_path):
    for setting, expected in (("false", 0), ("true", 1)):
        root = tmp_path / setting
        root.mkdir()
        write_chart(
            root,
            templates__deployment_yaml=swap(
                DEPLOYMENT,
                "automountServiceAccountToken: false",
                "automountServiceAccountToken: {{ .Values.automount }}",
            ),
            values_yaml=VALUES + f"automount: {setting}\n",
        )
        result = run(root)
        assert result.returncode == expected, result.stdout


# --------------------------------------------------------- the topology spread


def test_a_chart_with_no_spread_constraint_is_refused(tmp_path):
    write_chart(
        tmp_path,
        templates__deployment_yaml=swap(
            DEPLOYMENT, "      topologySpreadConstraints:\n", ""
        ),
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "sets no `topologySpreadConstraints`" in result.stdout


def test_the_spread_being_switched_off_in_values_is_refused(tmp_path):
    write_chart(
        tmp_path, values_yaml=swap(VALUES, "  enabled: true", "  enabled: false")
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "renders `topologySpreadConstraints` only when" in result.stdout
    assert "no D80 argument for being off" in result.stdout


def test_do_not_schedule_is_the_finding(tmp_path):
    write_chart(
        tmp_path,
        values_yaml=swap(
            VALUES, "whenUnsatisfiable: ScheduleAnyway", "whenUnsatisfiable: DoNotSchedule"
        ),
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "not `ScheduleAnyway`" in result.stdout
    assert "neither proceeds nor fails" in result.stdout


def test_prose_naming_the_forbidden_value_does_not_redden_a_correct_chart(tmp_path):
    """The fixture's comment says `DoNotSchedule` twice. The chart is correct."""
    assert "DoNotSchedule" in DEPLOYMENT
    write_chart(tmp_path)
    result = run(tmp_path)
    assert result.returncode == 0, result.stdout


def test_a_spread_with_no_strength_is_refused(tmp_path):
    write_chart(
        tmp_path,
        templates__deployment_yaml=swap(
            DEPLOYMENT,
            "          whenUnsatisfiable: {{ .Values.topologySpread.whenUnsatisfiable }}\n",
            "",
        ),
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "sets no `whenUnsatisfiable`" in result.stdout


def test_the_skew_on_a_sequence_item_is_read_rather_than_skipped(tmp_path):
    """The gate's first revision reported this missing on all seven real charts."""
    assert "        - maxSkew:" in DEPLOYMENT
    write_chart(tmp_path, name="green")
    write_chart(
        tmp_path,
        name="red",
        values_yaml=swap(VALUES, "  maxSkew: 1", "  maxSkew: 0"),
    )
    result = run(tmp_path, "--report")
    assert result.returncode == 1
    assert "green: PASS" in result.stdout
    assert "red: FAIL topology-spread" in result.stdout
    assert "not a positive integer" in result.stdout


def test_a_spread_with_no_skew_key_is_refused(tmp_path):
    write_chart(
        tmp_path,
        templates__deployment_yaml=swap(
            DEPLOYMENT,
            "        - maxSkew: {{ .Values.topologySpread.maxSkew }}\n",
            "        - labelSelector: {}\n",
        ),
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "sets no `maxSkew`" in result.stdout


def test_an_empty_topology_key_is_refused(tmp_path):
    write_chart(
        tmp_path,
        values_yaml=swap(VALUES, "  topologyKey: kubernetes.io/hostname", '  topologyKey: ""'),
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "resolves to an empty value" in result.stdout


# --------------------------------------------------------- the NetworkPolicy


def test_a_chart_with_no_network_policy_template_is_refused(tmp_path):
    write_chart(tmp_path, templates__networkpolicy_yaml=None)
    result = run(tmp_path)
    assert result.returncode == 1
    assert "declares `kind: NetworkPolicy`" in result.stdout


def test_a_policy_defaulting_to_disabled_passes(tmp_path):
    """D80: the template must exist; whether it is ON is the deployment's business."""
    assert "enabled: false" in VALUES
    write_chart(tmp_path)
    result = run(tmp_path)
    assert result.returncode == 0, result.stdout


def test_the_policy_is_found_by_kind_not_by_filename(tmp_path):
    write_chart(
        tmp_path,
        templates__networkpolicy_yaml=None,
        templates__ingress_rules_yaml=NETWORK_POLICY,
    )
    result = run(tmp_path)
    assert result.returncode == 0, result.stdout


# --------------------------------------------- the kind: line, three ways wrong


def test_a_trailing_comment_on_the_kind_line_does_not_hide_it(tmp_path):
    """`kind: NetworkPolicy  # one ingress policy` used to be a false refusal."""
    write_chart(
        tmp_path,
        templates__networkpolicy_yaml=swap(
            NETWORK_POLICY,
            "kind: NetworkPolicy\n",
            "kind: NetworkPolicy  # one ingress policy\n",
        ),
    )
    result = run(tmp_path)
    assert result.returncode == 0, result.stdout


def test_a_quoted_kind_value_does_not_hide_it(tmp_path):
    """`kind: "NetworkPolicy"` used to be a false refusal too."""
    write_chart(
        tmp_path,
        templates__networkpolicy_yaml=swap(
            NETWORK_POLICY, "kind: NetworkPolicy\n", 'kind: "NetworkPolicy"\n'
        ),
    )
    result = run(tmp_path)
    assert result.returncode == 0, result.stdout


def test_a_trailing_comment_on_kind_deployment_does_not_hide_the_chart(tmp_path):
    """`kind: Deployment  # the workload` used to report `renders no Deployment`

    with NO finding — the chart went unjudged and, beside another good chart,
    the tree still exited 0. That silent half is closed below by the third
    floor; this pins that the regex itself recognises the kind.
    """
    write_chart(
        tmp_path,
        templates__deployment_yaml=swap(
            DEPLOYMENT, "kind: Deployment\n", "kind: Deployment  # the workload\n"
        ),
    )
    result = run(tmp_path, "--report")
    assert result.returncode == 0, result.stdout
    assert "chart: PASS" in result.stdout


def test_a_commented_out_kind_line_does_not_satisfy_the_requirement(tmp_path):
    """`# kind: NetworkPolicy` must not count as the template declaring it."""
    write_chart(
        tmp_path,
        templates__networkpolicy_yaml=swap(
            NETWORK_POLICY, "kind: NetworkPolicy\n", "# kind: NetworkPolicy\n"
        ),
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "declares `kind: NetworkPolicy`" in result.stdout


# ------------------------------------------------------------ the three floors


def test_a_tree_with_no_chart_is_refused_rather_than_passed(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
    result = run(tmp_path)
    assert result.returncode == 1
    assert "below the floor" in result.stdout
    assert "nothing for this gate to judge" in result.stdout


def test_a_chart_that_renders_no_deployment_is_unjudged_and_trips_the_second_floor(
    tmp_path,
):
    """Charts found, none judged — what a broken `kind: Deployment` scan looks like."""
    write_chart(
        tmp_path,
        templates__deployment_yaml=None,
        templates__serviceaccount_yaml=SERVICE_ACCOUNT,
    )
    result = run(tmp_path, "--report")
    assert result.returncode == 1
    assert "not judged — renders no Deployment" in result.stdout
    assert "assertions evaluated, below the floor" in result.stdout


def test_a_vendored_subchart_is_not_this_repository_s_chart(tmp_path):
    write_chart(tmp_path, name="chart")
    write_chart(
        tmp_path / "chart" / "charts",
        name="dependency",
        templates__networkpolicy_yaml=None,
    )
    result = run(tmp_path, "--report")
    assert result.returncode == 0, result.stdout
    assert "dependency" not in result.stdout
    assert "1 charts, 1 judged" in result.stdout


def test_one_chart_unjudged_beside_a_good_one_trips_the_third_floor(tmp_path):
    """Ledger 715's class: the good chart's assertions keep the total above the
    second floor, so only a check on `judged` vs. `charts` catches this — the sum
    of assertions alone cannot, since one broken/different chart hides beside a
    passing one instead of the whole tree going to zero at once.
    """
    write_chart(tmp_path, name="good")
    other = tmp_path / "unexamined"
    (other / "templates").mkdir(parents=True)
    (other / "Chart.yaml").write_text(CHART_YAML, encoding="utf-8")
    (other / "templates" / "job.yaml").write_text(
        "apiVersion: batch/v1\nkind: Job\nmetadata:\n  name: helper\n",
        encoding="utf-8",
    )
    result = run(tmp_path, "--report")
    assert result.returncode == 1
    assert "good: PASS" in result.stdout
    assert "unexamined: not judged" in result.stdout
    assert "2 charts found, 1 judged" in result.stdout
    assert "never examined: unexamined" in result.stdout


# ---------------------------------------------------- parse failures are red


def test_an_unbalanced_template_is_refused(tmp_path):
    write_chart(
        tmp_path,
        templates__deployment_yaml=swap(DEPLOYMENT, "      {{- end }}\n", "", ),
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "do not balance" in result.stdout


def test_a_multi_document_values_file_is_refused(tmp_path):
    write_chart(tmp_path, values_yaml=VALUES + "---\nsecond: document\n")
    result = run(tmp_path)
    assert result.returncode == 1
    assert "more than one YAML document" in result.stdout


def test_a_deployment_spelled_outside_the_expected_path_is_refused(tmp_path):
    write_chart(
        tmp_path,
        templates__deployment_yaml=None,
        templates__workload_yaml=DEPLOYMENT,
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "`templates/deployment.yaml`" in result.stdout
    assert "which is not a state this gate accepts" in result.stdout


# -------------------- the equivalence test, which is why this file exists ----


def scanned(text: str) -> dict:
    """Every dotted path the hand scan resolves, as PyYAML would have loaded it."""
    sys.path.insert(0, str(GATE.parent))
    try:
        import chart_baseline
    finally:
        sys.path.pop(0)
    values = chart_baseline.scan_values(text)
    assert values.modelled, values.reason
    return {
        path: yaml.safe_load(raw)
        for path, raw in values.scalars.items()
        if raw is not chart_baseline.NOT_MODELLED
    }


def leaves(node, prefix: str = "") -> dict:
    """Every scalar leaf PyYAML finds, by dotted path. Sequences are not descended."""
    if isinstance(node, dict):
        found = {}
        for key, value in node.items():
            found.update(leaves(value, f"{prefix}.{key}" if prefix else str(key)))
        return found
    if isinstance(node, list):
        return {}
    return {prefix: node}


# Every shape the real files carry, plus the ones that would let a hand scan
# disagree quietly: an inline comment, a `#` inside a quoted scalar, a key name
# repeated at two depths, a blank line inside a block, a flow mapping, a
# sequence, and a comment at column zero in the middle of a nested block.
CORPUS = """\
replicaCount: 2
terminationGracePeriodSeconds: 35 # 5 + 25 + 5
preStopSleepSeconds: 5
image:
  repository: ghcr.io/yadgarhq/iam
  tag: ""
  pullPolicy: IfNotPresent
  enabled: true

topologySpread:
  enabled: true
  maxSkew: 1
# A comment at column zero inside a nested block.
  topologyKey: kubernetes.io/hostname
  whenUnsatisfiable: ScheduleAnyway

networkPolicy:
  enabled: false
  clients:
    - gateway
    - task
  scrapeFrom: {}
  note: "a # inside a quoted scalar is not a comment"

resources:
  limits:
    cpu: 500m
    memory: 256Mi
  requests:
    cpu: 100m
    memory: 128Mi

nested:
  deeper:
    enabled: false
    maxSkew: 3
"""


def test_the_hand_scan_agrees_with_pyyaml_on_every_scalar_leaf():
    """A disagreement means the stdlib scan is not a safe stand-in for a parser."""
    assert scanned(CORPUS) == leaves(yaml.safe_load(CORPUS))


def test_the_scan_refuses_to_answer_rather_than_answering_wrongly():
    """A sequence and a flow mapping are marked unresolvable, never guessed."""
    sys.path.insert(0, str(GATE.parent))
    try:
        import chart_baseline
    finally:
        sys.path.pop(0)
    values = chart_baseline.scan_values(CORPUS)
    assert values.raw("networkPolicy.clients") is None
    assert values.raw("networkPolicy.scrapeFrom") is None
    assert values.raw("networkPolicy.enabled") == "false"
    assert values.raw("nested.deeper.maxSkew") == "3"
    assert values.raw("topologySpread.topologyKey") == "kubernetes.io/hostname"
