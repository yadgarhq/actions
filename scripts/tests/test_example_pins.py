"""What `example_pins.py` rewrites in a parent chart's examples, and what it REFUSES.

ADR-0820: the commit a parent tag `vN` points at carries examples that pin `vN`.
Tag `v0.3.8` shipped `example/application.yaml` pinning `0.3.5`, because the pin
was moved by hand and every release after it was cut by a machine that did not
know the file existed. The rewriter is the half that knows.

THE FILES ARE MODELLED ON `yadgarhq/chart` PULL REQUEST 17, not invented: a
long comment header, the pin under `spec.source`, a comment above the pin, and a
`platform` Application in a file of its own. The cases that carry the design:

  `test_only_the_named_charts_move` — an Application for another chart in the
  same file keeps its pin byte for byte.

  `test_a_flow_style_site_is_refused_rather_than_skipped` — a file that names
  `chart: yadgar` in a shape the rewriter does not understand is a refusal. A
  rewriter that finds nothing and exits 0 is the defect this estate keeps
  shipping.

  `test_a_repository_without_examples_is_untouched` — the version job calls this
  in every repository, and only one of them has examples.

Run: python3 -m pytest scripts/tests/ -q
"""

import json
import os
import pathlib
import subprocess
import sys
from collections import namedtuple

import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import example_pins  # noqa: E402
import example_published  # noqa: E402
import next_version  # noqa: E402
import pr_body  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
CI_PR = ROOT / ".github" / "workflows" / "ci-pr.yaml"

BOT = "yadgarhq-bot[bot] <324366854+yadgarhq-bot[bot]@users.noreply.github.com>"

APPLICATION = """\
# THE PARENT. Render before you sync:
#
#   helm template yadgar oci://ghcr.io/yadgarhq/charts/yadgar --version 0.3.8
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: yadgar
  namespace: argocd
spec:
  project: default
  source:
    repoURL: ghcr.io/yadgarhq/charts
    chart: yadgar
    # BARE SEMVER, the parent chart's version.
    targetRevision: 0.3.5
    helm:
      valuesObject:
        global:
          hostname: yadgar.example.com
  destination:
    server: https://kubernetes.default.svc
    namespace: yadgar
  syncPolicy:
    automated: { prune: true, selfHeal: true }
    syncOptions: [CreateNamespace=true]
"""

KIND = """\
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: yadgar
  namespace: argocd
spec:
  project: default
  source:
    repoURL: ghcr.io/yadgarhq/charts
    chart: yadgar
    # THE SAME PIN AS `../application.yaml`. The suite holds the two equal.
    targetRevision: "0.3.5" # quoted, and a trailing comment
    helm:
      valuesObject:
        global:
          hostname: gateway.yadgar.internal
"""

OPERATORS = """\
# THE FOUR OPERATORS. (0.3.8 carries 0.1.19)
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: operators
  namespace: argocd
spec:
  project: default
  source:
    repoURL: ghcr.io/yadgarhq/charts
    chart: platform
    targetRevision: 0.1.18
    helm:
      valuesObject:
        operators:
          create: true
"""

CHART = """\
apiVersion: v2
name: yadgar
version: 0.1.0

dependencies:
  - name: gateway
    version: 0.9.54
    repository: oci://ghcr.io/yadgarhq/charts
  - name: platform
    version: 0.1.19
    repository: oci://ghcr.io/yadgarhq/charts
    condition: platform.enabled
"""

FILES = {
    "example/application.yaml": APPLICATION,
    "example/kind/application.yaml": KIND,
    "example/operators-application.yaml": OPERATORS,
}


def source(text, index=0):
    documents = [d for d in yaml.safe_load_all(text) if d]
    return documents[index]["spec"]["source"]


# ---------------------------------------------------------------------------
# The rewrite.
# ---------------------------------------------------------------------------


def test_the_three_pins_move_together():
    out = example_pins.stamp(FILES, "0.3.9", CHART)
    assert set(out) == set(FILES)
    assert source(out["example/application.yaml"])["targetRevision"] == "0.3.9"
    assert source(out["example/kind/application.yaml"])["targetRevision"] == "0.3.9"
    # THE PLATFORM PIN IS THE ONE THE CHART IN THE SAME COMMIT DECLARES.
    assert source(out["example/operators-application.yaml"])["targetRevision"] == "0.1.19"


def test_only_the_pin_line_changes():
    """A YAML round trip would reflow the comments and the flow mappings."""
    out = example_pins.stamp(FILES, "0.3.9", CHART)
    for path, before in FILES.items():
        after = out[path]
        a, b = before.splitlines(), after.splitlines()
        assert len(a) == len(b)
        changed = [(x, y) for x, y in zip(a, b) if x != y]
        assert len(changed) == 1, (path, changed)
        assert "targetRevision:" in changed[0][0]


def test_quotes_and_a_trailing_comment_survive():
    out = example_pins.stamp(FILES, "0.3.9", CHART)
    assert (
        '    targetRevision: "0.3.9" # quoted, and a trailing comment\n'
        in out["example/kind/application.yaml"]
    )


def test_comments_that_name_a_version_are_not_rewritten():
    """Only `targetRevision:` moves. Prose is the chart repository's to keep true."""
    out = example_pins.stamp(FILES, "0.3.9", CHART)
    assert "--version 0.3.8" in out["example/application.yaml"]
    assert "(0.3.8 carries 0.1.19)" in out["example/operators-application.yaml"]


def test_files_already_at_the_pin_are_not_returned():
    """No change, no commit: the tag goes on the commit the job was handed."""
    once = example_pins.stamp(FILES, "0.3.9", CHART)
    assert example_pins.stamp({**FILES, **once}, "0.3.9", CHART) == {}


def test_only_the_named_charts_move():
    """A `gateway` Application in the same file keeps its pin."""
    other = APPLICATION.replace("chart: yadgar", "chart: gateway").replace(
        "0.3.5", "0.9.50"
    )
    both = APPLICATION + "---\n" + other
    out = example_pins.stamp({"example/application.yaml": both}, "0.3.9", CHART)
    text = out["example/application.yaml"]
    assert source(text, 0)["targetRevision"] == "0.3.9"
    assert source(text, 1)["targetRevision"] == "0.9.50"


def test_a_sibling_mapping_is_not_mistaken_for_the_source():
    """`targetRevision` outside the source mapping that names the chart stays put."""
    text = APPLICATION.replace(
        "  destination:\n",
        "  info:\n    targetRevision: 9.9.9\n  destination:\n",
    )
    out = example_pins.stamp({"example/application.yaml": text}, "0.3.9", CHART)
    assert "    targetRevision: 9.9.9\n" in out["example/application.yaml"]
    assert source(out["example/application.yaml"])["targetRevision"] == "0.3.9"


def test_a_nested_key_of_the_same_name_is_not_the_pin():
    """A deeper `targetRevision:` inside the Helm values belongs to something else."""
    text = APPLICATION.replace(
        "        global:\n",
        "        other:\n          targetRevision: 1.2.3\n        global:\n",
    )
    out = example_pins.stamp({"example/application.yaml": text}, "0.3.9", CHART)
    assert "          targetRevision: 1.2.3\n" in out["example/application.yaml"]
    assert source(out["example/application.yaml"])["targetRevision"] == "0.3.9"


def test_a_sources_list_entry_is_rewritten():
    """Argo's multi-source form opens the mapping with a dash."""
    text = """\
kind: Application
spec:
  sources:
    - repoURL: ghcr.io/yadgarhq/charts
      targetRevision: 0.3.5
      chart: yadgar
    - repoURL: https://github.com/example/values
      targetRevision: main
      ref: values
"""
    out = example_pins.stamp({"example/application.yaml": text}, "0.3.9", CHART)
    loaded = yaml.safe_load(out["example/application.yaml"])
    assert loaded["spec"]["sources"][0]["targetRevision"] == "0.3.9"
    assert loaded["spec"]["sources"][1]["targetRevision"] == "main"


def test_a_flow_style_site_is_refused_rather_than_skipped():
    text = "kind: Application\nspec:\n  source: {chart: yadgar, targetRevision: 0.3.5}\n"
    with pytest.raises(example_pins.Refusal) as caught:
        example_pins.stamp({"example/application.yaml": text}, "0.3.9", CHART)
    assert "yadgar" in str(caught.value)


@pytest.mark.parametrize("key", ['"chart"', "'chart'"])
def test_a_quoted_chart_key_is_refused_rather_than_skipped(key):
    text = APPLICATION.replace("    chart: yadgar\n", f"    {key}: yadgar\n")
    with pytest.raises(example_pins.Refusal):
        example_pins.stamp({"example/application.yaml": text}, "0.3.9", CHART)


@pytest.mark.parametrize(
    "value", ["", '""', "''", "&pin 0.3.5", "*pin", "!!str 0.3.5"]
)
def test_a_pin_that_is_empty_or_an_anchor_alias_or_tag_is_refused(value):
    """Rewriting the token of an alias or a tag would change what it means."""
    text = APPLICATION.replace("targetRevision: 0.3.5", f"targetRevision: {value}".rstrip())
    with pytest.raises(example_pins.Refusal):
        example_pins.stamp({"example/application.yaml": text}, "0.3.9", CHART)


def test_a_mention_in_a_trailing_comment_is_prose():
    text = APPLICATION.replace(
        "    repoURL: ghcr.io/yadgarhq/charts\n",
        "    repoURL: ghcr.io/yadgarhq/charts # the registry chart: yadgar lives in\n",
    )
    out = example_pins.stamp({"example/application.yaml": text}, "0.3.9", CHART)
    assert source(out["example/application.yaml"])["targetRevision"] == "0.3.9"
    assert "# the registry chart: yadgar lives in\n" in out["example/application.yaml"]


def test_a_block_scalar_mention_fails_safe_as_a_refusal():
    """A `chart: yadgar` line inside a block scalar reads as a key. With no pin
    beside it, that is a refusal — never a rewrite of text inside a string."""
    text = APPLICATION.replace(
        "  project: default\n",
        "  project: default\n  info: |\n    chart: yadgar\n",
    )
    with pytest.raises(example_pins.Refusal):
        example_pins.stamp({"example/application.yaml": text}, "0.3.9", CHART)


def test_a_site_with_no_pin_is_refused():
    text = APPLICATION.replace("    targetRevision: 0.3.5\n", "")
    with pytest.raises(example_pins.Refusal):
        example_pins.stamp({"example/application.yaml": text}, "0.3.9", CHART)


def test_a_site_with_two_pins_is_refused():
    text = APPLICATION.replace(
        "    targetRevision: 0.3.5\n",
        "    targetRevision: 0.3.5\n    targetRevision: 0.3.4\n",
    )
    with pytest.raises(example_pins.Refusal):
        example_pins.stamp({"example/application.yaml": text}, "0.3.9", CHART)


def test_a_platform_example_without_a_platform_dependency_is_refused():
    chart = CHART.split("  - name: platform")[0]
    with pytest.raises(example_pins.Refusal) as caught:
        example_pins.stamp(
            {"example/operators-application.yaml": OPERATORS}, "0.3.9", chart
        )
    assert "platform" in str(caught.value)


def test_a_file_that_names_neither_chart_is_left_alone():
    """Another repository's `example/application.yaml` is not the parent's."""
    other = APPLICATION.replace("chart: yadgar", "chart: gateway")
    assert example_pins.stamp({"example/application.yaml": other}, "0.3.9", CHART) == {}


def test_a_commented_out_site_is_neither_rewritten_nor_refused():
    """`yadgarhq/config`'s example carries a commented-out multi-source block.

    A comment is prose. Counting it as a site the rewriter missed would refuse
    every release of a repository whose example documents an alternative form.
    """
    text = APPLICATION.replace(
        "apiVersion:",
        "#   sources:\n#     - chart: yadgar\n#       targetRevision: 0.1.0\n"
        "#     - {chart: yadgar, targetRevision: 0.1.0}\napiVersion:",
        1,
    )
    out = example_pins.stamp({"example/application.yaml": text}, "0.3.9", CHART)
    new = out["example/application.yaml"]
    assert "#       targetRevision: 0.1.0\n" in new
    assert source(new)["targetRevision"] == "0.3.9"


def test_a_repository_without_examples_is_untouched():
    """Absent files, and no Chart.yaml either: nothing is read and nothing moves."""
    assert example_pins.stamp({path: None for path in FILES}, "0.3.9", None) == {}


def test_a_leading_v_is_refused():
    with pytest.raises(example_pins.Refusal):
        example_pins.stamp(FILES, "v0.3.9", CHART)


# ---------------------------------------------------------------------------
# The commit on the parent's `main` must not cut a second version.
# ---------------------------------------------------------------------------


def test_the_stamp_message_makes_the_repository_derive_nothing():
    message = example_pins.message("0.3.9")
    verdict = next_version.derive(
        "v0.3.8", ["v0.3.8"], [message], [BOT], sorted(FILES)
    )
    assert verdict.rc == 0, "\n".join(verdict.lines)
    assert verdict.nxt == ""
    for line in message.splitlines():
        assert not pr_body.STARTS_ENTRY.match(line), line


def test_examples_are_not_files_the_estate_calls_shipping():
    assert not next_version.ships(sorted(FILES))


# ---------------------------------------------------------------------------
# The commit itself, through the git data API.
# ---------------------------------------------------------------------------

Done = namedtuple("Done", "returncode stdout stderr")


class Repo:
    """A `gh` stub holding commits, trees and one `main`, refusing a non-fast-forward."""

    def __init__(self, head="sha-0", files=None):
        self.trees = {head: dict(files or {})}
        self.parents = {head: None}
        self.main = head
        self.made = []
        self.calls = []
        self.move = None
        self.patch_error = None
        self.patches = []
        self.remote_tags = ["v0.3.8"]
        self.compare_status = "ahead"
        self.compared = []
        self.tag_error = None

    @staticmethod
    def body(args):
        with open(args[args.index("--input") + 1], encoding="utf-8") as fh:
            return json.load(fh)

    def __call__(self, args):
        self.calls.append(list(args))
        path = next(a for a in args if a.startswith("repos/"))
        method = args[args.index("-X") + 1] if "-X" in args else "GET"
        if "git/matching-refs/tags/v" in path:
            if self.tag_error:
                return Done(1, "", self.tag_error)
            return Done(0, "".join(f"refs/tags/{t}\n" for t in self.remote_tags), "")
        if "/compare/" in path:
            self.compared.append(path.split("/compare/", 1)[1])
            return Done(0, self.compare_status + "\n", "")
        if path.endswith("/commits/main"):
            return Done(0, self.main + "\n", "")
        if "/git/commits/" in path and method == "GET":
            return Done(0, "tree-" + path.rsplit("/", 1)[1] + "\n", "")
        if path.endswith("/git/trees"):
            data = self.body(args)
            base = data["base_tree"][len("tree-") :]
            files = dict(self.trees[base])
            for entry in data["tree"]:
                assert entry["mode"] == "100644" and entry["type"] == "blob"
                files[entry["path"]] = entry["content"]
            key = f"new-{len(self.trees)}"
            self.trees[key] = files
            return Done(0, "tree-" + key + "\n", "")
        if path.endswith("/git/commits"):
            data = self.body(args)
            sha = f"sha-{len(self.parents)}"
            self.trees[sha] = self.trees[data["tree"][len("tree-") :]]
            self.parents[sha] = data["parents"][0]
            self.made.append({"sha": sha, "message": data["message"], **data})
            return Done(0, sha + "\n", "")
        if path.endswith("/git/refs/heads/main"):
            if self.move is not None:
                self.move(self)
                self.move = None
            if self.patch_error:
                return Done(1, "", self.patch_error)
            data = self.body(args)
            self.patches.append(data)
            if not data.get("force") and self.parents[data["sha"]] != self.main:
                return Done(1, "", "gh: Update is not a fast forward (HTTP 422)")
            self.main = data["sha"]
            return Done(0, "{}", "")
        raise AssertionError(f"the stub was asked something it does not model: {args}")


def gh(repo):
    return example_pins.Api("yadgarhq/chart", run=repo)


def test_the_commit_is_one_fast_forward_carrying_every_change():
    repo = Repo(files={"chart/Chart.yaml": CHART, **FILES})
    changes = example_pins.stamp(FILES, "0.3.9", CHART)
    sha, error = example_pins.commit(gh(repo), "sha-0", changes, "ci: x")
    assert error is None
    assert repo.main == sha
    assert repo.parents[sha] == "sha-0"
    assert source(repo.trees[sha]["example/application.yaml"])["targetRevision"] == "0.3.9"
    assert repo.trees[sha]["chart/Chart.yaml"] == CHART
    assert repo.patches == [{"sha": sha, "force": False}]


def test_a_moved_main_refuses_the_commit_rather_than_overwriting_it():
    def other(repo):
        repo.parents["sha-x"] = repo.main
        repo.trees["sha-x"] = dict(repo.trees[repo.main])
        repo.main = "sha-x"

    repo = Repo(files=FILES)
    repo.move = other
    sha, error = example_pins.commit(
        gh(repo), "sha-0", example_pins.stamp(FILES, "0.3.9", CHART), "ci: x"
    )
    assert sha is None and error
    assert repo.main == "sha-x"


# ---------------------------------------------------------------------------
# `main()`, the version job's step.
# ---------------------------------------------------------------------------


def run_main(tmp_path, repo, files, version="0.3.9", sha="sha-0"):
    for path, text in files.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    out = tmp_path / "out"
    summary = tmp_path / "summary"
    env = {
        "REPO": "yadgarhq/chart",
        "SHA": sha,
        "VERSION": version,
        "GITHUB_OUTPUT": str(out),
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        rc = example_pins.main(run=repo)
    finally:
        os.chdir(cwd)
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return rc, out.read_text() if out.exists() else ""


def test_main_without_examples_tags_the_commit_it_was_handed(tmp_path):
    repo = Repo()
    rc, out = run_main(tmp_path, repo, {"src/main.rs": "fn main() {}\n"})
    assert rc == 0
    assert "target=sha-0\n" in out
    assert repo.made == [] and repo.patches == [], "nothing is written without examples"


def test_main_with_examples_already_pinned_makes_no_commit(tmp_path):
    stamped = {**FILES, **example_pins.stamp(FILES, "0.3.9", CHART)}
    repo = Repo()
    rc, out = run_main(tmp_path, repo, {"chart/Chart.yaml": CHART, **stamped})
    assert rc == 0
    assert "target=sha-0\n" in out
    assert repo.made == [] and repo.patches == []


CONFIG = pathlib.Path(__file__).resolve().parent / "fixtures" / "config"


def config_files():
    return {
        path: (CONFIG / path).read_text(encoding="utf-8")
        for path in ("chart/Chart.yaml", "example/application.yaml")
    }


def test_config_has_no_dependencies_and_no_platform_example():
    """The fixture is `yadgarhq/config`'s real files, and this is why they matter."""
    files = config_files()
    assert "dependencies:" not in files["chart/Chart.yaml"]
    assert "chart: config" in files["example/application.yaml"]


def test_a_module_example_with_a_dependency_less_chart_is_left_alone(tmp_path):
    """`yadgarhq/config`: a `chart: config` example and no `dependencies:` at all.

    Resolving the platform pin there refused, and the refusal reddened every
    `version` run in `config` — no config release could be tagged.
    """
    files = config_files()
    assert example_pins.stamp(
        {"example/application.yaml": files["example/application.yaml"]},
        "0.2.1", files["chart/Chart.yaml"],
    ) == {}
    repo = Repo()
    repo.remote_tags = ["v0.1.9", "v0.2.0"]
    rc, out = run_main(tmp_path, repo, files, version="0.2.1")
    assert rc == 0
    assert out == "target=sha-0\n"
    assert repo.made == [] and repo.patches == []


def test_a_real_platform_site_without_a_dependency_still_refuses():
    chart = "apiVersion: v2\nname: yadgar\nversion: 0.1.0\n"
    with pytest.raises(example_pins.Refusal):
        example_pins.stamp(
            {"example/operators-application.yaml": OPERATORS}, "0.3.9", chart
        )


def test_a_tag_cut_since_checkout_that_contains_this_merge_is_a_green_no_op(tmp_path):
    """Concurrent merges A and B, or `parent_bump.py` after A's checkout.

    Every tag is cut on `main` by fast-forward only, so a tag cut after this
    run's checkout descends from `github.sha` and its range already counts this
    merge. Tagging again would count it twice; red would report a release that
    was not lost. Green, with a notice, and nothing written.
    """
    repo = Repo(files={"chart/Chart.yaml": CHART, **FILES})
    repo.remote_tags = ["v0.3.8", "v0.3.10"]
    repo.compare_status = "ahead"
    rc, out = run_main(tmp_path, repo, {"chart/Chart.yaml": CHART, **FILES})
    assert rc == 0
    assert out == "target=\n"
    assert repo.made == [] and repo.patches == []
    assert repo.compared == ["sha-0...v0.3.10"]


def test_a_version_equal_to_a_remote_tag_is_the_lost_race_and_stays_green(tmp_path):
    """Another run already cut this number; the tag step's own arm says so."""
    repo = Repo(files={"chart/Chart.yaml": CHART, **FILES})
    repo.remote_tags = ["v0.3.8", "v0.3.9"]
    repo.compare_status = "identical"
    rc, out = run_main(tmp_path, repo, {"chart/Chart.yaml": CHART, **FILES})
    assert rc == 0
    assert out == "target=\n"
    assert repo.made == [] and repo.patches == []


@pytest.mark.parametrize("status", ["diverged", "behind"])
def test_a_newer_tag_that_does_not_contain_this_merge_is_red(tmp_path, status):
    """Off `main` (diverged), or older than this merge with a number at or above it."""
    repo = Repo(files={"chart/Chart.yaml": CHART, **FILES})
    repo.remote_tags = ["v0.3.8", "v0.3.10"]
    repo.compare_status = status
    rc, out = run_main(tmp_path, repo, {"chart/Chart.yaml": CHART, **FILES})
    assert rc == 1
    assert out == "target=\n"
    assert repo.made == [] and repo.patches == []


def test_the_freshness_check_also_runs_without_examples(tmp_path):
    repo = Repo()
    repo.remote_tags = ["v0.3.10"]
    repo.compare_status = "diverged"
    rc, out = run_main(tmp_path, repo, {"src/main.rs": "fn main() {}\n"})
    assert rc == 1
    assert out == "target=\n"


def test_a_tag_listing_that_fails_refuses(tmp_path):
    repo = Repo(files={"chart/Chart.yaml": CHART, **FILES})
    repo.tag_error = "gh: Server Error (HTTP 502)"
    with pytest.raises(example_pins.Refusal) as caught:
        run_main(tmp_path, repo, {"chart/Chart.yaml": CHART, **FILES})
    assert "could not be listed" in str(caught.value)
    assert repo.made == [] and repo.patches == []


@pytest.mark.parametrize("tags", [[], ["vendor-drop", "v1a"]])
def test_no_orderable_remote_tag_is_a_baseline_of_zero(tmp_path, tags):
    """`v0.0.0` rather than a crash: nothing orderable is below every version."""
    repo = Repo(files={"chart/Chart.yaml": CHART, **FILES})
    repo.remote_tags = tags
    rc, out = run_main(tmp_path, repo, {"chart/Chart.yaml": CHART, **FILES})
    assert rc == 0
    assert out == f"target={repo.main}\n"
    assert repo.compared == []


def test_a_version_that_is_not_plain_semver_is_refused_before_any_compare(tmp_path):
    repo = Repo()
    with pytest.raises(example_pins.Refusal) as caught:
        run_main(tmp_path, repo, {"src/main.rs": "fn main() {}\n"}, version="0.3.9-rc1")
    assert "0.3.9-rc1" in str(caught.value)
    assert repo.calls == []


def test_main_commits_the_stamp_and_hands_its_sha_to_the_tag(tmp_path):
    repo = Repo(files={"chart/Chart.yaml": CHART, **FILES})
    rc, out = run_main(tmp_path, repo, {"chart/Chart.yaml": CHART, **FILES})
    assert rc == 0
    assert f"target={repo.main}\n" in out
    assert repo.main != "sha-0"
    tree = repo.trees[repo.main]
    assert source(tree["example/application.yaml"])["targetRevision"] == "0.3.9"
    assert source(tree["example/operators-application.yaml"])["targetRevision"] == "0.1.19"


def test_main_defers_when_main_moved_rather_than_tagging_off_main(tmp_path):
    """A tag off `main` breaks `git describe`, so the next push derives instead."""

    def other(repo):
        repo.parents["sha-x"] = repo.main
        repo.trees["sha-x"] = dict(repo.trees[repo.main])
        repo.main = "sha-x"

    repo = Repo(files={"chart/Chart.yaml": CHART, **FILES})
    repo.move = other
    rc, out = run_main(tmp_path, repo, {"chart/Chart.yaml": CHART, **FILES})
    assert rc == 0
    assert "target=\n" in out


def test_main_reddens_when_the_write_fails_and_main_did_not_move(tmp_path):
    repo = Repo(files={"chart/Chart.yaml": CHART, **FILES})
    repo.patch_error = "gh: Resource not accessible by integration (HTTP 403)"
    rc, out = run_main(tmp_path, repo, {"chart/Chart.yaml": CHART, **FILES})
    assert rc == 1
    assert out == "target=\n", "a failed stamp must hand the tag step nothing to tag"


def test_main_reddens_on_a_refused_write_even_when_main_also_moved(tmp_path):
    """Only GitHub's non-fast-forward refusal defers. A 403 is a ruleset, not a race."""

    def other(repo):
        repo.parents["sha-x"] = repo.main
        repo.trees["sha-x"] = dict(repo.trees[repo.main])
        repo.main = "sha-x"

    repo = Repo(files={"chart/Chart.yaml": CHART, **FILES})
    repo.move = other
    repo.patch_error = "gh: Resource not accessible by integration (HTTP 403)"
    rc, out = run_main(tmp_path, repo, {"chart/Chart.yaml": CHART, **FILES})
    assert rc == 1
    assert out == "target=\n"


# ---------------------------------------------------------------------------
# The workflow. A tested script the workflow does not call runs nowhere.
# ---------------------------------------------------------------------------


def version_steps():
    loaded = yaml.safe_load(CI_PR.read_text(encoding="utf-8"))
    return {s.get("id") or s.get("name"): s for s in loaded["jobs"]["version"]["steps"]}


def test_the_version_job_stamps_before_it_tags():
    order = list(version_steps())
    assert order.index("stamp") < order.index("tag it")
    assert order.index("app") < order.index("stamp")


def test_the_stamp_step_runs_the_checked_in_script_with_the_app_token():
    step = version_steps()["stamp"]
    assert "example_pins.py" in step["run"]
    assert "set -euo pipefail" in step["run"]
    assert step["env"]["GH_TOKEN"] == "${{ steps.app.outputs.token }}"
    assert step["env"]["VERSION"] == "${{ steps.v.outputs.next }}"
    assert step["env"]["SHA"] == "${{ github.sha }}"
    assert step["if"] == version_steps()["tag it"]["if"].split(" && steps.stamp")[0]


def test_the_tag_points_at_the_commit_the_stamp_names():
    step = version_steps()["tag it"]
    assert step["env"]["TARGET"] == "${{ steps.stamp.outputs.target }}"
    assert 'object="${TARGET}"' in step["run"]
    assert "GITHUB_SHA" not in step["run"]
    assert "steps.stamp.outputs.target != ''" in step["if"]


def test_the_script_needs_nothing_beyond_the_standard_library():
    """The version job runs the runner's bare `python3`; PyYAML is not promised."""
    done = subprocess.run(
        [sys.executable, "-S", "-c", "import example_pins"],
        cwd=ROOT / "scripts", capture_output=True, text=True,
    )
    assert done.returncode == 0, done.stderr


# ---------------------------------------------------------------------------
# After the publish: every pin a released commit's examples carry is pullable.
# ---------------------------------------------------------------------------

CI_RELEASE = ROOT / ".github" / "workflows" / "ci-release.yaml"


def test_pinned_reads_every_example_pin():
    assert sorted(example_pins.pinned(FILES)) == [
        ("example/application.yaml", "yadgar", "0.3.5"),
        ("example/kind/application.yaml", "yadgar", "0.3.5"),
        ("example/operators-application.yaml", "platform", "0.1.18"),
    ]


def test_pinned_refuses_what_stamp_refuses():
    text = "kind: Application\nspec:\n  source: {chart: yadgar, targetRevision: 0.3.5}\n"
    with pytest.raises(example_pins.Refusal):
        example_pins.pinned({"example/application.yaml": text})


def registry(missing=()):
    seen = []

    def fetch(request):
        seen.append(request.full_url)
        if "/token" in request.full_url:
            return 200, b'{"token": "anonymous"}'
        if any(f"/{name}/manifests/{tag}" in request.full_url for name, tag in missing):
            return 404, b"{}"
        return 200, b'{"layers": [{"digest": "sha256:x"}]}'

    return fetch, seen


def run_published(tmp_path, files, fetch, capsys):
    for path, text in files.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    env = {"REGISTRY": "ghcr.io", "OWNER": "yadgarhq"}
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        rc = example_published.main(fetch=fetch, sleep=lambda _s: None)
    finally:
        os.chdir(cwd)
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return rc, capsys.readouterr().out


def test_every_published_pin_passes(tmp_path, capsys):
    fetch, seen = registry()
    rc, _ = run_published(tmp_path, FILES, fetch, capsys)
    assert rc == 0
    assert any("/yadgarhq/charts/yadgar/manifests/0.3.5" in u for u in seen)
    assert any("/yadgarhq/charts/platform/manifests/0.1.18" in u for u in seen)


def test_an_unpublished_pin_reddens_and_names_it(tmp_path, capsys):
    fetch, _ = registry(missing=[("yadgar", "0.3.5")])
    rc, out = run_published(tmp_path, FILES, fetch, capsys)
    assert rc == 1
    assert "::error::" in out
    assert "yadgar 0.3.5" in out


def test_a_repository_without_examples_asks_the_registry_nothing(tmp_path, capsys):
    fetch, seen = registry()
    rc, _ = run_published(tmp_path, {"chart/Chart.yaml": CHART}, fetch, capsys)
    assert rc == 0
    assert seen == []


def release_jobs():
    return yaml.safe_load(CI_RELEASE.read_text(encoding="utf-8"))["jobs"]


def test_the_release_checks_the_example_pins_after_the_chart_is_published():
    job = release_jobs()["examples"]
    assert "chart" in job["needs"]
    # `always()` because `image` is skipped in a chart-only repository, two hops up.
    assert "always()" in job["if"]
    assert "needs.chart.result == 'success'" in job["if"]
    runs = [s for s in job["steps"] if "run" in s]
    assert any("example_published.py\"" in s["run"] for s in runs)
    assert job["timeout-minutes"] <= 10
