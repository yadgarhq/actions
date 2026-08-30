<!--
  These sections are CHECKED BY CI, not suggested. `ci / passed` fails when one
  is missing or empty, so a template nobody fills in cannot merge.

  Delete nothing. Two lines each is usually enough.
-->

## What

<!-- What changed, in a sentence or two. Not a restatement of the diff. -->

## Why

<!--
  The reason, not the mechanism. If this follows from a decision or an open
  question in the record, name it (D42, O19) — that is what makes the record
  worth keeping.
-->

## Changelog

<!--
  One bullet per user-visible change, each in Conventional Commits form. These
  survive as the squash commit body, so they become the repository's history —
  and the version bump is derived from them, which is why the format is checked.

    - fix: a metadata leak in the audit relay that logged full request bodies
    - feat(recall): return partial results when one provider is unhealthy
    - feat!: drop the by-name arm of GetWikiPage

  `!` marks a breaking change. Types: build, chore, ci, docs, feat, fix, perf,
  refactor, revert, style, test.
-->

## Verification

<!--
  How you know it works. "CI is green" counts only where CI covers it; say so if
  it does not, and say what you ran instead.
-->

## Risk

<!--
  What breaks if this is wrong, and how it is undone. "None" is a valid answer
  and worth writing rather than leaving blank.
-->
