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

  SCOPE. This reverts the whole of yadgarhq/actions#PR: the Basic
  `x-access-token` header and its `::add-mask::` line in `repin.yaml`, the
  rewritten comment, and `scripts/tests/test_git_extraheader_basic.py`.
-->

## What

Reverts yadgarhq/actions#PR. The `open one pull request for the whole bump` step
in `repin.yaml` pushes with `Authorization: Bearer ${GH_TOKEN}` again. The
`::add-mask::` line and `scripts/tests/test_git_extraheader_basic.py` are
deleted.

## Why

The repin push step failed or leaked after the merge.

Expected signature, one of:

- `fatal: unable to access 'https://github.com/...': The requested URL returned error: 400` or `401` in the push step. GitHub changed how git smart-HTTP reads the header.
- The encoded header value showing in a job log. The runner masks it and the explicit `::add-mask::` is belt-and-braces, so this is unexpected.

Observed instead: [FILL IN - paste the failing job URL and the error line].

Note that the Bearer form is the state that failed with `could not read
Username for 'https://github.com'` (yaadgaar 319). Revert only if Basic is
proven worse.

## Changelog

- revert(repin): push with the Bearer header again, reverting the Basic x-access-token change

## Verification

A repin run in a consumer reaches `gh pr create` after the push step. The
`python3 -m pytest scripts/tests/ -q` suite passes after this revert only if
`scripts/tests/test_git_extraheader_basic.py` is deleted in the same commit.
That test asserts the Basic wiring this revert removes. Say so here.

## Risk

Brings back the 401 on every repin push (yaadgaar 319). Undo: re-apply
yadgarhq/actions#PR.

**The D80 question.** If the operator ran a different ingress, a different
cloud, or a different set of operators, would this still be correct, and would
it still be secure? Yes to both. This revert touches the one credential the
push step holds, the App installation token. It does not widen where the token
goes.
