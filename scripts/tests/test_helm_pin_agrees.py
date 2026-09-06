"""What `helm_pin_agrees.py` asserts, pinned so it cannot quietly stop asserting it.

MOST OF THIS FILE IS RED CASES, and that is deliberate rather than defensive. A
suite that only feeds a gate conforming input passes whether the gate works or
not — it certifies the fixture instead of the gate, which is the antipattern this
estate has now measured six times. So every refusal the script's own docstring
claims has a case here that DEMANDS it, and the suite goes red the day the script
stops refusing.

THE ANTI-GREP CASE IS THE LOAD-BEARING ONE. `v3.18.4` appears about seven times
in PROSE in this repository's workflows against three times as a step input, so a
gate that grepped the literal would agree with the comments and miss the steps.
`test_a_comment_naming_another_version_is_not_a_site` is the test that fails if
anybody rewrites this as a grep.

Run: python3 -m pytest scripts/tests/ -q
"""

import subprocess
import sys
from pathlib import Path

GATE = Path(__file__).resolve().parents[1] / "helm_pin_agrees.py"
REPO = Path(__file__).resolve().parents[2]

SHA = "azure/setup-helm@9bc31f4ebc9c6b171d7bfbaa5d006ae7abdb4310"


def step(version="v3.18.4", uses=SHA, comment=None):
    """One `azure/setup-helm` step; `version=None` omits the input entirely.

    `comment` is emitted ABOVE the `- uses:` line, which is where all seven of
    this repository's real `v3.18.4` comments sit (`ci-pr.yaml` 185-195).
    """
    lead = f"      # {comment}\n" if comment else ""
    if version is None:
        return f"{lead}      - uses: {uses}\n"
    return f"{lead}      - uses: {uses}\n        with:\n          version: {version}\n"


def workflow(*steps, name="ci"):
    body = f"name: {name}\non: push\njobs:\n  a-job:\n    runs-on: ubuntu-latest\n    steps:\n"
    return body + "".join(steps)


def write(tmp_path: Path, **files):
    """Lay out a `.github/workflows/` tree and return the root to run in."""
    directory = tmp_path / ".github" / "workflows"
    directory.mkdir(parents=True, exist_ok=True)
    for filename, text in files.items():
        (directory / filename.replace("__", ".")).write_text(text)
    return tmp_path


def run(cwd: Path):
    return subprocess.run(
        [sys.executable, str(GATE)],
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


# ------------------------------------------------------- the gate must REFUSE


def test_two_sites_that_disagree_are_refused(tmp_path):
    """THE DEFECT ITSELF. Ledger 649: nothing compared the copies of the pin."""
    root = write(
        tmp_path,
        a__yaml=workflow(step("v3.18.4")),
        b__yaml=workflow(step("v4.2.4")),
    )
    result = run(root)
    assert result.returncode == 1
    assert "do not agree" in result.stdout
    # It names BOTH versions, so the reader can see which site to move.
    assert "v3.18.4" in result.stdout and "v4.2.4" in result.stdout


def test_the_disagreement_message_names_every_site(tmp_path):
    root = write(
        tmp_path,
        a__yaml=workflow(step("v3.18.4")),
        b__yaml=workflow(step("v4.2.4")),
    )
    result = run(root)
    assert result.returncode == 1
    assert "a.yaml" in result.stdout and "b.yaml" in result.stdout


def test_a_single_site_is_refused_rather_than_passed(tmp_path):
    """A COMPARISON OF ONE IS NOT A COMPARISON.

    This is the case a gate is most likely to fail silently on: delete two of the
    three steps and a gate with no floor reports success having compared nothing.
    """
    root = write(tmp_path, a__yaml=workflow(step("v3.18.4")))
    result = run(root)
    assert result.returncode == 1
    assert "fewest this can compare" in result.stdout


def test_no_sites_at_all_is_refused(tmp_path):
    root = write(tmp_path, a__yaml=workflow(step(uses="actions/checkout@v5")))
    result = run(root)
    assert result.returncode == 1
    assert "0 `azure/setup-helm` step" in result.stdout


def test_a_step_with_no_version_input_is_refused(tmp_path):
    """The ORIGINAL defect: with no input the action asks the network."""
    root = write(
        tmp_path,
        a__yaml=workflow(step("v3.18.4")),
        b__yaml=workflow(step(None)),
    )
    result = run(root)
    assert result.returncode == 1
    assert "no `version:` input" in result.stdout
    assert "b.yaml" in result.stdout


def test_a_missing_workflows_directory_is_refused(tmp_path):
    result = run(tmp_path)
    assert result.returncode == 1
    assert "does not exist" in result.stdout


def test_an_unparseable_workflow_is_refused_rather_than_skipped(tmp_path):
    root = write(
        tmp_path,
        a__yaml=workflow(step("v3.18.4")),
        b__yaml="name: ci\non: push\njobs:\n  a-job:\n   steps: [ unclosed\n",
    )
    result = run(root)
    assert result.returncode == 1
    assert "not a YAML document" in result.stdout


# --------------------------------------------- it reads STEPS, never the prose


def test_a_comment_naming_another_version_is_not_a_site(tmp_path):
    """THE ANTI-GREP CASE.

    Both steps are pinned to the same version and a comment mentions a different
    one. A gate that grepped the literal reddens here; one that parses the steps
    does not. This repository's own workflows carry about seven such comments.
    """
    root = write(
        tmp_path,
        a__yaml=workflow(step("v3.18.4", comment="this used to install v4.2.4")),
        b__yaml=workflow(step("v3.18.4")),
    )
    result = run(root)
    assert result.returncode == 0, result.stdout


def test_another_action_pinned_to_another_version_is_ignored(tmp_path):
    root = write(
        tmp_path,
        a__yaml=workflow(step("v3.18.4"), step("v9.9.9", uses="actions/setup-go@v6")),
        b__yaml=workflow(step("v3.18.4")),
    )
    result = run(root)
    assert result.returncode == 0, result.stdout


def test_a_yml_workflow_is_read_too(tmp_path):
    """GitHub runs `.yml` as readily as `.yaml`, so a blind spot there is real."""
    root = write(
        tmp_path,
        a__yaml=workflow(step("v3.18.4")),
        b__yml=workflow(step("v4.2.4")),
    )
    result = run(root)
    assert result.returncode == 1
    assert "do not agree" in result.stdout


# -------------------------------------------------------------- and it PASSES


def test_agreeing_sites_pass_and_the_finding_says_what_was_compared(tmp_path):
    root = write(
        tmp_path,
        a__yaml=workflow(step("v3.18.4")),
        b__yaml=workflow(step("v3.18.4")),
        c__yaml=workflow(step("v3.18.4")),
    )
    result = run(root)
    assert result.returncode == 0, result.stdout
    # A FINDING, never a bare pass: the count and the version are both in it.
    assert "3 `azure/setup-helm` steps, all pinned to v3.18.4" in result.stdout


def test_this_repository_agrees_today(tmp_path):
    """LEDGER 649's OWN QUESTION, asked of the real tree rather than a fixture."""
    result = run(REPO)
    assert result.returncode == 0, result.stdout
    assert "all pinned to v3.18.4" in result.stdout
