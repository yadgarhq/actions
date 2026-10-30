<!--
  A PRE-WRITTEN REVERT BODY, required by ADR-0583.

  WHY IT EXISTS. Consumers call `ci-pr.yaml` at `@main`, and `ci / passed`
  reads its conditions from that file, so this change reaches every consumer
  the moment it merges. The `main` ruleset forbids a direct push, so a
  rollback is a pull request, and GitHub's generated revert body carries none
  of the five template sections and is refused by `ci / template`.

  HOW TO USE IT. Open the revert pull request, then paste this file as the body
  in place of the generated line. Replace every `#PR` with the number of the
  pull request being reverted. Fill in the bracketed slot under `## Why` with
  the signature you actually saw. Delete this comment. Change nothing else.

  SCOPE. This reverts the whole of yadgarhq/actions#PR: the
  `hook_tools_adopted` job in `ci-pr.yaml` and its entry in `passed.needs`;
  `scripts/hook_tools_adopted.py`; `private_consumers` and its `about` line in
  `classes.json`; the prose in `versions.yaml` and `hooks/tool_version.py`; and
  the test changes (`test_hook_tools_adopted.py`, the job lists in
  `test_ci_verdict.py` and `test_config_only.py`).
-->

## What

Reverts yadgarhq/actions#PR. Pull requests to `yadgarhq/actions` no longer run
the `hook_tools_adopted` job, and `ci / passed` no longer needs it. A move of a
hook-asserted tool's `version` is again refused by nothing.

## Why

`hook_tools_adopted` turned `ci / passed` red where it should be green.

Expected signature, one of:

- `ci / passed` in a consumer names `hook_tools_adopted` as reporting a result its condition did not ask for.
- `::error::` from `hook_tools_adopted.py` in a pull request that moves no hook-asserted tool, or one where every consumer accepts the new version.
- `::error::HEAD is not the pull request's merge commit` on an ordinary pull request.

Observed instead: [FILL IN — paste the failing job URL and the error line].

A consumer named as behind is NOT a reason to revert: that red is the check
working. Release the version in `accept` first and let every adoption pull
request merge, or update `private_consumers` in `classes.json`.

## Changelog

- revert(ci-pr): drop the hook_tools_adopted check on hook-asserted tool moves, reverting yaadgaar 231

## Verification

`ci / passed` goes green on the same head SHA with no `hook_tools_adopted`
row. `python3 -m pytest scripts/tests/ -q` passes with the test files reverted
in the same commit; the revert of yadgarhq/actions#PR does that.

## Risk

Brings back what yaadgaar 231 closed: a one-step move on main reddens every
consumer whose rev does not accept the new version (ADR-0983). Record what was
observed before closing, because ADR-0983 still stands and the check must come
back.

Undo: re-apply yadgarhq/actions#PR.

**The D80 question.** If the operator ran a different ingress, a different
cloud, or a different set of operators, would this still be correct, and would
it still be secure? Yes to both. It touches no trust boundary and passes no
secret.
