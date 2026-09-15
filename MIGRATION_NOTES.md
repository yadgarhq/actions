# Migration notes — the `version` job goes loud (ledger 935)

Everything here is for the OPERATOR to run or decide. Nothing in this file is
done by the pull request that carries it.

## 1. The revert body, pre-written (ADR-0583)

ADR-0583 requires this before the change merges, not after: every consumer pins
`ci-pr.yaml` at `@main`, the `main` ruleset forbids a direct push, and GitHub's
auto-generated revert body is the single line `Reverts yadgarhq/actions#N` —
which carries none of the five template sections and is refused by
`ci / template`. Without this, a rollback starts with authorship under time
pressure across nineteen repositories.

ADR-0583 says the body lives "in the repository that owns the plan", which for
ledger 935 is `yadgarhq/docs`. It is committed here as well so that a person
reverting has it in the repository they are reverting. If the operator wants the
canonical copy in `docs`, copy it there — do not delete this one before the
change has been live long enough to stop being reverted.

Use it verbatim as the body of the revert pull request:

```
| ## What
|
| Reverts the `version` job's six loud refusals and its bot-authored dependency
| synthesis, restoring the behaviour in which every no-tag outcome except a
| failed Changelog gate leaves the job green.
|
| ## Why
|
| The change reaches all nineteen consumers at `@main`, so a defect in it is an
| estate-wide red on every merge to `main`. Reverting is cheaper than diagnosing
| under that load. Ledger 935 owns the re-landing.
|
| ## Changelog
|
| - revert: the `version` job's loud refusals and its bot dependency synthesis
|
| ## Verification
|
| `python3 -m pytest scripts/tests/ -q` and `pre-commit run --all-files` on the
| revert commit. The post-merge `main / version` run on this repository is the
| real check: it must report a version or a green skip, not a red.
|
| ## Risk
|
| Reverting restores a known silence rather than an unknown state, so the risk is
| the defect ledger 935 exists to close, not a new one. A dependency merge stops
| cutting a tag again, and `yadgarhq/yadgar` stops reporting that its baseline is
| unusable.
|
| This change touches no trust boundary. It removes exit codes from a job that
| holds a release App token; it grants nothing and reads nothing new, so it is
| correct and secure under any ingress, cloud or operator set.
```

Strip the `| ` gutter before pasting. It is there because `ci / template` splits
a body on any `#` heading and does not strip fenced code blocks.

## 2. `yadgarhq/actions` carries a stray `v1` tag — decide what to do with it

`v1` is an **annotated** tag object at `71413bf9`, 53 commits behind `main`, while
`v1.22.0` is at `main`. So `git describe --tags --abbrev=0 --match 'v*'` returns
`v1.22.0` today and this repository does not redden.

That is safe by accident. Measured in a scratch clone: `git describe` breaks a
DISTANCE TIE in favour of the newer annotated tag. A fresh annotated tag at `main`
beat `v1.22.0`; a lightweight one lost to it. So if anyone ever moves or recreates
`v1` at a release commit, `v1` wins, `SEMVER` rejects it, and the repository that
publishes this workflow to all nineteen consumers reds on every merge.

Nothing reads `v1`: every consumer pins `ci-pr.yaml@main`, a branch. Deleting it
is a destructive remote-tag change and is the operator's call, not the agent's.
The `release-tags` ruleset blocks `deletion` on `refs/tags/v*` with no bypass
actors, so it needs a ruleset change first or must be left alone:

```
gh api -X DELETE repos/yadgarhq/actions/git/refs/tags/v1
```

Leaving it is also defensible — it is harmless where it sits. What is not
defensible is moving it.

## 3. `yadgarhq/yadgar` will red on its next merge, by design (ledger 936)

`yadgar`'s seven tags are all `v0.1.0aN`, so it has no plain-semver baseline and
has cut zero automatic tags ever. Under this change its next merge to `main`
fails the `version` job with a message asking for a first plain tag. That is the
intended one-time cost of ledger 936, and the repair is a single hand-cut tag.

**It must be an ANNOTATED tag, which takes two calls.** `POST /git/refs` on its
own makes a LIGHTWEIGHT tag, and item 2 above explains why that is not
interchangeable: `git describe` breaks a distance tie in favour of the newer
ANNOTATED tag, and a lightweight one LOSES to an annotated one at the same
commit. It is safe today only because `v0.1.0a7` sits 7 commits behind HEAD, so
distance decides rather than the tie rule. Cut the plain tag at the same commit
as an alpha and a lightweight tag silently fails to become the baseline. This is
the same two-call shape the `tag it` step uses, and for the same reason:

```
obj=$(gh api repos/yadgarhq/yadgar/git/tags \
        -f tag=v0.1.1 -f message='v0.1.1' \
        -f object=<main sha> -f type=commit --jq .sha)
gh api repos/yadgarhq/yadgar/git/refs -f ref=refs/tags/v0.1.1 -f sha="$obj"
```

Pick the number deliberately — it becomes the baseline every later version counts
from. `v0.1.0` is unavailable in spirit: `v0.1.0a7` already claims that release.

No other repository is in this state. All nineteen were swept: twelve carry only
plain `vX.Y.Z` tags, five carry none at all and stay green by design, `actions`
is item 2 above, and `yadgar` is this item.

## 4. Two dependency fixes are stranded and this change does not release them

Nothing re-runs the `version` job for an already-merged commit, so the synthesis
this pull request adds applies only to merges from now on.

- `yadgarhq/lifecycle` is one commit ahead of `v0.2.17`: `96692cc3`,
  `build(deps): bump sha2 from 0.10.9 to 0.11.0 (#24)`. **Pending a decision, not
  a formality.** `sha2` 0.10 → 0.11 is the breaking crypto-crate family this
  estate has been handling deliberately, so releasing it is a judgement call.
  Seven consumers pinned at `v0.2.17` do not carry this bump.
- `yadgarhq/telemetry` is one commit ahead of `v0.1.17`: `2f311b5d`,
  `build(deps): bump metrics-exporter-prometheus from 0.17.2 to 0.18.3 (#26)`.

Each needs a hand-cut tag if it is to ship. That is separate follow-up work.
