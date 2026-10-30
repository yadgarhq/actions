<!--
  A PRE-WRITTEN REVERT BODY, required by ADR-0583.

  WHY IT EXISTS. Consumers call `repin.yaml` at `@main`, so this change reaches
  every consumer the moment it merges. The `main` ruleset forbids a direct push,
  so a rollback is a pull request, and GitHub's generated revert body ("Reverts
  yadgarhq/actions#N") carries none of the five template sections and is refused
  by `ci / template`.

  HOW TO USE IT. Open the revert pull request, then paste this file as the body
  in place of the generated line. Replace every `#PR` with the number of the
  pull request being reverted. Fill in the bracketed slot under `## Why` with
  the signature you actually saw. Delete this comment. Change nothing else.

  SCOPE. This reverts the whole of yadgarhq/actions#PR: the org-default
  fallback in `scripts/repin.py` and `scripts/fan_out.py`, the `.github` entry
  in `classes.json` `exempt`, the reworded comment in `repin.yaml`, the README
  and `pr_body.py` wording, the test fixture, and actions' own two template
  copies, which come back.
-->

## What

Reverts yadgarhq/actions#PR. `fan_out.py` and `repin.py` read the pull request
template only from the target repository's `.github/pull_request_template.md`
again, and refuse a repository without one. `classes.json` no longer exempts
`.github`. Actions' own `pull_request_template.md` and
`.github/pull_request_template.md` come back.

## Why

The org-default fallback failed after the merge.

Expected signature, one of:

- `the organisation default (...) could not be read` in a fan-out row or a
  repin job, while `yadgarhq/.github` is reachable.
- A bot pull request body built from the wrong template.

Observed instead: [FILL IN - paste the failing job URL and the error line].

## Changelog

- revert(templates): read the pull request template only from the target repository again, reverting the org-default fallback

## Verification

`python3 -m pytest scripts/tests/ -q` passes after the revert. A fan-out dry run
from main reports no row with the org-default refusal.

## Risk

A consumer whose own template was already deleted (the
`chore/use-org-pr-template` pull requests) gets a refusal again from fan-out and
repin. Before this revert merges, restore `.github/pull_request_template.md` in
every such consumer. Without `.github` in `exempt`, every fan-out run also
reports `.github` as an error and exits 1. Undo: re-apply yadgarhq/actions#PR.

**The D80 question.** If the operator ran a different ingress, a different
cloud, or a different set of operators, would this still be correct, and would
it still be secure? Yes to both. The revert removes one unauthenticated HTTPS
read of a public file. It adds no credential and no network path.
