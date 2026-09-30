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

    @staticmethod
    def body(args):
        with open(args[args.index("--input") + 1], encoding="utf-8") as fh:
            return json.load(fh)

    def __call__(self, args):
        self.calls.append(list(args))
        path = next(a for a in args if a.startswith("repos/"))
        method = args[args.index("-X") + 1] if "-X" in args else "GET"
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
    assert repo.calls == [], "a repository without examples must not reach the API"


def test_main_with_examples_already_pinned_makes_no_commit(tmp_path):
    stamped = {**FILES, **example_pins.stamp(FILES, "0.3.9", CHART)}
    repo = Repo()
    rc, out = run_main(tmp_path, repo, {"chart/Chart.yaml": CHART, **stamped})
    assert rc == 0
    assert "target=sha-0\n" in out
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
    assert "target=" not in out or "target=\n" in out


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
