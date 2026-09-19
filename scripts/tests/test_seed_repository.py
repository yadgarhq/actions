"""What `hooks/seed_repository.py` REFUSES, pinned so it cannot quietly stop refusing.

ADR-0705. This pair is the OPTIONAL half of that decision's rule 3: the seeder and
the chart refuse bad input unconditionally and server-side, and upstream publishes a
reusable workflow and a pre-commit hook an organisation MAY reference to hear about
it at merge time instead of at sync. Convenience, never the guarantee — so the
interesting property of this suite is not that the gate passes good trees, it is that
every refusal it advertises has a case here that DEMANDS it.

MOST OF THIS FILE IS RED CASES, for the reason `test_helm_pin_agrees.py` gives: a
suite that only feeds a gate conforming input certifies the fixture rather than the
gate.

AND EVERY GREEN CASE IS PAIRED WITH A RED ONE IN THE SAME TREE, which is
`test_d80_portability.py`'s discipline and the half easier to forget. A test asserting
"this tree passes" also passes when the gate is dead.

THE ZERO-INPUT REFUSAL IS THE POINT OF `test_an_empty_tree_is_refused_by_both_parts`
AND ITS SIBLINGS. This estate has produced FIVE checks that exited 0 having inspected
nothing — `if ! cmd | jq -e`, a shallow-clone guard, `command -v actionlint &&
actionlint || echo`, `one_source_per_knob.py` reporting "0 knobs across 0 files", and
the `image` job's visibility guard asking an authenticated registry. Both halves here
refuse a tree they found nothing in, the way `chart_baseline.py` refuses a tree with
no `Chart.yaml` and `boot_reads_watched.py` refuses one with no `src/rotate.rs`.

THE SPLIT IS PINNED TOO, by `test_the_parts_judge_different_things`. One file, two
`entry:` lines, the same shape `chart_baseline.py` argues for: an organisation
declaring only wiki content references the front-matter half and not the other, and
that choice is a line in its own `.pre-commit-config.yaml` rather than an exemption
inside this gate. A test that ran both halves together would let one half's refusal
stand in for the other's.

TWO TESTS ARE DELIBERATELY ABOUT WHAT THE RECORD DOES NOT SAY.
`test_a_wiki_page_needs_no_named_field` and `test_an_agent_prompt_needs_no_named_field`
are what fail if somebody invents front-matter keys for those two roots. D36 names
four keys for `content/memories/` and names NONE for `content/wiki/` or
`prompts/agents/`, so requiring any there would be a guess shipped into somebody
else's repository as a false refusal.

Run: python3 -m pytest scripts/tests/ -q
"""

import ast
import subprocess
import sys
from pathlib import Path

GATE = Path(__file__).resolve().parents[2] / "hooks" / "seed_repository.py"

ASK = "seed-no-ask-prompt"
FRONT_MATTER = "seed-front-matter"

# A memory carrying every key D36 names, with a value on each.
GOOD_MEMORY = """---
id: 7f3c
tags: [onboarding, how-to]
anchor: true
visibility: ORG
---

The body.
"""

# A wiki page carrying front-matter and no named field at all, because the record
# names none for this root. See the module docstring.
GOOD_WIKI = """---
title: How to file an ADR
---

The body.
"""

GOOD_PROMPT = """---
pattern: dispatch-fix-bug
---

The body.
"""


def write(root: Path, **files) -> Path:
    """Lay out a seed declaration. `a__b__c_md` becomes `a/b/c.md`."""
    for key, text in files.items():
        relative = key.replace("__", "/").replace("_md", ".md")
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def declared(root: Path, **extra) -> Path:
    """A tree that is unambiguously a seed declaration, plus whatever is passed."""
    return write(
        root,
        content__memories__m1_md=GOOD_MEMORY,
        content__wiki__w1_md=GOOD_WIKI,
        prompts__agents__p1_md=GOOD_PROMPT,
        **extra,
    )


def run(part: str, root: Path, *extra: str):
    return subprocess.run(
        [sys.executable, str(GATE), "--part", part, "--root", str(root), *extra],
        capture_output=True,
        text=True,
    )


# ------------------------------------------------- the ask half must REFUSE


def test_an_ask_prompt_is_refused(tmp_path):
    """D33'S WHOLE GUARANTEE. The `ask` system prompt has no override at all.

    ADR-0705: "a custom seed repository able to define one would end that guarantee
    at the first organisation that tried". This is the case that says so.
    """
    root = declared(tmp_path, prompts__ask__system_md="whatever")
    result = run(ASK, root)
    assert result.returncode == 1
    assert "prompts/ask/system.md" in result.stdout
    # It says WHAT the file would break, not merely that a path is disallowed.
    assert "D33" in result.stdout


def test_an_ask_prompt_nested_deeper_is_refused(tmp_path):
    """The rule is the directory and everything under it, at any depth."""
    root = declared(tmp_path, prompts__ask__overrides__tenant_md="whatever")
    result = run(ASK, root)
    assert result.returncode == 1
    assert "prompts/ask/overrides/tenant.md" in result.stdout


def test_an_ask_prompt_of_any_extension_is_refused(tmp_path):
    """Not only `*.md`. A `.yaml`, a `.txt` or a `.j2` under there is the same claim."""
    root = declared(tmp_path)
    (root / "prompts" / "ask").mkdir(parents=True)
    (root / "prompts" / "ask" / "system.yaml").write_text("prompt: no")
    result = run(ASK, root)
    assert result.returncode == 1
    assert "prompts/ask/system.yaml" in result.stdout


def test_an_empty_ask_directory_is_refused(tmp_path):
    """A declaration in progress is a declaration.

    git cannot commit an empty directory, so this shape reaches the hook rather than
    CI — which is exactly the case a pre-commit hook is for, and the reason the
    directory rather than its contents is the subject.
    """
    root = declared(tmp_path)
    (root / "prompts" / "ask").mkdir(parents=True)
    result = run(ASK, root)
    assert result.returncode == 1
    assert "prompts/ask" in result.stdout


def test_a_case_variant_of_ask_is_refused(tmp_path):
    """FAIL SAFE. `prompts/Ask/` IS `prompts/ask/` on a case-insensitive filesystem.

    Linux and macOS would disagree about whether the same commit carries an override,
    and a rule that a rename defeats is not a rule. Matched case-insensitively for
    the same reason `no_build_cache.py` treats an unknown cache-ish input as a cache.
    """
    root = declared(tmp_path, prompts__Ask__system_md="whatever")
    result = run(ASK, root)
    assert result.returncode == 1
    assert "prompts/Ask/system.md" in result.stdout


def test_a_tree_holding_only_an_ask_prompt_is_refused_for_the_right_reason(tmp_path):
    """The offence outranks the layout floor, and the message must say which it is.

    A tree carrying `prompts/ask/` and nothing else trips both conditions. Refusing it
    as "nothing to inspect" would tell an organisation to add content when what it
    has to do is delete a file, so the order is pinned rather than incidental.
    """
    root = write(tmp_path, prompts__ask__system_md="whatever")
    result = run(ASK, root)
    assert result.returncode == 1
    assert "D33" in result.stdout
    assert "nothing" not in result.stdout.lower()


def test_the_rule_is_anchored_at_the_content_root(tmp_path):
    """`prompts/ask` AT THE ROOT OF THE DECLARATION, never any directory so named.

    This is the load-bearing scope decision and it is stated rather than assumed. The
    declaration's layout is `prompts/ask/` relative to the root the seeder reads (D36,
    ADR-0705), so `docs/prompts/ask/` is not an override — nothing reads it, and
    refusing it would refuse a repository for a path no component looks at. An
    organisation whose declaration sits in a subdirectory points `--root` (or the
    workflow's `content-root`) at that subdirectory, which re-anchors the rule where
    the declaration actually is.

    THE ANCHORING IS WHAT MAKES THE `.git` PRUNE FREE. Pruning the object store changes
    no verdict precisely because nothing under `.git` could have matched. A first draft
    of this test assumed the opposite and failed, which is how the scope got written
    down.

    Paired, three ways: `.git/prompts/ask/` passes, `docs/prompts/ask/` passes, and the
    real one at the root reddens.
    """
    root = declared(tmp_path)
    (root / ".git" / "prompts" / "ask").mkdir(parents=True)
    (root / ".git" / "prompts" / "ask" / "system.md").write_text("a git object")
    assert run(ASK, root).returncode == 0, run(ASK, root).stdout

    (root / "docs" / "prompts" / "ask").mkdir(parents=True)
    (root / "docs" / "prompts" / "ask" / "system.md").write_text("not a declaration")
    assert run(ASK, root).returncode == 0, run(ASK, root).stdout

    (root / "prompts" / "ask").mkdir(parents=True)
    (root / "prompts" / "ask" / "system.md").write_text("an override")
    result = run(ASK, root)
    assert result.returncode == 1
    assert "prompts/ask/system.md" in result.stdout


def test_the_content_root_re_anchors_the_rule(tmp_path):
    """An organisation whose declaration lives in a subdirectory points --root at it.

    The same file that passes when judged from the repository root REDDENS when the
    root is the declaration — which is what makes the anchoring above a scope rather
    than a blind spot.
    """
    outer = declared(tmp_path)
    inner = declared(tmp_path / "seeds")
    (inner / "prompts" / "ask").mkdir(parents=True)
    (inner / "prompts" / "ask" / "system.md").write_text("an override")

    # Judged from the outer root, `seeds/prompts/ask/` is not the declaration's own
    # `prompts/ask/` and is not this rule's subject.
    assert run(ASK, outer).returncode == 0, run(ASK, outer).stdout
    # Judged from the declaration itself, the same file is exactly the subject.
    result = run(ASK, inner)
    assert result.returncode == 1
    assert "prompts/ask/system.md" in result.stdout


def test_an_empty_tree_is_refused_by_both_parts(tmp_path):
    """THE ZERO-INPUT REFUSAL, and it is the reason this file exists.

    Five checks in this estate have reported success having inspected nothing. A
    green tick from either half must not mean "examined nothing": an organisation
    that declares no org or team content at all is a complete state under D34 and
    simply does not reference these hooks.
    """
    for part in (ASK, FRONT_MATTER):
        result = run(part, tmp_path)
        assert result.returncode == 1, part
        assert "prompts/agents" in result.stdout, part
        assert "content/wiki" in result.stdout, part
        assert "content/memories" in result.stdout, part


def test_a_missing_root_is_refused(tmp_path):
    for part in (ASK, FRONT_MATTER):
        result = run(part, tmp_path / "nowhere")
        assert result.returncode == 1, part
        assert "not a directory" in result.stdout, part


def test_a_part_is_required(tmp_path):
    """No default. A bare invocation must not mean one half without saying so."""
    result = subprocess.run(
        [sys.executable, str(GATE), "--root", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "--part" in result.stderr


# --------------------------------------------- the ask half must NOT refuse


def test_a_declared_tree_with_no_ask_passes_and_the_same_tree_with_one_does_not(
    tmp_path,
):
    """THE PAIR. A green assertion alone also passes when the gate is dead."""
    root = declared(tmp_path)
    good = run(ASK, root)
    assert good.returncode == 0, good.stdout
    # The success line names the roots it found, so the output says what it looked at.
    assert "content/memories" in good.stdout
    assert "content/wiki" in good.stdout
    assert "prompts/agents" in good.stdout

    (root / "prompts" / "ask").mkdir(parents=True)
    (root / "prompts" / "ask" / "system.md").write_text("whatever")
    bad = run(ASK, root)
    assert bad.returncode == 1


def test_an_agent_prompt_merely_named_ask_is_not_refused(tmp_path):
    """The near miss. `prompts/agents/ask.md` is an ORG agent prompt, which D34 allows.

    A substring rule over the path would refuse it — and refusing a legitimate org
    prompt for the letters in its name is how a gate becomes one people delete.
    """
    root = declared(tmp_path, prompts__agents__ask_md=GOOD_PROMPT)
    result = run(ASK, root)
    assert result.returncode == 0, result.stdout


def test_a_wiki_page_about_ask_is_not_refused(tmp_path):
    root = declared(tmp_path, content__wiki__ask_overview_md=GOOD_WIKI)
    result = run(ASK, root)
    assert result.returncode == 0, result.stdout


def test_a_directory_whose_name_merely_contains_ask_is_not_refused(tmp_path):
    """`content/wiki/tasks/` CONTAINS the letters `ask`, and a substring rule refuses it.

    The rule is a path SEGMENT — `prompts/ask` and what is under it — never a substring
    anywhere in the path. `tasks` is the word this estate uses most, so a substring rule
    would refuse the likeliest directory an organisation creates, for the letters in its
    name. Paired with the real offence in the same tree so this is not merely a tree
    that passes.
    """
    root = declared(tmp_path, content__wiki__tasks__how_to_file_md=GOOD_WIKI)
    assert run(ASK, root).returncode == 0, run(ASK, root).stdout

    (root / "prompts" / "ask").mkdir(parents=True)
    (root / "prompts" / "ask" / "system.md").write_text("whatever")
    assert run(ASK, root).returncode == 1


# ----------------------------------------- the front-matter half must REFUSE


def test_a_content_file_with_no_front_matter_is_refused(tmp_path):
    """D36: "Front-matter is mandatory and lint-enforced."

    D35 needs identity derived deterministically from the source, or every upgrade
    duplicates the corpus instead of updating it in place. That is the defect.
    """
    root = declared(tmp_path, content__wiki__w2_md="No front matter at all.\n")
    result = run(FRONT_MATTER, root)
    assert result.returncode == 1
    assert "content/wiki/w2.md" in result.stdout
    # THE MESSAGE, NOT MERELY THE REFUSAL. A first revision of this test asserted
    # `"D35" in stdout`, which the closing summary line also satisfies — so the gate
    # could lose the missing-front-matter branch entirely, fall through to "never
    # closed", and still pass. A mutation run found that. Assert the sentence.
    assert "carries no front-matter block" in result.stdout
    assert "D35" in result.stdout


def test_an_unclosed_front_matter_block_is_refused(tmp_path):
    root = declared(tmp_path, content__wiki__w2_md="---\ntitle: x\n\nbody\n")
    result = run(FRONT_MATTER, root)
    assert result.returncode == 1
    assert "content/wiki/w2.md" in result.stdout
    assert "never closed" in result.stdout


def test_an_empty_front_matter_block_is_refused(tmp_path):
    """`---\\n---` is a block that parses and declares nothing, which is worse.

    It is the shape that would slip past a check asking only whether the delimiters
    are there.
    """
    root = declared(tmp_path, content__wiki__w2_md="---\n---\n\nbody\n")
    result = run(FRONT_MATTER, root)
    assert result.returncode == 1
    assert "content/wiki/w2.md" in result.stdout
    assert "declares nothing" in result.stdout


def test_a_line_that_is_not_a_top_level_mapping_is_refused(tmp_path):
    """NOT A YAML PARSER, and it refuses what it cannot reduce rather than guessing.

    `language: script` means stdlib only, so this gate reads front-matter with a
    deliberately minimal scanner. Anything it cannot reduce to a top-level
    `key: value` — with indented continuations belonging to the key above — is
    REFUSED rather than assumed fine. That is the fail-safe direction: the seeder
    has a real parser and is the authority, and a merge-time hook that guessed would
    pass input the authority rejects.
    """
    root = declared(tmp_path, content__wiki__w2_md="---\nnot a mapping\n---\n\nbody\n")
    result = run(FRONT_MATTER, root)
    assert result.returncode == 1
    assert "content/wiki/w2.md" in result.stdout
    assert "not a mapping" in result.stdout


def test_a_memory_missing_a_named_key_is_refused(tmp_path):
    """D36 names four keys for `content/memories/`: id, tags, anchor, visibility."""
    for missing in ("id", "tags", "anchor", "visibility"):
        text = "".join(
            line + "\n"
            for line in GOOD_MEMORY.splitlines()
            if not line.startswith(f"{missing}:")
        )
        root = declared(tmp_path / missing)
        (root / "content" / "memories" / "m1.md").write_text(text)
        result = run(FRONT_MATTER, root)
        assert result.returncode == 1, missing
        assert f"`{missing}`" in result.stdout, missing
        assert "content/memories/m1.md" in result.stdout, missing


def test_a_memory_declaring_a_named_key_with_no_value_is_refused(tmp_path):
    """A key present and empty is not the key D36 asks for.

    A check reading only the key names would pass `visibility:` with nothing after
    it, and the seeder would then have to invent a visibility — which is the class
    of defect ADR-0569 exists to prevent one layer down.
    """
    root = declared(tmp_path)
    (root / "content" / "memories" / "m1.md").write_text(
        GOOD_MEMORY.replace("visibility: ORG", "visibility:")
    )
    result = run(FRONT_MATTER, root)
    assert result.returncode == 1
    assert "`visibility`" in result.stdout
    assert "no value" in result.stdout


def test_roots_that_exist_but_hold_no_content_file_are_refused(tmp_path):
    """THE FLOOR. The layout is there and there is nothing in it.

    This is the shape `one_source_per_knob.py` shipped as "0 knobs across 0 files" and
    the shape a renamed directory produces. The layout precondition alone does not
    catch it, so the count is its own refusal.
    """
    root = tmp_path
    for directory in ("content/memories", "content/wiki", "prompts/agents"):
        (root / directory).mkdir(parents=True)
    result = run(FRONT_MATTER, root)
    assert result.returncode == 1
    assert "0 content file(s)" in result.stdout


def test_a_root_holding_only_non_markdown_is_refused(tmp_path):
    """`*.md` is the subject, so a tree of `.txt` is a tree this half cannot judge.

    It must refuse rather than pass: a corpus written in some other extension is
    precisely a corpus this gate is not reading.
    """
    root = tmp_path
    (root / "content" / "wiki").mkdir(parents=True)
    (root / "content" / "wiki" / "w1.txt").write_text("body")
    result = run(FRONT_MATTER, root)
    assert result.returncode == 1
    assert "0 content file(s)" in result.stdout


def test_every_bad_file_is_named_not_only_the_first(tmp_path):
    """An organisation fixing one file per red run is an organisation deleting the hook."""
    root = declared(
        tmp_path,
        content__wiki__w2_md="body only\n",
        content__wiki__w3_md="---\ntitle: x\n",
    )
    result = run(FRONT_MATTER, root)
    assert result.returncode == 1
    assert "content/wiki/w2.md" in result.stdout
    assert "content/wiki/w3.md" in result.stdout


# ------------------------------------ the front-matter half must NOT refuse


def test_a_declared_tree_passes_and_the_same_tree_with_one_bad_file_does_not(tmp_path):
    """THE PAIR again, and the count is asserted so a silent zero cannot hide here."""
    root = declared(tmp_path)
    good = run(FRONT_MATTER, root)
    assert good.returncode == 0, good.stdout
    assert "3 content file(s)" in good.stdout

    (root / "content" / "wiki" / "w2.md").write_text("no front matter\n")
    bad = run(FRONT_MATTER, root)
    assert bad.returncode == 1


def test_a_wiki_page_needs_no_named_field(tmp_path):
    """WHAT THE RECORD DOES NOT SAY. D36 names no front-matter key for `content/wiki/`.

    Paired with a red case so this is not merely a tree that passes: the page with
    front-matter carrying one unrecognised key passes, and the page with no
    front-matter in the same tree reddens.
    """
    root = declared(tmp_path, content__wiki__w2_md="---\nanything: at all\n---\n\nx\n")
    assert run(FRONT_MATTER, root).returncode == 0

    (root / "content" / "wiki" / "w3.md").write_text("nothing at all\n")
    assert run(FRONT_MATTER, root).returncode == 1


def test_an_agent_prompt_needs_no_named_field(tmp_path):
    root = declared(tmp_path, prompts__agents__p2_md="---\nanything: at all\n---\n\nx\n")
    assert run(FRONT_MATTER, root).returncode == 0

    (root / "prompts" / "agents" / "p3.md").write_text("nothing at all\n")
    assert run(FRONT_MATTER, root).returncode == 1


def test_a_block_list_and_a_flow_list_are_both_accepted(tmp_path):
    """`tags:` is a list, and YAML writes a list two ways.

    A scanner accepting only `key: value` on one line would refuse the block form,
    which is the more common way to write four tags — a false refusal against
    perfectly good input, and the reason the scanner treats an INDENTED line as
    belonging to the key above it.
    """
    block = GOOD_MEMORY.replace(
        "tags: [onboarding, how-to]", "tags:\n  - onboarding\n  - how-to"
    )
    root = declared(tmp_path)
    (root / "content" / "memories" / "m1.md").write_text(block)
    assert run(FRONT_MATTER, root).returncode == 0, run(FRONT_MATTER, root).stdout

    # ...and the pair: an indented line with NO key above it belongs to nothing.
    (root / "content" / "memories" / "m2.md").write_text(
        "---\n  - orphan\nid: x\ntags: [a]\nanchor: true\nvisibility: ORG\n---\n"
    )
    assert run(FRONT_MATTER, root).returncode == 1


def test_a_comment_and_a_blank_line_inside_front_matter_are_accepted(tmp_path):
    root = declared(tmp_path)
    (root / "content" / "memories" / "m1.md").write_text(
        "---\n# why this memory exists\nid: x\n\ntags: [a]\nanchor: true\n"
        "visibility: ORG\n---\n"
    )
    assert run(FRONT_MATTER, root).returncode == 0

    (root / "content" / "memories" / "m2.md").write_text("---\n# only a comment\n---\n")
    assert run(FRONT_MATTER, root).returncode == 1


def test_content_is_walked_recursively(tmp_path):
    """`content/wiki/<slug>.md` is the record's layout; a slug may carry a directory.

    Paired: the nested good file passes, the nested bad one reddens, and the bad one
    is NAMED with its full relative path so an organisation can find it.
    """
    root = declared(tmp_path, content__wiki__team__nested_md=GOOD_WIKI)
    assert run(FRONT_MATTER, root).returncode == 0

    (root / "content" / "wiki" / "team" / "bad.md").write_text("no front matter\n")
    result = run(FRONT_MATTER, root)
    assert result.returncode == 1
    assert "content/wiki/team/bad.md" in result.stdout


def test_one_root_alone_is_enough_to_be_a_declaration(tmp_path):
    """D34: an organisation may declare wiki content and no prompts at all.

    Requiring all three roots would refuse that, so the layout precondition is ANY
    of the three. Paired with the empty-tree refusal above, which is what stops that
    laxness becoming a vacuous pass.
    """
    root = write(tmp_path, content__wiki__w1_md=GOOD_WIKI)
    assert run(FRONT_MATTER, root).returncode == 0
    assert run(ASK, root).returncode == 0


# ------------------------------------------------------ the split, and the shape


def test_the_parts_judge_different_things(tmp_path):
    """ONE FILE, TWO `entry:` LINES, and neither half stands in for the other.

    `chart_baseline.py` makes this argument: an organisation whose declaration has
    one problem and not the other references one hook and not both, and that choice
    is a line in its own config rather than an exemption inside this gate. A suite
    running the halves together would let one refusal cover for the other's death.
    """
    # Bad front-matter, no ask prompt: the front-matter half refuses, the other does not.
    root = declared(tmp_path / "a", content__wiki__w2_md="no front matter\n")
    assert run(FRONT_MATTER, root).returncode == 1
    assert run(ASK, root).returncode == 0

    # An ask prompt, front-matter all good: the other way round.
    other = declared(tmp_path / "b", prompts__ask__system_md="whatever")
    assert run(ASK, other).returncode == 1
    assert run(FRONT_MATTER, other).returncode == 0


def test_the_report_flag_names_every_file_it_judged(tmp_path):
    """A gate whose rule nobody can reconstruct from its output is the vacuous kind.

    `--report` is also how an organisation MEASURES its own tree before referencing
    the hook, which is what ADR-0607 asks of an adoption step.
    """
    root = declared(tmp_path)
    result = run(FRONT_MATTER, root, "--report")
    assert result.returncode == 0
    for name in ("content/memories/m1.md", "content/wiki/w1.md", "prompts/agents/p1.md"):
        assert name in result.stdout


def test_the_gate_imports_only_the_standard_library():
    """`language: script` provisions nothing, so an import must not need a `pip install`.

    An organisation referencing this hook has whatever python3 its machine carries.
    A future `import yaml` here — the obvious way to read front-matter — would fail
    on every adopter's laptop rather than in this suite, which is why the scanner
    above is hand-written and why it refuses what it cannot reduce.
    """
    allowed = {"argparse", "os", "pathlib", "re", "sys"}
    tree = ast.parse(GATE.read_text())
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    assert found <= allowed, f"not stdlib-only: {sorted(found - allowed)}"


def test_the_gate_claims_no_schema_validation():
    """THE SEAM, PINNED SO IT CANNOT QUIETLY BECOME A CLAIM.

    ADR-0705's rule 3 has THREE refusals. This pair implements two. The third — an
    invalid values schema — is not implementable in a `language: script` hook at all:
    helm is what enforces `values.schema.json`, at template time, and the standard
    library has no JSON Schema validator. So this gate must never print anything a
    reader could take for a schema verdict, and must never reach for a validator.

    THE MEASUREMENT LIVES IN THE GATE'S PROSE, NOT IN AN ASSERTION, and deliberately.
    An earlier revision of that prose said the schema did not exist on
    `yadgarhq/config@main`; it does, and the chart is pullable too. A test asserting
    either fact would have gone stale the same day and reddened this repository for a
    change in another one. What is pinned here is only what this file controls: that the
    seam is documented rather than omitted, and that no validator is imported.
    """
    source = GATE.read_text()
    assert "values.schema.json" in source, "the seam must be documented, not omitted"
    for forbidden in ("jsonschema", "import json"):
        assert forbidden not in source, f"the gate reaches for `{forbidden}`"
