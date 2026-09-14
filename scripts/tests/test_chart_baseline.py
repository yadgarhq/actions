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

LEDGER 897 ADDED A SECOND BLOCK OF PAIRS, one per false refusal the review found,
and every one of them was REPRODUCED against a scratch copy of `iam`'s real chart
before it was fixed. The shapes: a `with` guard over a MAPPING; the four distinct
causes that printed one wrong message; the `required "…"` and `| quote` wrappers the
estate writes 163 times and this gate refused; `{{- else }}`; the preStop-sleep
fail-open; and a Deployment at a filename other than `deployment.yaml`. Three more
turned up while reproducing those — `$.Values.x`, `{{ .relative }}` inside a `with`,
and a guard condition the gate could not read being DROPPED rather than refused.

TWO OF THOSE NINE ARE FAIL-OPENS, not false refusals, and they are the ones worth a
reader's attention: the grace floor silently fell from 35 to 30 when the preStop
sleep was not spelled `preStopSleepSeconds`, and a key under `{{- if and A B }}` was
judged as if it always rendered. A false refusal is loud. A fail-open is not.

The gate is run as a SUBPROCESS in every verdict test, because its subject is a
tree rather than a function — which is how pre-commit invokes it in a consumer.
"""

import subprocess
import sys
from pathlib import Path

import yaml

GATE = Path(__file__).resolve().parents[2] / "hooks" / "chart_baseline.py"

# THE TWO PUBLISHED HALVES (ledger 911, ADR-0685). `--part` has no default in the
# gate, so every run below names one; `run()` defaults to the deployment half because
# that is what all but a handful of these cases are about, and the policy cases pass
# `part=POLICY` explicitly rather than relying on a filter.
BASELINE = "chart-baseline"
POLICY = "chart-network-policy"


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


def run(root: Path, *arguments, part: str = BASELINE):
    return subprocess.run(
        [sys.executable, str(GATE), "--part", part, "--root", str(root), *arguments],
        capture_output=True,
        text=True,
    )


def findings_of(result) -> str:
    """Only the per-finding lines of a run.

    LEDGER 911 — THE SCOPE LINE QUOTES THE PHRASES THE FINDINGS USE, so a bare
    `"declares `kind: NetworkPolicy`" in result.stdout` now matches
    "examined whether a template declares `kind: NetworkPolicy`" and holds whatever the
    gate found. That made this file's own fail-open case vacuous: it passed against a
    deliberately naive split whose policy half returned "does not balance" and asserted
    nothing about the policy at all, caught only by re-running the mutation and reading
    the output. A legibility line is a new way for a message assertion to pass for the
    wrong reason, so message assertions read the findings and not the summary.

    A finding line is `<chart> [<field>] <message>`; the summary and scope lines start
    with the hook id and the `--report` table lines are indented.
    """
    return "\n".join(
        line
        for line in result.stdout.splitlines()
        if not line.startswith((BASELINE, POLICY)) and not line.startswith("  ")
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
        [sys.executable, str(GATE), "--part", BASELINE],
        cwd=tmp_path,
        capture_output=True,
        text=True,
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


# ------------------------------- the NetworkPolicy, now `chart-network-policy`
#
# These three cases are RETARGETED rather than rewritten (ledger 911). The assertion
# they exercise did not change — it moved to the half ADR-0685 split it into — so they
# keep asserting the same red and the same green through `part=POLICY`. Deleting them
# because the other half no longer carries the check is how a split loses coverage.


def test_a_chart_with_no_network_policy_template_is_refused(tmp_path):
    write_chart(tmp_path, templates__networkpolicy_yaml=None)
    result = run(tmp_path, part=POLICY)
    assert result.returncode == 1
    assert "declares `kind: NetworkPolicy`" in findings_of(result)


def test_a_policy_defaulting_to_disabled_passes(tmp_path):
    """D80: the template must exist; whether it is ON is the deployment's business."""
    assert "enabled: false" in VALUES
    write_chart(tmp_path)
    result = run(tmp_path, part=POLICY)
    assert result.returncode == 0, result.stdout


def test_the_policy_is_found_by_kind_not_by_filename(tmp_path):
    write_chart(
        tmp_path,
        templates__networkpolicy_yaml=None,
        templates__ingress_rules_yaml=NETWORK_POLICY,
    )
    result = run(tmp_path, part=POLICY)
    assert result.returncode == 0, result.stdout


# --------------------------------------------- the kind: line, three ways wrong
#
# LEDGER 911: THE THREE POLICY CASES HERE RUN ON `part=POLICY`, and the two asserting
# exit 0 are why that matters. The deployment half no longer reads the policy's `kind:`
# line at all, so `kind: NetworkPolicy  # one ingress policy` and
# `kind: "NetworkPolicy"` would have gone green there for a reason that has nothing to
# do with the regex the case exists to pin — a passing test asserting nothing. The
# `kind: Deployment` case below stays on the deployment half: subjecthood is that
# half's question. This is the coverage a split loses quietly if the tests are moved by
# exit code rather than by subject.


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
    result = run(tmp_path, part=POLICY)
    assert result.returncode == 0, result.stdout


def test_a_quoted_kind_value_does_not_hide_it(tmp_path):
    """`kind: "NetworkPolicy"` used to be a false refusal too."""
    write_chart(
        tmp_path,
        templates__networkpolicy_yaml=swap(
            NETWORK_POLICY, "kind: NetworkPolicy\n", 'kind: "NetworkPolicy"\n'
        ),
    )
    result = run(tmp_path, part=POLICY)
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
    result = run(tmp_path, part=POLICY)
    assert result.returncode == 1
    assert "declares `kind: NetworkPolicy`" in findings_of(result)


# ---------------------------- the three floors, EVALUATED IN EACH HALF
#
# LEDGER 911. Splitting a gate multiplies the ways it can go blind rather than moving
# them: whichever half is weaker can report success having examined nothing, which is
# ledger 715's class reintroduced on one side. So every one of the three floors is
# exercised in BOTH halves, by looping over the two parts rather than by trusting the
# shared code path — a floor read from `part.minimum_*` can be wired to the wrong
# part's number, and only running both catches that.


def test_a_tree_with_no_chart_is_refused_rather_than_passed(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
    for part in (BASELINE, POLICY):
        result = run(tmp_path, part=part)
        assert result.returncode == 1, (part, result.stdout)
        assert "below the floor" in result.stdout, part
        assert "nothing for this gate to judge" in result.stdout, part
        assert result.stdout.startswith(f"{part}:"), part


def test_a_chart_that_renders_no_deployment_is_unjudged_and_trips_the_second_floor(
    tmp_path,
):
    """Charts found, none judged — what a broken `kind: Deployment` scan looks like."""
    write_chart(
        tmp_path,
        templates__deployment_yaml=None,
        templates__serviceaccount_yaml=SERVICE_ACCOUNT,
    )
    for part in (BASELINE, POLICY):
        result = run(tmp_path, "--report", part=part)
        assert result.returncode == 1, (part, result.stdout)
        assert "not judged — renders no Deployment" in result.stdout, part
        assert "assertions evaluated, below the floor" in result.stdout, part


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
    for part in (BASELINE, POLICY):
        result = run(tmp_path, "--report", part=part)
        assert result.returncode == 1, (part, result.stdout)
        assert "good: PASS" in result.stdout, part
        assert "unexamined: not judged" in result.stdout, part
        assert "2 charts found, 1 judged" in result.stdout, part
        assert "never examined: unexamined" in result.stdout, part


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


# LEDGER 897 ITEM 6, AND A DELIBERATE CONTRACT CHANGE. The test that stood here
# asserted that a Deployment spelled outside `templates/deployment.yaml` is REFUSED,
# which is what the shipped gate did. It is replaced rather than deleted, because the
# shape it covered is now covered by two tests instead of one, and both halves of the
# pair are asserted.
#
# WHY THE CONTRACT MOVED. `template.first()` read `templates/deployment.yaml` while
# `declared_kinds` scanned all of `templates/`, so a chart with its workload at
# another filename was judged against whatever sat at that path. Measured on a
# scratch copy of `iam`'s real chart with the workload moved to `workload.yaml` and a
# ConfigMap left at `deployment.yaml`: FIVE findings, of which FOUR were false — the
# real template sets every field they named. Selecting the file BY its
# `kind: Deployment` declaration is the property the ServiceAccount and NetworkPolicy
# checks already have, and it makes the gate STRICTER: the Deployment is judged
# wherever it is, rather than unjudged whenever it moves. The ambiguous shape — more
# than one declaring template — is the one that is now refused, with one accurate
# message instead of four false ones.


def test_a_deployment_spelled_outside_the_expected_path_is_judged_there(tmp_path):
    """The pod spec is found by its `kind:`, not by its filename."""
    for grace, expected in ((35, 0), (29, 1)):
        root = tmp_path / f"tree{grace}"
        root.mkdir()
        write_chart(
            root,
            templates__deployment_yaml=(
                "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: cm\ndata:\n  a: b\n"
            ),
            templates__workload_yaml=DEPLOYMENT,
            values_yaml=swap(
                VALUES,
                "terminationGracePeriodSeconds: 35",
                f"terminationGracePeriodSeconds: {grace}",
            ),
        )
        result = run(root)
        assert result.returncode == expected, result.stdout
        if expected:
            # The finding names the file that actually holds the pod spec.
            assert "templates/workload.yaml" in result.stdout
            assert "below the floor of 35s" in result.stdout


def test_two_templates_declaring_a_deployment_are_refused(tmp_path):
    """One pod spec per chart. Two, and there is no rule for choosing."""
    write_chart(tmp_path, templates__canary_yaml=DEPLOYMENT)
    result = run(tmp_path)
    assert result.returncode == 1
    assert "2 templates declare `kind: Deployment`" in result.stdout
    assert "canary.yaml, deployment.yaml" in result.stdout


# ------------------------------------------- ledger 897: the false refusals
#
# EVERY CASE BELOW IS A PAIR ON THE SAME TREE — the legitimate shape PASSES and a
# genuinely broken variant of that same shape still REFUSES. A fix that only makes
# things pass has removed a check rather than corrected one, and the gate's first
# revision is this file's own evidence for why that matters: it reported "sets no
# `maxSkew`" against all seven correct charts.
#
# THE SIX SHAPES THE LEDGER NAMED, plus three found while reproducing them, each
# measured against a scratch copy of `iam`'s real chart on 2026-09-14 before being
# fixed here.

# The mapping-guard shape, spelled the way Helm actually renders it: inside
# `{{- with .Values.topologySpread }}` the dot is REBOUND to the mapping, so
# `.Values` is unreachable from that scope and `{{ .maxSkew }}` is the only legal
# spelling of the inner references. A fixture keeping `{{ .Values.topologySpread.* }}`
# inside the `with` would not render under Helm at all.
WITH_MAPPING = (
    DEPLOYMENT.replace(
        "{{- if .Values.topologySpread.enabled }}", "{{- with .Values.topologySpread }}"
    )
    .replace("{{ .Values.topologySpread.maxSkew }}", "{{ .maxSkew }}")
    .replace("{{ .Values.topologySpread.topologyKey }}", "{{ .topologyKey }}")
    .replace(
        "{{ .Values.topologySpread.whenUnsatisfiable }}", "{{ .whenUnsatisfiable }}"
    )
)


def test_a_with_guard_over_a_mapping_is_seen_rather_than_read_as_off(tmp_path):
    """ITEM 1. `scan_values` recorded only SCALARS, so a mapping parent had no entry,
    `values.raw()` answered None and `is_truthy(None)` was False. The commonest Helm
    guard idiom therefore reported that `values.yaml` "leaves it off" about a key it
    plainly sets with four sub-keys.
    """
    write_chart(tmp_path, templates__deployment_yaml=WITH_MAPPING)
    result = run(tmp_path)
    assert result.returncode == 0, result.stdout
    assert "0 findings" in result.stdout


def test_the_same_mapping_guard_still_refuses_a_hard_spread(tmp_path):
    """The red half: the guard is seen, and the value behind it is still judged."""
    write_chart(
        tmp_path,
        templates__deployment_yaml=WITH_MAPPING,
        values_yaml=swap(
            VALUES, "whenUnsatisfiable: ScheduleAnyway", "whenUnsatisfiable: DoNotSchedule"
        ),
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "not `ScheduleAnyway`" in result.stdout


def test_a_mapping_guard_whose_mapping_is_absent_is_still_refused(tmp_path):
    """And the message says ABSENT rather than the unmodelled-construct wording."""
    write_chart(
        tmp_path,
        templates__deployment_yaml=WITH_MAPPING,
        values_yaml=VALUES.replace(
            """topologySpread:
  enabled: true
  maxSkew: 1
  topologyKey: kubernetes.io/hostname
  whenUnsatisfiable: ScheduleAnyway
""",
            "",
        ),
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "sets no `topologySpread` at all" in result.stdout


# ITEM 2. FIVE CAUSES PRINTED ONE MESSAGE, and it was accurate for one of them. The
# mechanism: `Values.raw()` collapses the NOT_MODELLED sentinel to the same None that
# absence returns, so the sentinel added for exactly this never reached a message.
# Measured before the fix — the anchor, the alias, the sequence, the UNQUOTED template
# expression and the deleted key all printed "values.yaml leaves it off".
#
# THE QUOTED template expression is NOT in this list, and the ledger's count of five
# is one too many on that point: `terminationGracePeriodSeconds: "{{ .Values.grace }}"`
# is a plain scalar to the scanner, resolves, and was already refused accurately with
# "which is not an integer number of seconds". It has its own case below.
GRACE_GUARD_CAUSES = [
    ("terminationGracePeriodSeconds: &grace 35", "a construct this gate does not model"),
    ("terminationGracePeriodSeconds: *grace", "a construct this gate does not model"),
    ("terminationGracePeriodSeconds:\n  - 35", "a construct this gate does not model"),
    (
        "terminationGracePeriodSeconds: {{ .Values.grace }}",
        "a construct this gate does not model",
    ),
    ("", "sets no `terminationGracePeriodSeconds` at all"),
    ("terminationGracePeriodSeconds: 0", "a value Go reads as false"),
    ("terminationGracePeriodSeconds: false", "a value Go reads as false"),
]


def test_each_guard_cause_prints_its_own_message_and_all_of_them_are_red(tmp_path):
    """ITEM 2. A gate must distinguish "the path is absent" from "the path is not a
    scalar I model". Every case here is still exit 1 — the exit code was never the
    defect — and each now says which of the four states it found.
    """
    for index, (replacement, expected) in enumerate(GRACE_GUARD_CAUSES):
        root = tmp_path / f"cause{index}"
        root.mkdir()
        # `*grace` needs an anchor to alias, and it must sit on another key.
        values = VALUES
        if replacement == "terminationGracePeriodSeconds: *grace":
            values = swap(values, "replicaCount: 2", "graceSource: &grace 35\nreplicaCount: 2")
        if replacement == "":
            values = swap(values, "terminationGracePeriodSeconds: 35\n", "")
        else:
            values = swap(values, "terminationGracePeriodSeconds: 35", replacement)
        write_chart(root, values_yaml=values)
        result = run(root)
        assert result.returncode == 1, result.stdout
        assert expected in result.stdout, (replacement, result.stdout)
        if "does not model" in expected:
            # The gate must not claim the key is unset when it cannot read it.
            assert "NOT a claim that" in result.stdout


def test_a_quoted_template_expression_in_values_was_already_accurate(tmp_path):
    """The fifth cause the ledger grouped with the other four. It is a plain scalar to
    the scanner, so it resolved and was refused with the true reason all along.
    """
    write_chart(
        tmp_path,
        values_yaml=swap(
            VALUES,
            "terminationGracePeriodSeconds: 35",
            'terminationGracePeriodSeconds: "{{ .Values.grace }}"',
        ),
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "not an integer number of seconds" in result.stdout


def test_the_cannot_be_resolved_branch_is_reachable_behind_a_truthy_guard(tmp_path):
    """`judge_grace`'s dedicated resolution branch was UNREACHABLE for every real
    chart: the key sits under a guard that failed first and returned. With a mapping
    guard that is truthy and an inner key that is a sequence, it is reached.
    """
    write_chart(
        tmp_path,
        templates__deployment_yaml=WITH_MAPPING,
        values_yaml=swap(VALUES, "  maxSkew: 1", "  maxSkew:\n    - 1"),
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "`maxSkew` cannot be resolved" in result.stdout
    assert "a construct this gate does not model" in result.stdout


# ITEM 3, AND THE SCOPE LINE. Counted on `origin/main` of the seven module
# repositories on 2026-09-14, per `chart/templates/deployment.yaml`: `required "…"`
# 50 uses (iam 12, gateway 12, task 7, project 7, the three -db charts 4 each) and
# `| quote` 113. Both are endorsed idiom and both yielded "an expression this gate
# does not model". `| default` has ZERO uses and iam's own chart carries two comments
# arguing against it by name, so it is deliberately NOT modelled — and an unmodelled
# wrapper is REFUSED, which is red rather than green.
GRACE_WRAPPERS = [
    '{{ required "grace must be set; see the chart\'s values.yaml" .Values.terminationGracePeriodSeconds }}',
    "{{ . | quote }}",
    '{{ required "grace must be set" . | quote }}',
    "{{ $.Values.terminationGracePeriodSeconds }}",
    '{{ required "iamDb.tls.caSecret holds the bundle; see .Values.iamDb; must be set" .Values.terminationGracePeriodSeconds }}',
]


def test_the_endorsed_wrappers_resolve_and_the_bound_still_holds(tmp_path):
    """ITEM 3, plus shape 7 (`$.Values.x`, which reaches the root context from inside
    a `with` and is the only spelling available there). Each wrapper is asserted in
    BOTH directions on the same tree: 35 passes, 29 is refused by the floor.
    """
    for index, wrapper in enumerate(GRACE_WRAPPERS):
        for grace, expected in ((35, 0), (29, 1)):
            root = tmp_path / f"wrap{index}-{grace}"
            root.mkdir()
            write_chart(
                root,
                templates__deployment_yaml=swap(
                    DEPLOYMENT,
                    "terminationGracePeriodSeconds: {{ . }}",
                    f"terminationGracePeriodSeconds: {wrapper}",
                ),
                values_yaml=swap(
                    VALUES,
                    "terminationGracePeriodSeconds: 35",
                    f"terminationGracePeriodSeconds: {grace}",
                ),
            )
            result = run(root)
            assert result.returncode == expected, (wrapper, grace, result.stdout)
            if expected:
                assert "below the floor of 35s" in result.stdout


def test_a_relative_reference_inside_a_with_resolves(tmp_path):
    """SHAPE 8, found while reproducing item 1 and required by it. Inside
    `{{- with .Values.topologySpread }}`, `{{ .maxSkew }}` IS
    `.Values.topologySpread.maxSkew` — and it is the ONLY spelling Helm renders there.
    Refusing it made item 1's legitimate shape unprovable as well as unusable.
    """
    write_chart(
        tmp_path,
        templates__deployment_yaml=WITH_MAPPING,
        values_yaml=swap(VALUES, "  maxSkew: 1", "  maxSkew: 0"),
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "`maxSkew` resolves to `0`" in result.stdout


def test_a_wrapper_this_gate_does_not_model_is_still_refused(tmp_path):
    """The scope line, asserted rather than left implicit. `| default` is not modelled
    because the estate does not write it, and the refusal direction is the safe one.
    """
    for wrapper in (
        "{{ .Values.terminationGracePeriodSeconds | default 30 }}",
        '{{ include "grace" . }}',
        "{{ add .Values.terminationGracePeriodSeconds 5 }}",
    ):
        root = tmp_path / wrapper[3:9].strip().replace(".", "")
        root.mkdir()
        write_chart(
            root,
            templates__deployment_yaml=swap(
                DEPLOYMENT,
                "terminationGracePeriodSeconds: {{ . }}",
                f"terminationGracePeriodSeconds: {wrapper}",
            ),
        )
        result = run(root)
        assert result.returncode == 1, (wrapper, result.stdout)
        assert "an expression this gate does not model" in result.stdout


# ITEM 4. `{{- else }}` was neither a `BLOCK_OPENER` nor `end`, so it was ignored
# entirely — one push, one pop, balance holds, and `template.balanced` could not see
# the problem. The if-condition's guard therefore propagated into the else arm, and
# `template.first()` judged the branch that does NOT render. All seven module charts
# already carry an inline `{{ else }}` on their image line, so the construct is live.
def grace_if_else(if_branch, else_branch):
    """The grace period behind an `{{- if }}`/`{{- else }}` pair, guard off."""
    text = swap(
        DEPLOYMENT,
        "      terminationGracePeriodSeconds: {{ . }}\n      {{- end }}\n",
        f"      terminationGracePeriodSeconds: {if_branch}\n"
        f"      {{{{- else }}}}\n"
        f"      terminationGracePeriodSeconds: {else_branch}\n"
        f"      {{{{- end }}}}\n",
    )
    return swap(
        text,
        "{{- with .Values.terminationGracePeriodSeconds }}",
        "{{- if .Values.topologySpread.absent }}",
    )


def test_the_else_branch_is_judged_rather_than_read_as_rendering_nothing(tmp_path):
    """ITEM 4. The arm that renders is the arm that is judged."""
    for else_branch, expected in ((35, 0), (29, 1)):
        root = tmp_path / f"else{else_branch}"
        root.mkdir()
        write_chart(root, templates__deployment_yaml=grace_if_else(99, else_branch))
        result = run(root)
        assert result.returncode == expected, result.stdout
        if expected:
            assert "below the floor of 35s" in result.stdout
        else:
            # The unreachable if-branch must not be reported either way.
            assert "0 findings" in result.stdout


def test_an_unbalanced_else_is_still_caught(tmp_path):
    """`else` must be neither a push nor a pop: an if/else/end is one opener and one
    `end`. Pushing a frame here would unbalance every chart in the estate.
    """
    write_chart(
        tmp_path,
        templates__deployment_yaml=swap(grace_if_else(99, 35), "      {{- end }}\n", ""),
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "do not balance" in result.stdout


def test_an_else_if_branch_is_refused_rather_than_guessed(tmp_path):
    """`{{- else if X }}` renders when the first condition is false AND X is true.
    That is a conjunction, not a path, so the gate refuses instead of modelling it.
    """
    text = swap(
        DEPLOYMENT,
        "      terminationGracePeriodSeconds: {{ . }}\n      {{- end }}\n",
        "      terminationGracePeriodSeconds: 99\n"
        "      {{- else if .Values.replicaCount }}\n"
        "      terminationGracePeriodSeconds: 35\n"
        "      {{- end }}\n",
    )
    text = swap(
        text,
        "{{- with .Values.terminationGracePeriodSeconds }}",
        "{{- if .Values.topologySpread.absent }}",
    )
    write_chart(tmp_path, templates__deployment_yaml=text)
    result = run(tmp_path)
    assert result.returncode == 1
    assert "condition this gate does not model" in result.stdout


# ITEM 5, THE ONE FAIL-OPEN, and the most important case in this file. The floor's
# sleep term was added only when the literal dotted path `preStopSleepSeconds`
# appeared in `template.references` AND in `values.yaml`. A term DISCOVERED BY NAME
# silently became 0 whenever the name was anything else. Measured against `iam`'s real
# chart with the sleep inlined as a literal 5 and the grace period cut to 32: 25
# assertions, 0 findings, EXIT 0 — a pod given 27s for a 25s drain plus a 5s exit.
def hardcoded_sleep(seconds):
    """`iam`'s preStop handler with the sleep inlined and the values key deleted."""
    text = swap(DEPLOYMENT, "            {{- with .Values.preStopSleepSeconds }}\n", "")
    return swap(
        text,
        "                seconds: {{ . }}\n            {{- end }}\n",
        f"                seconds: {seconds}\n",
    )


def test_a_hardcoded_prestop_sleep_is_priced_into_the_floor(tmp_path):
    """ITEM 5. A derived bound whose terms are discovered BY NAME must refuse when it
    cannot find a term it expects, never quietly compute a weaker bound. The sleep is
    read from the handler's own structure instead, so its name no longer matters.
    """
    for sleep, grace, expected, floor in (
        (5, 32, 1, 35),  # the measured fail-open: 25 assertions, 0 findings, exit 0
        (5, 34, 1, 35),
        (5, 35, 0, 35),
        (10, 35, 1, 40),
        (10, 40, 0, 40),
    ):
        root = tmp_path / f"sleep{sleep}-grace{grace}"
        root.mkdir()
        write_chart(
            root,
            templates__deployment_yaml=hardcoded_sleep(sleep),
            values_yaml=swap(
                swap(VALUES, "preStopSleepSeconds: 5\n", ""),
                "terminationGracePeriodSeconds: 35",
                f"terminationGracePeriodSeconds: {grace}",
            ),
        )
        result = run(root)
        assert result.returncode == expected, (sleep, grace, result.stdout)
        if expected:
            assert f"below the floor of {floor}s" in result.stdout
            assert f"+ {sleep} preStop sleep" in result.stdout


def test_a_differently_named_sleep_value_is_priced_into_the_floor(tmp_path):
    """The other half of the same fail-open: the right structure, the wrong name."""
    text = swap(DEPLOYMENT, "            {{- with .Values.preStopSleepSeconds }}\n", "")
    text = swap(
        text,
        "                seconds: {{ . }}\n            {{- end }}\n",
        "                seconds: {{ .Values.drainSleep }}\n",
    )
    for grace, expected in ((38, 0), (35, 1)):
        root = tmp_path / f"named{grace}"
        root.mkdir()
        write_chart(
            root,
            templates__deployment_yaml=text,
            values_yaml=swap(
                swap(VALUES, "preStopSleepSeconds: 5", "drainSleep: 8"),
                "terminationGracePeriodSeconds: 35",
                f"terminationGracePeriodSeconds: {grace}",
            ),
        )
        result = run(root)
        assert result.returncode == expected, result.stdout
        if expected:
            assert "below the floor of 38s" in result.stdout


def test_a_prestop_handler_this_gate_cannot_price_is_refused(tmp_path):
    """An `exec` handler's duration is not knowable from the chart, so the floor is
    UNKNOWABLE rather than smaller. Assuming 0 is the fail-open above.
    """
    text = swap(DEPLOYMENT, "            {{- with .Values.preStopSleepSeconds }}\n", "")
    text = swap(
        text,
        "              sleep:\n                seconds: {{ . }}\n            {{- end }}\n",
        "              exec:\n                command: [sh, -c, sleep 5]\n",
    )
    write_chart(tmp_path, templates__deployment_yaml=text)
    result = run(tmp_path)
    assert result.returncode == 1
    assert "no `sleep: seconds:` under it" in result.stdout
    assert "cannot be computed" in result.stdout


# SHAPE 9, found while fixing item 4 and the same defect in its fail-open direction.
# `guards` was built as `tuple(path for _, path in stack if path)`, so a frame whose
# condition did not match `VALUES_REFERENCE` contributed NOTHING and the key inside it
# was judged as though it always rendered. `{{- if and A B }}` (6 uses across the
# seven charts) and `{{- if $var }}` (8) both take that branch. None sits over a
# baseline key today, which is the only reason nothing was red.
def test_a_guard_condition_this_gate_cannot_model_is_refused_not_ignored(tmp_path):
    for condition in (
        "{{- if and .Values.topologySpread.enabled .Values.replicaCount }}",
        "{{- if $spread }}",
        "{{- if eq .Values.topologySpread.enabled true }}",
        "{{- range .Values.topologySpread.zones }}",
    ):
        root = tmp_path / condition[8:14].strip().replace("$", "").replace(".", "")
        root.mkdir()
        write_chart(
            root,
            templates__deployment_yaml=swap(
                DEPLOYMENT, "{{- if .Values.topologySpread.enabled }}", condition
            ),
        )
        result = run(root)
        assert result.returncode == 1, (condition, result.stdout)
        assert "condition this gate does not model" in result.stdout
        assert "REFUSED rather than ignored" in result.stdout


def test_a_negated_guard_is_modelled_in_both_directions(tmp_path):
    """`{{- if not X }}` renders when X is falsey, and `not` of an absent key renders
    too. Modelled rather than refused, because the seven charts write it seven times.
    """
    for condition, expected in (
        ("{{- if not .Values.autoscaling.absent }}", 0),
        ("{{- if not .Values.topologySpread.enabled }}", 1),
        ("{{- if true }}", 0),
        ("{{- if false }}", 1),
    ):
        root = tmp_path / str(abs(hash(condition)))
        root.mkdir()
        write_chart(
            root,
            templates__deployment_yaml=swap(
                DEPLOYMENT, "{{- if .Values.topologySpread.enabled }}", condition
            ),
        )
        result = run(root)
        assert result.returncode == expected, (condition, result.stdout)


# --------------------------- ledger 897: the SAME rule for every field
#
# THE ASYMMETRY THIS BLOCK EXISTS AGAINST, found by review after the first six fixes
# landed. The guard work had been wired into `terminationGracePeriodSeconds` and the
# `topologySpreadConstraints` presence check — the two shapes the ledger named — and
# the other five lookups still used `template.first()` and evaluated NO guard. Both
# ledger 897 defects survived there, one in each direction, and each was measured on
# a scratch copy of `iam`'s real chart:
#
#   * an `{{- if }}`/`{{- else }}` pair over `automountServiceAccountToken` reported
#     "resolves to `true`, not `false`" about a chart that renders `false`;
#   * `automountServiceAccountToken`, `serviceAccountName` and `whenUnsatisfiable`
#     behind a guard that is OFF each passed with EXIT 0 while rendering nothing.
#
# The third is the worst of the three: a `whenUnsatisfiable` that renders nothing
# takes the API default `DoNotSchedule`, which is precisely the hung rollout this
# gate exists to prevent, reported green.
#
# A fix applied field-by-field is how five of seven fields came to differ from the
# two that were fixed, so the test is written over the WHOLE field set rather than
# over the fields that were wrong. `Template.first()` is deleted, and `renders()` is
# the only way to reach an occurrence, so a new field cannot skip the guard check
# without going out of its way.
GUARDABLE_FIELDS = [
    ("serviceAccountName", "      serviceAccountName: {{ .Chart.Name }}"),
    ("automountServiceAccountToken", "      automountServiceAccountToken: false"),
    ("terminationGracePeriodSeconds", "      terminationGracePeriodSeconds: {{ . }}"),
    ("topologySpreadConstraints", "      topologySpreadConstraints:"),
    ("maxSkew", "        - maxSkew: {{ .Values.topologySpread.maxSkew }}"),
    ("topologyKey", "          topologyKey: {{ .Values.topologySpread.topologyKey }}"),
    (
        "whenUnsatisfiable",
        "          whenUnsatisfiable: {{ .Values.topologySpread.whenUnsatisfiable }}",
    ),
]


def test_every_baseline_field_is_refused_when_its_guard_renders_nothing(tmp_path):
    """The fail-open half, over all seven fields rather than the two that were fixed."""
    for field, line in GUARDABLE_FIELDS:
        indent = " " * (len(line) - len(line.lstrip(" ")))
        root = tmp_path / f"off-{field}"
        root.mkdir()
        write_chart(
            root,
            templates__deployment_yaml=swap(
                DEPLOYMENT,
                line,
                f"{indent}{{{{- if .Values.topologySpread.absent }}}}\n"
                f"{line}\n"
                f"{indent}{{{{- end }}}}",
            ),
        )
        result = run(root)
        assert result.returncode == 1, (field, result.stdout)
        assert f"renders `{field}` only when" in result.stdout, (field, result.stdout)
        assert "sets no `topologySpread.absent` at all" in result.stdout


def test_every_baseline_field_still_passes_behind_a_guard_that_is_on(tmp_path):
    """The other half of the pair: the guard check must not refuse a rendering field."""
    for field, line in GUARDABLE_FIELDS:
        indent = " " * (len(line) - len(line.lstrip(" ")))
        root = tmp_path / f"on-{field}"
        root.mkdir()
        write_chart(
            root,
            templates__deployment_yaml=swap(
                DEPLOYMENT,
                line,
                f"{indent}{{{{- if .Values.topologySpread.enabled }}}}\n"
                f"{line}\n"
                f"{indent}{{{{- end }}}}",
            ),
        )
        result = run(root)
        assert result.returncode == 0, (field, result.stdout)


def test_an_else_branch_is_judged_for_the_service_account_too(tmp_path):
    """ITEM 4 over a field it was not originally wired into. The shipped gate read the
    if-branch and reported `true`, not `false`, about a chart that renders `false`.
    """
    for if_branch, else_branch, expected in (
        ("true", "false", 0),
        ("false", "true", 1),
    ):
        root = tmp_path / f"sa-else-{if_branch}"
        root.mkdir()
        write_chart(
            root,
            templates__deployment_yaml=swap(
                DEPLOYMENT,
                "      automountServiceAccountToken: false",
                "      {{- if .Values.topologySpread.absent }}\n"
                f"      automountServiceAccountToken: {if_branch}\n"
                "      {{- else }}\n"
                f"      automountServiceAccountToken: {else_branch}\n"
                "      {{- end }}",
            ),
        )
        result = run(root)
        assert result.returncode == expected, (if_branch, result.stdout)
        if expected:
            assert "not `false`" in result.stdout


# ------------------------------- ledger 911: the split, and what each half says
#
# ADR-0685 split this gate along the boundary of where its subjects live. Three of the
# four baseline fields are properties of the chart's own pod spec; the fourth asks for a
# `kind: NetworkPolicy` template, and `yadgarhq/gateway`'s policy legitimately lives in
# `yadgarhq/deploy` instead. One correct assertion was costing that repository the other
# three, because ADR-0584 forbids adopting a gate in a repository it hard-fails.
#
# NO EXEMPTION IS TESTED HERE BECAUSE THERE IS NONE. ADR-0685 rejects keying one on
# `kind: HTTPRoute` by name: it discriminates `gateway` from the other six today, but it
# encodes "ships an HTTPRoute" while the reason is "is edge-facing", so the next
# edge-facing module would inherit the exemption with nothing red. The split needs no
# exemption, and that is what these cases pin.


def test_a_part_must_be_named(tmp_path):
    """No default, so no invocation can mean one half without saying so."""
    write_chart(tmp_path)
    result = subprocess.run(
        [sys.executable, str(GATE), "--root", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2, result.stderr
    assert "--part" in result.stderr


def test_the_gateway_shape_passes_the_baseline_half_and_fails_the_policy_half(tmp_path):
    """THE CASE THE SPLIT EXISTS FOR, as a pair on one tree.

    A chart with the three deployment fields correct and no policy template — the shape
    `yadgarhq/gateway` ships — is now judged by both halves and refused by exactly one.
    Before the split the single gate refused the whole chart, so `gateway` forfeited
    three correct assertions to one it cannot satisfy in-tree.
    """
    write_chart(tmp_path, templates__networkpolicy_yaml=None)
    passing = run(tmp_path, part=BASELINE)
    assert passing.returncode == 0, passing.stdout
    assert findings_of(passing) == "", passing.stdout
    refused = run(tmp_path, part=POLICY)
    assert refused.returncode == 1, refused.stdout
    assert "declares `kind: NetworkPolicy`" in findings_of(refused)


def test_the_two_halves_sum_to_the_assertions_the_one_gate_evaluated(tmp_path):
    """No assertion vanished in the split (ADR-0645's direction).

    The pre-split gate evaluated 30 per conforming chart. The halves evaluate 29 and 1,
    over the SAME chart set, so the sum is the invariant that says the fourth check
    moved rather than being dropped. Read off the output rather than hardcoded on one
    side: the sum is the claim, not either number.
    """
    write_chart(tmp_path)
    counts = {}
    for part in (BASELINE, POLICY):
        result = run(tmp_path, part=part)
        assert result.returncode == 0, (part, result.stdout)
        assert "1 charts, 1 judged" in result.stdout, part
        counts[part] = int(
            result.stdout.split(" assertions evaluated")[0].rsplit(" ", 1)[1]
        )
    assert counts[POLICY] == 1, counts
    assert counts[BASELINE] + counts[POLICY] == 30, counts


def test_each_half_names_what_it_did_not_examine_on_a_pass(tmp_path):
    """ADR-0685: partial coverage is legible in the SUCCESS line.

    A green line that did not say so would silently change meaning at this commit —
    `chart-baseline` meant four fields before the split and three after it.
    """
    write_chart(tmp_path)
    for part, examined, declined in (
        (BASELINE, "grace period", "`kind: NetworkPolicy`"),
        (POLICY, "`kind: NetworkPolicy`", "the grace period"),
    ):
        result = run(tmp_path, part=part)
        assert result.returncode == 0, (part, result.stdout)
        assert f"{part}: examined " in result.stdout, part
        head, _, tail = result.stdout.partition("It did NOT examine")
        assert examined in head, (part, result.stdout)
        assert declined in tail, (part, result.stdout)
        # It must not imply the other half ran: this process cannot see the consumer's
        # `.pre-commit-config.yaml`, and ADR-0577 makes adoption per repository.
        assert "cannot say whether this repository references it" in tail, part
        assert "verdict over part of a chart's baseline" in tail, part


def test_each_half_names_what_it_did_not_examine_on_a_refusal_too(tmp_path):
    """The scope line is not a green-path decoration: a red verdict is equally partial."""
    write_chart(tmp_path, templates__networkpolicy_yaml=None, values_yaml=swap(
        VALUES, "terminationGracePeriodSeconds: 35", "terminationGracePeriodSeconds: 29"
    ))
    for part in (BASELINE, POLICY):
        result = run(tmp_path, part=part)
        assert result.returncode == 1, (part, result.stdout)
        assert f"{part}: examined " in result.stdout, part
        assert "It did NOT examine" in result.stdout, part


def test_the_policy_half_reads_no_pod_spec_and_no_values_file(tmp_path):
    """THE FAIL-OPEN THIS SPLIT WOULD OTHERWISE HAVE SHIPPED.

    The pre-split `judge_chart` asserts `template.balanced` and RETURNS on failure, so a
    chart whose Go template does not balance never reaches the policy check. Had the
    policy half reused that path it would have reported `judged=True` having asserted
    NOTHING about the policy — a chart with no `kind: NetworkPolicy` passing because a
    DIFFERENT file is malformed. Both halves are run on the same tree: the deployment
    half refuses the unbalanced template, and the policy half still refuses the missing
    policy.
    """
    write_chart(
        tmp_path,
        templates__networkpolicy_yaml=None,
        templates__deployment_yaml=swap(DEPLOYMENT, "      {{- end }}\n", "", ),
    )
    unbalanced = run(tmp_path, part=BASELINE)
    assert unbalanced.returncode == 1, unbalanced.stdout
    assert "do not balance" in unbalanced.stdout
    policy = run(tmp_path, part=POLICY)
    assert policy.returncode == 1, policy.stdout
    # The policy assertion is the ONLY finding: no structure finding stood in for it.
    assert findings_of(policy).count("\n") == 0, policy.stdout
    assert "declares `kind: NetworkPolicy`" in findings_of(policy), policy.stdout
    assert "balance" not in findings_of(policy), policy.stdout


def test_two_deployments_refuse_the_baseline_half_and_pass_the_policy_half(tmp_path):
    """A DELIBERATE BEHAVIOUR DELTA, named so a reviewer does not have to find it.

    Two `kind: Deployment` templates leave the deployment half with no rule for choosing
    a pod spec, so it refuses. A policy template is not a pod spec, so the policy half
    judges the chart and passes. No module chart has this shape, which is why the
    seven-tree sweep cannot show it.
    """
    write_chart(tmp_path, templates__second_yaml=DEPLOYMENT)
    refused = run(tmp_path, part=BASELINE)
    assert refused.returncode == 1, refused.stdout
    assert "templates declare `kind: Deployment`" in refused.stdout
    judged = run(tmp_path, part=POLICY)
    assert judged.returncode == 0, judged.stdout
    assert "1 charts, 1 judged, 1 assertions" in judged.stdout


# ---------------- ledger 911: the residual the ledger 897 car filed, both ways
#
# `prestop_sleep` discovered its `seconds` term by UNQUALIFIED KEY NAME across the whole
# deployment template. `SleepAction` is equally valid under `postStart`, and a
# `postStart` sleep runs at STARTUP rather than during the drain, so pricing it into the
# grace-period floor is a false refusal — the exact class ledger 897 closed. The filing
# believed a positional model was needed; it needed one `indent` field on
# `KeyOccurrence`, one line-ordered tuple on `Template`, and `block_of`.


def with_post_start(seconds):
    """The conforming chart's lifecycle block, with a `postStart` sleep added."""
    return swap(
        DEPLOYMENT,
        "          lifecycle:\n",
        "          lifecycle:\n"
        "            postStart:\n"
        "              sleep:\n"
        f"                seconds: {seconds}\n",
    )


def test_a_post_start_sleep_is_not_priced_into_the_grace_floor(tmp_path):
    """The false refusal, and the paired red that shows the floor still binds.

    Measured on `yadgarhq/iam`'s real chart at `origin/main` before the fix: a
    `postStart: sleep: seconds: 20` beside its own 5s `preStop` reported
    "`terminationGracePeriodSeconds: 35` is below the floor of 50s (25 DRAIN_BUDGET + 5
    exit margin + 20 preStop sleep)" — a number no `preStop` in that chart sets.
    """
    for post_start, grace, expected, floor in (
        (20, 35, 0, None),  # the false refusal: 35 covers the 5s preStop, not the 20
        (20, 29, 1, 35),  # the floor still binds on the preStop sleep alone
        (1, 29, 1, 35),  # and does not move when the postStart sleep shrinks
    ):
        root = tmp_path / f"post{post_start}-grace{grace}"
        root.mkdir()
        write_chart(
            root,
            templates__deployment_yaml=with_post_start(post_start),
            values_yaml=swap(
                VALUES,
                "terminationGracePeriodSeconds: 35",
                f"terminationGracePeriodSeconds: {grace}",
            ),
        )
        result = run(root)
        assert result.returncode == expected, (post_start, grace, result.stdout)
        if expected:
            assert f"below the floor of {floor}s" in result.stdout, result.stdout
            assert "+ 5 preStop sleep" in result.stdout, result.stdout


def test_a_post_start_sleep_does_not_satisfy_the_prestop_exec_refusal(tmp_path):
    """THE SAME LOOKUP'S FAIL-OPEN HALF, which the filing did not record.

    A `preStop` this gate cannot price — an `exec` handler — must be REFUSED, because
    assuming zero is the fail-open ADR-0601's bound exists to close. The unqualified
    lookup let a `postStart` sleep satisfy that check and be priced as the drain sleep
    instead. Measured on `iam`'s real chart with `preStop: exec:`, a 7s `postStart` sleep
    and the grace period at 40, the shipped v1.21.1 gate reports "OK — 1 charts, 1
    judged, 30 assertions evaluated, 0 findings", exit 0.
    """
    text = swap(with_post_start(7), "            {{- with .Values.preStopSleepSeconds }}\n", "")
    text = swap(
        text,
        "              sleep:\n                seconds: {{ . }}\n            {{- end }}\n",
        "              exec:\n                command: [sh, -c, sleep 5]\n",
    )
    write_chart(
        tmp_path,
        templates__deployment_yaml=text,
        values_yaml=swap(
            VALUES, "terminationGracePeriodSeconds: 35", "terminationGracePeriodSeconds: 40"
        ),
    )
    result = run(tmp_path)
    assert result.returncode == 1, result.stdout
    assert "no `sleep: seconds:` under it" in result.stdout


def test_a_sleep_outside_any_prestop_block_is_not_a_prestop_sleep(tmp_path):
    """`block_of` is indentation-scoped, so a `seconds:` elsewhere in the pod spec is
    not the handler's.

    A `postStart` handler is the shape the estate could actually grow; this is the
    general property under it. The stray key sits at CONTAINER level rather than under
    any handler, and the pre-ledger-911 lookup took it: `max(5, 99)` gives a floor of
    129 and refuses a chart shipping the estate's own 35.
    """
    text = swap(
        DEPLOYMENT,
        "          image: example\n",
        "          image: example\n          seconds: 99\n",
    )
    write_chart(tmp_path, templates__deployment_yaml=text)
    result = run(tmp_path)
    assert result.returncode == 0, result.stdout
    assert "1 charts, 1 judged" in result.stdout


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


def structure(text: str):
    """The mapping and null paths the hand scan records, for the guard differential."""
    sys.path.insert(0, str(GATE.parent))
    try:
        import chart_baseline
    finally:
        sys.path.pop(0)
    values = chart_baseline.scan_values(text)
    assert values.modelled, values.reason
    return set(values.mappings), set(values.nulls)


def branches(node, prefix: str = ""):
    """Every path PyYAML resolves to a NON-EMPTY mapping, and every one it calls None."""
    mappings, nulls = set(), set()
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict) and value:
                mappings.add(path)
            if value is None:
                nulls.add(path)
            deeper = branches(value, path)
            mappings |= deeper[0]
            nulls |= deeper[1]
    return mappings, nulls


def test_the_mapping_model_agrees_with_pyyaml_too():
    """LEDGER 897 ITEM 1 ADDED MODELLED STATE, so it gets the same evidence the scalar
    scan has. `Values.mappings` decides whether `{{- with .Values.X }}` renders, which
    is a verdict on the chart — a hand scan that guesses it wrong turns a green chart
    red, which is the failure this whole file is organised against.

    THE TRAP THIS CAUGHT, on the real files rather than on this corpus: a block
    sequence is written `clients:` and then `- gateway`, so `clients` is opened exactly
    like a mapping and never gains a child entry. Classifying it by "opened but not a
    parent" called it YAML null in `iam` and `project-db`, where it is a one-item list.
    Paths the scalar scan already recorded are excluded from both sets for that reason.
    """
    mappings, nulls = structure(CORPUS)
    assert (mappings, nulls) == branches(yaml.safe_load(CORPUS))


def test_a_non_empty_mapping_is_truthy_and_an_absent_one_is_not():
    """The four guard states, asserted directly rather than only through a verdict."""
    sys.path.insert(0, str(GATE.parent))
    try:
        import chart_baseline
    finally:
        sys.path.pop(0)
    values = chart_baseline.scan_values(CORPUS)
    assert values.truth("topologySpread") == chart_baseline.GUARD_ON
    assert values.truth("nested.deeper") == chart_baseline.GUARD_ON
    assert values.truth("networkPolicy.enabled") == chart_baseline.GUARD_OFF
    assert values.truth("networkPolicy.absent") == chart_baseline.GUARD_ABSENT
    # A sequence and a flow mapping are undecided, NOT off. This is the distinction
    # `raw()` collapsed and the whole of item 2 turns on.
    assert values.truth("networkPolicy.clients") == chart_baseline.GUARD_UNMODELLED
    assert values.truth("networkPolicy.scrapeFrom") == chart_baseline.GUARD_UNMODELLED


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
