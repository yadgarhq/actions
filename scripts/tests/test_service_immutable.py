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
  * `test_a_chart_that_renders_nothing_is_refused` and
    `test_a_new_chart_that_renders_nothing_by_design_is_accepted` are the two
    halves of the DERIVED document floor, and neither means anything without the
    other. The first is the regression -- a chart that rendered documents and now
    renders none is still refused. The second is ADR-0752's `yadgarhq/platform`,
    whose default render is empty on purpose. A floor that cannot tell them apart
    is either a gate that refuses a ruled design or a gate that passes having
    read nothing.
  * `test_a_new_chart_that_renders_nothing_and_declares_nothing_is_refused` is
    the other half of `test_a_new_chart_that_renders_nothing_by_design_is_
    accepted`, and it is the one that had to be constructed. Without it the
    derived floor lets a chart that broke on its FIRST pull request through for
    ever: the merged emptiness becomes the next run's BASE, so the floor derives
    to 0 again and never rises. The two charts render byte-identically; the only
    thing between them is whether the repository declares the values it does
    render under, and a test that only fed the gate the declaring one would
    certify the fixture.
  * `test_a_dependency_declared_at_base_is_resolved` is ADR-0725's regression:
    the BASE chart is rendered from a bare `git archive`, which never carries a
    resolved `charts/` any more than a fresh checkout does, and nothing else in
    this suite would notice `resolve_dependencies()` quietly stop running.

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
    """A render that said nothing is not a chart without a Service.

    THE REGRESSION THE DOCUMENT FLOOR EXISTS FOR, and the case that keeps the
    derived floor honest. The base here renders two documents, so the floor
    derives to 1 and this is still refused -- lifting the floor for a chart that
    NEVER rendered anything must not lift it for a chart that stopped.
    """
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
    # The floor is named as DERIVED and the base count it came from is printed,
    # so a reader can tell this refusal from the constant one it replaced.
    assert "against a floor of 1 derived from the 2 document(s)" in result.stdout


def test_a_chart_that_loses_its_only_documents_is_refused(tmp_path):
    """The same regression where no Service is involved at all.

    `yadgarhq/config` renders ConfigMaps and no Service, so the missing-Service
    comparison below can never fire on it and the DOCUMENT floor is the only
    thing standing between it and a silent emptying. This is that repository's
    shape: one document at the base, a condition flipped, nothing here.
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
            "chart/values.yaml": "render: true\n",
            "chart/templates/cm.yaml": (
                "{{- if .Values.render }}\napiVersion: v1\nkind: ConfigMap\n"
                "metadata:\n  name: {{ .Chart.Name }}\n{{- end }}\n"
            ),
        },
    )
    base = commit(root, "a chart of ConfigMaps and no Service")
    write(root, {"chart/values.yaml": "render: false\n"})
    commit(root, "flip the condition off")

    result = run(root, base)
    assert result.returncode == 1, result.stdout
    assert "produced 0 document(s)" in result.stdout
    assert "against a floor of 1 derived from the 1 document(s)" in result.stdout


# ---------------------------------------------------------------------------
# ADR-0752: a chart whose default render is empty BY DESIGN
# ---------------------------------------------------------------------------


def _empty_by_design(root):
    """A chart every one of whose objects sits behind a `create` toggle.

    `yadgarhq/platform`'s shape, reduced to the one property that matters here:
    `helm template` exits 0 and emits no document.
    """
    write(
        root,
        {
            "chart/Chart.yaml": CHART_YAML,
            "chart/values.yaml": "create: false\n",
            "chart/templates/account.yaml": (
                "{{- if .Values.create }}\n" + ACCOUNT + "{{- end }}\n"
            ),
        },
    )


def _new_repo(tmp_path, readme="no chart yet\n"):
    """A repository whose `main` holds a README and no `chart/` at all.

    `yadgarhq/platform`'s first pull request, which is the shape the base-absent
    arm is written for. Returns `(root, base_sha)`.
    """
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "suite@example.invalid")
    git(root, "config", "user.name", "suite")
    write(root, {"README.md": readme})
    return root, commit(root, "base")


def test_a_new_chart_that_renders_nothing_and_declares_nothing_is_refused(tmp_path):
    """THE ONE RUN THAT CAN ASK, and the case the derived floor alone lets past.

    A chart new on this branch whose render is empty BY ACCIDENT produces exactly
    what ADR-0752's design produces, so the floor cannot tell them apart. The
    floor's own bound does not save it either: the accident merges, the merged
    emptiness is the NEXT run's base, the floor derives to 0 again and stays
    there. This arm is the only moment anything is still checkable, and what it
    asks for is a values file in the tree -- see `declared_alternate_values`.

    The chart here is `_empty_by_design`'s, unchanged and undeclared.
    """
    root, base = _new_repo(tmp_path)
    _empty_by_design(root)
    commit(root, "add a chart whose render broke")

    result = run(root, base)
    assert result.returncode == 1, result.stdout
    assert "produced 0 document(s) here" in result.stdout
    assert "declares no alternate values file" in result.stdout


def test_an_empty_alternate_values_file_does_not_discharge_the_obligation(tmp_path):
    """`touch example/values.yaml` is not a declaration.

    An empty file parses to `None`, which DIFFERS from a populated
    `chart/values.yaml` -- so a bare difference test would accept it, and an
    obligation one keystroke discharges is not an obligation.
    """
    root, base = _new_repo(tmp_path)
    _empty_by_design(root)
    write(root, {"example/values.yaml": ""})
    commit(root, "add a chart whose render broke, and an empty values file")

    result = run(root, base)
    assert result.returncode == 1, result.stdout
    assert "declares no alternate values file" in result.stdout


def test_an_alternate_values_file_equal_to_the_defaults_does_not_count(tmp_path):
    """A copy of the defaults renders exactly the same nothing.

    The comparison is on PARSED data rather than bytes, so this copy differs in
    its comments and still does not count.
    """
    root, base = _new_repo(tmp_path)
    _empty_by_design(root)
    write(root, {"example/values.yaml": "# a copy, with a comment\ncreate: false\n"})
    commit(root, "add a chart whose render broke, and a copy of its defaults")

    result = run(root, base)
    assert result.returncode == 1, result.stdout
    assert "declares no alternate values file" in result.stdout


def test_a_new_chart_that_renders_nothing_by_design_is_accepted(tmp_path):
    """ADR-0752 and `yadgarhq/platform`, which is why the floor was derived.

    That decision requires the chart to "install on a bare cluster rendering
    nothing and requiring no CRD". The constant floor refused exactly this, and a
    ruled design and a gate cannot both be right -- so the gate moved. The base
    here has no `chart/` at all, which is `platform`'s first pull request.

    THE ONE DIFFERENCE FROM THE REFUSED CASE ABOVE is `example/values.yaml`. The
    renders are identical; `platform` carries that file today unchanged.
    """
    root, base = _new_repo(tmp_path)
    _empty_by_design(root)
    write(root, {"example/values.yaml": "# turn the layer on\ncreate: true\n"})
    commit(root, "add a chart that renders nothing at its defaults")

    result = run(root, base)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "new on this branch" in result.stdout
    # THE COUNT IS PRINTED ON THIS PATH TOO. A gate that passes having examined
    # nothing is the defect; a gate that passes having examined nothing AND SAID
    # SO against the floor it derived is a measurement. It is printed and not
    # asserted here: `floor` is provably 0 on this arm, so the comparison above
    # cannot fail. The number is asserted HERE, where it can.
    assert "produced 0 document(s) here, 0 of them Service(s)" in result.stdout
    assert "against a floor of 0" in result.stdout
    # The evidence the gate accepted is named, so the pass can be checked.
    assert "declares `example/values.yaml`" in result.stdout


def test_a_new_chart_that_renders_something_owes_no_values_file(tmp_path):
    """The control on the discriminator, and without it the obligation is wrong.

    What is asked for is owed by a chart whose DEFAULT render is empty, and by
    nothing else. Every repository that adds a normal chart on a branch -- one
    that renders its objects at its defaults -- must still pass carrying no
    `example/values.yaml` and no `chart/ci/`. A gate that asked every new chart
    for a second values file would be a new rule nobody decided.
    """
    root, base = _new_repo(tmp_path)
    write(
        root,
        {
            "chart/Chart.yaml": CHART_YAML,
            "chart/templates/service.yaml": service(headless=True),
        },
    )
    commit(root, "add a chart that renders a Service")

    result = run(root, base)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "produced 1 document(s) here, 1 of them Service(s)" in result.stdout
    assert "declares" not in result.stdout


def test_helms_own_ci_values_convention_counts(tmp_path):
    """`chart/ci/*-values.yaml` is the second accepted name, and not an alias.

    helm reads that directory as "the values this chart is meant to be exercised
    under", so a repository using it is already saying what this asks. Without
    this case the convention would be documented and unreachable.
    """
    root, base = _new_repo(tmp_path)
    _empty_by_design(root)
    write(root, {"chart/ci/on-values.yaml": "create: true\n"})
    commit(root, "add a chart that renders nothing at its defaults")

    result = run(root, base)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "declares `chart/ci/on-values.yaml`" in result.stdout


def test_a_chart_that_rendered_nothing_and_still_renders_nothing_is_accepted(tmp_path):
    """The second pull request against a by-design-empty chart.

    The base now HAS the chart and it renders nothing, so this is the arm the
    `config` reasoning was already written for -- a chart that rendered none and
    still renders none is a fact about the repository -- extended from Services
    to documents. Nothing is listed anywhere for it to work.

    THIS FIXTURE DECLARES NO ALTERNATE VALUES FILE, AND THAT IS THE POINT. The
    discriminator lives on the base-ABSENT arm alone; tightening this one would
    refuse `yadgarhq/platform`'s second pull request onwards, which is the ruled
    design from its second day. So this stays green, and with it the fact the
    script's comment now states plainly: an UNFIXED empty render is invisible to
    this gate from then on.
    """
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "suite@example.invalid")
    git(root, "config", "user.name", "suite")
    _empty_by_design(root)
    base = commit(root, "a chart that renders nothing at its defaults")
    write(root, {"chart/templates/account.yaml": None})
    commit(root, "edit the chart, still rendering nothing")

    result = run(root, base)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        "produced 0 document(s) here against a floor of 0 derived from the 0"
        in result.stdout
    )


def test_the_floor_rises_the_moment_the_base_renders_a_document(tmp_path):
    """The narrowing has a bottom, and this is where it stops.

    A by-design-empty chart that starts rendering something has a floor of 1 from
    the next pull request onwards. Without this, "the floor rises to 1" in the
    script's comment would be a claim rather than a behaviour.
    """
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "suite@example.invalid")
    git(root, "config", "user.name", "suite")
    _empty_by_design(root)
    commit(root, "a chart that renders nothing at its defaults")
    write(root, {"chart/values.yaml": "create: true\n"})
    base = commit(root, "turn the toggle on by default")
    write(root, {"chart/values.yaml": "create: false\n"})
    commit(root, "turn it back off")

    result = run(root, base)
    assert result.returncode == 1, result.stdout
    assert "against a floor of 1 derived from the 1 document(s)" in result.stdout


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


# ---------------------------------------------------------------------------
# ADR-0725: a chart's dependencies are resolved before either side renders
# ---------------------------------------------------------------------------


def test_a_dependency_declared_at_base_is_resolved(repo):
    """The regression this gate could reintroduce with no warning.

    `chart_at` writes the BASE chart from a bare `git archive` -- history, so a
    `dependencies:` key there is exactly as unresolved as a fresh checkout's
    would be without `ci-pr.yaml`'s workflow-level step. The dependency here is
    a local `file://` sibling INSIDE `chart/`, never an OCI pull -- so
    `git archive chart` carries it, this test needs no network, and what is
    under test is `resolve_dependencies()` alone. Before that function existed,
    this failed with "helm template failed on the chart as of `<base>`": the
    "missing in charts/ directory" refusal moved one hop later rather than
    being fixed.
    """
    root, _ = repo
    write(
        root,
        {
            "chart/Chart.yaml": (
                CHART_YAML
                + 'dependencies:\n'
                + "  - name: subchart\n"
                + "    version: 0.1.0\n"
                + '    repository: "file://./vendor-src/subchart"\n'
            ),
            "chart/vendor-src/subchart/Chart.yaml": (
                "apiVersion: v2\nname: subchart\nversion: 0.1.0\n"
            ),
            "chart/vendor-src/subchart/templates/cm.yaml": (
                "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: sub\n"
            ),
        },
    )
    base = commit(root, "declare a dependency")
    write(root, {"chart/templates/service.yaml": service(headless=True, port=50052)})
    commit(root, "move the port")

    # `ci-pr.yaml`'s `precommit`/`portability`/`service_immutable` jobs each run
    # this before the gate starts, for the checkout's HEAD only -- reproduced
    # here so the BASE side, resolved inside the gate itself, is the only thing
    # this test actually exercises.
    subprocess.run(
        ["helm", "dependency", "update", "chart"],
        cwd=str(root),
        check=True,
        capture_output=True,
    )

    result = run(root, base)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Compared 2 immutable field(s) across 1 Service(s)" in result.stdout
