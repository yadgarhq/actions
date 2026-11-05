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


# (LITERAL_COUNT_TESTS, PUBLISHED_PIN_TESTS): moved by hand, with the list.
EXPECTED_LIST_SIZES = (14, 7)

PASSING_PARENT_TEST = "def test_the_parent_is_fine():\n    assert True\n"


def listed_tests(body: str = "    pass\n", skip: tuple[str, ...] = ()) -> str:
    """A `test_parent_chart.py` defining every test the gate deselects, each
    with `body`: the fixture parent mirrors the real parent's listed ids, so
    the gate's stale-list refusal sees them collected."""
    names = [nodeid.split("::", 1)[1] for nodeid in pre_pin_parent.DESELECTED if nodeid not in skip]
    return "".join(f"def {name}():\n{body}\n" for name in names)


def parent(
    directory: Path,
    dependencies: dict[str, str],
    tests: str | None = PASSING_PARENT_TEST,
    listed: str | None = None,
) -> Path:
    """`<directory>` as a parent chart whose `dependencies:` carry the given
    `{name: version}` pins -- textually, the shape `parent_pin.pins()` reads.

    The parent checkout's root (the chart's parent directory) also carries
    `scripts/tests/test_parent.py` holding `tests` (yaadgaar 536), and
    `scripts/tests/test_parent_chart.py` holding `listed` (default: every
    deselected test, passing); `tests=None` writes no `scripts/tests/` at all."""
    directory.mkdir(parents=True, exist_ok=True)
    if tests is not None:
        suite = directory.parent / "scripts" / "tests"
        suite.mkdir(parents=True, exist_ok=True)
        (suite / "test_parent.py").write_text(tests)
        (suite / "test_parent_chart.py").write_text(listed_tests() if listed is None else listed)
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


# --------------------------------------------- the parent's own pytest (yaadgaar 536)
#
# A render that passes is not the whole of what yadgarhq/chart's CI asks of a
# pin: `python3 -m pytest scripts/tests/` is the other half, and a pin PR that
# reddens it merges anyway when only the render is checked. The tests below
# are real pytest runs over a fixture parent (the helm stub stays a stub).

# True only while the candidate is vendored: the stub `helm package` writes
# b"stub-tarball", and the gate restores parent main's own file (or its
# absence) before the parent-main run.
VENDORED = (
    "import pathlib\n\n"
    "def vendored():\n"
    "    return any(p.read_bytes() == b'stub-tarball' for p in pathlib.Path('chart/charts').glob('*.tgz'))\n\n"
)


# Red only with the candidate vendored, so parent main's own run does not
# excuse it (attribution, below).
FAILING_PARENT_TEST = (
    VENDORED
    + "def test_the_gateway_route_still_renders():\n"
    "    assert not vendored(), 'route missing'\n"
    "\n"
    "def test_an_unrelated_check_passes():\n"
    "    assert True\n"
)


def onboarded_with(
    root: Path, tests: str | None, listed: str | None = None, published: bytes | None = b"published"
) -> Path:
    """`published` is the bytes of the parent's own `charts/widget-1.2.3.tgz`
    (what `helm dependency update` fetched); None means the file is absent."""
    chart(root / "chart", "widget")
    directory = parent(root / ".pre-pin-parent" / "chart", dependencies={"widget": "1.2.3"}, tests=tests, listed=listed)
    if published is not None:
        (directory / "charts").mkdir(exist_ok=True)
        (directory / "charts" / "widget-1.2.3.tgz").write_bytes(published)
    return directory


def test_a_failing_parent_test_refuses_and_names_the_test(tmp_path):
    """RED HALF of the mutation pair (the green half is every passing test
    above, which now run a passing parent suite)."""
    root = repository(tmp_path)
    onboarded_with(root, FAILING_PARENT_TEST)
    result, calls = run(root)
    assert result.returncode != 0
    assert "::error::" in result.stdout
    assert "Failing tests:\n  scripts/tests/test_parent.py::test_the_gateway_route_still_renders\n" in result.stdout
    assert "yadgarhq/chart's own tests fail with 'widget' vendored at 1.2.3" in result.stdout
    assert "route missing" in result.stdout
    assert [c[0] for c in calls] == ["lint", "template", "package", "lint", "template"]  # renders ran first


def test_only_the_failing_tests_are_named_in_the_list(tmp_path):
    root = repository(tmp_path)
    onboarded_with(root, FAILING_PARENT_TEST)
    result, _ = run(root)
    message = pre_pin_parent.parent_tests_message("widget", "1.2.3", subprocess.CompletedProcess(
        args=[], returncode=1,
        stdout="FAILED scripts/tests/test_a.py::test_one - boom\nERROR scripts/tests/test_b.py::test_two\n1 failed\n",
        stderr="",
    ))
    assert "scripts/tests/test_a.py::test_one" in message
    assert "scripts/tests/test_b.py::test_two" in message
    assert result.returncode != 0


def test_a_passing_parent_suite_leaves_the_pass_path_as_it_was(tmp_path):
    root = repository(tmp_path)
    onboarded_with(root, PASSING_PARENT_TEST)
    result, calls = run(root)
    assert result.returncode == 0, result.stdout
    assert "::error::" not in result.stdout
    assert "yadgarhq/chart's own tests" in result.stdout and "add no failure" in result.stdout
    assert [c[0] for c in calls] == ["lint", "template", "package", "lint", "template"]


def test_a_parent_test_that_cannot_be_collected_fails_closed(tmp_path):
    root = repository(tmp_path)
    onboarded_with(root, "def test_x(:\n")
    result, _ = run(root)
    assert result.returncode != 0
    assert "::error::" in result.stdout
    assert "yadgarhq/chart's own tests" in result.stdout


def test_a_parent_with_no_scripts_tests_fails_closed(tmp_path):
    root = repository(tmp_path)
    onboarded_with(root, None)
    result, _ = run(root)
    assert result.returncode != 0
    assert "::error::" in result.stdout
    assert "scripts/tests is missing" in result.stdout


def test_a_suite_that_collects_nothing_fails_closed(tmp_path):
    """pytest exits 5 for no tests; a green exit over nothing is no evidence.
    The identities move, so every listed test is deselected and nothing runs."""
    root = repository(tmp_path)
    onboarded_with_example(root, "VALUE = 1\n", listed_tests())
    result, _ = run(root, **renders(ESTATE, ONE_MORE_CONFIGMAP))
    assert result.returncode != 0
    assert "::error::" in result.stdout


def test_a_missing_pytest_fails_closed(tmp_path):
    root = repository(tmp_path)
    onboarded_with(root, PASSING_PARENT_TEST)
    interpreter = tmp_path / "no-pytest-python"
    interpreter.write_text("#!/bin/sh\necho 'No module named pytest' >&2\nexit 1\n")
    interpreter.chmod(0o755)
    result, _ = run(root, PRE_PIN_PARENT_PYTHON=interpreter)
    assert result.returncode != 0
    assert "::error::" in result.stdout
    assert "No module named pytest" in result.stdout


def test_an_interpreter_that_does_not_exist_fails_closed(tmp_path):
    root = repository(tmp_path)
    onboarded_with(root, PASSING_PARENT_TEST)
    result, _ = run(root, PRE_PIN_PARENT_PYTHON=tmp_path / "absent")
    assert result.returncode != 0
    assert "::error::" in result.stdout


def test_a_suite_that_outlives_its_ceiling_fails_closed(tmp_path):
    root = repository(tmp_path)
    onboarded_with(root, "import time\n\ndef test_slow():\n    time.sleep(30)\n")
    result, _ = run(root, PRE_PIN_PARENT_TESTS_TIMEOUT="2")
    assert result.returncode != 0
    assert "::error::" in result.stdout
    assert "did not finish within 2 seconds" in result.stdout


def test_the_parent_suite_is_not_run_when_a_render_already_refused(tmp_path):
    root = repository(tmp_path)
    onboarded_with(root, FAILING_PARENT_TEST)
    result, _ = run(root, STUB_LINT_EXIT="1")
    assert result.returncode == 1
    assert "K-8 order" in result.stdout
    assert "test_the_gateway_route_still_renders" not in result.stdout


def test_the_parent_suite_does_not_inherit_the_gates_pythonpath(tmp_path):
    """A PYTHONPATH in the gate's environment (the workflow set one to the
    directory holding the staged gate, `pre_pin_parent.py`, `repin.py`, ...,
    until yaadgaar 655) must not reach the parent suite, where a parent test
    module of the same name would be shadowed by it."""
    root = repository(tmp_path)
    onboarded_with(root, "import os\n\ndef test_no_pythonpath():\n    assert not os.environ.get('PYTHONPATH')\n")
    result, _ = run(root)
    assert result.returncode == 0, result.stdout


def test_the_parent_suite_runs_from_the_checkouts_root(tmp_path):
    root = repository(tmp_path)
    onboarded_with(
        root,
        "import os\n\ndef test_cwd():\n    assert os.path.isdir('scripts/tests') and os.path.isdir('chart')\n",
    )
    result, _ = run(root)
    assert result.returncode == 0, result.stdout


def test_the_no_op_paths_do_not_run_the_parent_suite(tmp_path):
    root = repository(tmp_path)
    chart(root / "chart", "widget")
    parent(root / ".pre-pin-parent" / "chart", dependencies={"other": "1.0.0"}, tests=FAILING_PARENT_TEST)
    result, calls = run(root)
    assert result.returncode == 0
    assert calls == []


# --------------------------------------------- the deselect list (Max's ruling 2026-11-03, option (a))


def objects(*identities: tuple[str, str, str]) -> str:
    """A `helm template` stdout holding one document per `(apiVersion, kind, name)`."""
    return "".join(
        f"---\n# Source: x/templates/{kind.lower()}.yaml\napiVersion: {api}\nkind: {kind}\nmetadata:\n  name: {name}\n"
        for api, kind, name in identities
    )


ESTATE = (("v1", "Service", "gateway"), ("apps/v1", "Deployment", "gateway"))
ONE_MORE_CONFIGMAP = ESTATE + (("v1", "ConfigMap", "widget-extra"),)

# Red only with the candidate vendored, so parent main's own run never excuses it.
LISTED_RED_WHEN_VENDORED = VENDORED + listed_tests("    assert not vendored(), 'count moved'\n")


def renders(baseline_ci, vendored_ci, baseline_example=None, vendored_example=None) -> dict[str, str]:
    """Stub stdout for the four renders the gate compares (the example
    defaults to the ci render on each side)."""
    return {
        "STUB_BASELINE_TEMPLATE_STDOUT": objects(*baseline_ci),
        "STUB_TEMPLATE_STDOUT": objects(*vendored_ci),
        "STUB_BASELINE_EXAMPLE_TEMPLATE_STDOUT": objects(*(baseline_example or baseline_ci)),
        "STUB_EXAMPLE_TEMPLATE_STDOUT": objects(*(vendored_example or vendored_ci)),
    }


def onboarded_with_example(root: Path, tests: str, listed: str) -> Path:
    directory = onboarded_with(root, tests, listed=listed)
    with_example(directory)
    return directory


def test_a_candidate_that_adds_a_configmap_deselects_the_literal_tests_and_passes(tmp_path):
    """GREEN HALF (yaadgaar 563): the object identities move, so the literal
    counts are the parent's to move after the pin, and they do not block."""
    root = repository(tmp_path)
    onboarded_with_example(root, PASSING_PARENT_TEST, LISTED_RED_WHEN_VENDORED)
    result, _ = run(root, **renders(ESTATE, ONE_MORE_CONFIGMAP))
    assert result.returncode == 0, result.stdout
    assert "::error::" not in result.stdout
    assert "the object identities moved" in result.stdout
    assert "+ ('v1', 'ConfigMap', 'widget-extra')" in result.stdout


def test_a_label_change_that_moves_no_identity_runs_the_literal_tests_and_blocks(tmp_path):
    """RED HALF (yaadgaar 563): the same identities on both sides, so the
    literal-count tests run, and their non-count assertions block."""
    root = repository(tmp_path)
    labels = pre_pin_parent.PARENT_TEST_FILE + "::test_global_image_and_labels_still_render"
    body = VENDORED + listed_tests().replace(
        "def test_global_image_and_labels_still_render():\n    pass\n",
        "def test_global_image_and_labels_still_render():\n    assert not vendored(), 'label lost'\n",
    )
    assert "label lost" in body
    onboarded_with_example(root, PASSING_PARENT_TEST, body)
    result, _ = run(root, **renders(ESTATE, ESTATE))
    assert result.returncode == 1, result.stdout
    assert f"Failing tests:\n  {labels}\n" in result.stdout
    assert "the object identities did not move" in result.stdout


def test_a_same_count_rename_still_deselects(tmp_path):
    """Counts alone are not the identity set: a renamed object moves it."""
    root = repository(tmp_path)
    onboarded_with_example(root, PASSING_PARENT_TEST, LISTED_RED_WHEN_VENDORED)
    renamed = (("v1", "Service", "gateway-renamed"), ESTATE[1])
    result, _ = run(root, **renders(ESTATE, renamed))
    assert result.returncode == 0, result.stdout


def test_a_duplicated_identity_still_deselects(tmp_path):
    """A multiset, not a set: a second object of one identity moves `len(documents)`."""
    root = repository(tmp_path)
    onboarded_with_example(root, PASSING_PARENT_TEST, LISTED_RED_WHEN_VENDORED)
    result, _ = run(root, **renders(ESTATE, ESTATE + (ESTATE[0],)))
    assert result.returncode == 0, result.stdout


def test_a_move_in_the_example_render_alone_still_deselects(tmp_path):
    """The adopter render counts too: an object only the example values reach."""
    root = repository(tmp_path)
    onboarded_with_example(root, PASSING_PARENT_TEST, LISTED_RED_WHEN_VENDORED)
    result, _ = run(root, **renders(ESTATE, ESTATE, ESTATE, ONE_MORE_CONFIGMAP))
    assert result.returncode == 0, result.stdout


def test_no_example_values_means_unsure_and_the_literal_tests_run(tmp_path):
    """FAIL CLOSED: no adopter render to compare, so nothing is deselected for counts."""
    root = repository(tmp_path)
    onboarded_with(root, PASSING_PARENT_TEST, listed=LISTED_RED_WHEN_VENDORED)
    result, _ = run(root, **renders(ESTATE, ONE_MORE_CONFIGMAP))
    assert result.returncode == 1, result.stdout
    assert "cannot compare the object identities" in result.stdout


def test_a_red_baseline_means_unsure_and_the_literal_tests_run(tmp_path):
    """FAIL CLOSED: a parent main that does not render has no identity set."""
    root = repository(tmp_path)
    onboarded_with_example(root, PASSING_PARENT_TEST, LISTED_RED_WHEN_VENDORED)
    result, _ = run(root, STUB_BASELINE_LINT_EXIT="1", **renders(ESTATE, ONE_MORE_CONFIGMAP))
    assert result.returncode == 1, result.stdout
    assert "cannot compare the object identities" in result.stdout


def test_an_unparsable_render_means_unsure_and_the_literal_tests_run(tmp_path):
    root = repository(tmp_path)
    onboarded_with_example(root, PASSING_PARENT_TEST, LISTED_RED_WHEN_VENDORED)
    stub = renders(ESTATE, ONE_MORE_CONFIGMAP)
    stub["STUB_TEMPLATE_STDOUT"] += "---\nkind: [unclosed\n"
    result, _ = run(root, **stub)
    assert result.returncode == 1, result.stdout
    assert "cannot compare the object identities" in result.stdout


def test_an_empty_render_means_unsure_and_the_literal_tests_run(tmp_path):
    """Nothing rendered on both sides is equal, and proves nothing."""
    root = repository(tmp_path)
    onboarded_with_example(root, PASSING_PARENT_TEST, LISTED_RED_WHEN_VENDORED)
    result, _ = run(root)
    assert result.returncode == 1, result.stdout
    assert "cannot compare the object identities" in result.stdout


def test_a_literal_test_parent_main_also_fails_is_excused_by_the_same_selection(tmp_path):
    """Attribution reruns parent main with the vendored run's OWN selection:
    a literal test red on both sides is main's, not this pull request's."""
    root = repository(tmp_path)
    labels = pre_pin_parent.PARENT_TEST_FILE + "::test_global_image_and_labels_still_render"
    body = listed_tests().replace(
        "def test_global_image_and_labels_still_render():\n    pass\n",
        "def test_global_image_and_labels_still_render():\n    assert False, 'red on main too'\n",
    )
    onboarded_with_example(root, PASSING_PARENT_TEST, body)
    result, _ = run(root, PARENT_SHA="abc1234", **renders(ESTATE, ESTATE))
    assert result.returncode == 0, result.stdout
    assert f"already fail at abc1234 with nothing of this pull request vendored; not this PR's failures:\n  {labels}" in result.stdout


def test_a_red_unlisted_test_beside_red_listed_ones_still_blocks_and_is_named(tmp_path):
    """RED HALF: the same red listed tests with the identities moved, plus one
    real parent failure."""
    root = repository(tmp_path)
    onboarded_with_example(root, FAILING_PARENT_TEST, LISTED_RED_WHEN_VENDORED)
    result, _ = run(root, **renders(ESTATE, ONE_MORE_CONFIGMAP))
    assert result.returncode == 1
    assert "Failing tests:\n  scripts/tests/test_parent.py::test_the_gateway_route_still_renders\n\n" in result.stdout
    for nodeid in pre_pin_parent.DESELECTED:
        assert f"\n  {nodeid}\n" not in result.stdout


def test_a_stale_literal_test_refuses_even_when_the_identities_did_not_move(tmp_path):
    """The stale-list check covers the whole list, not only what is deselected."""
    gone = sorted(pre_pin_parent.LITERAL_COUNT_TESTS)[0]
    root = repository(tmp_path)
    onboarded_with_example(root, PASSING_PARENT_TEST, listed_tests(skip=(gone,)))
    result, _ = run(root, **renders(ESTATE, ESTATE))
    assert result.returncode == 1
    assert "no longer collects" in result.stdout
    assert f"\n  {gone}" in result.stdout


def test_identity_census_is_pure_and_counts_duplicates():
    census = pre_pin_parent.identity_census(objects(*ESTATE, ESTATE[0]) + "---\n# empty\n---\nfoo: bar\n")
    assert census == {("v1", "Service", "gateway"): 2, ("apps/v1", "Deployment", "gateway"): 1}
    assert pre_pin_parent.identity_census("") is None
    assert pre_pin_parent.identity_census("kind: [unclosed\n") is None


def test_a_listed_test_the_parent_no_longer_collects_refuses_by_name(tmp_path):
    """STALE LIST = RED: a renamed parent test is not silently deselected-nothing."""
    gone = sorted(pre_pin_parent.DESELECTED)[0]
    root = repository(tmp_path)
    onboarded_with(root, PASSING_PARENT_TEST, listed=listed_tests(skip=(gone,)))
    result, _ = run(root)
    assert result.returncode == 1
    assert "no longer collects" in result.stdout
    assert f"\n  {gone}" in result.stdout


def test_a_deselect_that_would_also_drop_an_unlisted_test_refuses(tmp_path):
    """pytest's `--deselect` is a prefix match; `test_x` would swallow `test_x_y`."""
    swallowed = sorted(pre_pin_parent.DESELECTED)[0].split("::", 1)[1] + "_too"
    root = repository(tmp_path)
    onboarded_with_example(root, PASSING_PARENT_TEST, listed_tests() + f"def {swallowed}():\n    assert False\n")
    result, _ = run(root, **renders(ESTATE, ONE_MORE_CONFIGMAP))
    assert result.returncode == 1
    assert "would also drop" in result.stdout and swallowed in result.stdout


def test_selection_matches_exactly_or_with_params_only():
    listed = ["f.py::test_a"]
    collected = ["f.py::test_a", "f.py::test_a[1]", "f.py::test_ab", "f.py::test_b"]
    deselect, stale, overreach = pre_pin_parent.selection(collected, listed)
    assert deselect == ["f.py::test_a", "f.py::test_a[1]"]
    assert stale == []
    assert overreach == [("f.py::test_a", "f.py::test_ab")]
    assert pre_pin_parent.selection(["f.py::test_b"], listed)[1] == ["f.py::test_a"]


def test_the_deselect_list_is_named_exact_and_reasoned():
    """The list cannot silently grow: every entry is one named function in the
    parent's test file (no bare file, no class, no params), every one carries
    its reason, and the count is a literal here, moved by hand with the list."""
    literal, published = pre_pin_parent.LITERAL_COUNT_TESTS, pre_pin_parent.PUBLISHED_PIN_TESTS
    assert set(pre_pin_parent.DESELECTED) == set(literal) | set(published)
    assert not set(literal) & set(published)
    for nodeid, reason in pre_pin_parent.DESELECTED.items():
        assert re.fullmatch(re.escape(pre_pin_parent.PARENT_TEST_FILE) + r"::test_\w+", nodeid), nodeid
        assert reason.strip(), nodeid
    assert (len(literal), len(published)) == EXPECTED_LIST_SIZES


def test_an_exit_1_that_names_no_failing_test_fails_closed(tmp_path):
    """A run that says 'failed' but names nothing cannot be attributed, so it
    is never excused against parent main."""
    root = repository(tmp_path)
    onboarded_with(root, PASSING_PARENT_TEST)
    interpreter = tmp_path / "silent-python"
    interpreter.write_text(
        "#!/bin/sh\n"
        "case \"$*\" in *--collect-only*) echo 'scripts/tests/test_parent.py::test_x';"
        " for t in " + " ".join(pre_pin_parent.DESELECTED) + "; do echo \"$t\"; done; exit 0;; esac\n"
        "exit 1\n"
    )
    interpreter.chmod(0o755)
    result, _ = run(root, PRE_PIN_PARENT_PYTHON=interpreter)
    assert result.returncode == 1
    assert "did not run to a verdict (exit 1)" in result.stdout


def fake_pytest(tmp_path: Path, run_body: str, collect_body: str = "") -> Path:
    """An interpreter standing in for `python3 -m pytest`: `--collect-only`
    prints one unlisted id plus every listed one, any other call runs `run_body`."""
    interpreter = tmp_path / "fake-python"
    interpreter.write_text(
        "#!/bin/sh\n"
        "case \"$*\" in *--collect-only*) " + collect_body + "echo 'scripts/tests/test_parent.py::test_x';"
        " for t in " + " ".join(pre_pin_parent.DESELECTED) + "; do echo \"$t\"; done; exit 0;; esac\n"
        + run_body
    )
    interpreter.chmod(0o755)
    return interpreter


def test_an_exit_other_than_0_or_1_is_never_excused_against_parent_main(tmp_path):
    """Exit 2 (interrupted, a collection error mid-run) on BOTH runs, naming the
    same test: the subtraction would excuse it, so it must refuse first."""
    root = repository(tmp_path)
    onboarded_with(root, PASSING_PARENT_TEST)
    interpreter = fake_pytest(tmp_path, "echo 'FAILED scripts/tests/test_parent.py::test_x'\nexit 2\n")
    result, _ = run(root, PRE_PIN_PARENT_PYTHON=interpreter)
    assert result.returncode == 1
    assert "did not run to a verdict (exit 2)" in result.stdout


def test_the_ceiling_is_one_budget_across_every_parent_pytest_run(tmp_path):
    """Collect and run each fit the ceiling alone, but not together."""
    root = repository(tmp_path)
    onboarded_with(root, PASSING_PARENT_TEST)
    sleep = shutil.which("sleep")
    interpreter = fake_pytest(tmp_path, f"{sleep} 1.5\nexit 0\n", collect_body=f"{sleep} 1.5; ")
    result, _ = run(root, PRE_PIN_PARENT_PYTHON=interpreter, PRE_PIN_PARENT_TESTS_TIMEOUT="2")
    assert result.returncode == 1, result.stdout
    assert "did not finish within 2 seconds" in result.stdout


# --------------------------------------------- the pull request's own root cannot steer the parent's pytest


def test_a_pytest_ini_at_the_pull_requests_root_cannot_turn_the_run_into_a_no_op(tmp_path):
    """Its `addopts` reach the run without `-c /dev/null`: `--collect-only`
    runs nothing and exits 0."""
    root = repository(tmp_path)
    onboarded_with(root, FAILING_PARENT_TEST)
    (root / "pytest.ini").write_text("[pytest]\naddopts = --collect-only\n")
    result, _ = run(root)
    assert result.returncode == 1, result.stdout
    assert "test_the_gateway_route_still_renders" in result.stdout


def test_a_conftest_at_the_pull_requests_root_cannot_force_the_run_green(tmp_path):
    root = repository(tmp_path)
    onboarded_with(root, FAILING_PARENT_TEST)
    # The ini makes the pull request's root the rootdir, and so the conftest
    # search reaches it: the pair is the measured attack.
    (root / "pytest.ini").write_text("[pytest]\n")
    (root / "conftest.py").write_text(
        "import pytest\n\n"
        "@pytest.hookimpl(hookwrapper=True)\n"
        "def pytest_runtest_makereport(item, call):\n"
        "    outcome = yield\n"
        "    report = outcome.get_result()\n"
        "    report.outcome = 'passed'\n"
        "    report.longrepr = None\n"
    )
    result, _ = run(root)
    assert result.returncode == 1, result.stdout
    assert "test_the_gateway_route_still_renders" in result.stdout


def test_pytest_addopts_from_the_job_does_not_reach_the_parents_run(tmp_path):
    root = repository(tmp_path)
    onboarded_with(root, FAILING_PARENT_TEST)
    result, _ = run(root, PYTEST_ADDOPTS="--deselect scripts/tests/test_parent.py::test_the_gateway_route_still_renders")
    assert result.returncode == 1, result.stdout


def test_the_parent_suite_runs_isolated_from_any_ini_and_cache():
    assert pre_pin_parent.PYTEST_ISOLATION == ["-p", "no:cacheprovider", "--rootdir=.", "--confcutdir=.", "-c", os.devnull]


# --------------------------------------------- attribution against parent main (fix 4)

def test_a_failure_only_after_vendoring_is_this_pull_requests(tmp_path):
    root = repository(tmp_path)
    onboarded_with(root, VENDORED + "def test_new_red():\n    assert not vendored()\n")
    result, _ = run(root)
    assert result.returncode == 1
    assert "Failing tests:\n  scripts/tests/test_parent.py::test_new_red\n" in result.stdout
    assert "already fail" not in result.stdout


def test_a_failure_parent_main_already_has_is_named_as_mains_and_does_not_block(tmp_path):
    root = repository(tmp_path)
    onboarded_with(root, "def test_old_red():\n    assert False\n")
    result, _ = run(root, PARENT_SHA="abc1234")
    assert result.returncode == 0, result.stdout
    assert "::warning::yadgarhq/chart main's own tests already fail at abc1234" in result.stdout
    assert "\n  scripts/tests/test_parent.py::test_old_red" in result.stdout
    assert "::error::" not in result.stdout


def test_a_new_failure_beside_an_old_one_blocks_and_names_only_the_new(tmp_path):
    root = repository(tmp_path)
    onboarded_with(
        root,
        VENDORED + "def test_old_red():\n    assert False\n\ndef test_new_red():\n    assert not vendored()\n",
    )
    result, _ = run(root)
    assert result.returncode == 1
    assert "Failing tests:\n  scripts/tests/test_parent.py::test_new_red\n\n" in result.stdout
    assert "already fail" in result.stdout and "\n  scripts/tests/test_parent.py::test_old_red" in result.stdout


def test_a_parent_main_that_cannot_be_tested_excuses_nothing(tmp_path):
    """Fail closed: a baseline that proves nothing subtracts nothing."""
    root = repository(tmp_path)
    onboarded_with(
        root,
        VENDORED + "if not vendored():\n    raise ImportError('main cannot import')\n\n"
        "def test_red():\n    assert False\n",
    )
    result, _ = run(root)
    assert result.returncode == 1
    assert "could not be run as a baseline" in result.stdout
    assert "Failing tests:\n  scripts/tests/test_parent.py::test_red\n" in result.stdout


def test_no_published_tarball_means_no_baseline_and_nothing_excused(tmp_path):
    """FAIL-OPEN FOUND IN REVIEW: with no published `charts/widget-1.2.3.tgz`,
    a parent-main run would render without the dependency at all, so it is no
    baseline for this chart; every vendored failure stays this PR's."""
    root = repository(tmp_path)
    onboarded_with(
        root,
        "import pathlib\n\n"
        "def test_needs_the_dependency():\n"
        "    assert pathlib.Path('chart/charts/widget-1.2.3.tgz').read_bytes() == b'published'\n",
        published=None,
    )
    result, _ = run(root)
    assert result.returncode == 1, result.stdout
    assert "no published" in result.stdout
    assert "Failing tests:\n  scripts/tests/test_parent.py::test_needs_the_dependency\n" in result.stdout
    assert "already fail" not in result.stdout


def test_parent_main_is_not_rerun_when_the_vendored_run_is_green(tmp_path):
    """The green path is one suite run: a test that would fail on main never runs there."""
    root = repository(tmp_path)
    onboarded_with(root, VENDORED + "def test_green_when_vendored():\n    assert vendored()\n")
    result, _ = run(root)
    assert result.returncode == 0, result.stdout
    assert "already fail" not in result.stdout


def test_the_published_tarball_is_restored_byte_for_byte_for_the_baseline(tmp_path):
    """Red vendored, green on the restored published bytes: a new failure, and
    the published file is what is left in `charts/`."""
    root = repository(tmp_path)
    parent_dir = onboarded_with(
        root,
        "import pathlib\n\n"
        "def test_sees_main():\n"
        "    assert pathlib.Path('chart/charts/widget-1.2.3.tgz').read_bytes() == b'published'\n",
    )
    result, _ = run(root)
    assert result.returncode == 1, result.stdout
    assert "Failing tests:\n  scripts/tests/test_parent.py::test_sees_main\n" in result.stdout
    assert (parent_dir / "charts" / "widget-1.2.3.tgz").read_bytes() == b"published"


def test_the_job_installs_the_parents_test_tools_from_versions_yaml_before_the_gate():
    steps = workflow_jobs()["pre_pin_parent"]["steps"]
    names = [s.get("name", "") for s in steps]
    install = next(s for s in steps if "install the parent chart's test tools" in s.get("name", ""))
    assert names.index(install["name"]) < next(i for i, s in enumerate(steps) if "pre_pin_parent.py" in str(s.get("run", "")) and "python3" in s["run"])
    assert install["env"]["PYTEST_VERSION"] == "${{ needs.detect.outputs.pytest }}"
    assert install["env"]["SHELLCHECK_PY_VERSION"] == "${{ needs.detect.outputs.shellcheck_py }}"
    assert "pytest==${PYTEST_VERSION}" in install["run"]
    assert "shellcheck-py==${SHELLCHECK_PY_VERSION}" in install["run"]


def test_the_job_keeps_its_timeout_and_the_ceiling_fits_inside_it():
    job = workflow_jobs()["pre_pin_parent"]
    assert job["timeout-minutes"] == 30
    assert pre_pin_parent.PARENT_TESTS_TIMEOUT_SECONDS <= 20 * 60


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
