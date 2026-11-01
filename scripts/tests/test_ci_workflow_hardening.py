"""Yaadgaar 243 (members 320, 357). Three hardening properties of the shared
workflows, each pinned by YAML shape and each paired with a mutation that must
go red:

* the release image build carries `revision` and `version` as labels AND
  annotations, so a live digest maps to its commit;
* `ci-pr.yaml` bounds every job and the `install protoc` steps, and retries
  apt a bounded number of times;
* the chart `package and push` retries `helm package -u` and `helm push`
  separately, bounded, logging in once.

Run: python3 -m pytest scripts/tests/ -q
"""

import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CI_RELEASE = ROOT / ".github" / "workflows" / "ci-release.yaml"
CI_PR = ROOT / ".github" / "workflows" / "ci-pr.yaml"

REVISION = "org.opencontainers.image.revision=${{ github.sha }}"
VERSION = "org.opencontainers.image.version=${{ needs.detect.outputs.version }}"
LOOP = re.compile(r"for\s+\w+\s+in\s+1\s+2\s+3\s*;\s*do")


def jobs(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"]


def lines(value):
    return [line.strip() for line in str(value or "").splitlines() if line.strip()]


# ------------------------------------------------------------ image labels


def push_step(doc=None):
    doc = doc or jobs(CI_RELEASE)
    steps = [s for s in doc["image"]["steps"] if s.get("id") == "push"]
    assert len(steps) == 1
    return steps[0]["with"]


def label_problems(with_):
    found = []
    for key in ("labels", "annotations"):
        have = lines(with_.get(key))
        for want in (REVISION, VERSION):
            if want not in have:
                found.append(f"{key} lack {want}")
    return found


def test_image_carries_revision_and_version_as_labels_and_annotations():
    assert label_problems(push_step()) == []


def test_mutation_labels_without_revision_is_red():
    assert label_problems({"labels": VERSION, "annotations": f"{REVISION}\n{VERSION}"})


def test_mutation_annotations_missing_is_red():
    assert label_problems({"labels": f"{REVISION}\n{VERSION}"})


# --------------------------------------------------------- ci-pr timeouts


def protoc_steps(doc=None):
    doc = doc or jobs(CI_PR)
    return [
        (name, s)
        for name, job in doc.items()
        for s in job.get("steps", [])
        if s.get("name") == "install protoc"
    ]


def timeout_problems(doc):
    found = []
    for name, job in doc.items():
        if "timeout-minutes" not in job and "uses" not in job:
            found.append(f"job {name} has no timeout-minutes")
    steps = protoc_steps(doc)
    if len(steps) != 2:
        found.append(f"expected 2 install protoc steps, found {len(steps)}")
    for name, step in steps:
        t = step.get("timeout-minutes")
        if not isinstance(t, int) or t > 5:
            found.append(f"{name}: install protoc timeout-minutes is {t!r}")
        run = step.get("run", "")
        if not LOOP.search(run):
            found.append(f"{name}: install protoc apt is not in a loop bounded at 3")
        if "Acquire::Retries" not in run or "Acquire::http::Timeout" not in run:
            found.append(f"{name}: install protoc sets no apt retries/timeout")
        if not re.search(r"^\s*exit\s+1|\[\s*\"?\$\w+\"?\s*=\s*3\s*\]\s*&&\s*exit\s+1", run, re.M):
            found.append(f"{name}: install protoc swallows a final failure")
    return found


def test_ci_pr_jobs_and_protoc_steps_are_bounded():
    assert timeout_problems(jobs(CI_PR)) == []


def test_mutation_job_without_timeout_is_red():
    doc = jobs(CI_PR)
    del doc["test"]["timeout-minutes"]
    assert any("job test" in p for p in timeout_problems(doc))


def test_mutation_protoc_without_timeout_is_red():
    doc = jobs(CI_PR)
    for _, step in protoc_steps(doc):
        step.pop("timeout-minutes", None)
    assert any("timeout-minutes" in p for p in timeout_problems(doc))


def test_mutation_protoc_unbounded_apt_is_red():
    doc = jobs(CI_PR)
    for _, step in protoc_steps(doc):
        step["run"] = "sudo apt-get update -qq\nsudo apt-get install -y protobuf-compiler\n"
    assert any("bounded at 3" in p for p in timeout_problems(doc))


# ----------------------------------------------------- chart publish retry


def chart_steps(doc=None):
    doc = doc or jobs(CI_RELEASE)
    return doc["chart"]["steps"]


def publish_problems(steps):
    found = []
    pkg = [s for s in steps if s.get("name") == "package"]
    push = [s for s in steps if s.get("name") == "push"]
    if len(pkg) != 1 or len(push) != 1:
        return ["expected one `package` and one `push` step"]
    for label, step, call in (("package", pkg[0], "helm package chart -u"), ("push", push[0], "helm push")):
        run = step.get("run", "")
        if call not in run:
            found.append(f"{label}: no `{call}`")
        if not LOOP.search(run):
            found.append(f"{label}: no loop bounded at 3 attempts")
        if "&& break" not in run:
            found.append(f"{label}: a success does not leave the loop")
        if not re.search(r"\bsleep\b", run):
            found.append(f"{label}: no backoff")
        if not re.search(r"exit\s+1", run):
            found.append(f"{label}: no non-zero exit after the final failure")
        if not isinstance(step.get("timeout-minutes"), int):
            found.append(f"{label}: no timeout-minutes")
    if push[0].get("run", "").count("helm registry login") != 1:
        found.append("push: login is not done exactly once")
    if "helm registry login" in pkg[0].get("run", ""):
        found.append("package: logs in")
    return found


def test_chart_package_and_push_retry_separately_and_log_in_once():
    assert publish_problems(chart_steps()) == []


def test_chart_steps_keep_their_order():
    names = [s.get("name") for s in chart_steps()]
    assert names.index("package") + 1 == names.index("push")


def test_mutation_bare_push_is_red():
    steps = chart_steps()
    for s in steps:
        if s.get("name") == "push":
            s["run"] = 'helm registry login x\nhelm push ./*.tgz oci://x\n'
    assert publish_problems(steps)


def test_mutation_loop_that_never_breaks_is_red():
    steps = chart_steps()
    for s in steps:
        if s.get("name") == "push":
            s["run"] = s["run"].replace("&& break", "&& true")
    assert any("leave the loop" in p for p in publish_problems(steps))


def test_mutation_bare_package_is_red():
    steps = chart_steps()
    for s in steps:
        if s.get("name") == "package":
            s["run"] = "helm package chart -u\n"
    assert publish_problems(steps)
