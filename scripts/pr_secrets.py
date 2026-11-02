#!/usr/bin/env python3
"""YAADGAAR 518 (h). No secret but GITHUB_TOKEN on the pull request path.

WHY. A dependabot pull request's CI runs in GitHub's dependabot sandbox, which
hands it no repository secret. ADR-1119's sweeper REFRESHES such a pull request
with update-branch, so the newest push is the App's, and the `synchronize` run
it triggers is outside that sandbox. Dependabot-authored code then runs with
every secret a pull-request job reads. The refresh is safe only while no job
reachable from `pull_request`, `pull_request_target` or `workflow_run` reads a
secret other than `GITHUB_TOKEN`. This script fails when one does.

WHAT IT READS. Every workflow under `<root>/.github/workflows` that one of those
events triggers, and every reusable workflow its jobs call: `./.github/...` in
the same tree, `yadgarhq/actions/.github/workflows/<file>@<ref>` in the actions
tree (`--actions`, by default the tree this script is in), read at that tree's
commit, not at `<ref>`. Any other call cannot be read and is a problem.

REACHABLE. A job is reachable from an event unless its `if:` PROVABLY excludes
that event: a top-level `&&` conjunct `github.event_name == '<other>'`,
`github.event_name != '<event>'`, `startsWith(github.ref, 'refs/tags/')` or
`false`. Anything else, `||` at the top level included, is reachable: the
guard fails closed. A called workflow's `if:` is judged with the caller's
event, which is what GitHub gives it.

A READ is any use of the `secrets` context in an expression of a reachable job
(`${{ ... }}` anywhere in the job, and every `if:`) or of the workflow-level
`env:` of a workflow the event reaches, other than
`secrets.GITHUB_TOKEN`, and `secrets: inherit`. A caller's `secrets:` mapping
counts: it hands the secret to the pull request run.

    pr_secrets.py ROOT [--actions DIR]     exit 1 and one ::error:: line per problem
"""

import argparse
import re
import sys
from pathlib import Path

import yaml

PR_EVENTS = ("pull_request", "pull_request_target", "workflow_run")
HERE = Path(__file__).resolve().parents[1]
EXPR = re.compile(r"\$\{\{(.*?)\}\}", re.S)
SECRETS = re.compile(r"(?<![\w.\-])secrets(?![\w\-])", re.I)
TOKEN = re.compile(r"\s*\.\s*GITHUB_TOKEN(?![\w\-])", re.I)
LOCAL = re.compile(r"\./(\.github/workflows/[A-Za-z0-9_-][A-Za-z0-9_.-]*\.ya?ml)")
ACTIONS = re.compile(r"yadgarhq/actions/(\.github/workflows/[A-Za-z0-9_-][A-Za-z0-9_.-]*\.ya?ml)@[^\s/]+")
EVENT_IS = re.compile(r"github\.event_name\s*(==|!=)\s*'([^']*)'", re.I)
TAGS = re.compile(r"startsWith\(\s*github\.ref\s*,\s*'refs/tags/'\s*\)", re.I)


def conjuncts(text):
    """The top-level `&&` parts of an expression, or None when a top-level `||`
    makes them unprovable."""
    parts, depth, quote, start, i = [], 0, False, 0, 0
    while i < len(text):
        c = text[i]
        if quote:
            if c == "'":
                if text[i + 1:i + 2] == "'":
                    i += 1
                else:
                    quote = False
        elif c == "'":
            quote = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif depth == 0 and text[i:i + 2] == "||":
            return None
        elif depth == 0 and text[i:i + 2] == "&&":
            parts.append(text[start:i])
            start = i + 2
            i += 1
        i += 1
    return parts + [text[start:]]


def excluded(cond, event):
    """Whether the `if:` `cond` proves the job never runs for `event`."""
    if isinstance(cond, bool):
        return not cond
    if not isinstance(cond, str):
        return False
    text = cond.strip()
    if text.startswith("${{") and text.endswith("}}"):
        text = text[3:-2]
    parts = conjuncts(text)
    for part in parts or ():
        part = part.strip()
        if part.lower() == "false" or TAGS.fullmatch(part):
            return True
        m = EVENT_IS.fullmatch(part)
        if m and (m.group(2).lower() != event) == (m.group(1) == "=="):
            return True
    return False


def strings(node, key=None):
    """Every (key, string) pair in a job, the key being the mapping key above it."""
    if isinstance(node, str):
        yield key, node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from strings(v, k)
    elif isinstance(node, list):
        for v in node:
            yield from strings(v, key)


def reads(job):
    """Every secret this job reads, by the text that reads it."""
    found = []
    if job.get("secrets") == "inherit":
        found.append("secrets: inherit")
    for key, text in strings(job):
        exprs = EXPR.findall(text)
        if key == "if" and "${{" not in text:
            exprs.append(text)
        for expr in exprs:
            for m in SECRETS.finditer(expr):
                if not TOKEN.match(expr, m.end()):
                    found.append(expr.strip())
    return found


def load(path):
    try:
        doc = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        return None, f"cannot read or parse: {type(exc).__name__}"
    if not isinstance(doc, dict) or not isinstance(doc.get("jobs"), dict) or not doc["jobs"]:
        return None, "no jobs mapping"
    return doc, None


def triggers(doc):
    on = doc.get(True, doc.get("on"))
    if isinstance(on, str):
        return {on}
    if isinstance(on, (list, dict)):
        return {e for e in on if isinstance(e, str)}
    return set()


def check(root, actions=HERE):
    """Returns `(problems, reached)`: problem lines, and every (workflow path,
    job, event) judged reachable."""
    root, actions = Path(root), Path(actions)
    problems, reached, seen = [], [], set()

    def name(path):
        for base in (root, actions):
            try:
                return str(path.relative_to(base))
            except ValueError:
                pass
        return str(path)

    def visit(path, base, doc, event):
        if (path.resolve(), event) in seen:
            return
        seen.add((path.resolve(), event))
        # The workflow's own `env:` reaches every job in it, so it is read once
        # per workflow the event reaches, a called one included.
        problems.extend(f"{name(path)}: workflow env on {event}: reads {r}"
                        for r in reads({"env": doc.get("env")}))
        for job_name, job in doc["jobs"].items():
            where = f"{name(path)}: job {job_name!r} on {event}"
            if not isinstance(job, dict):
                problems.append(f"{where}: not a mapping")
                continue
            if excluded(job.get("if"), event):
                continue
            reached.append((path, job_name, event))
            problems.extend(f"{where}: reads {r}" for r in reads(job))
            uses = job.get("uses")
            if uses is None:
                continue
            local, remote = LOCAL.fullmatch(str(uses)), ACTIONS.fullmatch(str(uses))
            callee = (base / local.group(1) if local else actions / remote.group(1) if remote else None)
            called, error = load(callee) if callee is not None and callee.is_file() else (None, "missing")
            if called is None:
                problems.append(f"{where}: cannot read the called workflow {uses!r} ({error})")
                continue
            visit(callee, base if local else actions, called, event)

    workflows = root / ".github" / "workflows"
    for path in sorted(list(workflows.glob("*.yaml")) + list(workflows.glob("*.yml"))):
        doc, error = load(path)
        if doc is None:
            problems.append(f"{name(path)}: {error}")
            continue
        for event in PR_EVENTS:
            if event in triggers(doc):
                visit(path, root, doc, event)
    return problems, reached


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("root")
    parser.add_argument("--actions", default=str(HERE))
    args = parser.parse_args(argv)
    problems, reached = check(args.root, args.actions)
    for problem in problems:
        print(f"::error::pull request path reads a secret: {problem}")
    print(f"{len(reached)} job(s) reachable from {', '.join(PR_EVENTS)}; {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
