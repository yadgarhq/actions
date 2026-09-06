"""What `d80_portability.py` REFUSES, pinned so it cannot quietly stop refusing.

LEDGER 731, AND THE SAME CLASS AS 625. This gate runs in the `portability` job
of `ci-pr.yaml` on every pull request in every consumer repository, and until
this file nothing exercised it. D80 was promoted to an invariant precisely
because an invariant held by attention decays when attention is scarce; a gate
held by nothing at all is the same failure one level down.

MOST OF THIS FILE IS RED CASES, for the reason `test_helm_pin_agrees.py` gives:
a suite that only feeds a gate conforming input certifies the fixture rather
than the gate. Every refusal below is one the gate MUST make, and the suite goes
red the day it stops making it.

AND EVERY GREEN CASE IS PAIRED WITH A RED ONE IN THE SAME TREE, which is the
other half of that argument and the half easier to forget. `aws-lc-rs` must not
trip the cloud-SDK check — that is a real regression the script's own comment
records — but a test that only asserts "this tree passes" also passes when the
check is dead. So `aws-lc-rs` is fed alongside `aws-sdk-s3`, and the assertion is
that the gate refuses and names ONE of them. The same pairing covers
`x-forwarded-for` against `x-envoy-*`, `proto/` against a scanned directory, an
exemption on the line above against one two lines above, and `yadgarhq/deploy`
against a repository with no exemption reading the identical tree.

WHY `helm` IS A STUB HERE, said plainly rather than discovered later. The gate's
ENTIRE contract with helm is `argv -> (returncode, multi-document YAML on
stdout)`: `helm_render` runs one command, checks the return code, and every
verdict after that derives from the parsed documents. A stub honouring that
contract is therefore a complete substitute FOR TESTING THE GATE, and it is the
only option that runs in all four places this suite has to run — a bare
checkout, the `pytest-scripts` pre-commit hook (`language: python`, which
provisions an interpreter and cannot provision a binary), the `pytest` job in
`ci-self.yaml`, and the `precommit` job in `ci-pr.yaml`. A `skipif` on the
binary is the shape ADR-0578 rejects: it would report green having measured
nothing in three of those four.

WHAT THIS SUITE THEREFORE DOES NOT PROVE, in the gate's own idiom: that a real
Go-templated chart emits the documents the stub emits. It proves what the gate
concludes FROM a set of rendered documents, and that the values it hands the
second render are the ones that turn a chart's optional parts off. The stub is
values-driven for exactly that reason — `flip_enabled`'s output decides what
renders, so the seam is under test rather than mocked past.

Run: python3 -m pytest scripts/tests/ -q
"""

import subprocess
import sys
from pathlib import Path

GATE = Path(__file__).resolve().parents[1] / "d80_portability.py"
REPO = Path(__file__).resolve().parents[2]

# A STAND-IN FOR `helm template`, and nothing more of helm than the gate uses.
#
#   values          `<chart>/values.yaml`, with every `-f` file merged over it,
#                   the way helm merges them.
#   `_render`       the documents this chart emits. An entry carrying `when:
#                   a.dotted.path` renders only while that path is truthy, which
#                   is how a real chart guards a resource behind an `enabled`
#                   key — and is what makes `flip_enabled` decide the second
#                   render rather than the stub.
#   `@IMAGE@`       substituted the way every real chart in this estate writes
#                   its image reference: `repository@digest` WHEN A DIGEST IS
#                   SET, and `repository:tag` otherwise. That precedence is not
#                   cosmetic — see the note under `substitute` below.
#   `_fail`         render fails outright.
#   `_fail_unless`  render fails while the named path is falsy, which is the
#                   chart that cannot be turned off without breaking.
STUB_HELM = '''
"""A stand-in for `helm template`. See test_d80_portability.py for the contract."""
import sys

import yaml


def merge(base, over):
    for key, value in (over or {}).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge(base[key], value)
        else:
            base[key] = value
    return base


def get(values, path):
    node = values
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


argv = sys.argv[1:]
if not argv or argv[0] != "template":
    sys.stderr.write("stub helm: only `helm template` is modelled\\n")
    sys.exit(2)

positional, overrides, rest = [], [], argv[1:]
index = 0
while index < len(rest):
    if rest[index] in ("-f", "--values"):
        overrides.append(rest[index + 1])
        index += 2
    else:
        positional.append(rest[index])
        index += 1

chart = positional[1] if len(positional) > 1 else "."
try:
    values = yaml.safe_load(open(chart + "/values.yaml").read()) or {}
except OSError as exc:
    sys.stderr.write("stub helm: %s\\n" % exc)
    sys.exit(1)
for path in overrides:
    merge(values, yaml.safe_load(open(path).read()) or {})

if values.get("_fail"):
    sys.stderr.write("stub helm: `_fail` is set in the values\\n")
    sys.exit(1)
guard = values.get("_fail_unless")
if guard and not get(values, guard):
    sys.stderr.write("stub helm: this chart does not render while `%s` is false\\n" % guard)
    sys.exit(1)

# DIGEST WINS OVER TAG, copied from `chart/templates/deployment.yaml` in iam,
# task and gateway: `{{ if .Values.image.digest }}@{{ ... }}{{ else }}:{{ tag }}`.
# It is load-bearing rather than incidental. The gate reproduces D65's rewrite by
# passing values through `-f`, and a `-f` file MERGES: it can set `.image.digest`
# but it cannot delete the `.image.tag` sitting in `chart/values.yaml`. So the
# published shape is reached only because the template prefers the digest. A
# stub preferring the tag would report the gate broken; a chart preferring the
# tag would BE broken, and no check here would see it.
image = values.get("image") or {}
if image.get("digest"):
    reference = "%s@%s" % (image.get("repository", ""), image["digest"])
else:
    reference = "%s:%s" % (image.get("repository", ""), image.get("tag", ""))


def substitute(node):
    if isinstance(node, dict):
        return {k: substitute(v) for k, v in node.items()}
    if isinstance(node, list):
        return [substitute(v) for v in node]
    if isinstance(node, str):
        return node.replace("@IMAGE@", reference)
    return node


documents = []
for document in values.get("_render") or []:
    when = document.get("when")
    if when is not None and not get(values, when):
        continue
    documents.append(substitute({k: v for k, v in document.items() if k != "when"}))
sys.stdout.write(yaml.safe_dump_all(documents))
'''


def tree(tmp_path, files):
    """Lay out a repository under review and return its root.

    The stub lives OUTSIDE that root: the gate walks `.` for source-tree
    tripwires, and a fixture that scanned the test rig would be measuring this
    file rather than the repository.
    """
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    binaries = tmp_path / "stub-bin"
    binaries.mkdir(exist_ok=True)
    helm = binaries / "helm"
    # The interpreter running this suite, rather than whatever `python3` resolves
    # to: the gate itself is spawned with `sys.executable` and needs PyYAML, so
    # that interpreter is the one already known to carry it.
    helm.write_text("#!" + sys.executable + "\n" + STUB_HELM)
    helm.chmod(0o755)
    return root


def run(root, repo="yadgarhq/iam", summary=None):
    environment = {
        "D80_REPO": repo,
        "PATH": str(root.parent / "stub-bin") + ":/usr/bin:/bin",
    }
    if summary:
        environment["GITHUB_STEP_SUMMARY"] = str(summary)
    return subprocess.run(
        [sys.executable, str(GATE)],
        capture_output=True,
        text=True,
        cwd=str(root),
        env=environment,
    )


# A container carrying whatever a test needs to say about it. Everything below
# renders through the stub's `@IMAGE@`, so the release-shaped render is exercised
# by every chart fixture rather than only by the three that assert on it.
def container(extra=""):
    return (
        "    spec:\n"
        "      template:\n"
        "        spec:\n"
        "          containers:\n"
        "            - name: app\n"
        '              image: "@IMAGE@"\n' + extra
    )


DEPLOYMENT = (
    "  - apiVersion: apps/v1\n"
    "    kind: Deployment\n"
    "    metadata:\n"
    "      name: app\n"
)

SCALED_OBJECT = (
    "  - apiVersion: keda.sh/v1alpha1\n"
    "    kind: ScaledObject\n"
    "    metadata:\n"
    "      name: worker\n"
)


def guarded(document, path):
    """The same document, rendered only while `path` is truthy — a real chart's
    `{{- if .Values.<path> }}`, expressed the way the stub reads it."""
    return f"  - when: {path}\n" + document.replace("  - ", "    ", 1)


# ------------------------------------ the property: can an adopter turn it off?


def test_a_crd_no_value_can_turn_off_is_refused(tmp_path):
    """THE PROPERTY THIS GATE EXISTS FOR, and the case it must never miss.

    A `keda.sh` resource behind no `enabled` key survives the all-off render, so
    the chart cannot install on a cluster without KEDA's CRDs.
    """
    root = tree(
        tmp_path,
        {
            "chart/values.yaml": "_render:\n"
            + DEPLOYMENT
            + container()
            + SCALED_OBJECT
        },
    )
    result = run(root)
    assert result.returncode == 1, result.stdout
    # It names the resource, the group and what to do about it.
    assert "ScaledObject/worker" in result.stdout
    assert "keda.sh" in result.stdout
    assert "every `enabled` key false" in result.stdout


def test_the_same_resource_behind_an_enabled_key_passes(tmp_path):
    """THE PAIR, and the seam it holds down is `flip_enabled`.

    Identical to the test above but for the guard. If `flip_enabled` ever stopped
    flipping — or stopped reaching a nested key — the second render would keep
    the ScaledObject and THIS test goes red, not the one above it.
    """
    root = tree(
        tmp_path,
        {
            "chart/values.yaml": "keda:\n  enabled: true\n_render:\n"
            + DEPLOYMENT
            + container()
            + guarded(SCALED_OBJECT, "keda.enabled")
        },
    )
    result = run(root)
    assert result.returncode == 0, result.stdout
    assert "keda.enabled" in result.stdout
    assert "**Yes.** The all-off render contains only built-in" in result.stdout


def test_a_gateway_route_still_has_to_be_switchable_off(tmp_path):
    """WHERE THE TABLE AND THE VERDICT DELIBERATELY DISAGREE.

    `gateway.networking.k8s.io` is classified SPECIFICATION and D80 permits
    depending on it — but `classify()` feeds the prose and the notes only. The
    property is that EVERY CRD-bearing resource can be switched off, so an
    unguarded HTTPRoute is refused like any other. This is the test that goes red
    if somebody "fixes" the gate to exempt the specification.
    """
    root = tree(
        tmp_path,
        {
            "chart/values.yaml": "_render:\n"
            "  - apiVersion: gateway.networking.k8s.io/v1\n"
            "    kind: HTTPRoute\n"
            "    metadata:\n"
            "      name: api\n"
        },
    )
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "HTTPRoute/api" in result.stdout


def test_a_guarded_route_on_by_default_is_permitted_and_not_nagged(tmp_path):
    """A SPECIFICATION on by default gets no note; that is D80's own wording."""
    root = tree(
        tmp_path,
        {
            "chart/values.yaml": "route:\n  enabled: true\n_render:\n"
            "  - when: route.enabled\n"
            "    apiVersion: gateway.networking.k8s.io/v1\n"
            "    kind: HTTPRoute\n"
            "    metadata:\n"
            "      name: api\n"
        },
    )
    result = run(root)
    assert result.returncode == 0, result.stdout
    assert "SPECIFICATION" in result.stdout
    assert "ON by default" not in result.stdout


def test_a_product_on_by_default_is_noted_rather_than_refused(tmp_path):
    """The NOTE half of the same render: a product an adopter must install."""
    root = tree(
        tmp_path,
        {
            "chart/values.yaml": "certs:\n  enabled: true\n_render:\n"
            "  - when: certs.enabled\n"
            "    apiVersion: cert-manager.io/v1\n"
            "    kind: Certificate\n"
            "    metadata:\n"
            "      name: serving\n"
        },
    )
    result = run(root)
    assert result.returncode == 0, result.stdout
    assert "PRODUCT" in result.stdout
    assert "is ON by default" in result.stdout


def test_a_group_the_table_has_never_heard_of_is_treated_as_a_product(tmp_path):
    """THE ALLOWLIST'S WHOLE POINT. An unknown group must fail safe, because a
    denylist passes vacuously the moment somebody uses a name it was never told
    about."""
    root = tree(
        tmp_path,
        {
            "chart/values.yaml": "_render:\n"
            "  - apiVersion: invented.example.com/v1\n"
            "    kind: Widget\n"
            "    metadata:\n"
            "      name: thing\n"
        },
    )
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "invented.example.com" in result.stdout


def test_a_chart_that_does_not_render_at_all_is_refused(tmp_path):
    root = tree(tmp_path, {"chart/values.yaml": "_fail: true\n_render: []\n"})
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "failed on the default values" in result.stdout


def test_a_chart_that_cannot_render_with_everything_off_is_refused(tmp_path):
    """The other half of switchability: turning the parts off must still render.

    A chart whose template blows up once its optional half is disabled is as
    uninstallable as one that emits a CRD nobody has.
    """
    root = tree(
        tmp_path,
        {
            "chart/values.yaml": "extras:\n  enabled: true\n"
            "_fail_unless: extras.enabled\n_render:\n" + DEPLOYMENT + container()
        },
    )
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "cannot switch its optional parts off" in result.stdout


# ----------------------------------------------- the release-shaped render (D65)


def test_an_image_that_must_already_be_on_the_node_is_refused(tmp_path):
    root = tree(
        tmp_path,
        {
            "chart/values.yaml": "_render:\n"
            + DEPLOYMENT
            + container("              imagePullPolicy: Never\n")
        },
    )
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "imagePullPolicy" in result.stdout and "Never" in result.stdout


def test_a_host_port_is_refused(tmp_path):
    root = tree(
        tmp_path,
        {
            "chart/values.yaml": "_render:\n"
            + DEPLOYMENT
            + container("              ports:\n                - hostPort: 8080\n")
        },
    )
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "hostPort" in result.stdout


def test_a_moving_tag_in_the_published_shape_is_refused(tmp_path):
    """Hard-coded rather than taken from `.image`, so D65's rewrite cannot reach
    it — which is exactly the shape that ships broken."""
    root = tree(
        tmp_path,
        {
            "chart/values.yaml": "_render:\n"
            + DEPLOYMENT
            + "    spec:\n"
            "      template:\n"
            "        spec:\n"
            "          containers:\n"
            "            - name: app\n"
            '              image: "ghcr.io/yadgarhq/sidecar:latest"\n'
        },
    )
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "a moving tag in the PUBLISHED shape" in result.stdout


def test_a_latest_tag_sitting_in_git_is_not_a_finding(tmp_path):
    """THE PAIR, and the false positive it stops is on every repository here.

    `ci-release` sets `.image.digest` and deletes `.image.tag` at package time,
    so the `latest` in git never reaches an adopter. A gate reading the in-git
    values would redden the whole estate for it.
    """
    root = tree(
        tmp_path,
        {
            "chart/values.yaml": "image:\n"
            "  repository: ghcr.io/yadgarhq/app\n"
            "  tag: latest\n"
            "_render:\n" + DEPLOYMENT + container()
        },
    )
    result = run(root)
    assert result.returncode == 0, result.stdout
    assert "No `imagePullPolicy: Never`, no `hostPort`, no `:latest`" in result.stdout


# --------------------------------------------------- source-tree tripwires


def test_the_cloud_metadata_endpoint_is_refused(tmp_path):
    root = tree(tmp_path, {"deploy/pod.yaml": "env: http://169.254.169.254/latest\n"})
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "the cloud instance metadata endpoint" in result.stdout
    assert "deploy/pod.yaml:1" in result.stdout


def test_a_declared_cloud_sdk_is_refused_and_the_crypto_backend_is_not(tmp_path):
    """THE PAIR THAT CERTIFIES THE CHECK RATHER THAN THE FIXTURE.

    `aws-lc-rs` and `aws-lc-sys` are ring's crypto backend and have nothing to do
    with AWS the platform — `yadgar-client` carries both today, so a `^aws-`
    prefix would redden a repository doing nothing wrong. Feeding it ALONE would
    pass just as happily with the check deleted, so the real SDK is in the same
    file and the gate has to separate them.
    """
    root = tree(
        tmp_path,
        {
            "Cargo.toml": "[dependencies]\n"
            'aws-lc-rs = "1"\n'
            'aws-lc-sys = "0.2"\n'
            'aws-sdk-s3 = "1"\n'
        },
    )
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "aws-sdk-s3" in result.stdout
    assert "aws-lc-rs" not in result.stdout
    assert "aws-lc-sys" not in result.stdout


def test_the_same_crate_in_the_lockfile_is_a_note_rather_than_a_refusal(tmp_path):
    """A lockfile entry is somebody else's choice, so it must not fail a repo."""
    root = tree(
        tmp_path,
        {"Cargo.lock": '[[package]]\nname = "aws-sdk-s3"\nversion = "1.0.0"\n'},
    )
    result = run(root)
    assert result.returncode == 0, result.stdout
    assert "transitively" in result.stdout
    assert "aws-sdk-s3" in result.stdout


def test_an_envoy_header_is_refused_and_x_forwarded_for_is_not(tmp_path):
    """THE PAIR D80 ITSELF DRAWS. `X-Forwarded-For` is the behaviour every
    ingress shares, so depending on it is the PORTABLE form; `x-envoy-*` belongs
    to one implementation."""
    root = tree(
        tmp_path,
        {
            "src/relay.rs": 'let a = req.header("x-forwarded-for");\n'
            'let b = req.header("x-envoy-external-address");\n'
        },
    )
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "an Envoy-specific header" in result.stdout
    assert "src/relay.rs:2" in result.stdout
    assert "src/relay.rs:1" not in result.stdout


def test_cluster_dns_compiled_into_rust_is_refused_and_a_manifest_is_not(tmp_path):
    """A manifest NAMING a service is configuration; a `.rs` file is a constant
    an adopter cannot re-point, which is why the check is scoped to `.rs`."""
    root = tree(
        tmp_path,
        {
            "src/dial.rs": 'const UP: &str = "task-db.yadgar.svc.cluster.local";\n',
            "deploy/env.yaml": "value: task-db.yadgar.svc.cluster.local\n",
        },
    )
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "cluster-internal DNS compiled in" in result.stdout
    assert "src/dial.rs:1" in result.stdout
    assert "deploy/env.yaml" not in result.stdout


def test_an_exemption_on_the_line_above_suppresses_and_one_further_up_does_not(
    tmp_path,
):
    """THE PAIR THAT PINS THE WINDOW. The convention is the line or the one above
    it — in the diff, where a reviewer sees it. Two lines up is out of the
    window, and a test asserting only the suppression would pass with the window
    widened to the whole file."""
    root = tree(
        tmp_path,
        {
            "a/near.yaml": "# d80: exempt - the emulator's own fixture\n"
            "endpoint: http://169.254.169.254/\n",
            "b/far.yaml": "# d80: exempt - too far up to count\n"
            "\n"
            "endpoint: http://169.254.169.254/\n",
        },
    )
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "b/far.yaml:3" in result.stdout
    assert "a/near.yaml" not in result.stdout


def test_the_vendored_contract_is_not_scanned_and_the_same_line_elsewhere_is(
    tmp_path,
):
    """`proto/` is a vendored contract and `.github/` is not shipped, so neither
    is scanned. Asserting the skip alone would pass with the whole walk deleted,
    so the identical line sits in a directory that IS scanned."""
    root = tree(
        tmp_path,
        {
            "proto/yadgar/v1/task.yaml": "note: nginx.ingress.kubernetes.io/x\n",
            ".github/workflows/ci.yaml": "note: nginx.ingress.kubernetes.io/x\n",
            "src/routes.yaml": "note: nginx.ingress.kubernetes.io/x\n",
        },
    )
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "src/routes.yaml:1" in result.stdout
    # NAMED PATHS, not the bare directory: the gate's own prose says `proto/` and
    # `.github/` are the two it does not scan, so a bare substring would match
    # that sentence and pass with the skip deleted.
    assert "proto/yadgar/v1/task.yaml" not in result.stdout
    assert ".github/workflows/ci.yaml" not in result.stdout


# ------------------------------------------------------- the ONE exemption


def test_the_reference_deployment_is_exempt_and_another_repository_is_not(tmp_path):
    """ONE TREE, TWO NAMES. D80 exempts a reference deployment BY NAME, so the
    only honest way to test the exemption is to prove the same tree fails without
    it — otherwise the test passes with every check deleted."""
    root = tree(
        tmp_path,
        {
            "src/relay.rs": 'let h = req.header("x-envoy-external-address");\n',
            "chart/values.yaml": "_render:\n" + SCALED_OBJECT,
        },
    )
    exempt = run(root, repo="yadgarhq/deploy")
    assert exempt.returncode == 0, exempt.stdout
    assert "REFERENCE DEPLOYMENT and is EXEMPT BY NAME" in exempt.stdout

    other = run(root, repo="yadgarhq/gateway")
    assert other.returncode == 1, other.stdout
    assert "an Envoy-specific header" in other.stdout
    assert "ScaledObject/worker" in other.stdout


# ---------------------------------------------------- reporting, not verdicts


def test_a_repository_with_no_chart_says_so_and_still_scans_the_tree(tmp_path):
    """A SKIPPED HALF IS NOT A PASSED HALF. Ten of this estate's repositories
    have no `chart/`, so the tripwires are the whole of the gate for them."""
    clean = tree(tmp_path / "clean", {"src/lib.rs": "fn main() {}\n"})
    result = run(clean)
    assert result.returncode == 0, result.stdout
    assert "No `chart/` in this repository" in result.stdout
    assert "No cloud provider name found." in result.stdout

    dirty = tree(tmp_path / "dirty", {"src/lib.rs": 'let x = "169.254.169.254";\n'})
    assert run(dirty).returncode == 1


def test_the_violations_reach_the_step_summary(tmp_path):
    """The summary is what a reviewer reads; a red that only reaches stdout is a
    red nobody sees."""
    summary = tmp_path / "summary.md"
    root = tree(tmp_path, {"deploy/pod.yaml": "env: http://169.254.169.254/\n"})
    assert run(root, summary=summary).returncode == 1
    text = summary.read_text()
    assert "### D80 violations" in text
    assert "the cloud instance metadata endpoint" in text


def test_the_gate_says_which_part_of_d80_it_cannot_check(tmp_path):
    """D80's highest-severity class is a security control resting on an
    undeclared environment default, and no grep finds it. A gate that implied
    otherwise is the failure this estate has already found twice, so the
    disclaimer is part of the output rather than a comment in the source."""
    root = tree(tmp_path, {"src/lib.rs": "fn main() {}\n"})
    result = run(root)
    assert result.returncode == 0, result.stdout
    assert "### What this job does NOT check" in result.stdout
    assert "would this still be correct" in result.stdout


def test_this_repository_passes_today(tmp_path):
    """LEDGER 731'S OWN QUESTION, asked of the real tree rather than a fixture."""
    binaries = tmp_path / "stub-bin"
    binaries.mkdir()
    result = subprocess.run(
        [sys.executable, str(GATE)],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        env={"D80_REPO": "yadgarhq/actions", "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stdout
    assert "No `chart/` in this repository" in result.stdout
