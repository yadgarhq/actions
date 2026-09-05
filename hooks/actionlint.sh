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
# THE PROBE NEUTRALISES `SHELLCHECK_OPTS`, and that is a correctness fix rather
# than tidiness. SC2086 is emitted at severity `info`, the weakest class there
# is, so a developer carrying `SHELLCHECK_OPTS=--severity=warning` in a profile
# or direnv deletes the finding while the rule class is running perfectly well.
# Measured: `--severity=warning`, `--severity=error` and `--exclude=SC2086` each
# made this hook refuse a clean tree. Only the PROBE is neutralised; the real
# `exec` below keeps honouring the setting, because the developer meant it for
# their own workflows. Measured too, and the reason no wider scrubbing is
# needed: file-based shellcheck config never reaches the probe at all — a
# repository-root `.shellcheckrc`, `$HOME/.shellcheckrc`, `$HOME/.config/`
# and `$XDG_CONFIG_HOME` were all inert. actionlint feeds the script to its
# checker on stdin, and stdin has no directory to anchor an rc lookup to.
# `SHELLCHECK_OPTS` is the single environmental surface that gets through.
#
# The refusal prints the probe's own output, because fail-closed means this hook
# can refuse for a reason that is not a missing shellcheck, and the captured
# output carries the discriminator. It is a one-line test: actionlint reports
# `Rule "shellcheck" was disabled: exec: "shellcheck": executable file not
# found in $PATH` when the BINARY is missing, and omits that line entirely when
# the binary ran and the finding was merely suppressed.
#
# WHAT DOES NOT CAUSE A REFUSAL, measured rather than assumed, because an
# earlier draft of this file asserted the opposite in the message it prints: a
# consuming repository's `.github/actionlint.yaml` cannot affect the probe. Not
# its `paths:`/`ignore:` rules, and not a syntactically malformed file — the
# same config that hard-errors when actionlint lints a FILE leaves a stdin lint
# untouched, because reading from `-` skips project-config discovery. The stub
# is isolated from the repository it is protecting.
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
probe=$(SHELLCHECK_OPTS='' actionlint -verbose -stdin-filename probe.yaml - 2>&1 <<'STUB'
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

Read the probe output below to tell the two cases apart. A line reading
`Rule "shellcheck" was disabled: exec: "shellcheck": executable file not found`
means the binary is genuinely missing — install it. If that line is ABSENT, the
binary ran and something suppressed the finding; the usual cause is a
`SHELLCHECK_OPTS` severity floor or exclusion in your environment, since SC2086
is reported at the weakest severity shellcheck has.

--- probe output ---
MSG
  printf '%s\n' "$probe" >&2
  exit 1
  ;;
esac

exec actionlint "$@"
