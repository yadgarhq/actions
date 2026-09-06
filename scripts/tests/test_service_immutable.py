"""What `service_immutable.py` refuses, pinned so it cannot quietly stop refusing.

MOST OF THIS FILE IS RED CASES, for the reason `test_helm_pin_agrees.py` states:
a suite that only feeds a gate conforming input certifies the fixture rather than
the gate. Every refusal the script's docstring claims has a case here that DEMANDS
it.

THE LOAD-BEARING CASES ARE THE TWO THIS GATE EXISTS FOR AND THE TWO THAT KEEP IT
HONEST:

  * `test_adding_clusterip_none_is_refused` is ledger 615 reproduced as a diff --
    the pull request that would have been stopped.
  * `test_removing_clusterip_none_is_refused` is the other direction, where the
    apiserver does not fail at all and the chart's intent silently does not
    arrive. A gate that only caught the loud half would be half a gate.
  * `test_a_port_change_is_accepted` is the control. Without it a gate that
    reddened on EVERY chart edit would pass this suite.
  * `test_spec_type_is_not_compared` pins a deliberate scope decision. `type` is
    left out because no chart in the estate renders it and the transitions the
    apiserver refuses depend on the live Service rather than the field. Widening
    the gate to it is allowed; doing so SILENTLY is what this test prevents.

REAL `git` AND REAL `helm`, no mocks. The gate shells out to both, and the
defects it has already had -- a base ref that did not resolve, a `# Source:`
mapping aligned by index -- live in exactly that seam.

Run: python3 -m pytest scripts/tests/ -q
"""

import subprocess
import sys
from pathlib import Path

import pytest

GATE = Path(__file__).resolve().parents[1] / "service_immutable.py"

CHART_YAML = "apiVersion: v2\nname: demo\nversion: 0.1.0\n"

# `{{ .Chart.Name }}` rather than a literal, because that is what every Service
# template in the estate renders and it is what makes the `# Source:` mapping
# non-trivial.
#
# SUBSTITUTED WITH `str.replace`, NEVER `str.format`. A helm template is made of
# `{{` and `{}`-formatting eats exactly those: the first revision of this file
# used `.format` and rendered `name: { .Chart.Name }`, which helm parses as a MAP
# and rejects with "cannot unmarshal object into Go struct field .metadata.name".
# Every test then failed for the fixture rather than for the gate.
SERVICE = """apiVersion: v1
kind: Service
metadata:
  name: {{ .Chart.Name }}
spec:
__EXTRA__  selector:
    app: {{ .Chart.Name }}
  ports:
    - name: grpc
      port: __PORT__
      targetPort: grpc
"""

# A second document from a second template, so a run has more than the Service in
# it and the `# Source:` lookup has something to get wrong.
ACCOUNT = """apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ .Chart.Name }}
"""


def service(headless=False, port=50051, service_type=None):
    extra = ""
    if headless:
        extra += "  clusterIP: None\n"
    if service_type:
        extra += f"  type: {service_type}\n"
    return SERVICE.replace("__EXTRA__", extra).replace("__PORT__", str(port))


def _git_env():
    """The environment a fixture repository is built in, with the caller's out.

    HERMETIC BECAUSE IT HAD TO BE, and the failure is worth recording. Every
    `GIT_*` variable is dropped, because this suite is itself run by the
    `pytest-scripts` pre-commit hook DURING a `git commit` — and `git commit`
    exports `GIT_INDEX_FILE` and friends, which a nested `git` in a temporary
    directory then obeys instead of its own repository.

    `core.hooksPath` is neutralised for the same reason one level up: a developer
    with pre-commit installed through `init.templateDir` gets its hook copied
    into every `git init`, and the fixture's first commit then fails with "No
    .pre-commit-config.yaml file was found" — a suite that goes red for the
    machine it runs on rather than for the gate.
    """
    import os

    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def git(root: Path, *args):
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
        env=_git_env(),
    )
    assert result.returncode == 0, result.stderr
    return result


def commit(root: Path, message: str):
    git(root, "add", "-A")
    git(root, "-c", "commit.gpgsign=false", "commit", "-q", "-m", message)
    return git(root, "rev-parse", "HEAD").stdout.strip()


def write(root: Path, files: dict[str, str]):
    """Lay out (or delete, on a `None` value) files under `root`."""
    for name, text in files.items():
        path = root / name
        if text is None:
            if path.exists():
                path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


@pytest.fixture
def repo(tmp_path):
    """A git repository whose `chart/` renders one headless Service at the base.

    Returns `(root, base_sha)`. A test edits the tree, commits, and runs the gate
    with `SERVICE_IMMUTABLE_BASE` pointing at `base_sha` -- which is the override
    the gate's own `base_ref` documents for exactly this purpose.
    """
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "suite@example.invalid")
    git(root, "config", "user.name", "suite")
    write(
        root,
        {
            "chart/Chart.yaml": CHART_YAML,
            "chart/templates/service.yaml": service(headless=True),
            "chart/templates/serviceaccount.yaml": ACCOUNT,
        },
    )
    return root, commit(root, "base")


def run(root: Path, base: str | None):
    env_base = {"SERVICE_IMMUTABLE_BASE": base} if base else {}
    return subprocess.run(
        [sys.executable, str(GATE)],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
        env={**_clean_env(), **env_base},
    )


def _clean_env():
    """The runner's own `GITHUB_BASE_REF` must not decide a test's base ref.

    Built on `_git_env` so the gate's own `git` calls are as hermetic as the
    fixture's — see that docstring for the `GIT_*` leak this closes.
    """
    return {
        k: v
        for k, v in _git_env().items()
        if k not in ("GITHUB_BASE_REF", "SERVICE_IMMUTABLE_BASE")
    }


# ---------------------------------------------------------------------------
# The two changes this gate exists for
# ---------------------------------------------------------------------------


def test_adding_clusterip_none_is_refused(repo):
    """Ledger 615, as the pull request that introduced it."""
    root, base = repo
    write(root, {"chart/templates/service.yaml": service(headless=False)})
    commit(root, "give the base a VIP")
    # Base is headless, HEAD is not -- run it the other way round by making the
    # BASE the VIP and HEAD headless, which is the direction ledger 615 took.
    vip = git(root, "rev-parse", "HEAD").stdout.strip()
    write(root, {"chart/templates/service.yaml": service(headless=True)})
    commit(root, "make it headless")

    result = run(root, vip)
    assert result.returncode == 1, result.stdout
    assert "clusterIP" in result.stdout
    assert "'None'" in result.stdout or '"None"' in result.stdout
    assert base  # the fixture's first commit is not what this case compares


def test_removing_clusterip_none_is_refused(repo):
    """The silent direction: the apiserver keeps what it has and nothing fails."""
    root, base = repo
    write(root, {"chart/templates/service.yaml": service(headless=False)})
    commit(root, "drop the headless marker")

    result = run(root, base)
    assert result.returncode == 1, result.stdout
    assert "clusterIP" in result.stdout
    assert "absent" in result.stdout


def test_the_refusal_names_the_file_to_edit(repo):
    """The `# Source:` mapping is keyed on the Service NAME, never on an index.

    An earlier revision of the gate aligned two lists by position and named
    `serviceaccount.yaml` for a defect in `service.yaml`, because helm opens its
    output with a leading `---`. The second template in the fixture is what makes
    that mistake reachable, so this test fails if anybody reintroduces it.
    """
    root, base = repo
    write(root, {"chart/templates/service.yaml": service(headless=False)})
    commit(root, "drop the headless marker")

    result = run(root, base)
    assert result.returncode == 1, result.stdout
    assert "chart/templates/service.yaml" in result.stdout
    assert "serviceaccount.yaml" not in result.stdout


def test_the_refusal_carries_the_procedure(repo):
    """A gate that only says no makes the next person guess at the fix."""
    root, base = repo
    write(root, {"chart/templates/service.yaml": service(headless=False)})
    commit(root, "drop the headless marker")

    result = run(root, base)
    assert "delete svc" in result.stdout
    assert "Force=true,Replace=true" in result.stdout
    assert "615" in result.stdout


# ---------------------------------------------------------------------------
# The controls: the gate has to be able to say yes
# ---------------------------------------------------------------------------


def test_a_port_change_is_accepted(repo):
    """Without this, a gate that reddened on every edit would pass this suite."""
    root, base = repo
    write(root, {"chart/templates/service.yaml": service(headless=True, port=50052)})
    commit(root, "move the port")

    result = run(root, base)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No immutable field changes" in result.stdout


def test_an_unchanged_chart_is_accepted(repo):
    root, base = repo
    write(root, {"README.md": "unrelated\n"})
    commit(root, "touch nothing that renders")

    result = run(root, base)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_service_added_on_this_branch_is_accepted(repo):
    """There is no live counterpart to conflict with -- the apiserver creates it."""
    root, base = repo
    write(
        root,
        {
            "chart/templates/second.yaml": (
                "apiVersion: v1\nkind: Service\nmetadata:\n  name: second\n"
                "spec:\n  clusterIP: None\n  ports:\n    - port: 1\n"
            )
        },
    )
    commit(root, "add a second Service")

    result = run(root, base)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_chart_new_on_this_branch_is_accepted(tmp_path):
    """No `chart/` at the base means there is no earlier Service to compare."""
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "suite@example.invalid")
    git(root, "config", "user.name", "suite")
    write(root, {"README.md": "no chart yet\n"})
    base = commit(root, "base")
    write(
        root,
        {
            "chart/Chart.yaml": CHART_YAML,
            "chart/templates/service.yaml": service(headless=True),
        },
    )
    commit(root, "add the chart")

    result = run(root, base)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "new on this branch" in result.stdout


def test_a_repository_with_no_chart_reports_that_it_read_nothing(tmp_path):
    """`yadgarhq/config` renders no Service, and that is a fact about the repo.

    A constant floor above zero would redden it for ever, which is the gate people
    learn to work around.
    """
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "suite@example.invalid")
    git(root, "config", "user.name", "suite")
    write(root, {"README.md": "no chart here\n"})
    base = commit(root, "base")

    result = run(root, base)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Nothing was inspected" in result.stdout


# ---------------------------------------------------------------------------
# Refusals that are about the gate itself rather than about a Service
# ---------------------------------------------------------------------------


def test_a_deleted_service_is_refused(repo):
    """Argo prunes what a chart stops rendering, so this deletes the live one."""
    root, base = repo
    write(root, {"chart/templates/service.yaml": None})
    commit(root, "delete the Service template")

    result = run(root, base)
    assert result.returncode == 1, result.stdout
    assert "is gone" in result.stdout
    assert "prunes" in result.stdout


def test_an_unresolvable_base_refuses_rather_than_skipping(repo):
    """A gate with one side of the comparison must not report agreement."""
    root, _ = repo
    result = run(root, "0000000000000000000000000000000000000000")
    assert result.returncode == 1, result.stdout
    assert "cannot resolve" in result.stdout


def test_a_chart_that_renders_nothing_is_refused(repo):
    """A render that said nothing is not a chart without a Service."""
    root, base = repo
    write(
        root,
        {
            "chart/templates/service.yaml": None,
            "chart/templates/serviceaccount.yaml": (
                "{{- if false }}\n" + ACCOUNT + "{{- end }}\n"
            ),
        },
    )
    commit(root, "render nothing at all")

    result = run(root, base)
    assert result.returncode == 1, result.stdout
    assert "produced 0 document(s)" in result.stdout


def test_a_broken_chart_is_refused_rather_than_reported_clean(repo):
    root, base = repo
    write(root, {"chart/templates/service.yaml": "{{ this is not a template\n"})
    commit(root, "break the template")

    result = run(root, base)
    assert result.returncode == 1, result.stdout
    assert "cannot speak" in result.stdout


# ---------------------------------------------------------------------------
# The scope decision, pinned
# ---------------------------------------------------------------------------


def test_spec_type_is_not_compared(repo):
    """DELIBERATE. See the script's enumeration: no chart in the estate renders
    `spec.type`, and which `type` transitions the apiserver refuses depends on the
    live Service rather than on the field. Widening the gate to it is allowed --
    after somebody establishes the behaviour. Doing it silently is not, and this
    test is what makes the widening visible.
    """
    root, base = repo
    write(
        root,
        {
            "chart/templates/service.yaml": service(
                headless=False, service_type="NodePort"
            )
        },
    )
    commit(root, "become a NodePort")

    result = run(root, base)
    # `clusterIP` still changed here, so the run is red. What this pins is the
    # COUNT: two fields per Service and not three. Asserting on the absence of the
    # string "`spec.type`" would not work and its failure is instructive -- the
    # refusal's own text names `spec.type` while explaining why the escape clause
    # in the apiserver's sentence is unavailable here.
    assert result.returncode == 1, result.stdout
    assert "Compared 2 immutable field(s) across 1 Service(s)" in result.stdout
    assert "`spec.type` changes from" not in result.stdout


def test_the_count_is_reported_on_a_clean_run(repo):
    """"Compared two fields and they agree" and "compared nothing" look identical
    without the count, and only one of them is a pass."""
    root, base = repo
    write(root, {"chart/templates/service.yaml": service(headless=True, port=50052)})
    commit(root, "move the port")

    result = run(root, base)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Compared 2 immutable field(s) across 1 Service(s)" in result.stdout
