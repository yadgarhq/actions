<!--
  A PRE-WRITTEN REVERT BODY, required by ADR-0583.

  WHY IT EXISTS. Consumers call `ci-pr.yaml` and `ci-release.yaml` at `@main`
  (ADR-0981 keeps that), so this change reaches every consumer the moment it
  merges, with no adoption step. The `main` ruleset forbids a direct push, so a
  rollback is a pull request, and GitHub's generated revert body ("Reverts
  yadgarhq/actions#N") carries none of the five template sections and is
  refused by `ci / template`.

  HOW TO USE IT. Open the revert pull request, then paste this file as the body
  in place of the generated line. Replace every `#PR` with the number of the
  pull request being reverted. Fill in the bracketed slot under `## Why` with
  the signature you actually saw. Delete this comment. Change nothing else.

  SCOPE. This reverts the whole of yadgarhq/actions#PR: the `own_sha` steps, the
  `ref: ${{ steps.own_sha.outputs.sha }}` on every second checkout of
  `yadgarhq/actions`, the one summary line per run, the rewritten comments, and
  `scripts/tests/test_own_sha_checkout.py`. Every second checkout goes back to
  `ref: main`.
-->

## What

Reverts yadgarhq/actions#PR. Every second checkout of `yadgarhq/actions` in
`ci-pr.yaml`, `ci-release.yaml`, `repin.yaml` and `seed-declaration.yaml` goes
back to `ref: main`. The `own_sha` check steps, the run-summary line and
`scripts/tests/test_own_sha_checkout.py` are deleted.

## Why

A job that checks out `yadgarhq/actions` started to fail in a consumer after
the merge, before its gate ran.

Expected signature, one of:

- `::error::job.workflow_sha is not a 40-hex commit SHA: '<value>'. There is no fallback to main (ADR-0981).` in the step `the actions commit this job runs (ADR-0981)`. GitHub stopped exposing `job.workflow_sha`, or exposed it in a different form, so the read through `fromJSON(toJSON(job))` came back empty or wrong.
- `fatal: reference is not a tree: <sha>`, `couldn't find remote ref`, or `The process '/usr/bin/git' failed with exit code 128` in the `actions/checkout` step after `own_sha`. The commit exists in the workflow file's history but the checkout cannot fetch it.
- `.ci-actions/scripts/<name>.py: No such file` or `the gate is missing at .ci-actions/...` in a step after the checkout. The checkout fetched a commit that does not carry the script the job expects.

Observed instead: [FILL IN — paste the failing job URL and the error line].

The check fails closed by design, so this failure blocks `ci / passed` in every
consumer at once. It does not let an unreviewed script run.

## Changelog

- revert(ci): check out yadgarhq/actions at main again in every second checkout, reverting the ledger 1756 S2a own-SHA change

## Verification

From a consumer, the job that was red runs its second checkout at `main` and
goes green on the same head SHA. The checkout step log shows
`git ... fetch ... origin +refs/heads/main*`. `yadgarhq/actions` cannot show
either state: on its own runs every converted checkout is skipped by
`github.repository != 'yadgarhq/actions'`.

`python3 -m pytest scripts/tests/ -q` passes after this revert only if
`scripts/tests/test_own_sha_checkout.py` is deleted in the same commit. That
test asserts the own-SHA wiring this revert removes. Say so here.

## Risk

Brings back the split-brain ADR-0981 rules out: the workflow file runs at the
commit the caller resolved `@main` to, while its scripts come from whatever
`main` is when the second checkout runs. A push to `main` during a run can
pair a workflow with scripts it was never tested against. That is the known
state before yadgarhq/actions#PR and it ran for months. Record what was
observed before closing, because ADR-0981 still stands and the fix must come
back.

Undo: re-apply yadgarhq/actions#PR.

**The D80 question.** If the operator ran a different ingress, a different
cloud, or a different set of operators, would this still be correct, and would
it still be secure? Yes to both. This revert touches no trust boundary. It
changes which commit of a public repository a CI step reads. A wrong commit can
cause a false red or a false green in a gate, and it grants nothing.
