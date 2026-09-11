<!--
  A PRE-WRITTEN REVERT BODY, required by ADR-0583.

  WHY IT EXISTS. Consumers pin the shared workflows at `@main`, so a change to
  `.github/workflows/ci-pr.yaml` reaches all eighteen of them the moment it
  merges, with no adoption step. The `main` ruleset forbids a direct push, so a
  rollback must be a pull request — and GitHub's auto-generated revert body is
  the single line "Reverts yadgarhq/actions#N", which carries none of the five
  template sections and is refused by `ci / template`. Writing this while calm
  costs minutes. Writing it with eighteen repositories red costs the incident.

  HOW TO USE IT. Open the revert pull request, then paste this file as the body
  in place of the generated line. Replace every `#PR` with the number of the pull
  request being reverted, and fill in the bracketed slot under `## Why` with the
  signature you actually saw. Delete this comment. Change nothing else.

  SCOPE. This reverts ONE line of `ci-pr.yaml` — the capture in the
  `the ignored suites that need a private-CA engine` step. It does NOT revert the
  `hooks/no_test_skips.py` or `.pre-commit-hooks.yaml` changes from the same pull
  request: those reach consumers only when each bumps its own `rev:`, so they are
  not an incident and must not be dragged into an urgent rollback.
-->

## What

Reverts the `ci-pr.yaml` half of yadgarhq/actions#PR — the private-CA step's
`cargo test ... -- --ignored` capture goes back to `2>&1 | tee "$out"`, merging
cargo's stderr into the file the anchored `grep -E '^test result:'` reads. The
`hooks/no_test_skips.py` and `.pre-commit-hooks.yaml` changes from that pull
request are deliberately left in place.

## Why

The `ignored` step began failing in a consumer after the merge.

Expected signature, one of:

- `printed no summary this step could read, so nothing here says the suite ran: <no test result line>` — the step read no summary at all.
- `executed no ignored test (...)` — a summary was read, and its counts did not satisfy the floor.

Observed instead: [FILL IN — paste the failing job URL and the error line].

The step fails closed, so the failure mode being reverted is a FALSE RED: `ci /
test` blocks a merge in a repository whose private-CA suite actually ran. Only
`yadgarhq/store` reaches this step today, because it is the only repository whose
test source names `YADGAR_TEST_TLS_DSN`; a consumer that does not name it skips
the step and is unaffected.

## Changelog

- revert(ci): restore the private-CA step's `2>&1` capture in `ci-pr.yaml`, reverting the ledger 842 change

## Verification

`ci / test` is green again in the consumer named above, on the same head sha that
was red. `yadgarhq/actions` has no `Cargo.toml`, so its own `ci / test` never
runs and cannot demonstrate either state — the evidence is the consumer's job,
not this repository's.

`python3 -m pytest scripts/tests/ -q` reports one failure after this revert,
`test_no_cargo_capture_merges_stderr_into_the_anchored_parse`, and that is the
gate working rather than collateral damage: it is the test that asserts no
`cargo test` capture in `ci-pr.yaml` merges stderr into an anchored parse. Delete
that test in the same commit as the revert and say so here, or the revert cannot
merge.

## Risk

Restores the latent defect ledger 842 describes: libtest writes `test result:` to
stdout while cargo writes `Compiling`/`Running`/`Finished` to stderr, so merging
the two descriptors lets a progress line land in front of an anchored summary and
hide it. The consequence is a false red on a healthy run, which is the same class
as whatever prompted this revert — so record what was observed before closing,
because reverting to a known defect to escape an unknown one is a trade that
needs a follow-up entry.

Undo: re-apply yadgarhq/actions#PR.

**The D80 question.** If the operator ran a different ingress, a different cloud,
or a different set of operators, would this still be correct — and would it still
be secure? Yes to both, and this change touches no trust boundary. It moves one
file descriptor in a CI step that reads a test summary. It reaches no network
beyond the runner, names no cloud, and its only property is whether a red is
true. A wrong verdict here blocks a merge; it grants nothing.
