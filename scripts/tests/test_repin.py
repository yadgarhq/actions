"""What `repin.py` asserts, pinned so it cannot quietly stop asserting it.

THE ORDERING TEST IS THE WHOLE REASON THIS FILE EXISTS, and it is written to go
RED under the mistake it exists to catch rather than green under both orderings.
ADR-0690 attributes nineteen closed downgrade pull requests to Dependabot
ordering git tags LEXICALLY: `"v0.2.9" > "v0.2.10"` on a character comparison,
because `9` sorts above `1` at the fifth character. So every fixture here that
touches ordering carries both tags, and
`test_a_lexical_sort_would_pick_the_wrong_one` asserts that the two orderings
DISAGREE on it. A fixture the two agree on proves nothing about which one ran.

THE BODY IS CHECKED BY RUNNING `pr_body.review`, NOT BY RESTATING ITS RULES.
`pr_body.py`'s own docstring is headed "ONE REGEX IS NOT ONE VERDICT"; a second
copy of `BULLET` in this file would be the third reader of a contract that
exists to have one. So the generated body is handed to the real validator in
BOTH of its modes — unwrapped, as `ci / template` reads a pull request body, and
wrapped, as the `version` job reads the squash commit message — and both must
report no problems and the same bump.

Run: python3 -m pytest scripts/tests/ -q
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pr_body  # noqa: E402
import repin  # noqa: E402

GATE = Path(__file__).resolve().parents[1] / "repin.py"

# THE TRAP, AS MEASURED. `store` really did run past v0.2.9 to v0.2.13 while
# every consumer sat at the last tag where lexical and semver order agreed.
TRAP_TAGS = ["v0.1.0", "v0.2.0", "v0.2.9", "v0.2.10", "v0.2.13"]


# --------------------------------------------------------------------------
# ordering
# --------------------------------------------------------------------------


def test_greatest_is_the_semver_greatest_not_the_lexical_one():
    assert repin.greatest(TRAP_TAGS) == "v0.2.13"


def test_a_lexical_sort_would_pick_the_wrong_one():
    """The fixture DISCRIMINATES. Without this the test above proves nothing."""
    assert sorted(TRAP_TAGS)[-1] == "v0.2.9"
    assert repin.greatest(TRAP_TAGS) != sorted(TRAP_TAGS)[-1]


def test_order_is_by_parsed_integers_at_every_position():
    assert repin.greatest(["v0.9.0", "v0.10.0"]) == "v0.10.0"
    assert repin.greatest(["v9.0.0", "v10.0.0"]) == "v10.0.0"
    assert repin.greatest(["v1.2.9", "v1.2.10"]) == "v1.2.10"


def test_input_order_does_not_decide():
    assert repin.greatest(list(reversed(TRAP_TAGS))) == "v0.2.13"


def test_a_tag_that_is_not_plain_semver_is_ignored_rather_than_crashing():
    """`yadgarhq/yadgar` carries `v0.1.0a6`; it is the estate's named precedent."""
    tags = ["v0.1.0", "v0.1.0a6", "v1.0.0-rc1", "latest", "v0.2.0", ""]
    assert repin.greatest(tags) == "v0.2.0"


def test_no_orderable_tag_at_all_yields_nothing():
    assert repin.greatest(["latest", "v0.1.0a6"]) is None
    assert repin.greatest([]) is None


# --------------------------------------------------------------------------
# reading the manifest
# --------------------------------------------------------------------------

MANIFEST = """\
[package]
name = "yadgar-iam-db"
version = "0.1.0"

[dependencies]
# Pinned by RELEASE TAG, not by branch.
yadgar-store = { git = "https://github.com/yadgarhq/store.git", tag = "v0.2.9" }
tonic = { version = "0.14", default-features = false, features = [
  "codegen",
  "tls-ring",
] }
metrics = "0.24"
yadgar-telemetry = { git = "https://github.com/yadgarhq/telemetry.git", tag = "v0.1.10", features = ["grpc"] }
somebody-else = { git = "https://github.com/tokio-rs/tokio.git", tag = "v1.0.0" }

[dev-dependencies]
yadgar-dial = { git = "https://github.com/yadgarhq/dial.git", tag = "v0.2.6" }

[build-dependencies]
tonic-prost-build = "0.14"

[target.'cfg(unix)'.dependencies]
yadgar-lifecycle = { git = "https://github.com/yadgarhq/lifecycle.git", tag = "v0.2.10" }
"""


def deps_by_name(text=MANIFEST):
    return {d.name: d for d in repin.git_deps(text)}


def test_every_in_org_git_dependency_is_found_whatever_table_it_is_in():
    found = deps_by_name()
    assert set(found) == {
        "yadgar-store",
        "yadgar-telemetry",
        "yadgar-dial",
        "yadgar-lifecycle",
    }


def test_the_producer_repository_comes_from_the_url_not_from_the_crate_name():
    found = deps_by_name()
    assert found["yadgar-store"].producer == "store"
    assert found["yadgar-lifecycle"].producer == "lifecycle"
    assert found["yadgar-telemetry"].tag == "v0.1.10"


def test_a_registry_dependency_and_a_foreign_git_one_are_not_ours():
    found = deps_by_name()
    assert "tonic" not in found
    assert "metrics" not in found
    assert "somebody-else" not in found


def test_nothing_about_the_crate_list_is_hardcoded():
    """A repository that gains a crate needs no edit here."""
    text = MANIFEST.replace(
        'metrics = "0.24"',
        'metrics = "0.24"\n'
        'yadgar-brandnew = { git = "https://github.com/yadgarhq/brandnew.git",'
        ' tag = "v0.3.1" }',
    )
    found = deps_by_name(text)
    assert found["yadgar-brandnew"].producer == "brandnew"


def test_a_revision_pin_is_refused_rather_than_skipped():
    """ADR-0526: an in-org crate is pinned by a published tag, never a revision."""
    text = MANIFEST.replace('tag = "v0.2.9"', 'rev = "d6abe3e26c1cff4b4df9956a30f2"')
    try:
        repin.git_deps(text)
    except repin.Refusal as refusal:
        assert "yadgar-store" in str(refusal)
        assert "ADR-0526" in str(refusal)
    else:
        raise AssertionError("a revision pin must refuse, not be skipped")


def test_a_branch_pin_is_refused_rather_than_skipped():
    text = MANIFEST.replace('tag = "v0.2.9"', 'branch = "main"')
    try:
        repin.git_deps(text)
    except repin.Refusal as refusal:
        assert "yadgar-store" in str(refusal)
    else:
        raise AssertionError("a branch pin must refuse, not be skipped")


def test_a_bare_git_dependency_with_no_pin_at_all_is_refused():
    text = MANIFEST.replace(', tag = "v0.2.9"', "")
    try:
        repin.git_deps(text)
    except repin.Refusal as refusal:
        assert "yadgar-store" in str(refusal)
    else:
        raise AssertionError("an unpinned in-org git dependency must refuse")


def test_a_pin_this_cannot_order_is_refused_rather_than_read_as_current():
    text = MANIFEST.replace('tag = "v0.2.9"', 'tag = "v0.2.9a1"')
    try:
        repin.git_deps(text)
    except repin.Refusal as refusal:
        assert "v0.2.9a1" in str(refusal)
    else:
        raise AssertionError("an unorderable pin must refuse")


def test_an_in_org_url_this_cannot_parse_is_refused_rather_than_ignored():
    text = MANIFEST.replace(
        "https://github.com/yadgarhq/store.git",
        "https://example.invalid/yadgarhq/store/tree/main",
    )
    try:
        repin.git_deps(text)
    except repin.Refusal as refusal:
        assert "yadgarhq" in str(refusal)
    else:
        raise AssertionError("an unparseable in-org url must refuse")


# --------------------------------------------------------------------------
# planning
# --------------------------------------------------------------------------

TAGS = {
    "store": TRAP_TAGS,
    "telemetry": ["v0.1.9", "v0.1.10", "v0.1.17"],
    "dial": ["v0.2.6"],
    "lifecycle": ["v0.2.9", "v0.2.10", "v0.2.17"],
}


def test_only_the_stale_pins_are_proposed():
    bumps = repin.plan(repin.git_deps(MANIFEST), TAGS)
    assert {(b.dep.name, b.dep.tag, b.tag) for b in bumps} == {
        ("yadgar-store", "v0.2.9", "v0.2.13"),
        ("yadgar-telemetry", "v0.1.10", "v0.1.17"),
        ("yadgar-lifecycle", "v0.2.10", "v0.2.17"),
    }


def test_a_pin_already_at_the_semver_greatest_tag_is_left_alone():
    bumps = repin.plan(repin.git_deps(MANIFEST), TAGS)
    assert "yadgar-dial" not in {b.dep.name for b in bumps}


def test_nothing_stale_plans_nothing():
    current = {
        "store": ["v0.2.9"],
        "telemetry": ["v0.1.10"],
        "dial": ["v0.2.6"],
        "lifecycle": ["v0.2.10"],
    }
    assert repin.plan(repin.git_deps(MANIFEST), current) == []


def test_a_producer_with_no_orderable_tag_is_refused_not_treated_as_current():
    tags = dict(TAGS, store=["latest"])
    try:
        repin.plan(repin.git_deps(MANIFEST), tags)
    except repin.Refusal as refusal:
        assert "store" in str(refusal)
    else:
        raise AssertionError("a producer this cannot order must refuse")


def test_a_pin_ahead_of_every_published_tag_is_refused():
    """A pin nobody published is a defect, and it must not read as up to date."""
    tags = dict(TAGS, store=["v0.1.0", "v0.2.0"])
    try:
        repin.plan(repin.git_deps(MANIFEST), tags)
    except repin.Refusal as refusal:
        assert "v0.2.9" in str(refusal)
    else:
        raise AssertionError("a pin ahead of every tag must refuse")


# --------------------------------------------------------------------------
# rewriting
# --------------------------------------------------------------------------


def test_the_rewrite_moves_the_tag_and_nothing_else():
    bumps = repin.plan(repin.git_deps(MANIFEST), TAGS)
    after = repin.rewrite(MANIFEST, bumps)
    before_lines = MANIFEST.splitlines()
    after_lines = after.splitlines()
    assert len(before_lines) == len(after_lines)
    changed = [
        (b, a) for b, a in zip(before_lines, after_lines) if b != a
    ]
    assert len(changed) == 3
    assert 'tag = "v0.2.13"' in after
    assert 'tag = "v0.1.17"' in after
    assert 'tag = "v0.2.17"' in after
    assert 'tag = "v0.2.9"' not in after


def test_the_rewrite_keeps_every_comment():
    bumps = repin.plan(repin.git_deps(MANIFEST), TAGS)
    after = repin.rewrite(MANIFEST, bumps)
    assert "# Pinned by RELEASE TAG, not by branch." in after
    assert after.count("#") == MANIFEST.count("#")


def test_the_rewrite_leaves_an_untouched_dependency_byte_identical():
    bumps = repin.plan(repin.git_deps(MANIFEST), TAGS)
    after = repin.rewrite(MANIFEST, bumps)
    assert 'yadgar-dial = { git = "https://github.com/yadgarhq/dial.git", tag = "v0.2.6" }' in after
    assert 'somebody-else = { git = "https://github.com/tokio-rs/tokio.git", tag = "v1.0.0" }' in after


def test_odd_spacing_around_the_pin_is_preserved():
    text = MANIFEST.replace('tag = "v0.2.9"', 'tag="v0.2.9"')
    bumps = repin.plan(repin.git_deps(text), TAGS)
    after = repin.rewrite(text, bumps)
    assert 'tag="v0.2.13"' in after


UNREACHABLE = [
    # A DOTTED TABLE. `tomllib` reads the pin; no line carries the declaration
    # AND the tag.
    """\
[dependencies.yadgar-store]
git = "https://github.com/yadgarhq/store.git"
tag = "v0.2.9"
""",
    # AN INLINE TABLE WHOSE ARRAY VALUE SPANS LINES, which TOML allows and which
    # `tonic` in `gateway`'s real manifest already does — here it pushes `tag`
    # off the declaration's line.
    """\
[dependencies]
yadgar-store = { git = "https://github.com/yadgarhq/store.git", features = [
  "pool",
], tag = "v0.2.9" }
""",
]


def test_a_pin_the_rewrite_cannot_reach_is_refused_rather_than_skipped():
    """Both shapes are valid TOML and both are unreachable line by line."""
    for text in UNREACHABLE:
        bumps = repin.plan(repin.git_deps(text), TAGS)
        assert bumps, text
        try:
            repin.rewrite(text, bumps)
        except repin.Refusal as refusal:
            assert "yadgar-store" in str(refusal)
        else:
            raise AssertionError(f"an unreachable pin must refuse: {text}")


def test_the_same_crate_in_two_tables_has_both_lines_moved():
    """Two declarations at one tag are ONE bump, and both lines move."""
    text = MANIFEST.replace(
        'yadgar-dial = { git = "https://github.com/yadgarhq/dial.git", tag = "v0.2.6" }',
        'yadgar-store = { git = "https://github.com/yadgarhq/store.git", tag = "v0.2.9" }',
    )
    bumps = repin.plan(repin.git_deps(text), TAGS)
    after = repin.rewrite(text, bumps)
    assert 'tag = "v0.2.9"' not in after
    assert after.count('tag = "v0.2.13"') == 2


# --------------------------------------------------------------------------
# the branch name
# --------------------------------------------------------------------------


def test_the_branch_name_is_deterministic_from_the_bumps():
    bumps = repin.plan(repin.git_deps(MANIFEST), TAGS)
    assert repin.branch(bumps) == repin.branch(list(reversed(bumps)))


def test_the_branch_name_names_every_bump():
    bumps = repin.plan(repin.git_deps(MANIFEST), TAGS)
    name = repin.branch(bumps)
    assert name.startswith("repin/")
    for fragment in ("store-v0.2.13", "telemetry-v0.1.17", "lifecycle-v0.2.17"):
        assert fragment in name


def test_a_different_bump_is_a_different_branch():
    one = repin.plan(repin.git_deps(MANIFEST), TAGS)
    two = repin.plan(repin.git_deps(MANIFEST), dict(TAGS, store=["v0.2.14"]))
    assert repin.branch(one) != repin.branch(two)


def test_the_branch_name_is_a_usable_git_ref():
    bumps = repin.plan(repin.git_deps(MANIFEST), TAGS)
    name = repin.branch(bumps)
    assert len(name) <= 200
    assert not any(c in name for c in " ~^:?*[\\")
    assert ".." not in name and not name.endswith(".lock")


# --------------------------------------------------------------------------
# the pull request body, checked by the real validator
# --------------------------------------------------------------------------

TEMPLATE = """\
<!--
  INSTRUCTIONS — everything in a comment is stripped before the emptiness test.
-->

## What

<!-- What changed. -->

## Why

<!-- The reason, not the mechanism. -->

## Changelog

<!-- Conventional Commits bullets. -->

## Verification

<!-- How you know it works. -->

## Risk

<!-- What breaks if this is wrong, and the D80 question. -->
"""


def generated_body(template=TEMPLATE, tags=TAGS):
    bumps = repin.plan(repin.git_deps(MANIFEST), tags)
    return repin.body(template, bumps, "yadgarhq/iam-db")


def wrap(text, width=pr_body.WRAP):
    """GitHub's greedy 72-column wrap, applied line by line as it applies it."""
    out = []
    for line in text.splitlines():
        if len(line) <= width:
            out.append(line)
            continue
        current = ""
        for word in line.split():
            candidate = f"{current} {word}".strip()
            if current and len(candidate) > width:
                out.append(current)
                current = word
            else:
                current = candidate
        out.append(current)
    return "\n".join(out)


def test_the_body_passes_the_template_gate_as_a_pull_request_body():
    problems, bump, count = pr_body.review(generated_body(), wrapped=False)
    assert problems == []
    assert count == 3


def test_the_body_passes_the_same_gate_as_a_wrapped_squash_message():
    body = generated_body()
    strict, bump, count = pr_body.review(body, wrapped=False)
    problems, wrapped_bump, wrapped_count = pr_body.review(
        wrap(body), wrapped=True
    )
    assert problems == []
    assert wrapped_bump == bump
    assert wrapped_count == count


def test_a_chore_deps_changelog_implies_a_patch_bump():
    """`ci-pr.yaml`'s version job shifts the 0.x ladder down; patch stays patch."""
    _, bump, _ = pr_body.review(generated_body(), wrapped=False)
    assert bump == "patch"


def test_every_changelog_line_is_one_unwrapped_bullet():
    body = generated_body()
    lines = pr_body.sections(body)["Changelog"]
    entries = [l for l in lines if l.strip()]
    assert len(entries) == 3
    for line in entries:
        assert pr_body.BULLET.match(line), line
        assert len(line) <= pr_body.WRAP, line


def test_no_line_outside_the_changelog_parses_as_a_changelog_entry():
    """`commit_entries` reads EVERY line of a commit, section or not."""
    body = generated_body()
    changelog = set(pr_body.sections(body)["Changelog"])
    for line in body.splitlines():
        if line in changelog:
            continue
        assert not pr_body.BULLET.match(line), line


def test_the_derivation_reads_exactly_the_three_entries_back():
    entries, matches, lenient = pr_body.commit_entries(
        wrap(generated_body()).splitlines()
    )
    assert len(matches) == 3
    assert pr_body.bump_for(matches) == pr_body.bump_for(lenient) == "patch"
    for entry in entries:
        assert entry.startswith("- chore(deps): bump yadgar-")


def test_the_body_carries_every_heading_of_the_target_template_in_order():
    body = generated_body()
    assert [h for _, h in repin.headings(body)] == [
        h for _, h in repin.headings(TEMPLATE)
    ]


def test_a_heading_the_template_grew_is_carried_and_filled():
    """The templates have drifted across generations; nothing here assumes five."""
    drifted = TEMPLATE + "\n## Rollout\n\n<!-- Who turns it on. -->\n"
    body = repin.body(
        drifted, repin.plan(repin.git_deps(MANIFEST), TAGS), "yadgarhq/iam-db"
    )
    found = pr_body.sections(body)
    assert "Rollout" in found
    assert any(l.strip() for l in found["Rollout"])
    assert pr_body.review(body, wrapped=False)[0] == []


def test_a_template_missing_a_required_heading_is_refused():
    broken = TEMPLATE.replace("## Changelog", "## Change log")
    try:
        repin.body(
            broken, repin.plan(repin.git_deps(MANIFEST), TAGS), "yadgarhq/iam-db"
        )
    except repin.Refusal as refusal:
        assert "Changelog" in str(refusal)
    else:
        raise AssertionError("a template that cannot yield a valid body must refuse")


def test_the_body_names_the_decision_it_follows_from():
    body = generated_body()
    assert "ADR-0690" in body
    assert "ADR-0526" in body


def test_the_body_states_what_was_not_verified():
    """An automated bump that claims a human read the producers' diffs is lying."""
    body = generated_body()
    assert "NOT DONE" in body


# --------------------------------------------------------------------------
# the command line, which is what the workflow actually runs
# --------------------------------------------------------------------------


def run(*args, cwd, env=None):
    import os

    return subprocess.run(
        [sys.executable, str(GATE), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env={**os.environ, **(env or {})},
    )


def workspace(tmp_path, manifest=MANIFEST, template=TEMPLATE):
    (tmp_path / "Cargo.toml").write_text(manifest)
    (tmp_path / ".github").mkdir(exist_ok=True)
    (tmp_path / ".github" / "pull_request_template.md").write_text(template)
    return tmp_path


def test_apply_rewrites_the_manifest_from_a_written_plan(tmp_path):
    tree = workspace(tmp_path)
    bumps = repin.plan(repin.git_deps(MANIFEST), TAGS)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(repin.as_json(bumps)))
    result = run("apply", str(plan), cwd=tree)
    assert result.returncode == 0, result.stderr
    after = (tree / "Cargo.toml").read_text()
    assert 'tag = "v0.2.13"' in after
    assert 'tag = "v0.2.9"' not in after
    assert "# Pinned by RELEASE TAG, not by branch." in after


def test_apply_prints_the_crates_it_moved(tmp_path):
    tree = workspace(tmp_path)
    bumps = repin.plan(repin.git_deps(MANIFEST), TAGS)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(repin.as_json(bumps)))
    result = run("apply", str(plan), cwd=tree)
    assert "yadgar-store" in result.stdout
    assert "yadgar-telemetry" in result.stdout


def test_apply_refuses_an_empty_plan(tmp_path):
    """Nothing to do is decided by `plan`; reaching `apply` with it is a bug."""
    tree = workspace(tmp_path)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps([]))
    result = run("apply", str(plan), cwd=tree)
    assert result.returncode != 0
    assert "empty" in (result.stdout + result.stderr)


def test_apply_refuses_when_the_manifest_moved_under_it(tmp_path):
    """A plan carries the tag it read; a manifest that no longer holds it is stale."""
    tree = workspace(tmp_path, manifest=MANIFEST.replace('tag = "v0.2.9"', 'tag = "v0.2.13"'))
    bumps = repin.plan(repin.git_deps(MANIFEST), TAGS)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(repin.as_json(bumps)))
    result = run("apply", str(plan), cwd=tree)
    assert result.returncode != 0
    assert "yadgar-store" in (result.stdout + result.stderr)


def test_an_unknown_subcommand_is_refused(tmp_path):
    result = run("frobnicate", cwd=workspace(tmp_path))
    assert result.returncode != 0


def test_producers_names_every_repository_whose_tags_must_be_listed(tmp_path):
    result = run("producers", cwd=workspace(tmp_path))
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["dial", "lifecycle", "store", "telemetry"]


def test_producers_refuses_a_manifest_it_cannot_read(tmp_path):
    tree = workspace(tmp_path, manifest=MANIFEST.replace('tag = "v0.2.9"', 'branch = "main"'))
    result = run("producers", cwd=tree)
    assert result.returncode != 0
    assert "ADR-0526" in (result.stdout + result.stderr)


def tags_dir(tmp_path, tags=TAGS):
    directory = tmp_path / "tags"
    directory.mkdir()
    for producer, names in tags.items():
        (directory / producer).write_text("\n".join(names) + "\n")
    return directory


def test_plan_writes_the_plan_the_body_and_the_head_branch(tmp_path):
    tree = workspace(tmp_path)
    out = tmp_path / "out.txt"
    result = run(
        "plan",
        str(tags_dir(tmp_path)),
        str(tmp_path / "plan.json"),
        str(tmp_path / "body.md"),
        cwd=tree,
        env={"GITHUB_OUTPUT": str(out), "GITHUB_REPOSITORY": "yadgarhq/iam-db"},
    )
    assert result.returncode == 0, result.stderr
    written = json.loads((tmp_path / "plan.json").read_text())
    assert {r["name"] for r in written} == {
        "yadgar-store",
        "yadgar-telemetry",
        "yadgar-lifecycle",
    }
    outputs = dict(l.split("=", 1) for l in out.read_text().splitlines())
    assert outputs["stale"] == "true"
    assert outputs["branch"] == repin.branch(repin.from_json(written))
    assert "-p yadgar-store" in outputs["crates"]
    assert pr_body.review((tmp_path / "body.md").read_text(), wrapped=False)[0] == []


def test_plan_says_so_and_stops_when_every_pin_is_current(tmp_path):
    tree = workspace(tmp_path)
    current = {
        "store": ["v0.2.9"],
        "telemetry": ["v0.1.10"],
        "dial": ["v0.2.6"],
        "lifecycle": ["v0.2.10"],
    }
    out = tmp_path / "out.txt"
    result = run(
        "plan",
        str(tags_dir(tmp_path, current)),
        str(tmp_path / "plan.json"),
        str(tmp_path / "body.md"),
        cwd=tree,
        env={"GITHUB_OUTPUT": str(out)},
    )
    assert result.returncode == 0, result.stderr
    assert out.read_text().strip() == "stale=false"
    assert not (tmp_path / "plan.json").exists()
    assert "Nothing to do" in result.stdout


def test_plan_refuses_a_producer_whose_tags_were_never_listed(tmp_path):
    tree = workspace(tmp_path)
    partial = {k: v for k, v in TAGS.items() if k != "store"}
    result = run(
        "plan",
        str(tags_dir(tmp_path, partial)),
        str(tmp_path / "plan.json"),
        str(tmp_path / "body.md"),
        cwd=tree,
        env={"GITHUB_OUTPUT": str(tmp_path / "out.txt")},
    )
    assert result.returncode != 0
    assert "store" in (result.stdout + result.stderr)


def test_a_repository_with_no_in_org_crate_is_not_an_error(tmp_path):
    tree = workspace(tmp_path, manifest='[package]\nname = "x"\n\n[dependencies]\nmetrics = "0.24"\n')
    out = tmp_path / "out.txt"
    result = run(
        "plan",
        str(tags_dir(tmp_path)),
        str(tmp_path / "plan.json"),
        str(tmp_path / "body.md"),
        cwd=tree,
        env={"GITHUB_OUTPUT": str(out)},
    )
    assert result.returncode == 0, result.stderr
    assert out.read_text().strip() == "stale=false"


def test_a_refusal_is_reported_as_an_error_annotation_not_a_traceback(tmp_path):
    tree = workspace(tmp_path, manifest=MANIFEST.replace('tag = "v0.2.9"', 'rev = "deadbeef"'))
    result = run(
        "plan",
        str(tags_dir(tmp_path)),
        str(tmp_path / "plan.json"),
        str(tmp_path / "body.md"),
        cwd=tree,
        env={"GITHUB_OUTPUT": str(tmp_path / "out.txt")},
    )
    assert result.returncode == 1
    assert "::error::" in result.stdout
    assert "Traceback" not in result.stderr
