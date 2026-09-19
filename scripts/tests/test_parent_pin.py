"""What `parent_pin.py` writes into a parent chart, and what it REFUSES.

MOST OF THIS FILE IS RED CASES, and that is the estate's measured habit rather
than caution. A suite that feeds a rewriter only conforming input certifies the
fixture instead of the rewriter, and every refusal `parent_pin.py`'s docstring
claims has a case here that DEMANDS it.

THE LOAD-BEARING TEST IS `test_the_lexically_greater_downgrade_is_refused`, and
the numbers in it are this estate's own. `gateway` has 74 published chart tags
whose semver-greatest is `0.9.48`; a lexical maximum of the same list answers
`0.9.9`. That is ADR-0690's defect — Dependabot ordering tags as text produced
nineteen DOWNGRADE pull requests titled "bump" across seven repositories, and
not one internal pin advanced for weeks. A pin written from a lexical comparison
is a downgrade nothing downstream reports, so the test asserts the text
comparison out loud (`"0.9.9" > "0.9.48"`) and then demands the refusal. Change
the comparison in the script to string ordering and this test goes red.

THE SECOND ONE THAT EARNS ITS PLACE IS
`test_a_module_breaking_bump_makes_the_parent_minor_not_major`. Every
patch-on-patch case is the same number under the estate's pre-1.0 ladder and
under ordinary semver, so a suite of patch bumps cannot tell a reuse of
`next_version.compute` from a second copy of the arithmetic. A breaking module
bump against a parent at `0.4.7` gives `0.5.0` under the ladder and `1.0.0`
under ordinary semver. That test is what fails the day somebody inlines the
second copy.

FIXTURES ARE PYTHON STRINGS, NOT CHECKED-IN YAML. `prettier` runs on every
committed YAML file in this repository, so a fixture on disk would be reformatted
and the byte-identity assertions would then be asserting on prettier's output
instead of the rewriter's.

Run: python3 -m pytest scripts/tests/ -q
"""

import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import parent_pin  # noqa: E402

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "parent_pin.py"

# THE EIGHT REAL PINS, measured against the live registry on 2026-09-19 and
# recorded in `plans/the-parent-chart-and-dogfooding-it.md`. Six of the eight are
# wrong under a lexical maximum, which is why the fixture is these numbers rather
# than round ones.
PUBLISHED = {
    "config": "0.1.4",
    "gateway": "0.9.48",
    "iam": "0.8.39",
    "iam-db": "0.7.40",
    "project": "0.1.17",
    "project-db": "0.3.5",
    "task": "0.5.28",
    "task-db": "0.6.29",
}

REPOSITORY = "oci://ghcr.io/yadgarhq/charts"

HEAD = "apiVersion: v2\nname: yadgar\nversion: 0.4.7\n\n"


def block(pins=None):
    """`dependencies:` in BLOCK style — what a hand-written Chart.yaml holds."""
    lines = ["dependencies:"]
    for name, version in (pins or PUBLISHED).items():
        lines += [
            f"  - name: {name}",
            f"    version: {version}",
            f"    repository: {REPOSITORY}",
        ]
    return HEAD + "\n".join(lines) + "\n"


def flow(pins=None):
    """One flow mapping per line, which is what prettier leaves a SHORT entry as."""
    lines = ["dependencies:"]
    for name, version in (pins or PUBLISHED).items():
        lines.append(
            f"  - {{ name: {name}, version: {version}, repository: {REPOSITORY} }}"
        )
    return HEAD + "\n".join(lines) + "\n"


def prettier(pins=None):
    """The MULTI-LINE flow mapping prettier produces once an entry is too long.

    MEASURED, not inferred from the plan's rendering. The eight-dependency
    Chart.yaml of `plans/the-parent-chart-and-dogfooding-it.md` was written to a
    file in this repository and run through this repository's own prettier hook
    on 2026-09-19: four entries stayed on one line and four were broken open
    exactly like this. `PRETTIER_MIX` below is that output verbatim, so a real
    parent chart carries BOTH shapes and a rewriter handling only one of them
    handles no real file.
    """
    lines = ["dependencies:"]
    for name, version in (pins or PUBLISHED).items():
        lines += [
            "  - {",
            f"      name: {name},",
            f"      version: {version},",
            f"      repository: {REPOSITORY},",
            "    }",
        ]
    return HEAD + "\n".join(lines) + "\n"


SHAPES = {"block": block, "flow": flow, "prettier": prettier}


# THE MEASURED OUTPUT, verbatim. `pre-commit run prettier` over the plan's own
# eight-dependency Chart.yaml, 2026-09-19. Four entries fit on a line and four
# did not, which is why a fixture of one homogeneous shape is not enough: this is
# what the release path would actually open.
PRETTIER_MIX = """apiVersion: v2
name: yadgar
version: 0.1.0 # the parent's own version; item 3 cuts it
dependencies:
  - { name: config, version: 0.1.5, repository: oci://ghcr.io/yadgarhq/charts }
  - {
      name: gateway,
      version: 0.9.48,
      repository: oci://ghcr.io/yadgarhq/charts,
    }
  - { name: iam, version: 0.8.39, repository: oci://ghcr.io/yadgarhq/charts }
  - { name: iam-db, version: 0.7.40, repository: oci://ghcr.io/yadgarhq/charts }
  - {
      name: project,
      version: 0.1.17,
      repository: oci://ghcr.io/yadgarhq/charts,
    }
  - {
      name: project-db,
      version: 0.3.5,
      repository: oci://ghcr.io/yadgarhq/charts,
    }
  - { name: task, version: 0.5.28, repository: oci://ghcr.io/yadgarhq/charts }
  - {
      name: task-db,
      version: 0.6.29,
      repository: oci://ghcr.io/yadgarhq/charts,
    }
"""


def test_the_two_shapes_prettier_emits_in_one_file_are_both_rewritten():
    """The only fixture in this file that is a real tool's output.

    A multi-line entry sits between two single-line ones here, so a rewriter that
    mis-read where an entry ends would move the wrong pin or none. The parent's
    own `version: 0.1.0` line and its comment are inside the assertion too: this
    writes the DEPENDENCY pin and never the parent's own field, which
    `ci-release.yaml` stamps from the tag (`helm package chart --version
    "$VERSION"`).
    """
    out, old = parent_pin.pin(PRETTIER_MIX, "gateway", "0.9.49")
    assert old == "0.9.48"
    assert out == PRETTIER_MIX.replace("0.9.48", "0.9.49")
    assert parent_pin.pins(out) == {
        "config": "0.1.5",
        "gateway": "0.9.49",
        "iam": "0.8.39",
        "iam-db": "0.7.40",
        "project": "0.1.17",
        "project-db": "0.3.5",
        "task": "0.5.28",
        "task-db": "0.6.29",
    }
    assert "version: 0.1.0 # the parent's own version; item 3 cuts it" in out


def test_a_short_entry_in_that_file_is_rewritten_too():
    out, old = parent_pin.pin(PRETTIER_MIX, "iam", "0.8.40")
    assert old == "0.8.39"
    assert out == PRETTIER_MIX.replace("0.8.39", "0.8.40")


# --------------------------------------------------------------- the rewrite


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_a_forward_patch_bump_is_written(shape):
    out, old = parent_pin.pin(SHAPES[shape](), "gateway", "0.9.49")
    assert old == "0.9.48"
    assert parent_pin.pins(out)["gateway"] == "0.9.49"


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_every_other_dependency_is_byte_identical(shape):
    """ASSERTED ON BYTES, never on a re-parse.

    A YAML round trip would pass this while renormalising quotes, key order and
    flow style across the whole file, and an adopter reading the diff of a
    release would see eight entries move when one version did.
    """
    text = SHAPES[shape]()
    out, _ = parent_pin.pin(text, "gateway", "0.9.49")

    before, after = text.splitlines(keepends=True), out.splitlines(keepends=True)
    assert len(before) == len(after)
    moved = [i for i in range(len(before)) if before[i] != after[i]]
    assert len(moved) == 1, [before[i] for i in moved]
    assert "0.9.48" in before[moved[0]] and "0.9.49" in after[moved[0]]
    # AND THE WHOLE FILE, so a change outside the dependency block counts too.
    assert out == text.replace("0.9.48", "0.9.49")


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_a_wider_version_still_leaves_the_rest_alone(shape):
    """`0.9.48` -> `0.10.0` is two characters shorter, so the line is REBUILT."""
    text = SHAPES[shape]()
    out, _ = parent_pin.pin(text, "gateway", "0.10.0")
    assert parent_pin.pins(out)["gateway"] == "0.10.0"
    assert out == text.replace("0.9.48", "0.10.0")
    for name, version in PUBLISHED.items():
        if name != "gateway":
            assert parent_pin.pins(out)[name] == version


def test_a_quoted_pin_keeps_its_quotes():
    text = HEAD + 'dependencies:\n  - name: gateway\n    version: "0.9.48"\n'
    out, old = parent_pin.pin(text, "gateway", "0.9.49")
    assert old == "0.9.48"
    assert '"0.9.49"' in out


def test_a_trailing_comment_on_the_pin_survives():
    text = HEAD + "dependencies:\n  - name: gateway\n    version: 0.9.48 # pinned\n"
    out, _ = parent_pin.pin(text, "gateway", "0.9.49")
    assert out.endswith("version: 0.9.49 # pinned\n")


# ------------------------------------------------------- ADR-0690, the point


def test_the_lexically_greater_downgrade_is_refused():
    """The measurement that makes this script worth having.

    `0.9.9` is greater than `0.9.48` as TEXT and smaller as a version. A lexical
    comparison accepts this write, the parent then pins a gateway forty releases
    old, and every check downstream stays green because a valid chart version
    was published.
    """
    assert "0.9.9" > "0.9.48"  # text ordering, stated so the red case is readable

    with pytest.raises(parent_pin.Refusal) as raised:
        parent_pin.pin(block(), "gateway", "0.9.9")

    message = str(raised.value)
    assert "0.9.48" in message and "0.9.9" in message


@pytest.mark.parametrize(
    "offered", ["0.9.47", "0.8.39", "0.9.9", "0.1.4"], ids=lambda v: v.replace(".", "_")
)
def test_any_older_version_is_refused(offered):
    with pytest.raises(parent_pin.Refusal):
        parent_pin.pin(block(), "gateway", offered)


def test_the_pinned_version_offered_again_is_refused():
    """EQUAL IS A REFUSAL, decided rather than left to fall through.

    A re-offer means the caller believes a release happened that did not, and the
    parent version this derives would then cut a NEW parent artefact pinning a
    byte-identical set. Refusing says so at the producer. A caller that wants
    idempotence reads the pin first — `pins()` is the same function this uses —
    rather than relying on a silent no-op.
    """
    with pytest.raises(parent_pin.Refusal) as raised:
        parent_pin.pin(block(), "gateway", "0.9.48")
    assert "0.9.48" in str(raised.value)


# ------------------------------------------------ nothing inspected, nothing 0


def test_a_module_absent_from_dependencies_is_refused():
    with pytest.raises(parent_pin.Refusal) as raised:
        parent_pin.pin(block(), "estate", "0.1.0")
    assert "estate" in str(raised.value)


def test_a_missing_dependencies_block_is_refused():
    with pytest.raises(parent_pin.Refusal):
        parent_pin.pin(HEAD, "gateway", "0.9.49")


def test_a_dependencies_key_with_nothing_under_it_is_refused():
    """AND THE REFUSAL MUST BE THE RIGHT ONE.

    Without the empty-block check the module lookup refuses next, one line later,
    reporting that `gateway` is absent from a block it never established exists.
    Asserting only `Refusal` lets that substitution pass, so the message is
    asserted too — a refusal naming the wrong cause sends a reader to the wrong
    file.
    """
    with pytest.raises(parent_pin.Refusal) as raised:
        parent_pin.pin(HEAD + "dependencies:\n", "gateway", "0.9.49")
    assert "no entries" in str(raised.value)


def test_a_dependencies_key_followed_by_another_key_is_refused():
    text = HEAD + "dependencies:\nmaintainers:\n  - name: nobody\n"
    with pytest.raises(parent_pin.Refusal) as raised:
        parent_pin.pin(text, "gateway", "0.9.49")
    assert "no entries" in str(raised.value)


def test_an_empty_flow_sequence_is_refused():
    with pytest.raises(parent_pin.Refusal) as raised:
        parent_pin.pin(HEAD + "dependencies: []\n", "gateway", "0.9.49")
    assert "no module" in str(raised.value)


def test_a_whole_sequence_on_the_key_line_is_refused():
    """A shape this does not rewrite, refused by name rather than half-handled."""
    text = HEAD + "dependencies: [{ name: gateway, version: 0.9.48 }]\n"
    with pytest.raises(parent_pin.Refusal) as raised:
        parent_pin.pin(text, "gateway", "0.9.49")
    assert "key line" in str(raised.value)


def test_two_dependencies_keys_are_refused():
    text = block() + "dependencies:\n  - name: gateway\n    version: 0.9.48\n"
    with pytest.raises(parent_pin.Refusal):
        parent_pin.pin(text, "gateway", "0.9.49")


def test_the_same_module_twice_is_refused():
    text = block() + "  - name: gateway\n    version: 0.9.48\n"
    with pytest.raises(parent_pin.Refusal) as raised:
        parent_pin.pin(text, "gateway", "0.9.49")
    assert "twice" in str(raised.value) or "two" in str(raised.value)


def test_an_entry_with_no_name_is_refused():
    """A nested sequence inside an entry is the real case this catches.

    `tags:` under a dependency puts a `-` line inside the entry, and a rewriter
    that read it as a new entry would be reading a shape it does not understand.
    Refusing names the file; guessing writes a pin somewhere nobody looked.
    """
    text = HEAD + (
        "dependencies:\n"
        "  - name: gateway\n"
        "    version: 0.9.48\n"
        "    tags:\n"
        "      - fast\n"
    )
    with pytest.raises(parent_pin.Refusal) as raised:
        parent_pin.pin(text, "gateway", "0.9.49")
    # THE `name:` COUNT IS THE CAUSE, and the version-count check one line below
    # would otherwise refuse this too — with a message pointing at the wrong key.
    assert "name:" in str(raised.value)


def test_an_entry_with_two_version_keys_is_refused():
    text = HEAD + "dependencies:\n  - name: gateway\n    version: 0.9.48\n    version: 0.9.47\n"
    with pytest.raises(parent_pin.Refusal):
        parent_pin.pin(text, "gateway", "0.9.49")


def test_an_entry_with_no_version_key_is_refused():
    text = HEAD + f"dependencies:\n  - name: gateway\n    repository: {REPOSITORY}\n"
    with pytest.raises(parent_pin.Refusal):
        parent_pin.pin(text, "gateway", "0.9.49")


# ------------------------------------------------- what a version may look like


@pytest.mark.parametrize("pinned", ["^0.9.0", "0.9.x", "v0.9.48", "0.9.48-rc1", "latest"])
def test_a_pin_that_is_not_a_plain_version_is_refused(pinned):
    """A range or a prerelease cannot be ORDERED, so it is refused, not guessed.

    `yadgarhq/yadgar` carries `v0.1.0a6`, so an unorderable version string is a
    real shape in this estate rather than a hypothetical one.
    """
    text = HEAD + f"dependencies:\n  - name: gateway\n    version: {pinned}\n"
    with pytest.raises(parent_pin.Refusal):
        parent_pin.pin(text, "gateway", "0.9.49")


@pytest.mark.parametrize("offered", ["v0.9.49", "0.9.49-rc1", "0.9", "", "latest"])
def test_an_offer_that_is_not_a_plain_version_is_refused(offered):
    """A `v` PREFIX IS REFUSED RATHER THAN STRIPPED.

    The value at the real call site is a git tag (`v0.9.49`), and a Helm chart
    version must not carry the `v`. Stripping it silently would make the caller's
    mistake invisible and publish a parent whose pin never resolves; refusing
    makes it loud at the producer.
    """
    with pytest.raises(parent_pin.Refusal):
        parent_pin.pin(block(), "gateway", offered)


# ------------------------------------------- the parent's own next version


def test_a_module_patch_makes_the_parent_patch():
    nxt, _, last = parent_pin.parent_version(["v0.4.7"], "0.9.48", "0.9.49")
    assert (nxt, last) == ("0.4.8", "v0.4.7")


def test_a_module_breaking_bump_makes_the_parent_minor_not_major():
    """THE TEST THAT TELLS A REUSED LADDER FROM A COPIED ONE.

    `next_version.compute` carries the estate's pre-1.0 branch: under `0.x` a
    breaking change bumps MINOR. Ordinary semver would answer `1.0.0` here. A
    module going `0.9.48` -> `0.10.0` is a breaking change under that same
    ladder, so the parent at `0.4.7` moves to `0.5.0`.
    """
    nxt, _, _ = parent_pin.parent_version(["v0.4.7"], "0.9.48", "0.10.0")
    assert nxt == "0.5.0"


def test_a_parent_past_one_takes_ordinary_semver():
    """Read off `compute` as well, so the ladder has one home even above 1.0."""
    assert parent_pin.parent_version(["v1.4.7"], "0.9.48", "0.10.0")[0] == "2.0.0"
    assert parent_pin.parent_version(["v1.4.7"], "0.9.48", "0.9.49")[0] == "1.4.8"


def test_the_parent_baseline_is_the_semver_greatest_tag():
    """The same ordering defect, one field over.

    A lexical maximum of these three tags is `v0.4.9`, which would derive
    `0.4.10` — a version the parent already published. `repin.greatest` orders by
    parsed integers and is reused rather than re-written.
    """
    tags = ["v0.4.7", "v0.4.10", "v0.4.9"]
    assert max(tags) == "v0.4.9"  # text ordering, stated so the case is readable
    nxt, _, last = parent_pin.parent_version(tags, "0.9.48", "0.9.49")
    assert (nxt, last) == ("0.4.11", "v0.4.10")


def test_an_unorderable_tag_is_not_a_baseline():
    nxt, _, last = parent_pin.parent_version(["v0.4.7", "v0.1.0a6"], "0.9.48", "0.9.49")
    assert (nxt, last) == ("0.4.8", "v0.4.7")


def test_a_bullet_the_changelog_regex_stops_parsing_is_refused(monkeypatch):
    """The ladder is consulted through a SYNTHESISED bullet, so its parse is a
    dependency like any other.

    `pr_body.BULLET` is this repository's own and it moves — its type list has
    grown. If it ever stops matching the bullets here, `bump_for` reads
    `None.group` and a release path dies with an AttributeError, which reads like
    an outage rather than a version rule that needs updating. This asserts the
    refusal instead of the crash.
    """
    monkeypatch.setattr(parent_pin, "BULLET", __import__("re").compile("^never$"))
    with pytest.raises(parent_pin.Refusal) as raised:
        parent_pin.parent_version(["v0.4.7"], "0.9.48", "0.9.49")
    assert "BULLET" in str(raised.value)


def test_a_parent_with_no_tag_is_refused():
    """The first tag of a repository is cut by hand — `next_version.py`'s rule.

    There is no baseline to derive from, so this refuses instead of inventing
    `0.1.0`, which would be a second version rule in a script whose whole point
    is that there is only one.
    """
    with pytest.raises(parent_pin.Refusal):
        parent_pin.parent_version([], "0.9.48", "0.9.49")
    with pytest.raises(parent_pin.Refusal):
        parent_pin.parent_version(["v0.1.0a6"], "0.9.48", "0.9.49")


@pytest.mark.parametrize(
    "old,new,expected",
    [
        ("0.9.48", "0.9.49", "0.4.8"),  # one patch release
        ("0.9.48", "0.9.52", "0.4.8"),  # four missed patch releases: still a patch
        ("0.9.48", "0.11.3", "0.5.0"),  # a minor moved somewhere in the gap
        ("0.9.48", "1.0.0", "0.5.0"),  # the module reached 1.0
    ],
    ids=["one", "gap", "gap_minor", "major"],
)
def test_a_gap_is_classified_by_the_widest_field_that_moved(old, new, expected):
    """Several module releases since the parent last moved is a real case.

    ADR-0722 has every module release cut a parent, but a parent bump that failed
    or was never wired leaves the pin several releases behind. The ladder is
    probed first for an exact single-step match; a gap falls back to the widest
    field that moved, which cannot understate the movement.
    """
    assert parent_pin.parent_version(["v0.4.7"], old, new)[0] == expected


# ----------------------------------------------------------------- the script


def run(tmp_path, text, *args, outputs=None):
    chart = tmp_path / "Chart.yaml"
    chart.write_text(text, encoding="utf-8")
    env = {"PATH": "/usr/bin:/bin"}
    if outputs is not None:
        env["GITHUB_OUTPUT"] = str(outputs)
    done = subprocess.run(
        [sys.executable, str(SCRIPT), str(chart), *args],
        capture_output=True,
        text=True,
        env=env,
    )
    return done, chart


def test_the_script_rewrites_the_file_and_reports_the_parent_version(tmp_path):
    outputs = tmp_path / "outputs.txt"
    done, chart = run(
        tmp_path, block(), "gateway", "0.9.49", "v0.4.7", outputs=outputs
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert parent_pin.pins(chart.read_text(encoding="utf-8"))["gateway"] == "0.9.49"
    written = outputs.read_text(encoding="utf-8")
    assert "pin=0.9.49" in written
    assert "parent=0.4.8" in written


def test_the_script_refuses_a_downgrade_and_leaves_the_file_untouched(tmp_path):
    """NO PARTIAL WRITE. Every refusal happens before the file is opened for
    writing, so a refused run cannot leave a parent chart half-re-pinned."""
    text = block()
    done, chart = run(tmp_path, text, "gateway", "0.9.9", "v0.4.7")
    assert done.returncode == 1
    assert "::error::" in done.stdout + done.stderr
    assert chart.read_text(encoding="utf-8") == text


def test_a_refused_parent_version_writes_no_pin_either(tmp_path):
    """BOTH DERIVATIONS BEFORE EITHER WRITE, pinned as an ordering.

    `v0.1.0a6` is not orderable, so the parent has no baseline and this run cuts
    no version. A script that wrote the pin first would leave the chart re-pinned
    for a release that then tags nothing — a parent whose `Chart.yaml` has moved
    and whose published artefacts have not.
    """
    text = block()
    done, chart = run(tmp_path, text, "gateway", "0.9.49", "v0.1.0a6")
    assert done.returncode == 1
    assert chart.read_text(encoding="utf-8") == text


def test_the_script_refuses_an_absent_module(tmp_path):
    done, _ = run(tmp_path, block(), "estate", "0.1.0", "v0.4.7")
    assert done.returncode == 1
    assert "estate" in done.stdout + done.stderr


def test_the_script_refuses_an_empty_dependency_block(tmp_path):
    done, _ = run(tmp_path, HEAD + "dependencies: []\n", "gateway", "0.9.49", "v0.4.7")
    assert done.returncode == 1


def test_the_script_refuses_the_wrong_arguments(tmp_path):
    done, _ = run(tmp_path, block(), "gateway")
    assert done.returncode == 2
    assert "usage" in done.stdout + done.stderr


def test_a_missing_chart_file_is_refused(tmp_path):
    done = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(tmp_path / "nothing" / "Chart.yaml"),
            "gateway",
            "0.9.49",
            "v0.4.7",
        ],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert done.returncode == 1
    assert "Chart.yaml" in done.stdout + done.stderr
