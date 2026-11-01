#!/usr/bin/env bash
# ADR-0569: a configuration knob has ONE source and NO compiled-in default.
#
# This gate exists because the rule was held by attention alone. An estate-wide
# audit found no hook, no lint and no clippy.toml anywhere that would catch a
# new compiled-in default in review, and ADR-0574 rules that a defect is closed
# as a CLASS rather than as an instance: the change that ships is the one that
# makes the next occurrence impossible or visible.
#
# THE GATE ASSERTS THAT IT ACTUALLY SEARCHED SOMETHING, and prints the count it
# searched. A grep-driven check that passes because it globbed nothing is the
# fifth instance of the check-that-cannot-fail class this estate met in a week,
# and shipping one as the guard against a different class would be its own joke.
# `grep` exits 1 on no-match, which is this gate's success, and 2 on error —
# `! grep ...` cannot tell those apart, so the exit status is read explicitly.
set -euo pipefail

# A SINGLE-CRATE LAYOUT AND A WORKSPACE LAYOUT BOTH COUNT. The five repositories
# that carried a copy of this gate are all single-crate, so root-level src/ was
# the whole search. yadgarhq/estate is crates/harness and crates/front with no
# src/ at the root, and against that layout the gate exited 1 reading "a gate
# that searches nothing proves nothing" — a refusal that names a real rule while
# checking no file. Published estate-wide, that made estate a permanent
# non-adopter, and ADR-0577 says a gate no consumer can reference is a gate that
# never closes.
#
# An unmatched glob expands to the literal pattern under bash's default
# nullglob-off, and `[ -d "crates/*/src" ]` is false for a literal, so the
# single-crate case is unchanged and needs no shell option set.
roots=()
for d in src tests benches crates/*/src crates/*/tests crates/*/benches; do
    [ -d "$d" ] && roots+=("$d")
done

if [ ${#roots[@]} -eq 0 ]; then
    echo "ADR-0569 gate: NO RUST SOURCE ROOT FOUND. Looked for src/, tests/ and" >&2
    echo "  benches/, and the same three under crates/*/, beneath $(pwd), and" >&2
    echo "  none exist. This is a failure and not a pass: a gate that searches" >&2
    echo "  nothing proves nothing." >&2
    exit 1
fi

files=()
while IFS= read -r f; do files+=("$f"); done < <(find "${roots[@]}" -type f -name '*.rs' | sort)

if [ ${#files[@]} -eq 0 ]; then
    echo "ADR-0569 gate: MATCHED 0 RUST FILES under ${roots[*]}. The source roots" >&2
    echo "  exist but hold no .rs file, so this run checked nothing. A directory-" >&2
    echo "  exists check is not the same guard as a non-empty-glob check." >&2
    exit 1
fi

echo "ADR-0569 gate: searching ${#files[@]} Rust file(s) under ${roots[*]}."

fail=0

# A WHOLE-LINE COMMENT IS PROSE, NOT CODE. `//`, `///` and `//!` lines are
# dropped before a hit is reported: a doc comment that quotes a forbidden shape
# (gateway src/invalidate.rs:95 quotes `EnvFilter::new("info")`) configures
# nothing. Trailing comments are kept, because the ADR-0569-EXCEPTION marker
# lives in one. Every report line is `path:N:` or `path:   N<TAB>` (nl -ba).
drop_comment_lines() {
    grep -vE '^[^:]+:[[:space:]]*[0-9]+(:|'$'\t'')[[:space:]]*//' || true
}

# Reports a violation when the pattern MATCHES. Reading grep's status explicitly
# keeps a grep error (2) from being mistaken for the no-match success (1).
forbid() {
    local pattern="$1" title="$2" guidance="$3" raw hits status
    set +e
    # -H FORCES THE FILENAME PREFIX even when exactly one file is searched —
    # bare `grep -nE` drops it for a single file, which breaks every reader
    # of this output (drop_comment_lines, the report itself) in a single-file
    # repository.
    raw=$(grep -HnE "$pattern" "${files[@]}")
    status=$?
    set -e
    if [ "$status" -eq 0 ]; then
        # An exception is allowed where the argument for it is written down. The
        # marker is the whole of the escape hatch, so `git grep ADR-0569-EXCEPTION`
        # answers "which knobs keep a fallback, and why" in one command.
        hits=$(printf '%s\n' "$raw" | drop_comment_lines | grep -v 'ADR-0569-EXCEPTION' || true)
        if [ -n "$hits" ]; then
            echo "" >&2
            echo "ADR-0569 VIOLATION — $title" >&2
            while IFS= read -r line; do echo "  $line" >&2; done <<<"$hits"
            echo "  $guidance" >&2
            fail=1
        fi
    elif [ "$status" -ne 1 ]; then
        echo "ADR-0569 gate: grep failed with status $status while checking: $title" >&2
        exit 1
    fi
}

# The deleted signature itself. `env_or(key, default)` gave every compiled-in
# default in this binary a place to live; without it a default has no parameter
# to sit in.
forbid \
    'fn[[:space:]]+env_or\b' \
    'fn env_or is back.' \
    'Use env_required(key), which has no default parameter. ADR-0569.'

# A RENAME IS NOT A FIX. gateway already carried a second helper of this shape
# (parse_bucket_env(key, default)), so forbidding only the literal name env_or
# would pass env_or_default on day one.
forbid \
    'fn[[:space:]]+[A-Za-z0-9_]*env[A-Za-z0-9_]*[[:space:]]*(<[^>]*>)?\([^)]*default[[:space:]]*:' \
    "an environment helper takes a 'default' parameter." \
    'A knob reader must not accept a fallback. ADR-0569.'

# The inline form, which neither check above sees. An exception is allowed and
# is meant to be readable as one: mark the line ADR-0569-EXCEPTION and say why.
#
# SCANNED OVER A CHAIN-REJOINED VIEW, and that is not a refinement — without it
# this pattern misses the exact shape it forbids. `cargo fmt` breaks a method
# chain of three or more calls, so the fallback lands on its own line and a
# single-line pattern cannot see it. Measured on rustfmt's own output: the
# split form of `env::var("K").unwrap_or_else(...).parse().unwrap()` passed this
# gate with exit 0 while the one-line form in the same file was caught. A gate
# that the repository's own required formatter disarms is the check-that-cannot-
# fail class, in the guard built to close it.
#
# `nl -ba` numbers every line BEFORE the join, so the number that survives is the
# line the chain starts on and the report still points at real source.
forbid_joined() {
    local pattern="$1" title="$2" guidance="$3" raw hits f
    raw=""
    for f in "${files[@]}"; do
        local joined
        # YAADGAAR 338: a call whose OWN argument list rustfmt split across lines
        # (`var(` / `"K",` / `)`) is rejoined before the chain is: a line ending
        # in `(` takes the next line, a line opening with `)` joins the one
        # above, THEN a line opening with `.` joins. The order matters, because
        # the `)` join is what puts the chain's first `.` next to its call.
        # Only those two bracket edges are joined, never a trailing `,`, so a
        # long argument or field list does not fuse into one line and let an
        # unrelated `env::var` and `unwrap_or` meet.
        #
        # THE RESULT IS THE UNION OF TWO VIEWS, each filtered for the marker on
        # its own, and the union is what keeps the widening from weakening the
        # gate (ADR-0645). The bracket joins fuse lines, so on their own they can
        # (a) carry a marker from an argument line or from the closing line of an
        # OUTER call onto a read it does not cover, and (b) let a comment ending
        # in `(` swallow the next real read, which `drop_comment_lines` then
        # drops. The chain-only view is the gate as it was before the widening,
        # so everything it refused is still refused.
        joined=$( {
            nl -ba "$f" \
                | perl -0pe 's/\(\n *\d+\t[ \t]*/(/g; s/\n *\d+\t[ \t]*\)/)/g; s/\n\s*\d+\t\s*\./\./g' \
                | grep -E "$pattern" | grep -v 'ADR-0569-EXCEPTION'
            nl -ba "$f" | perl -0pe 's/\n\s*\d+\t\s*\./\./g' \
                | grep -E "$pattern" | grep -v 'ADR-0569-EXCEPTION'
        } | sort -un || true)
        if [ -n "$joined" ]; then
            raw+="$(printf '%s\n' "$joined" | sed "s|^|$f:|")"$'\n'
        fi
    done
    hits=$(printf '%s' "$raw" | drop_comment_lines | grep -v 'ADR-0569-EXCEPTION' || true)
    if [ -n "$hits" ]; then
        echo "" >&2
        echo "ADR-0569 VIOLATION — $title" >&2
        while IFS= read -r line; do [ -n "$line" ] && echo "  $line" >&2; done <<<"$hits"
        echo "  $guidance" >&2
        fail=1
    fi
}

forbid_joined \
    '(env::var(_os)?|option_env!)\(.*unwrap_or(_else|_default)?\((.*)?$' \
    'a raw environment read falls back inline.' \
    'Use env_required(key), or mark the line ADR-0569-EXCEPTION with the argument.'

# LEDGER 965, UNIT C-A1. The widening: four more compiled-in-default shapes
# the census (plan-C.md) found with no marker, plus a fifth read already
# forbidden above but narrowed.
#
# P1 — a lookup/env/get call keyed by a CONFIG KEY, falling back inline
# through any number of lowercase-method chain links. ANCHORED TO AN
# UPPERCASE KEY (quoted or bare), not to the call name alone: the unanchored
# shape matches any map/JSON lookup chained into unwrap_or, and measured
# 2026-10-03 against yadgarhq/yadgar @ origin/main it false-positives on
# seven lines that read an untyped value by a lowercase field name or a
# variable, never a config knob (e.g. src/hook/guard.rs:60 `payload
# .get("tool_name").and_then(Value::as_str).unwrap_or("")`). A config key in
# this estate is always ALL_CAPS, bare (gateway broker.rs: `lookup(URL)`) or
# quoted (`env("YADGAR_CREDENTIAL_TTL_SECONDS")`).
#
# KNOWN LIMITS A GREP CANNOT CLOSE, named rather than silently missed: a
# chain link with its OWN nested parentheses (`.filter(|v| !v.is_empty())`)
# breaks the `\.[a-z_]+\([^)]*\)` link pattern, because `[^)]*` stops at the
# link's first `)`; a path-qualified key (`env(keys::URL)`) is not a bare
# `[A-Z][A-Z0-9_]*` token; and rustfmt may split the call's OWN arguments
# (not only the method chain) across lines, which `forbid_joined`'s single
# chain-rejoin pass does not reassemble. Each is a textual shape outside
# this pattern's reach, not a case the gate claims to exempt.
# YAADGAAR 338 closes two of the limits above and leaves one open on purpose.
# CLOSED: a key may be borrowed (`&KEY`) and, in the presence test P3 only,
# path-qualified (`keys::TRUST_HEADERS`), its LAST segment still ALL_CAPS so
# the uppercase anchor, and with it the measured false-positive guard above, is
# unchanged; the call's own
# argument list may be split by rustfmt (the join in `forbid_joined`); `var` and
# `var_os` count as lookup names, as does any name a `use ...::var as NAME`
# imports; `option_env!` counts beside `env::var`.
# LEFT OPEN, MEASURED (1 of 2): a path-qualified key in P1. `yadgarhq/iam`
# `src/key_identity/tests.rs:73` and `src/service/tests.rs:211` read
# `metadata.get(yadgar_dial::GRPC_TIMEOUT).map(|v| v.to_str().unwrap_or_default()..)`,
# a gRPC header constant, and a path-qualified P1 key newly reds both.
# LEFT OPEN, MEASURED (2 of 2): a chain link with its own nested parentheses
# (`.and_then(|v| v.parse().ok())`). Widening the link pattern to nest was
# written and swept on 2026-11-01 against the consumers' origin/main; it found
# no config fallback and one false positive, `yadgarhq/project`
# `src/service/request_id.rs:52` (`req.metadata().get(REQUEST_ID_HEADER)
# .and_then(..).map(|v| capped(v).into_owned()).unwrap_or_default()`), a gRPC
# header constant that is not a knob. The widening would newly red that
# consumer on its next rev bump, so it is not shipped; the sweep found no real
# instance, so nothing is lost today. Telling the header constant from a knob
# needs the receiver's type, not a regex.
# ALSO OPEN: a fallback applied through an intermediate binding
# (`let v = env::var("K"); v.unwrap_or(..)`); the sweep found none.
KEY='&?"?[A-Z][A-Z0-9_]*"?'
KEY_PATH='(&?"[A-Z][A-Z0-9_]*"|&?([A-Za-z_][A-Za-z0-9_]*::)*[A-Z][A-Z0-9_]*)'
LINK='\.[a-z_]+\([^)]*\)'
aliases=$(grep -hE '^[[:space:]]*(pub[[:space:]]+)?use[[:space:]].*env::' "${files[@]}" \
    | grep -oE '\bvar(_os)?[[:space:]]+as[[:space:]]+[A-Za-z_][A-Za-z0-9_]*' \
    | sed -E 's/.*[[:space:]]//' | sort -u | paste -sd'|' - || true)
names='lookup|env|get|var|var_os'
[ -n "$aliases" ] && names="$names|$aliases"

forbid_joined \
    "($names)\\([[:space:]]*${KEY}[[:space:]]*,?[[:space:]]*\\)($LINK)*\\.unwrap_or(_else|_default)?\\(" \
    'a lookup/env/get call on a CONFIG KEY falls back inline.' \
    'Use env_required(key), or mark the line ADR-0569-EXCEPTION with the argument.'

# P2 — the name itself asserts a fallback exists.
forbid \
    '(const|static)\s+DEFAULT_[A-Z0-9_]*\s*:' \
    'a const or static item is named DEFAULT_*.' \
    'Rename it, read it explicitly as a knob, or mark it ADR-0569-EXCEPTION.'

# P3 — presence tested against a compiled-in "off" literal instead of a read.
# SAME UPPERCASE-KEY ANCHOR AS P1, and for the same reason: `lookup`/`env`
# read a config key exactly as often as `get` does here (gateway attest.rs:219
# `lookup(TRUST_HEADERS) == Some("1")`), while an untyped field read like
# `v.get("type").as_deref() == Some("stdio")` is not a config knob.
forbid \
    "($names)\\([[:space:]]*${KEY_PATH}[[:space:]]*,?[[:space:]]*\\)(\\.as_deref\\(\\))?[[:space:]]*(!=|==)[[:space:]]*Some\\(\"" \
    'presence of a value is tested against Some("...") instead of reading it.' \
    'Read the value explicitly, or mark the line ADR-0569-EXCEPTION.'

# P4 — a compiled-in log-level fallback.
forbid \
    'EnvFilter::new\(' \
    'EnvFilter::new(...) compiles in a log-level fallback.' \
    'Mark it ADR-0569-EXCEPTION(LIB): the log level is observability, not behaviour.'

# P5 — a `None` arm beside an env read that answers with a bare
# SCREAMING_SNAKE_CASE constant is a compiled-in default under another name.
# SCOPED TO FILES THAT ACTUALLY READ THE ENVIRONMENT (`lookup(`, `env(` or
# `env::var(` present somewhere in the file), so an ordinary `None => CONST`
# in a match that has nothing to do with configuration is not swept in. The
# word-boundary guard on `lookup(`/`env(` keeps a name like `my_lookup(` or
# `other_env(` from putting a file in scope it should not be in.
p5_raw=""
for f in "${files[@]}"; do
    if grep -qE '(^|[^A-Za-z0-9_])(lookup|env)\(|env::var\(' "$f"; then
        p5_file_hits=$(grep -nE 'None\s*=>\s*(Ok\()?[A-Z][A-Z0-9_]{2,}\s*[,)]' "$f" || true)
        if [ -n "$p5_file_hits" ]; then
            p5_raw+="$(printf '%s\n' "$p5_file_hits" | sed "s|^|$f:|")"$'\n'
        fi
    fi
done
p5_hits=$(printf '%s' "$p5_raw" | drop_comment_lines | grep -v 'ADR-0569-EXCEPTION' || true)
if [ -n "$p5_hits" ]; then
    echo "" >&2
    echo "ADR-0569 VIOLATION — a None arm beside an env read returns a bare constant." >&2
    while IFS= read -r line; do [ -n "$line" ] && echo "  $line" >&2; done <<<"$p5_hits"
    echo "  Use env_required(key), or mark the line ADR-0569-EXCEPTION with the argument." >&2
    fail=1
fi

if [ "$fail" -ne 0 ]; then
    echo "" >&2
    echo "ADR-0569: a knob is read from one source and refuses the boot when absent." >&2
    exit 1
fi

echo "ADR-0569 gate: no compiled-in configuration default found."
