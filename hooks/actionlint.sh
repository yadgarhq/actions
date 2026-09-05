#!/usr/bin/env bash
#
# actionlint, with its one unenforced dependency enforced.
#
# actionlint pipes every `run:` block to shellcheck, and SILENTLY DISABLES that
# whole rule class when the binary is absent: a workflow whose only defect is
# SC2086 exits 0 and prints nothing. So a bare `actionlint` entry reports Passed
# for two different states — workflows are clean, or workflows are clean except
# for a rule class nobody ran.
#
# That is the defect this repository already closed for actionlint's own
# absence, one level down. A named check that every repository references has to
# mean one thing, and "Passed, having checked nothing" is not a thing it can
# mean. So this hook refuses instead.
#
# THE PROBE IS POSITIVE: it feeds a stub whose only defect is SC2086 and
# requires SC2086 in the answer. It does NOT match on actionlint's own
# `Rule "shellcheck" was disabled` line, and the difference is the whole point.
# Matching that string is fail-OPEN in every direction the string can move —
# a reworded message, a `-verbose` output change, a config error that stops
# actionlint before it reports rule state, and the hook goes back to reporting
# Passed having checked nothing, which is the exact failure it exists to close.
# Requiring the finding is fail-CLOSED: anything that stops shellcheck from
# running also stops SC2086 from appearing, and the hook refuses.
#
# THE PROBE ASKS actionlint, NOT $PATH, and that distinction is load-bearing
# rather than fussy. nixpkgs ships actionlint as a wrapper that puts shellcheck
# on an internal PATH for the wrapped binary alone, so `command -v shellcheck`
# reports "missing" on a machine where the rule class is in fact running.
# Measured: the wrapper at .../actionlint-1.7.12/bin/actionlint prepends the
# store path of that binary itself. A guard written against $PATH would fail
# for the developers most likely to have the tool.
#
# The refusal prints the probe's own output, because fail-closed means this
# hook also refuses for reasons that are not a missing shellcheck — a malformed
# `.github/actionlint.yaml` in the consuming repository is the likely one, and
# it exits non-zero without ever reaching the shellcheck rule. The captured
# output is what tells those two apart, so it is not decoration.
#
# BEFORE REWRAPPING THESE COMMENTS: a comment of the form `# shellcheck ...` is
# read as one of shellcheck's own directives and fails to parse (SC1072/SC1073),
# so that word must never open a line in this file. Found by running shellcheck
# on this script, which is the only reason it is written down.
#
# The probe is a stub workflow on stdin, so it costs a millisecond and its
# answer cannot be affected by the repository's own workflows. It is a QUOTED
# heredoc so the stub's own `$GITHUB_ENV` reaches actionlint unexpanded — and
# so that this file needs no `disable=SC2016` directive, which it cannot carry
# for the parsing reason above.
set -uo pipefail

# Kept identical to what a bare `language: system` entry produced before this
# script existed. Under `language: script` an absent binary would otherwise
# surface as this file's exit 127, which names nothing.
if ! command -v actionlint >/dev/null 2>&1; then
  echo "Executable \`actionlint\` not found" >&2
  exit 1
fi

# `echo $GITHUB_ENV` is an unquoted expansion and nothing else, so shellcheck
# reports SC2086 on it and actionlint reports nothing of its own. Measured
# against actionlint 1.7.12 and shellcheck 0.11.0: with shellcheck the probe
# exits 1 and names SC2086; without it the probe exits 0 and prints nothing.
probe=$(actionlint -verbose -stdin-filename probe.yaml - 2>&1 <<'STUB'
on: push
jobs:
  probe:
    runs-on: ubuntu-latest
    steps:
      - run: echo $GITHUB_ENV
STUB
) || true

case "$probe" in
*SC2086*) ;;
*)
  cat >&2 <<'MSG'
actionlint did not report SC2086 on a stub whose only defect is SC2086, so its
shellcheck rule class is not running. A bare `actionlint` would exit 0 here and
report Passed, having checked no `run:` block at all. This hook refuses instead,
because a green tick has to mean one thing.

The usual cause is that shellcheck is not installed. Every `run:` block in this
repository then goes unchecked — SC2086 word splitting, SC2046 unquoted
substitution, and the rest. actionlint's native rules (expression, context,
unknown-key) are unaffected, which is why nothing else looks wrong.

  apt-get install shellcheck
  brew install shellcheck
  nix profile install nixpkgs#shellcheck

https://github.com/koalaman/shellcheck#installing

If shellcheck IS installed, the probe output below names the real cause — a
malformed `.github/actionlint.yaml` stops actionlint before any rule runs.

--- probe output ---
MSG
  printf '%s\n' "$probe" >&2
  exit 1
  ;;
esac

exec actionlint "$@"
