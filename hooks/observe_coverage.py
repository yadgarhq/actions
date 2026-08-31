#!/usr/bin/env python3
"""observe-coverage — every handler must be instrumented (D67, D59).

D59 deferred this hook because the thing it checks did not yet exist. It does
now: `yadgar-telemetry`'s `observe::Call`.

WHAT IT CHECKS, and why this is a grep rather than a judgement: a handler either
opens a `Call::start` or it does not. `Call` records on Drop, so once one is
started the record cannot be lost by an early return — which is why starting one
is the whole requirement.

WHAT IT DELIBERATELY DOES NOT CHECK: whether the outcome, class or row count are
*right*. A hook cannot know that, and pretending to would be the "two definitions
of clean" failure this project keeps refusing. It answers one question: is this
handler instrumented at all?

The failure it prevents is silent. A module that quietly stops emitting looks
exactly like a module nobody called — which is the reading that would break D15's
retirement rule, and D51 already established the pattern of enforcing a call that
is otherwise easy to skip.

TWO TRANSPORTS, ONE QUESTION.

  gRPC  — a handler is `async fn name(&self, ...)` inside a generated tonic
          server trait. tonic hands us a closed, named set for free.
  HTTP  — there is no such trait, so until now the hook found ZERO handlers in
          `gateway` and checked nothing in the one service whose numbers D67
          exists for: bytes and words returned TO THE CALLER. Every other hop
          sees protobuf, which answers a different question.

WHAT COUNTS AS AN HTTP HANDLER, and why it is not "the function the router
names". The router registration is a SCOPE GATE, not the unit — it says which
functions to look inside. The unit is finer, because the gateway serves three
methods through ONE axum handler: requiring only `handle` to open a `Call` would
pass a file in which two of the three methods had quietly stopped emitting, which
is the exact failure this hook exists to prevent.

So, inside a route-registered function:

  - if it dispatches on the caller's method — `match request.method.as_str()` —
    each ARM with a bounded label is the handler unit;
  - otherwise the function itself is the unit, which is the ordinary REST shape.

A CATCH-ALL ARM IS NEVER A HANDLER, and this is the load-bearing exclusion rather
than a convenience. Its only available label is the string the caller invented,
and D67's cardinality rule means a caller must not be able to mint a Prometheus
series. `gateway`'s unknown-method arm is left uninstrumented on purpose for
precisely that reason; a rule that demanded a record there would be demanding a
D67 violation. The omission stays visible in the code because a catch-all arm is
visibly a catch-all.

WHAT IT MUST NOT DO is fire on something that is not a handler. An earlier
version flagged every `async fn` in a file once a service impl appeared anywhere
in it, so adding tests failed the check that exists to encourage tests. A check
that cries wolf gets ignored, and then it protects nothing. That is why helpers
(`reply`, `origin_ok`, `shape`), the 405 fallback, and every function the router
never reaches are outside the rule entirely. So are `handle`'s own early returns
for malformed JSON, a failed `validate` and a header cross-check: no bounded
method label has been read yet at that point, so there is nothing to record
under, which is the same D67 reason the catch-all is excluded.

FITTED TO ONE SHAPE, DELIBERATELY. There is exactly one `Router::new()` in the
organisation today. A rule fitted to the one HTTP dispatch shape that exists — and
silent on every file that does not match it — is worth more than a general rule
that guesses. When a second HTTP service lands, extend this with a real second
example in hand rather than an imagined one.
"""

import re
import sys

# --------------------------------------------------------------------------
# gRPC — unchanged. tonic gives a closed set of named methods; take it.
# --------------------------------------------------------------------------

# An RPC handler in a tonic service impl: `async fn name(&self, ...)`.
HANDLER = re.compile(r"^\s*async fn (\w+)\s*\(\s*$|^\s*async fn (\w+)\s*\(&self", re.M)
# The impl blocks we care about — a generated tonic server trait.
SERVICE_IMPL = re.compile(r"impl\s+\w*Service\s+for\s+\w+")


# A test module defines FAKE services to test against, and its own test
# functions are `async fn` too. Scanning them produced a hook that failed on a
# repository for adding tests — the check firing on the thing it was meant to
# encourage.
def _is_test_source(path: str) -> bool:
    """Test sources only, decided by PATH.

    Deliberately not "the file contains `#[cfg(test)]`" — every production
    service file here ends with `#[cfg(test)] mod tests;`, so that rule skips
    exactly the files this hook exists to check. Caught by testing that the hook
    still fails on a handler with its `Call::start` removed; it did not, and the
    hook would have passed everything forever while looking like it worked.

    Scoping `handlers_in` to the impl block is what makes the narrow rule safe:
    a test module inside a production file is no longer scanned anyway.
    """
    return path.endswith("tests.rs") or "/tests/" in f"/{path}"


def handlers_in(text: str):
    """Yield (name, body) for each handler INSIDE a service impl.

    Scoped to the impl block, not the file. The first version searched the whole
    file once a service impl appeared anywhere in it, so an ordinary `async fn`
    elsewhere — a helper, a constructor — was reported as an uninstrumented
    handler. That is a check firing on something it was never about, which is
    worse than one that misses: it teaches people the check is noise.
    """
    m = SERVICE_IMPL.search(text)
    if not m:
        return
    lines = text.splitlines()

    # Find the impl block's extent by brace depth from its opening line.
    start = text[: m.start()].count("\n")
    depth = 0
    started = False
    end = len(lines)
    for i in range(start, len(lines)):
        depth += lines[i].count("{") - lines[i].count("}")
        if "{" in lines[i]:
            started = True
        if started and depth <= 0:
            end = i + 1
            break

    for i, line in enumerate(lines):
        if not (start <= i < end):
            continue
        m = re.match(r"\s*async fn (\w+)\(", line)
        if not m:
            continue
        # Body runs to the next handler or the end — enough to look for the call.
        body = []
        depth = 0
        started = False
        for l in lines[i:]:
            depth += l.count("{") - l.count("}")
            body.append(l)
            if "{" in l:
                started = True
            if started and depth <= 0:
                break
        yield m.group(1), "\n".join(body)


# --------------------------------------------------------------------------
# The one call that counts, and the one way to say "not here, on purpose".
# --------------------------------------------------------------------------

CALL = "Call::start"

# The deliberate omission, written down. Two properties matter more than the
# syntax: it is IN THE CODE, so it shows up in the diff a reviewer reads, and it
# CARRIES A REASON, so the next person finds an argument rather than a mystery.
#
# The reason is not checked, because a hook cannot judge one. It is checked for
# EXISTENCE, which is the part a hook can do and the part that makes a reviewer
# ask. A marker with no reason fails.
EXEMPT = re.compile(r"//\s*observe-coverage:\s*exempt\b\s*[-—:]?\s*(.*)")


def _exempt_reason(text: str, idx: int):
    """The exemption attached to whatever starts at `idx`, if there is one.

    Looked for on the line itself and on the contiguous `//` lines above it —
    the two places a Rust author would put it, and nowhere else, so an exemption
    cannot drift away from the thing it exempts.

    Returns the reason (possibly empty, which is a failure the caller reports),
    or None when there is no marker at all.
    """
    # Whole lines, both sides. Taking `text[:idx]` directly would leave the
    # partial line that `idx` sits in as the first thing the loop looks at, and
    # a signature is not a comment — so the marker above it was never reached.
    line_start = text.rfind("\n", 0, idx) + 1
    lines = text[:line_start].splitlines()
    here = text[line_start:].split("\n", 1)[0]
    m = EXEMPT.search(here)
    if m:
        return m.group(1).strip()
    for line in reversed(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("//"):
            break
        m = EXEMPT.search(stripped)
        if m:
            return m.group(1).strip()
    return None


# --------------------------------------------------------------------------
# HTTP — reading Rust well enough to find the dispatch, and no better.
# --------------------------------------------------------------------------

ROUTER = re.compile(r"\bRouter::new\s*\(")
# `post(handle)` in a `.route(...)`. The negative lookbehind on `.` is what keeps
# `headers.get(...)` and `.iter().any(...)` out: a METHOD call is not a route.
METHOD_ROUTER = re.compile(
    r"(?<![\w.])(?:get|post|put|delete|patch|head|options|trace|any)\s*\(\s*(\w+)\s*\)"
)
# `.fallback(...)` is deliberately NOT here. A fallback answers "no route
# matched", so it has no bounded route label to record under — the same reason
# the catch-all arm is excluded. Recording one would put the caller's URL into a
# metric, which is D67's cardinality rule read backwards.
# THE SCRUTINEE NAME IS LOAD-BEARING, and this is the one way this hook degrades
# quietly. If the dispatch is ever rewritten as `match request.rpc_name.as_str()`
# this stops matching, the file falls back to treating the route-registered
# function as the unit, and `handle` passes on the strength of any ONE arm's
# `Call::start` — three checked methods silently become one that cannot fail.
# Left narrow anyway: matching every string `match` inside a handler would fire
# on a content-type or a header check, and a check that cries wolf gets ignored.
# So it is written down here instead, for whoever renames it.
DISPATCH = re.compile(r"\bmatch\b[^{;]*\.method\b[^{;]*\{")
# `const TOOLS_LIST: &str = "tools/list";` — so a failure can name the method an
# operator recognises instead of the identifier the code happens to use.
CONST_STR = re.compile(r'\bconst\s+(\w+)\s*:\s*&(?:\'static\s+)?str\s*=\s*"([^"]*)"')
BARE_CALL = re.compile(r"(?<![\w:.])(\w+)\s*\(")
NOT_A_CALL = {"if", "while", "for", "match", "return", "fn", "let", "else"}


def _blank_noncode(text: str) -> str:
    """`text` with string, char and comment CONTENTS replaced by spaces.

    Same length and same newlines, so an offset into one is an offset into the
    other. Every structural scan below runs on this and every substring that
    gets read or reported is taken from the original — which is how a brace in a
    comment or a comma in a string literal stops being able to move an arm
    boundary. `format!("unknown method: {other}")` is exactly that case, and it
    sits inside the arm this hook must classify correctly.
    """
    out = list(text)
    i, n = 0, len(text)

    def blank(lo: int, hi: int) -> None:
        for k in range(max(lo, 0), min(hi, n)):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        two = text[i : i + 2]
        if two == "//":
            j = text.find("\n", i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
        elif two == "/*":
            # Rust block comments nest, so a depth counter rather than a find.
            depth, j = 0, i
            while j < n:
                if text[j : j + 2] == "/*":
                    depth += 1
                    j += 2
                elif text[j : j + 2] == "*/":
                    depth -= 1
                    j += 2
                    if depth == 0:
                        break
                else:
                    j += 1
            blank(i, j)
            i = j
        elif text[i] == "r" and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_")):
            m = re.match(r'r(#*)"', text[i:])
            if not m:
                i += 1
                continue
            close = '"' + m.group(1)
            j = text.find(close, i + m.end())
            j = n if j < 0 else j + len(close)
            blank(i, j)
            i = j
        elif text[i] == '"':
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == '"':
                    j += 1
                    break
                j += 1
            blank(i + 1, j - 1)
            i = j
        elif text[i] == "'":
            # A LIFETIME LOOKS LIKE A CHAR LITERAL and there are lifetimes in
            # this very file (`fn header<'a>(h: &'a HeaderMap, ...)`). Treating
            # `'a` as an opening quote would blank everything up to the next
            # apostrophe and silently swallow real code, so: a char literal is
            # an escape, or exactly one character before the closing quote.
            if text[i + 1 : i + 2] == "\\":
                j = text.find("'", i + 2)
                j = n if j < 0 else j + 1
                blank(i + 1, j - 1)
                i = j
            elif text[i + 2 : i + 3] == "'":
                blank(i + 1, i + 2)
                i += 3
            else:
                i += 1
        else:
            i += 1
    return "".join(out)


def _match_brace(blank: str, open_idx: int) -> int:
    """Index just past the `}` closing the `{` at `open_idx`, or -1."""
    depth = 0
    for i in range(open_idx, len(blank)):
        if blank[i] == "{":
            depth += 1
        elif blank[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return -1


def _functions(text: str, blank: str) -> dict:
    """name -> (decl_start, body_start, body_end) for every `fn` in the file.

    `decl_start` is the `fn` keyword rather than the opening brace, because that
    is where an exemption comment sits — above the signature, which for
    `tools_call` is five lines long.
    """
    out = {}
    for m in re.finditer(r"\bfn\s+(\w+)", blank):
        # Walk to the `{` that opens the body: the first one outside the
        # parameter list, so `impl FnOnce() -> Value` as a parameter type cannot
        # be mistaken for it.
        depth, i, brace = 0, m.end(), -1
        while i < len(blank):
            c = blank[i]
            if c in "([":
                depth += 1
            elif c in ")]":
                depth -= 1
            elif c == ";" and depth == 0:
                break  # a trait method declaration, no body
            elif c == "{" and depth == 0:
                brace = i
                break
            i += 1
        if brace < 0:
            continue
        end = _match_brace(blank, brace)
        if end > 0:
            out[m.group(1)] = (m.start(), brace, end)
    return out


def _arms(text: str, blank: str, open_idx: int):
    """Yield (pattern_src, pattern_idx, body_src) for a match block's arms.

    Arm extents are found on the blanked text by depth, so a nested closure's
    `=>` and a struct literal's braces cannot end an arm early. An arm whose
    extent cannot be determined is not silently skipped — `main` reports the
    match as unreadable, because a parser that quietly gives up is how a check
    passes everything forever while looking like it works.
    """
    end = _match_brace(blank, open_idx)
    if end < 0:
        return
    end -= 1  # exclude the closing brace
    i = open_idx + 1
    arm_start = i
    depth = 0
    while i < end:
        c = blank[i]
        if c in "([{":
            depth += 1
            i += 1
            continue
        if c in ")]}":
            depth -= 1
            i += 1
            continue
        if depth == 0 and blank[i : i + 2] == "=>":
            pat_end = i
            j = i + 2
            while j < end and blank[j].isspace():
                j += 1
            if j < end and blank[j] == "{":
                body_end = _match_brace(blank, j)
                if body_end < 0:
                    return
            else:
                d, k = 0, j
                while k < end:
                    ch = blank[k]
                    if ch in "([{":
                        d += 1
                    elif ch in ")]}":
                        if d == 0:
                            break
                        d -= 1
                    elif ch == "," and d == 0:
                        break
                    k += 1
                body_end = k
            raw = text[arm_start:pat_end]
            # The pattern is the arm's own text; the comment lines above it are
            # where an exemption lives and are stripped here so they cannot be
            # mistaken for part of the pattern.
            pattern = "\n".join(
                l for l in raw.splitlines() if not l.strip().startswith("//")
            ).strip()
            pat_idx = arm_start + (len(raw) - len(raw.lstrip()))
            yield pattern, pat_idx, text[j:body_end]
            i = body_end
            while i < end and (blank[i].isspace() or blank[i] == ","):
                i += 1
            arm_start = i
            continue
        i += 1


def _labels(pattern: str, consts: dict):
    """The bounded labels an arm serves, or None when it is a catch-all.

    A CATCH-ALL IS NOT A HANDLER (D67). `_` and a binding like `other` both mean
    "whatever the caller sent", and the only label available for such an arm is
    a string the caller invented — so requiring a record there would require a
    caller-mintable Prometheus series, which is the thing D67 forbids.

    Rust tells a const pattern from a binding by resolving the name, and this
    does the same with the file's own `const NAME: &str` table. That distinction
    is the whole classification: `DISCOVER` and `other` are both bare
    identifiers, and they are opposite answers.

    A GUARDED arm (`"x" if ready =>`) is treated as a catch-all too. It serves
    its label only sometimes, so it is not the closed label-to-response mapping
    this rule is about, and the falling-through case is somebody else's arm.
    """
    labels = []
    for part in pattern.split("|"):
        part = part.strip()
        if not part or part == "_" or " if " in f" {part} ":
            return None
        m = re.fullmatch(r'"((?:[^"\\]|\\.)*)"', part)
        if m:
            labels.append(m.group(1))
        elif part in consts:
            labels.append(consts[part])
        elif re.fullmatch(r"[A-Z][A-Z0-9_]*", part) or "::" in part:
            # A const declared elsewhere. Its value is not readable here, so the
            # identifier is the best name a failure can carry.
            labels.append(part)
        else:
            return None  # a binding: catch-all
    return labels or None


def _instrumented(body: str, text: str, fns: dict, seen=None):
    """Is `Call::start` reachable from `body` through same-file calls?

    Returns (True, None) or (False, unresolved) where `unresolved` names a call
    the search could not follow — a function defined in another file, or a
    closure passed in as a parameter. That is not a separate failure: the unit
    has already failed for want of a `Call::start`. It only makes the message
    say WHERE the search stopped instead of implying the code is empty.
    """
    if CALL in body:
        return True, None
    seen = seen if seen is not None else set()
    unresolved = None
    for m in BARE_CALL.finditer(_blank_noncode(body)):
        name = m.group(1)
        if name in NOT_A_CALL or name in seen:
            continue
        seen.add(name)
        if name not in fns:
            # Only a snake_case name is worth naming as a dead end. `Some(..)`
            # and `Outcome { .. }` are constructors, not calls the search failed
            # to follow, and reporting them as such makes the hint noise.
            if re.fullmatch(r"[a-z_][a-z0-9_]*", name):
                unresolved = unresolved or name
            continue
        _, lo, hi = fns[name]
        ok, deeper = _instrumented(text[lo:hi], text, fns, seen)
        if ok:
            return True, None
        unresolved = unresolved or deeper
    return False, unresolved


def http_failures(path: str, text: str):
    """Every HTTP handler in `path` that is not instrumented and not exempt."""
    blank = _blank_noncode(text)
    if not ROUTER.search(blank):
        return
    fns = _functions(text, blank)
    consts = dict(CONST_STR.findall(text))

    for reg in METHOD_ROUTER.finditer(blank):
        name = reg.group(1)
        if name not in fns:
            reason = _exempt_reason(text, reg.start())
            if reason is None:
                yield (
                    f"{path}: route handler `{name}` is not defined in this file, "
                    "so whether it is instrumented cannot be checked here"
                )
            elif not reason:
                yield f"{path}: `{name}` is marked exempt with no reason"
            continue
        decl, lo, hi = fns[name]
        body = text[lo:hi]
        dispatch = DISPATCH.search(blank, lo, hi)
        if not dispatch:
            # The ordinary REST shape: the registered function IS the handler.
            yield from _check(path, name, body, decl, text, fns)
            continue
        open_idx = dispatch.end() - 1
        if _match_brace(blank, open_idx) < 0:
            yield f"{path}: the method dispatch in `{name}` could not be read"
            continue
        for pattern, pat_idx, arm in _arms(text, blank, open_idx):
            labels = _labels(pattern, consts)
            if labels is None:
                continue  # a catch-all is not a handler — see `_labels`
            for label in labels:
                yield from _check(path, label, arm, pat_idx, text, fns)


def _check(path: str, label: str, body: str, idx: int, text: str, fns: dict):
    ok, unresolved = _instrumented(body, text, fns)
    if ok:
        return
    reason = _exempt_reason(text, idx)
    if reason is not None:
        if not reason:
            yield f"{path}: `{label}` is marked exempt with no reason"
        return
    tail = f" (the search could not follow the call to `{unresolved}`)" if unresolved else ""
    yield f"{path}: `{label}` opens no observe::Call{tail}"


# --------------------------------------------------------------------------


def main(paths):
    failures = []
    for path in paths:
        try:
            text = open(path, encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            continue
        if _is_test_source(path):
            continue
        for name, body in handlers_in(text):
            if CALL not in body:
                failures.append(f"{path}: `{name}` opens no observe::Call")
        failures.extend(http_failures(path, text))

    if failures:
        print("observe-coverage: uninstrumented handlers\n")
        for f in failures:
            print(f"  {f}")
        print(
            "\nEvery handler must open a `Call::start(...)` (D67). The Call\n"
            "records on Drop, so an early return cannot lose the record — starting\n"
            "one is the whole requirement.\n\n"
            "A module that quietly stops emitting looks exactly like a module\n"
            "nobody called, which is the reading that would break D15.\n\n"
            "An omission that is deliberate is written down, above the arm or the\n"
            "function, WITH A REASON:\n\n"
            "    // observe-coverage: exempt — why this one records nothing\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
