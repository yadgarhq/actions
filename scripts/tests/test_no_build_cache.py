"""What `no_build_cache.py` REFUSES, pinned so it cannot quietly stop refusing.

LEDGER 715. The gate stands between the estate and a one-line speed-up: a
`cache-from`/`cache-to` pair on a `docker/build-push-action` step, which freezes
the `apt-get upgrade -y` layer in `containers/rust-build/Containerfile` at
whatever versions the cache holds. Nothing else reports that — the findings it
lets back in are MEDIUM and UNKNOWN and the scan gate is CRITICAL,HIGH — so a
gate that stopped refusing would be as silent as the defect.

MOST OF THIS FILE IS RED CASES, for the reason `test_helm_pin_agrees.py` gives:
a suite that only feeds a gate conforming input certifies the fixture rather than
the gate. Every refusal the script's docstring claims has a case here that
DEMANDS it.

AND EVERY GREEN CASE IS PAIRED WITH A RED ONE IN THE SAME TREE, which is
`test_d80_portability.py`'s discipline and the half easier to forget.
`cache-binary` on `docker/setup-buildx-action` must NOT redden — it caches the
buildx binary download, not a layer — but a test that only asserts "this tree
passes" also passes when the gate is dead. So it is fed alongside a real
`cache-from`, and the assertion is that the gate refuses and names the build
step ALONE. The anti-grep comment is paired the same way.

THE FAIL-SAFE CASE IS THE ONE THAT AGES. `test_an_unknown_cache_input_is_refused`
feeds a key that no version of the action offers today. A gate written as a
two-name denylist passes it while the cache it names does exactly what
`cache-from` does, so that test is what fails if anybody narrows the rule to the
names buildx happens to use this year.

AND THE EXCEPTION IS PINNED TOO, because it points the other way.
`docker/build-push-action` takes `no-cache` and `no-cache-filters`, which
DISABLE a cache. A gate matching the substring alone refuses them — refusing the
very input that guarantees the apt layer re-executes, under a message saying it
declared a cache. `test_disabling_the_cache_is_not_declaring_one` is what fails
if that comes back.

Run: python3 -m pytest scripts/tests/ -q
"""

import subprocess
import sys
from pathlib import Path

GATE = Path(__file__).resolve().parents[1] / "no_build_cache.py"
REPO = Path(__file__).resolve().parents[2]

BUILD = "docker/build-push-action@53b7df96c91f9c12dcc8a07bcb9ccacbed38856a"
BUILDX = "docker/setup-buildx-action@37fe631027851001ddb9b187196cc803df7f5f0e"


def step(uses=BUILD, comment=None, **inputs):
    """One step; `**inputs` become its `with:` mapping, absent when empty.

    `comment` is emitted ABOVE the `- uses:` line, which is where every real
    comment in this repository's workflows sits.
    """
    lead = f"      # {comment}\n" if comment else ""
    text = f"{lead}      - uses: {uses}\n"
    if inputs:
        text += "        with:\n"
        for key, value in inputs.items():
            text += f"          {key.replace('_', '-')}: {value}\n"
    return text


def workflow(*steps, name="images"):
    body = f"name: {name}\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n"
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


def test_cache_from_is_refused(tmp_path):
    """THE DEFECT ITSELF. The one-line speed-up that freezes the apt layer."""
    root = write(
        tmp_path,
        a__yaml=workflow(step(push="false"), step(cache_from="type=gha", push="true")),
    )
    result = run(root)
    assert result.returncode == 1
    assert "`cache-from`" in result.stdout
    # It says WHAT the cache would break, not merely that a key is disallowed.
    assert "apt-get upgrade -y" in result.stdout


def test_cache_to_is_refused(tmp_path):
    root = write(
        tmp_path,
        a__yaml=workflow(step(push="false"), step(cache_to="type=gha,mode=max")),
    )
    result = run(root)
    assert result.returncode == 1
    assert "`cache-to`" in result.stdout


def test_a_registry_cache_is_refused_too(tmp_path):
    """Not only the GitHub Actions backend — a registry cache freezes it the same."""
    root = write(
        tmp_path,
        a__yaml=workflow(
            step(push="false"),
            step(cache_from="type=registry,ref=ghcr.io/yadgarhq/rust-build:cache"),
        ),
    )
    result = run(root)
    assert result.returncode == 1
    assert "`cache-from`" in result.stdout


def test_an_unknown_cache_input_is_refused(tmp_path):
    """THE FAIL-SAFE CASE, and the one that ages.

    No version of the action offers this input today. A gate written as a
    denylist of the two names buildx uses this year passes it while the input
    does exactly what those two do, so an unknown cache-ish key must fail SAFE.
    """
    root = write(
        tmp_path,
        a__yaml=workflow(step(push="false"), step(layer_cache_backend="type=s3")),
    )
    result = run(root)
    assert result.returncode == 1
    assert "`layer-cache-backend`" in result.stdout


def test_both_cache_inputs_on_one_step_are_named_together(tmp_path):
    root = write(
        tmp_path,
        a__yaml=workflow(
            step(push="false"),
            step(cache_from="type=gha", cache_to="type=gha,mode=max"),
        ),
    )
    result = run(root)
    assert result.returncode == 1
    assert "`cache-from`" in result.stdout and "`cache-to`" in result.stdout


def test_the_refusal_names_the_step_that_carries_the_cache(tmp_path):
    """Two workflows, one offender. The reader must be told which."""
    root = write(
        tmp_path,
        clean__yaml=workflow(step(push="false"), step(push="true")),
        dirty__yaml=workflow(step(cache_from="type=gha")),
    )
    result = run(root)
    assert result.returncode == 1
    assert "dirty.yaml" in result.stdout
    assert "clean.yaml" not in result.stdout


def test_a_single_build_step_is_refused_rather_than_passed(tmp_path):
    """A GATE WITH NOTHING TO INSPECT REPORTS SUCCESS.

    Delete the publishing workflows and a gate with no floor goes green having
    read nothing — the failure this estate has now measured several times.
    """
    root = write(tmp_path, a__yaml=workflow(step(push="false")))
    result = run(root)
    assert result.returncode == 1
    assert "fewest this can inspect" in result.stdout


def test_no_build_steps_at_all_is_refused(tmp_path):
    root = write(tmp_path, a__yaml=workflow(step(uses="actions/checkout@v5")))
    result = run(root)
    assert result.returncode == 1
    assert "0 `docker/build-push-action` step" in result.stdout


def test_a_missing_workflows_directory_is_refused(tmp_path):
    result = run(tmp_path)
    assert result.returncode == 1
    assert "does not exist" in result.stdout


def test_an_unparseable_workflow_is_refused_rather_than_skipped(tmp_path):
    root = write(
        tmp_path,
        a__yaml=workflow(step(push="false"), step(push="true")),
        b__yaml="name: ci\non: push\njobs:\n  a-job:\n   steps: [ unclosed\n",
    )
    result = run(root)
    assert result.returncode == 1
    assert "not a YAML document" in result.stdout


def test_a_yml_workflow_is_read_too(tmp_path):
    """GitHub runs `.yml` as readily as `.yaml`, so a blind spot there is real."""
    root = write(
        tmp_path,
        a__yaml=workflow(step(push="false"), step(push="true")),
        b__yml=workflow(step(cache_from="type=gha")),
    )
    result = run(root)
    assert result.returncode == 1
    assert "b.yml" in result.stdout


# ------------------------------------- it reads STEP INPUTS, never the prose


def test_a_comment_naming_the_cache_is_not_a_declaration(tmp_path):
    """THE ANTI-GREP CASE, paired with a real one in the same tree.

    This gate's own explanation, in `no_build_cache.py` and in the workflows,
    says `cache-from` in prose repeatedly. A gate that grepped the literal
    reddens on the comment that documents the rule.
    """
    root = write(
        tmp_path,
        a__yaml=workflow(
            step(push="false", comment="NO cache-from/cache-to here, see ledger 715"),
            step(push="true", comment="a cache-to would freeze the apt layer"),
        ),
    )
    result = run(root)
    assert result.returncode == 0, result.stdout


def test_the_buildx_binary_cache_is_not_a_layer_cache(tmp_path):
    """DON'T OVER-REACH, and prove the gate is alive while not reaching.

    `docker/setup-buildx-action`'s `cache-binary` caches the buildx binary
    download, not a single image layer. It is fed here beside a genuine
    `cache-from` on a build step: the gate must refuse and name the BUILD step
    only. A tree with the binary cache alone would pass whether the gate works
    or not.
    """
    root = write(
        tmp_path,
        a__yaml=workflow(
            step(uses=BUILDX, cache_binary="true"),
            step(push="false"),
            step(cache_from="type=gha"),
        ),
    )
    result = run(root)
    assert result.returncode == 1
    assert "`cache-from`" in result.stdout
    assert "cache-binary" not in result.stdout


def test_disabling_the_cache_is_not_declaring_one(tmp_path):
    """THE GATE MUST NOT BLOCK ITS OWN REINFORCEMENT, paired with a real cache.

    `no-cache` is the input a maintainer reaches for to GUARANTEE the apt `RUN`
    re-executes — the exact property this gate exists to protect. A rule
    matching the substring `cache` alone refuses it, under a message saying it
    declared a cache, which is how a rule becomes one people work around. It is
    fed here beside a genuine `cache-from`: the gate must refuse and name the
    `cache-from` ALONE. A tree with `no-cache` by itself would pass whether the
    gate works or not.
    """
    root = write(
        tmp_path,
        a__yaml=workflow(
            step(no_cache="true"),
            step(no_cache_filters="apt"),
            step(cache_from="type=gha"),
        ),
    )
    result = run(root)
    assert result.returncode == 1
    assert "`cache-from`" in result.stdout
    assert "no-cache" not in result.stdout


def test_a_tree_that_only_disables_the_cache_is_green(tmp_path):
    """The other half: `no-cache` on its own must not redden anything."""
    root = write(
        tmp_path,
        a__yaml=workflow(step(no_cache="true"), step(no_cache_filters="apt")),
    )
    result = run(root)
    assert result.returncode == 0, result.stdout


def test_ordinary_build_inputs_do_not_redden(tmp_path):
    root = write(
        tmp_path,
        a__yaml=workflow(
            step(push="false", load="true", tags="rust-build:candidate"),
            step(push="true", context="containers/runtime", provenance="true"),
        ),
    )
    result = run(root)
    assert result.returncode == 0, result.stdout


def test_a_step_with_no_with_mapping_does_not_redden(tmp_path):
    root = write(tmp_path, a__yaml=workflow(step(), step()))
    result = run(root)
    assert result.returncode == 0, result.stdout


# -------------------------------------------------------------- and it PASSES


def test_the_finding_says_what_was_inspected(tmp_path):
    """A FINDING, never a bare pass: the count and every site are in it."""
    root = write(
        tmp_path,
        a__yaml=workflow(step(push="false"), step(push="true")),
        b__yaml=workflow(step(push="true")),
    )
    result = run(root)
    assert result.returncode == 0, result.stdout
    assert "3 `docker/build-push-action` steps, none declaring a layer cache" in result.stdout
    assert "a.yaml" in result.stdout and "b.yaml" in result.stdout


def test_this_repository_declares_no_cache_today(tmp_path):
    """LEDGER 715's OWN QUESTION, asked of the real tree rather than a fixture.

    All three image-building workflows are named, so this fails if a build step
    moves somewhere this gate does not read.
    """
    result = run(REPO)
    assert result.returncode == 0, result.stdout
    assert "none declaring a layer cache" in result.stdout
    for workflow_name in (
        "base-images.yaml",
        "estate-runner-image.yaml",
        "ci-release.yaml",
    ):
        assert workflow_name in result.stdout
