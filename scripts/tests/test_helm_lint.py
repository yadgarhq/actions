"""What the `helm-lint` hook runs, pinned argv by argv (ADR-0806).

THE ENTRY USED TO BE `bash -c 'helm lint --strict chart && helm template
ci-render chart > /dev/null'`. It is a script now only because it has to read a
file, and the first promise it makes is that a repository WITHOUT that file sees
no difference at all: the same two commands, the same argv, `template` not run
when `lint` fails, and helm's own exit status passed through. Every one of those
is asserted below against a stub that records what it was called with.

THE SECOND PROMISE is the reason the script exists. A chart that declares the
operator API versions its default render needs gets each one on `helm template`
as `--api-versions`, and ONLY on `helm template`: `helm lint` has no such flag on
either helm major, and it does not need one, because lint grades a template's
`fail` as INFO rather than an error (measured on helm v3.18.4 and v4.3.0 with a
chart that fails unless `keda.sh/v1alpha1` is declared: lint exits 0, a bare
`helm template` exits 1).

THE RED CASE IS END TO END. The stub can be told to refuse a render missing a
given API version, the way the estate's `require-api` render checks do; the same
chart then passes with the declaration and fails without it. A test that only
compared argv lists would pass a script that built the right list and never
handed it to helm.

WHY A STUB and not helm, for the reason `test_d80_portability.py` gives: this
suite runs inside the `pytest-scripts` pre-commit hook, which can provision an
interpreter and cannot provision a binary.

Run: python3 -m pytest scripts/tests/ -q
"""

import json
import os
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parents[2] / "hooks" / "helm_lint.py"

STUB_HELM = r'''
import json, os, sys

argv = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a") as log:
    log.write(json.dumps(argv) + "\n")

if argv[0] == "lint":
    sys.exit(int(os.environ.get("STUB_LINT_EXIT", "0")))
if argv[0] == "template":
    passed = [argv[i + 1] for i, a in enumerate(argv) if a == "--api-versions"]
    for needed in filter(None, os.environ.get("STUB_REQUIRE", "").split()):
        if needed not in passed:
            sys.stderr.write("stub helm: this render needs the API %s\n" % needed)
            sys.exit(1)
    sys.stdout.write("kind: ConfigMap\n")
    sys.exit(int(os.environ.get("STUB_TEMPLATE_EXIT", "0")))
sys.exit(2)
'''


def repository(tmp_path, declaration=None):
    root = tmp_path / "repo"
    (root / "chart").mkdir(parents=True)
    (root / "chart" / "Chart.yaml").write_text("apiVersion: v2\nname: x\nversion: 0.1.0\n")
    if declaration is not None:
        (root / "chart" / "ci").mkdir()
        (root / "chart" / "ci" / "api-versions.txt").write_text(declaration)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    helm = binaries / "helm"
    helm.write_text("#!" + sys.executable + "\n" + STUB_HELM)
    helm.chmod(0o755)
    return root


def run(root, **stub):
    log = root.parent / "helm.log"
    environment = {
        "PATH": str(root.parent / "bin") + ":/usr/bin:/bin",
        "STUB_LOG": str(log),
        **{k: str(v) for k, v in stub.items()},
    }
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        cwd=str(root),
        capture_output=True,
        text=True,
        env=environment,
    )
    calls = []
    if log.exists():
        calls = [json.loads(line) for line in log.read_text().splitlines()]
    return result, calls


# ----------------------------------------------- no declaration: nothing changes


def test_without_a_declaration_the_argv_is_the_one_the_bash_entry_ran(tmp_path):
    result, calls = run(repository(tmp_path))
    assert result.returncode == 0, result.stderr
    assert calls == [
        ["lint", "--strict", "chart"],
        ["template", "ci-render", "chart"],
    ]


def test_a_failing_lint_stops_before_the_render_and_its_status_passes_through(
    tmp_path,
):
    result, calls = run(repository(tmp_path), STUB_LINT_EXIT=3)
    assert result.returncode == 3
    assert calls == [["lint", "--strict", "chart"]]


def test_a_failing_render_passes_its_status_through(tmp_path):
    result, calls = run(repository(tmp_path), STUB_TEMPLATE_EXIT=4)
    assert result.returncode == 4
    assert len(calls) == 2


def test_the_render_output_is_discarded_as_before(tmp_path):
    """`> /dev/null` in the old entry: the rendered manifests are not the
    hook's output, only helm's diagnostics are."""
    result, _ = run(repository(tmp_path))
    assert "kind: ConfigMap" not in result.stdout


# ------------------------------------------------- a declaration reaches helm


def test_each_declared_version_is_passed_to_the_render_and_not_to_lint(tmp_path):
    root = repository(
        tmp_path, "# operators\nkeda.sh/v1alpha1\ncert-manager.io/v1\n"
    )
    result, calls = run(root)
    assert result.returncode == 0, result.stderr
    assert calls == [
        ["lint", "--strict", "chart"],
        [
            "template",
            "ci-render",
            "chart",
            "--api-versions",
            "keda.sh/v1alpha1",
            "--api-versions",
            "cert-manager.io/v1",
        ],
    ]


def test_a_render_that_needs_a_declared_version_passes(tmp_path):
    root = repository(tmp_path, "keda.sh/v1alpha1\n")
    result, _ = run(root, STUB_REQUIRE="keda.sh/v1alpha1")
    assert result.returncode == 0, result.stderr


def test_the_same_render_without_the_declaration_is_refused(tmp_path):
    """THE RED CASE for the pair above: identical but for the file."""
    root = repository(tmp_path)
    result, _ = run(root, STUB_REQUIRE="keda.sh/v1alpha1")
    assert result.returncode == 1
    assert "keda.sh/v1alpha1" in result.stderr


def test_a_version_missing_from_the_declaration_is_refused(tmp_path):
    root = repository(tmp_path, "cert-manager.io/v1\n")
    result, _ = run(root, STUB_REQUIRE="cert-manager.io/v1 keda.sh/v1alpha1")
    assert result.returncode == 1
    assert "keda.sh/v1alpha1" in result.stderr


def test_a_malformed_declaration_is_refused_before_helm_runs(tmp_path):
    root = repository(tmp_path, "keda.sh/v1alpha1 cert-manager.io/v1\n")
    result, calls = run(root)
    assert result.returncode == 1
    assert calls == []
    assert "chart/ci/api-versions.txt:1" in result.stderr


def test_a_declaration_that_is_not_utf8_is_refused_without_a_traceback(tmp_path):
    root = repository(tmp_path, "")
    (root / "chart" / "ci" / "api-versions.txt").write_bytes(b"\xff\xfe\n")
    result, calls = run(root)
    assert result.returncode == 1
    assert calls == []
    assert "Traceback" not in result.stderr
    assert "chart/ci/api-versions.txt" in result.stderr


def test_the_hook_is_published_as_this_script():
    """The manifest entry is what consumers run; the script is only reachable
    through it. `language: script` runs the file directly, so it needs the
    executable bit as well."""
    manifest = (HOOK.parents[1] / ".pre-commit-hooks.yaml").read_text()
    block = manifest.split("- id: helm-lint\n", 1)[1].split("\n- id:", 1)[0]
    assert "entry: hooks/helm_lint.py" in block
    assert "language: script" in block
    assert "pass_filenames: false" in block
    assert "files: ^chart/" in block
    assert os.access(HOOK, os.X_OK)
