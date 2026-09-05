#!/usr/bin/env bash
#
# actionlint, with its one unenforced dependency enforced.
#
# actionlint pipes every `run:` block to shellcheck, and SILENTLY DISABLES that
# whole rule class when the binary is absent: a workflow whose only defect is
# SC2086 exits 0 and prints nothing. The skip is visible only under `-verbose`,
# as `Rule "shellcheck" was disabled`. So a bare `actionlint` entry reports
# Passed for two different states — workflows are clean, or workflows are clean
# except for a rule class nobody ran.
#
# That is the defect this repository already closed for actionlint's own
# absence, one level down. A named check that every repository references has to
# mean one thing, and "Passed, having checked nothing" is not a thing it can
# mean. So this hook refuses instead.
#
# THE PROBE ASKS actionlint, NOT $PATH, and that distinction is load-bearing
# rather than fussy. nixpkgs ships actionlint as a wrapper that puts shellcheck
# on an internal PATH for the wrapped binary alone, so `command -v shellcheck`
# reports "missing" on a machine where the rule class is in fact running. A
# guard written that way would fail for the developers most likely to have the
# tool. Asking actionlint holds however the binary was installed.
#
# BEFORE REWRAPPING THESE COMMENTS: a comment of the form `# shellcheck ...` is
# read as one of shellcheck's own directives and fails to parse (SC1072/SC1073),
# so that word must never open a line in this file. Found by running shellcheck
# on this script, which is the only reason it is written down.
#
# The probe is a stub workflow on stdin, so it costs a millisecond and its
# answer cannot be affected by the repository being linted.
set -uo pipefail

# Kept identical to what a bare `language: system` entry produced before this
# script existed. Under `language: script` an absent binary would otherwise
# surface as this file's exit 127, which names nothing.
if ! command -v actionlint >/dev/null 2>&1; then
  echo "Executable \`actionlint\` not found" >&2
  exit 1
fi

probe=$(printf 'on: push\njobs:\n  probe:\n    runs-on: ubuntu-latest\n    steps:\n      - run: "true"\n' |
  actionlint -verbose -stdin-filename probe.yaml - 2>&1 >/dev/null) || true

case "$probe" in
*'Rule "shellcheck" was disabled'*)
  cat >&2 <<'MSG'
shellcheck is not installed, so actionlint would skip its entire shellcheck
rule class and still exit 0. This hook refuses rather than reporting Passed,
because a green tick has to mean one thing.

Every `run:` block in this repository's workflows goes unchecked without it —
SC2086 word splitting, SC2046 unquoted substitution, and the rest. actionlint's
native rules (expression, context, unknown-key) are unaffected.

Install shellcheck, then run this hook again:

  apt-get install shellcheck
  brew install shellcheck
  nix profile install nixpkgs#shellcheck

https://github.com/koalaman/shellcheck#installing
MSG
  exit 1
  ;;
esac

exec actionlint "$@"
