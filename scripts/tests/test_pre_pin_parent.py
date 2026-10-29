"""What `pre_pin_parent.py` REFUSES, pinned so it cannot quietly stop refusing
(C-A3).

WHY A STUB, for the reason `test_helm_lint.py` and `test_d80_portability.py`
give at length: this suite runs inside the `pytest-scripts` pre-commit hook
(`language: python`, self-provisioned venv) and in `ci-self.yaml`'s `pytest`
job, neither of which can provision a `helm` binary. The stub's contract with
this script is `argv -> (exit code, stdout, stderr)` for `package`, `lint` and
`template` -- the three subcommands this script invokes -- so every test below
proves what the script DOES with a given helm outcome, not that helm's own
schema validation works (that half is proven on real trees, recorded in the
pull request per this card's acceptance line, not here).

MOST OF THIS FILE IS RED CASES, same discipline as `test_d80_portability.py`:
a suite that only feeds the script conforming input certifies the fixture
rather than the gate.

WHAT THIS SUITE DOES NOT PROVE, in the gate's own idiom (`test_d80_portability`'s
phrase): that a chart requiring a key the parent fixture lacks actually fails
real `helm lint --strict` schema validation, or that setting the key actually
clears it. That is a property of HELM and of ADR-0845's schema shape, already
measured on real trees for C-P0 and C-A2 and re-measured here as this card's
own acceptance sweep (recorded in the pull request, not in this file). What
THIS suite proves is narrower and is the whole of what a stub can honestly
stand for: given a helm verdict, does the script build the right `package`,
`lint` and `template` argv, vendor the tarball by the right name, stop before
`template` when `lint` already refused, and -- the one property real helm
cannot hand back on its own -- print the K-8 order explanation the raw helm
text never contains. `test_a_refused_lint_names_k8_order_and_the_pin` and
`test_a_passing_lint_and_template_let_the_job_succeed` are the mutation pair
over that property: same stub, exit code flipped, message and short-circuit
asserted either way.

Run: python3 -m pytest scripts/tests/ -q
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

GATE = Path(__file__).resolve().parents[1] / "pre_pin_parent.py"
REPO = Path(__file__).resolve().parents[2]
CI_PR = REPO / ".github" / "workflows" / "ci-pr.yaml"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pre_pin_parent  # noqa: E402

STUB_HELM = r'''
import json, os, pathlib, sys

argv = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a") as log:
    log.write(json.dumps(argv) + "\n")

if argv[0] == "package":
    chart_dir = pathlib.Path(argv[1])
    version = argv[argv.index("--version") + 1]
    dest = pathlib.Path(argv[argv.index("-d") + 1])
    name = None
    for line in (chart_dir / "Chart.yaml").read_text().splitlines():
        if line.startswith("name:"):
            name = line.split(":", 1)[1].strip()
            break
    code = int(os.environ.get("STUB_PACKAGE_EXIT", "0"))
    if code == 0:
        dest.mkdir(parents=True, exist_ok=True)
        (dest / f"{name}-{version}.tgz").write_bytes(b"stub-tarball")
    sys.stderr.write(os.environ.get("STUB_PACKAGE_STDERR", ""))
    sys.exit(code)


def phase_prefix():
    # Baseline = the parent as checked out, before the candidate tarball is vendored.
    for tgz in pathlib.Path(argv[2]).glob("charts/*.tgz"):
        if tgz.read_bytes() == b"stub-tarball":
            return ""
    return "BASELINE_"


def verdict(verb, example):
    key = "STUB_" + phase_prefix() + ("EXAMPLE_" if example else "") + verb
    sys.stdout.write(os.environ.get(key + "_STDOUT", ""))
    sys.stderr.write(os.environ.get(key + "_STDERR", ""))
    sys.exit(int(os.environ.get(key + "_EXIT", "0")))


for flag, value in zip(argv, argv[1:]):
    # The seeder render-check refuses chart's example placeholder by design.
    if flag == "-f" and "REPLACE-WITH-A-UUIDV7-YOU-MINT" in pathlib.Path(value).read_text():
        sys.stderr.write("Error: administrators must be a uuidv7, got a placeholder\n")
        sys.exit(1)

if argv[0] == "lint":
    verdict("LINT", False)

if argv[0] == "template":
    verdict("TEMPLATE", any(a.endswith("example/values.yaml") for a in argv))

sys.exit(2)
'''


def chart(directory: Path, name: str, version: str = "9.9.9") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "Chart.yaml").write_text(f"apiVersion: v2\nname: {name}\nversion: {version}\n")
    (directory / "values.yaml").write_text("{}\n")
    return directory


def parent(directory: Path, dependencies: dict[str, str]) -> Path:
    """`<directory>` as a parent chart whose `dependencies:` carry the given
    `{name: version}` pins -- textually, the shape `parent_pin.pins()` reads."""
    directory.mkdir(parents=True, exist_ok=True)
    lines = ["apiVersion: v2", "name: yadgar", "version: 0.1.0"]
    if dependencies:
        lines.append("dependencies:")
        for name, version in dependencies.items():
            lines += [f"  - name: {name}", f"    version: {version}", "    repository: oci://ghcr.io/yadgarhq/charts"]
    (directory / "Chart.yaml").write_text("\n".join(lines) + "\n")
    (directory / "values.yaml").write_text("{}\n")
    return directory


def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    binaries = tmp_path / "bin"
    binaries.mkdir()
    helm = binaries / "helm"
    helm.write_text("#!" + sys.executable + "\n" + STUB_HELM)
    helm.chmod(0o755)
    return root


def run(root: Path, **stub) -> tuple[subprocess.CompletedProcess, list[list[str]]]:
    log = root.parent / "helm.log"
    environment = {
        "PATH": str(root.parent / "bin") + ":/usr/bin:/bin:" + os.path.dirname(shutil.which("git") or "/usr/bin/git"),
        "STUB_LOG": str(log),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        **{k: str(v) for k, v in stub.items()},
    }
    result = subprocess.run(
        [sys.executable, str(GATE)],
        cwd=str(root),
        capture_output=True,
        text=True,
        env=environment,
    )
    calls = []
    if log.exists():
        calls = [json.loads(line) for line in log.read_text().splitlines()]
    return result, calls


# --------------------------------------------- the three narrated no-ops


def test_no_local_chart_directory_is_narrated_and_not_an_error(tmp_path):
    root = repository(tmp_path)
    result, calls = run(root)
    assert result.returncode == 0, result.stderr
    assert calls == []
    assert "no chart" in result.stdout
    assert "::error::" not in result.stdout


def test_a_name_absent_from_the_parents_dependencies_is_narrated(tmp_path):
    root = repository(tmp_path)
    chart(root / "chart", "widget")
    parent(root / ".pre-pin-parent" / "chart", dependencies={"other": "1.0.0"})
    result, calls = run(root)
    assert result.returncode == 0, result.stderr
    assert calls == []
    assert "not among yadgarhq/chart" in result.stdout


def test_a_parent_with_no_dependencies_block_is_a_loud_refusal_not_a_quiet_skip(tmp_path):
    """`parent_pin.pins()` itself refuses a parent chart carrying no top-level
    `dependencies:` at all ("nothing inspected must never be exit 0" is that
    module's own rule). A REAL `yadgarhq/chart` always has one; this is what
    the checkout landing somewhere unexpected looks like, and it must not be
    mistaken for "this chart is not onboarded yet"."""
    root = repository(tmp_path)
    chart(root / "chart", "widget")
    parent(root / ".pre-pin-parent" / "chart", dependencies={})
    result, calls = run(root)
    assert result.returncode != 0
    assert calls == []
    assert "::error::" in result.stdout
    assert "no top-level" in result.stdout or "no top-level" in result.stderr


# --------------------------------------------- the vendor step itself


def test_the_candidate_is_packaged_at_the_parents_pin_and_vendored_by_name(tmp_path):
    root = repository(tmp_path)
    chart(root / "chart", "widget", version="9.9.9")
    parent(root / ".pre-pin-parent" / "chart", dependencies={"widget": "1.2.3"})
    result, calls = run(root)
    assert result.returncode == 0, result.stderr
    package_call = calls[2]
    assert package_call[:2] == ["package", "chart"]
    assert "-u" in package_call
    assert package_call[package_call.index("--version") + 1] == "1.2.3"
    assert package_call[package_call.index("--app-version") + 1] == "1.2.3"
    vendored = root / ".pre-pin-parent" / "chart" / "charts" / "widget-1.2.3.tgz"
    assert vendored.is_file(), sorted(p.name for p in (root / ".pre-pin-parent" / "chart" / "charts").iterdir())
    assert vendored.read_bytes() == b"stub-tarball"


def test_a_failed_package_refuses_before_any_lint_or_template(tmp_path):
    root = repository(tmp_path)
    chart(root / "chart", "widget")
    parent(root / ".pre-pin-parent" / "chart", dependencies={"widget": "1.2.3"})
    result, calls = run(root, STUB_PACKAGE_EXIT="5", STUB_PACKAGE_STDERR="chart.yaml: bad\n")
    assert result.returncode != 0
    assert [c[0] for c in calls] == ["lint", "template", "package"]
    assert "chart.yaml: bad" in result.stdout + result.stderr


# --------------------------------------------- lint and template argv


def test_bare_lint_and_render_with_no_parent_fixture(tmp_path):
    """A parent with no `chart/ci/values.yaml` is linted and rendered bare --
    C-A2's backward-compatibility promise, carried into this gate too."""
    root = repository(tmp_path)
    chart(root / "chart", "widget")
    parent(root / ".pre-pin-parent" / "chart", dependencies={"widget": "1.2.3"})
    result, calls = run(root)
    assert result.returncode == 0, result.stderr
    assert calls[3] == ["lint", "--strict", ".pre-pin-parent/chart"]
    assert calls[4] == ["template", "ci-render", ".pre-pin-parent/chart"]


def test_the_parents_own_ci_values_file_reaches_both_lint_and_render(tmp_path):
    root = repository(tmp_path)
    chart(root / "chart", "widget")
    parent_dir = parent(root / ".pre-pin-parent" / "chart", dependencies={"widget": "1.2.3"})
    (parent_dir / "ci").mkdir()
    (parent_dir / "ci" / "values.yaml").write_text("tls:\n  enabled: true\n")
    result, calls = run(root)
    assert result.returncode == 0, result.stderr
    assert calls[3] == ["lint", "--strict", ".pre-pin-parent/chart", "-f", ".pre-pin-parent/chart/ci/values.yaml"]
    assert calls[4] == [
        "template", "ci-render", ".pre-pin-parent/chart",
        "-f", ".pre-pin-parent/chart/ci/values.yaml",
    ]


def test_the_parents_declared_api_versions_reach_the_render_and_not_the_lint(tmp_path):
    root = repository(tmp_path)
    chart(root / "chart", "widget")
    parent_dir = parent(root / ".pre-pin-parent" / "chart", dependencies={"widget": "1.2.3"})
    (parent_dir / "ci").mkdir()
    (parent_dir / "ci" / "api-versions.txt").write_text("keda.sh/v1alpha1\ncert-manager.io/v1\n")
    result, calls = run(root)
    assert result.returncode == 0, result.stderr
    assert calls[3] == ["lint", "--strict", ".pre-pin-parent/chart"]
    assert calls[4] == [
        "template", "ci-render", ".pre-pin-parent/chart",
        "--api-versions", "keda.sh/v1alpha1",
        "--api-versions", "cert-manager.io/v1",
    ]


# --------------------------------------------- the K-8 refusal, paired red/green


def test_a_refused_lint_names_k8_order_and_the_pin(tmp_path):
    """THE RED HALF OF THE MUTATION PAIR. The stub stands in for the real
    case K-8 exists for -- the candidate's own chart requiring a key the
    parent fixture does not set -- by refusing `lint` the way real `helm
    lint --strict` refuses a schema violation. Asserts the designed sentence,
    not a substring (the preamble's own rule for this card)."""
    root = repository(tmp_path)
    chart(root / "chart", "widget")
    parent(root / ".pre-pin-parent" / "chart", dependencies={"widget": "1.2.3"})
    result, calls = run(
        root,
        STUB_LINT_EXIT="1",
        STUB_LINT_STDERR="Error: values don't meet the specifications of the schema(s)\n",
    )
    assert result.returncode == 1
    assert "yadgarhq/chart main refuses to lint with 'widget' vendored at 1.2.3" in result.stdout
    assert "K-8 order" in result.stdout
    assert "chart/ci/values.yaml, example/ values, and the parent chart test" in result.stdout
    assert "values don't meet the specifications of the schema(s)" in result.stdout
    assert [c[0] for c in calls] == ["lint", "template", "package", "lint"]  # template never runs after a refused lint


def test_a_passing_lint_and_template_let_the_job_succeed(tmp_path):
    """THE GREEN HALF OF THE MUTATION PAIR: same candidate, same pin, same
    parent tree as the refusal test above -- only the stub's exit code moves,
    which is the one thing a stub can honestly stand for (see module
    docstring). Whether a real schema refuses or clears is proven on real
    trees, not reproduced here."""
    root = repository(tmp_path)
    chart(root / "chart", "widget")
    parent(root / ".pre-pin-parent" / "chart", dependencies={"widget": "1.2.3"})
    result, calls = run(root)
    assert result.returncode == 0, result.stderr
    assert [c[0] for c in calls] == ["lint", "template", "package", "lint", "template"]


def test_a_refused_template_also_names_k8_order(tmp_path):
    root = repository(tmp_path)
    chart(root / "chart", "widget")
    parent(root / ".pre-pin-parent" / "chart", dependencies={"widget": "1.2.3"})
    result, calls = run(
        root,
        STUB_TEMPLATE_EXIT="1",
        STUB_TEMPLATE_STDERR="Error: execution error at (widget/templates/x.yaml:2:4): feature.enabled must be set\n",
    )
    assert result.returncode == 1
    assert "yadgarhq/chart main refuses to template with 'widget' vendored at 1.2.3" in result.stdout
    assert "K-8 order" in result.stdout
    assert "feature.enabled must be set" in result.stdout
    assert [c[0] for c in calls] == ["lint", "template", "package", "lint", "template"]


# --------------------------------------------- baseline: parent main rendered before vendoring (yaadgaar 342)

BASELINE_SENTENCE = (
    "yadgarhq/chart main is red at abc1234 before this pull request's chart was "
    "vendored; not this PR's K-8 order"
)


def onboarded(root: Path) -> Path:
    chart(root / "chart", "widget")
    return parent(root / ".pre-pin-parent" / "chart", dependencies={"widget": "1.2.3"})


def test_a_red_parent_main_is_named_and_not_blamed_on_the_pull_request(tmp_path):
    root = repository(tmp_path)
    onboarded(root)
    result, calls = run(
        root,
        PARENT_SHA="abc1234",
        STUB_BASELINE_LINT_EXIT="1",
        STUB_BASELINE_LINT_STDERR="Error: bad pin\n",
        STUB_LINT_EXIT="1",
    )
    assert result.returncode == 1
    assert BASELINE_SENTENCE in result.stdout
    assert "Error: bad pin" in result.stdout
    assert "refuses to lint with" in result.stdout  # the vendored result is printed too
    assert [c[0] for c in calls] == ["lint", "package", "lint"]  # vendored and rendered again


def test_both_renders_red_prints_the_baseline_and_the_vendored_failure(tmp_path):
    root = repository(tmp_path)
    onboarded(root)
    result, _ = run(
        root, PARENT_SHA="abc1234",
        STUB_BASELINE_LINT_EXIT="1", STUB_BASELINE_LINT_STDERR="Error: old red\n",
        STUB_LINT_EXIT="1", STUB_LINT_STDERR="Error: new red\n",
    )
    assert result.returncode == 1
    assert BASELINE_SENTENCE in result.stdout
    assert "Error: old red" in result.stdout
    assert "refuses to lint with 'widget' vendored at 1.2.3" in result.stdout
    assert "Error: new red" in result.stdout


def test_a_red_parent_main_template_is_also_named(tmp_path):
    root = repository(tmp_path)
    onboarded(root)
    result, calls = run(root, PARENT_SHA="abc1234", STUB_BASELINE_TEMPLATE_EXIT="1", STUB_TEMPLATE_EXIT="1")
    assert result.returncode == 1
    assert BASELINE_SENTENCE in result.stdout
    assert [c[0] for c in calls] == ["lint", "template", "package", "lint", "template"]


def test_a_red_baseline_that_this_pull_request_repairs_passes_with_a_warning(tmp_path):
    """The deadlock: a chart PR that fixes a red parent main must not be
    blocked by that red. Vendored and rendered again, green -> pass, loudly."""
    root = repository(tmp_path)
    onboarded(root)
    result, calls = run(root, PARENT_SHA="abc1234", STUB_BASELINE_LINT_EXIT="1")
    assert result.returncode == 0, result.stdout
    assert "::warning::" in result.stdout
    assert "abc1234" in result.stdout
    assert "repairs" in result.stdout
    assert "::error::" not in result.stdout
    assert [c[0] for c in calls] == ["lint", "package", "lint", "template"]


def test_a_failure_only_after_vendoring_keeps_the_k8_wording(tmp_path):
    root = repository(tmp_path)
    onboarded(root)
    result, _ = run(root, PARENT_SHA="abc1234", STUB_LINT_EXIT="1")
    assert result.returncode == 1
    assert "K-8 order" in result.stdout
    assert "is red at" not in result.stdout


def test_the_baseline_sha_falls_back_to_the_checkouts_head(tmp_path):
    root = repository(tmp_path)
    parent_dir = onboarded(root)
    git = ["git", "-C", str(parent_dir.parent)]
    env = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull, "PATH": os.environ["PATH"]}
    subprocess.run([*git, "init", "-q"], check=True, env=env)
    subprocess.run([*git, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                    "--allow-empty", "-m", "x"], check=True, env=env)
    head = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True, env=env).stdout.strip()
    result, _ = run(root, STUB_BASELINE_LINT_EXIT="1")
    assert f"red at {head} before" in result.stdout


# --------------------------------------------- example/values.yaml render (yaadgaar 345)


def with_example(parent_dir: Path) -> None:
    example = parent_dir.parent / "example"
    example.mkdir()
    (example / "values.yaml").write_text("gateway:\n  enabled: true\n")


def test_the_example_values_are_rendered_after_the_ci_values(tmp_path):
    root = repository(tmp_path)
    with_example(onboarded(root))
    result, calls = run(root)
    assert result.returncode == 0, result.stderr
    assert [c[0] for c in calls[:3]] == ["lint", "template", "template"]  # baseline: ci, example
    assert calls[4] == ["lint", "--strict", ".pre-pin-parent/chart"]
    assert calls[5] == ["template", "ci-render", ".pre-pin-parent/chart"]
    assert calls[6][:4] == ["template", "ci-render-example", ".pre-pin-parent/chart", "-f"]
    assert calls[6][4].endswith("example/values.yaml") and len(calls[6]) == 5


def test_no_example_values_means_no_example_render(tmp_path):
    root = repository(tmp_path)
    onboarded(root)
    _, calls = run(root)
    assert not any(c[:2] == ["template", "ci-render-example"] for c in calls)


def test_a_refused_example_render_names_the_file_and_k8_step_2(tmp_path):
    root = repository(tmp_path)
    with_example(onboarded(root))
    result, _ = run(
        root,
        PARENT_SHA="abc1234",
        STUB_EXAMPLE_TEMPLATE_EXIT="1",
        STUB_EXAMPLE_TEMPLATE_STDERR="Error: feature.enabled must be set\n",
    )
    assert result.returncode == 1
    assert "example/values.yaml" in result.stdout
    assert "K-8 step 2" in result.stdout
    assert "feature.enabled must be set" in result.stdout
    assert "is red at" not in result.stdout


def test_a_red_example_on_parent_main_is_the_baseline_not_the_pull_request(tmp_path):
    root = repository(tmp_path)
    with_example(onboarded(root))
    result, calls = run(
        root, PARENT_SHA="abc1234",
        STUB_BASELINE_EXAMPLE_TEMPLATE_EXIT="1", STUB_EXAMPLE_TEMPLATE_EXIT="1",
    )
    assert result.returncode == 1
    assert BASELINE_SENTENCE in result.stdout
    assert "package" in [c[0] for c in calls]


PLACEHOLDER = "REPLACE-WITH-A-UUIDV7-YOU-MINT"
CI_ADMINISTRATOR = "01900000-0000-7000-8000-000000000001"


def test_the_example_placeholder_is_replaced_in_a_temp_copy_before_rendering(tmp_path):
    """chart's example/values.yaml ships a placeholder the seeder render-check
    refuses by design; chart's own tests render it with a fixed uuidv7."""
    root = repository(tmp_path)
    parent_dir = onboarded(root)
    example = parent_dir.parent / "example"
    example.mkdir()
    original = f"administrators:\n  - {PLACEHOLDER}\n"
    (example / "values.yaml").write_text(original)
    # the stub reads the -f file at call time; capture what it was handed
    result, calls = run(root)
    assert result.returncode == 0, result.stdout
    example_calls = [c for c in calls if c[:2] == ["template", "ci-render-example"]]
    assert len(example_calls) == 2  # baseline and vendored
    for call in example_calls:
        handed = call[call.index("-f") + 1]
        assert handed != ".pre-pin-parent/example/values.yaml"
        assert handed.endswith("example/values.yaml")
    assert (example / "values.yaml").read_text() == original  # the checkout is untouched


def test_the_placeholder_free_example_content_is_passed_through_unchanged(tmp_path):
    root = repository(tmp_path)
    with_example(onboarded(root))
    result, _ = run(root)
    assert result.returncode == 0, result.stdout


# --------------------------------------------- the pure failure_message helper


def test_failure_message_is_pure_and_names_the_pin_and_k8():
    result = subprocess.CompletedProcess(args=[], returncode=1, stdout="Error: boom\n", stderr="")
    message = pre_pin_parent.failure_message("widget", "1.2.3", "lint", result)
    assert "widget" in message and "1.2.3" in message and "K-8 order" in message
    assert "Error: boom" in message


# --------------------------------------------- the workflow shape


def workflow_jobs() -> dict:
    import yaml

    return yaml.safe_load(CI_PR.read_text())["jobs"]


def test_the_job_is_skipped_on_yadgarhq_chart_itself():
    condition = str(workflow_jobs()["pre_pin_parent"].get("if", ""))
    assert "github.repository != 'yadgarhq/chart'" in condition


def test_the_job_carries_the_text_edit_short_circuit():
    condition = str(workflow_jobs()["pre_pin_parent"].get("if", ""))
    assert "text_edit" in condition


def test_the_job_resolves_the_parents_dependencies_literally():
    """ADR-0725's static gate (`chart_dependencies_resolved.py`) greps `run:`
    text for this literal; it has to be real YAML a human or the gate can
    read, not buried inside the staged script's own subprocess call."""
    steps = workflow_jobs()["pre_pin_parent"]["steps"]
    runs = [step.get("run", "") for step in steps if isinstance(step.get("run"), str)]
    assert any("helm dependency update chart" in run for run in runs)


def test_the_job_installs_the_same_helm_pin_as_every_other_chart_job():
    steps = workflow_jobs()["pre_pin_parent"]["steps"]
    helm_steps = [s for s in steps if str(s.get("uses", "")).startswith("azure/setup-helm@")]
    assert len(helm_steps) == 1
    # LEDGER 1756 S2b: the same read of `versions.yaml` as every other chart job.
    assert helm_steps[0]["with"]["version"] == "v${{ needs.detect.outputs.helm }}"


def test_passed_gates_the_new_job():
    needs = workflow_jobs()["passed"]["needs"]
    assert "pre_pin_parent" in needs


def test_every_staged_cp_target_is_importable_with_nothing_else_on_the_path(tmp_path):
    """THE REAL BUG THIS CAUGHT (measured on `yadgarhq/actions` pull request
    110, run 37739798664): `parent_pin.py` imports `next_version`, which
    imports `pr_body`; the staging step copied `pre_pin_parent.py`,
    `api_versions.py`, `chart_values_override.py` and `parent_pin.py` alone,
    so the staged copy failed on `ModuleNotFoundError: No module named
    'next_version'` on every repository OTHER than `yadgarhq/actions` itself
    (whose own checkout still carries the whole tree next to the script).

    This reproduces the staging step for real: every `cp "$src"
    "$RUNNER_TEMP/<name>"` line in the job's own `run:` block is read back,
    each source file is copied to a FRESH, OTHERWISE EMPTY directory under
    its staged name, and `import pre_pin_parent` is attempted with ONLY that
    directory on `sys.path` -- no `scripts/` fallback, the same isolation a
    consumer repository's checkout gives the real staging step.
    """
    steps = workflow_jobs()["pre_pin_parent"]["steps"]
    stage_step = next(s for s in steps if s.get("name") == "stage the gate, then restore the tree")
    copies = re.findall(r'cp "\$\((?:dirname "\$src"\))?/?([A-Za-z0-9_./]+)" "\$RUNNER_TEMP/([A-Za-z0-9_.]+)"', stage_step["run"])
    # The literal shell is `cp "$src" ...` and `cp "$api_versions_reader" ...`
    # (a variable, not a path) for most lines; read the variable DEFINITIONS
    # instead, which is what the real step actually resolves to a path.
    assigned = dict(re.findall(r'^(\w+)="\$\(dirname "\$src"\)/([A-Za-z0-9_.]+)"', stage_step["run"], re.MULTILINE))
    staged_names = re.findall(r'cp "\$(\w+)" "\$RUNNER_TEMP/([A-Za-z0-9_.]+)"', stage_step["run"])
    assert staged_names, "no `cp` lines found in the staging step; did its name change?"

    staged = tmp_path / "staged"
    staged.mkdir()
    for var, dest_name in staged_names:
        if var == "src":
            source_name = "pre_pin_parent.py"
        else:
            assert var in assigned, f"{var!r} is cp'd but never assigned from `$(dirname \"$src\")/...`"
            source_name = assigned[var]
        source = Path(__file__).resolve().parents[1] / source_name
        assert source.is_file(), f"{source} (staged as {dest_name}) does not exist"
        (staged / dest_name).write_text(source.read_text())

    result = subprocess.run(
        [sys.executable, "-c", "import pre_pin_parent"],
        cwd=str(staged),
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_ci_verdict_can_evaluate_the_real_condition():
    """The condition this job actually carries, through the gate that reads
    `passed`'s `needs:` in every one of eighteen repositories -- refused rather
    than guessed is `ci_verdict.Refused`, and this is the test that would catch
    a reference the gate has not been taught. `text_edit` is registered the
    way the real `passed` step registers it (`make_text_edit_resolver`),
    rather than guessed at, because the bare whitelist alone refuses it."""
    sys.path.insert(0, str(REPO / "scripts"))
    import ci_verdict

    conditions = dict(ci_verdict.load_conditions(CI_PR, "passed"))
    condition = conditions["pre_pin_parent"]

    def evaluate_as(repository: str) -> bool:
        ctx = {"detect": {"outputs": {"text_edit": "false"}}}
        ci_verdict.register_deferred_reference(
            ci_verdict.TEXT_EDIT_REF,
            ci_verdict.make_text_edit_resolver(
                needs=ctx, repo=repository, sha="deadbeef", run_id="1",
                check_name="ci / passed", token="t",
                fetch=lambda url: (_ for _ in ()).throw(AssertionError("not a text-only edit; must not fetch")),
            ),
        )
        ci_verdict.register_deferred_reference(
            ci_verdict.CONFIG_ONLY_REF,
            ci_verdict.make_config_only_resolver(
                needs=ctx, repo=repository, base_ref="main", sha="deadbeef", token="t",
                fetch=lambda url: (_ for _ in ()).throw(AssertionError("not config-only; must not fetch")),
            ),
        )
        try:
            resolver = ci_verdict.make_resolver(
                event="pull_request", author_type="User", repository=repository, needs=ctx,
            )
            return ci_verdict.evaluate(condition, resolver)
        finally:
            ci_verdict._DEFERRED.clear()

    assert evaluate_as("yadgarhq/gateway") is True
    assert evaluate_as("yadgarhq/chart") is False


# --------------------------------------------- retry on every `helm dependency update` (yaadgaar 345)


def test_every_helm_dependency_update_site_retries_three_times():
    """OCI pulls from GHCR flake; a bare call reds `ci / passed` in the PR's
    repo. Every `run:` block anywhere in the workflows that resolves
    dependencies must loop, and keep the literal ADR-0725's static gate greps."""
    import yaml

    sites = 0
    for path in (REPO / ".github" / "workflows").glob("*.yaml"):
        for job in (yaml.safe_load(path.read_text()).get("jobs") or {}).values():
            for step in job.get("steps") or []:
                run_text = step.get("run")
                if isinstance(run_text, str) and re.search(r"helm\s+dependency\s+update\b", run_text):
                    sites += 1
                    assert "for i in 1 2 3" in run_text, (path.name, run_text)
                    assert "sleep" in run_text, (path.name, run_text)
    assert sites >= 5, sites


def test_the_render_step_exports_the_parents_head_for_the_baseline_message():
    steps = workflow_jobs()["pre_pin_parent"]["steps"]
    gate = next(s for s in steps if "pre_pin_parent.py" in str(s.get("run", "")) and "python3" in s["run"])
    assert "PARENT_SHA" in gate["run"] or "PARENT_SHA" in str(gate.get("env", ""))
