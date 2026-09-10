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

THE UNIT OF ANALYSIS IS THE CRATE, NOT THE FILE, and that is the whole of ledger
824. This hook used to resolve everything file-locally, so it COLLIDED with the
`complexity` hook's 500-line ceiling: a repository forced to split a large file
then failed observability, and no split satisfied both gates. Measured twice on
2026-09-10, by two independent mechanisms:

  - `iam`. A Rust trait impl cannot span files and `impl IamService for Iam` was
    865 lines, so the handler bodies had to move out. Each handler then delegated
    — `self.login_inner(req, call).await` — and the check was a plain substring
    scan of the wrapper body that followed nothing at all. Even the HTTP side's
    search could not have followed it: its call pattern excluded any identifier
    preceded by `.`, so a method call was invisible by construction.
  - `gateway`. Splitting a 2553-line `src/http.rs` moved six handler bodies into
    sibling modules while the `.route(...)` registrations stayed behind, and the
    hook refused all six as "not defined in this file".

Both repositories shipped a workaround. Both workarounds are correct, and neither
is the point: the next repository to cross the ceiling meets the same wall, and
the estate plans fifty more. So resolution is now crate-wide — a callee, a route
handler and a label constant are all looked up across every module of the crate
the file belongs to.

DISCOVERY IS CRATE-WIDE AND CREDITING IS NOT, and that separation is the whole of
ledger 838. Finding the handlers needs the crate — a `.route(..)` and the function
it names end up in different files the moment a repository crosses the file-size
ceiling, and enumerating them is what ledger 824 fixed. DECIDING whether a
handler is instrumented is a different question, and following calls across a
crate to answer it LOOSENED the gate: measured on `gateway` under mutation, six of
eight HTTP units went green with their own `Call::start` deleted, where the
file-local gate refuses. ADR-0645 forbids that direction whether or not it is
disclosed. Nothing was wrong on the real tree — all eight are green under both
rules on `origin/main` — which is the point: a loosening is LATENT and visible
only under mutation, because a green tree cannot detect one.

THE TWO CONJUNCTS a callee's `Call::start` must satisfy to stand in for its
caller's, both of them or neither:

  1. RESULT POSITION. The call is the caller's tail expression or its sole
     `return`. `DISCOVER => measured(DISCOVER, || discover(&id))` is the whole of
     that arm, so `measured`'s `Call` is unavoidably the arm's. A call in an `if
     let` condition is not — `guard(..)` decides whether to refuse, and the
     handler carries on past it.
  2. UNCONDITIONAL IN THE CALLEE. The `Call::start` is the callee's first
     statement, reached before any branch or early return. `measured` passes;
     `tools_call` does not, because its `Call` sits below an early `return` on the
     throttle path.

Otherwise the unit opens a `Call` in its OWN body. `_result_span` and
`_opens_call_unconditionally` are the two conjuncts, and both err towards
REFUSING CREDIT — a false red, which is loud, over a false green, which is the
silent failure this file exists to prevent.

ONE HOP, NEVER TWO. The recursion that used to run here is what the measurement
caught: it followed `guard`, declined the `Call` on its refusal arm, then followed
`guard`'s own callee `too_many` and accepted the `Call` on ITS refusal arm — two
hops from a handler whose own `Call::start` had been deleted. No shape in this
estate needs a chain.

AND `tools/call` TAKES A WRITTEN EXEMPTION rather than a rule bent to fit it. Its
`Call` is conditional and no syntactic rule can soundly credit it; it is
nonetheless correct, because the early path is recorded separately by
`record_throttled`. That is a judgement, and it is expressed as one — a marker in
the code, carrying a reason, which a reviewer reads. A rule stretched until it
accepted that shape would have accepted five wrong ones with it.

WHAT CRATE-WIDE RESOLUTION MUST NOT DO is pass a handler that is not
instrumented. Rust resolves a name through modules and imports; this does not,
because a `use`-graph is a compiler's job. It uses a LADDER instead, and the
bottom rung is what keeps the rule honest:

  1. a module qualifier, when the call site carries one — `dispatch::routes()`
     picks the `routes` in `http::dispatch` out of the three this crate defines;
  2. the same file, which is where a name resolves in Rust more often than not;
  3. a name that is unique in the crate;
  4. otherwise NOTHING IS FOLLOWED. An ambiguous name fails closed and the
     message says so.

Rung 4 is the one that matters. Following every same-named candidate and passing
on any one of them would let an unrelated function's `Call::start` certify a
handler that has none, which is this hook's only real failure mode — a green that
means nothing. A false red is loud and gets fixed; a false green is the silent
failure the hook exists to prevent.

TEST SOURCES ARE OUT OF THE INDEX, not merely out of the scan. A crate-flat name
table that included them would let a test harness's `Call::start` certify the
production handler it shares a name with, and would make an ordinary test double
`fn login` an ambiguity that reddens a green tree. Both directions are wrong, so
`tests.rs`, anything under `tests/`, and any inline `#[cfg(test)]` item are
blanked out before anything is indexed.

TWO TRANSPORTS, ONE QUESTION.

  gRPC  — a handler is `async fn name(&self, ...)` inside a generated tonic
          server trait. tonic hands us a closed, named set for free.
  HTTP  — there is no such trait, so until now the hook found ZERO handlers in
          `gateway` and checked nothing in the one service whose numbers D67
          exists for: bytes and words returned TO THE CALLER. Every other hop
          sees protobuf, which answers a different question.

BOTH TRANSPORTS NOW RUN THROUGH THE SAME RULE. The gRPC side used to be a
substring test on the wrapper's own text, which is what made `iam`'s delegation
invisible; it now credits a callee on exactly the two conjuncts above, and accepts
the same written-down exemption. Two rules for one question was the reason a split
could break one transport and not the other. Every gRPC wrapper in the estate
opens its own `Call::start` in its own body today, so none of them needs crediting
at all — checked across `iam`, `iam-db`, `task`, `task-db`, `project` and
`project-db`.

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

A ROUTER ANYWHERE IN THE CRATE OPENS EVERY FILE OF IT. The gate used to be "this
file contains `Router::new(`", which is what let `gateway`'s split hide six
handlers: the registrations and the router lived in one file and the bodies in
another. `gateway` answers that today with a `pub(super) fn routes()` per module
merged in the root `router()`, and that shape needs no special case here — a
`.route(...)` is a registration wherever it is written, and the merge is just
Rust.

A CATCH-ALL ARM IS NEVER A HANDLER, and this is the load-bearing exclusion rather
than a convenience. Its only available label is the string the caller invented,
and D67's cardinality rule means a caller must not be able to mint a Prometheus
series. `gateway`'s unknown-method arm is left uninstrumented on purpose for
precisely that reason; a rule that demanded a record there would be demanding a
D67 violation. The omission stays visible in the code because a catch-all arm is
visibly a catch-all.

A LABEL IS COSMETIC AND A VERDICT IS NOT, so the two resolve differently. An
ambiguous callee fails closed (above); an ambiguous or absent label constant
falls back to the identifier the code uses and NEVER to a failure. Resolving
constants across the crate is what turns `gateway`'s `DISCOVER` and `TOOLS_LIST`
into `server/discover` and `tools/list` in a message an operator reads — those
constants stayed in `src/http.rs` when the dispatch moved to
`src/http/dispatch.rs` (ledger 827). Making a naming defect able to redden a tree
would be trading a real gate for a cosmetic one.

WHAT IT MUST NOT DO is fire on something that is not a handler. An earlier
version flagged every `async fn` in a file once a service impl appeared anywhere
in it, so adding tests failed the check that exists to encourage tests. A check
that cries wolf gets ignored, and then it protects nothing. That is why helpers
(`reply`, `origin_ok`, `shape`), the 405 fallback, and every function the router
never reaches are outside the rule entirely. So are `handle`'s own early returns
for malformed JSON, a failed `validate` and a header cross-check: no bounded
method label has been read yet at that point, so there is nothing to record
under, which is the same D67 reason the catch-all is excluded.

FITTED TO THE SHAPES THAT EXIST, DELIBERATELY. The calls credit travels through
are a bare `name(...)`, `self.name(...)`, `Self::name(...)` and a module-qualified
`a::b::name(...)`, optionally followed by `.await`. A method call on some other
receiver — `bucket.start(...)` — is NOT followed, even though the crate table
would often resolve it, because that is the form most likely to collide with a std
method and certify a handler by accident. When a repository delegates through a
field rather than through `self`, extend this with that real example in hand
rather than an imagined one.

AN EXEMPTION IS A CLAIM, NOT A CHECK. This file verifies that a marker carries a
reason; it can never verify that the reason is TRUE. So an
`observe-coverage: exempt` line is a sentence under human review exactly like any
other line of the diff, and the marker must sit adjacent to what it exempts —
see `_exempt_reason`, whose walk used to skip blank lines and could therefore
reattach a stale reason to unrelated code (ledger 838).
"""

import os
import re
import sys

# --------------------------------------------------------------------------
# gRPC — tonic gives a closed set of named methods; take it.
# --------------------------------------------------------------------------

# The impl blocks we care about — a generated tonic server trait.
SERVICE_IMPL = re.compile(r"impl\s+\w*Service\s+for\s+\w+")
ASYNC_FN = re.compile(r"\basync\s+fn\s+(\w+)\s*\(")


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

    A `#[cfg(test)]` module written INLINE in a production file is a different
    question and is handled differently — see `_blank_cfg_test`. Skipping the
    whole file for one is the mistake above; leaving its contents in a crate-wide
    name table is the mistake ledger 824 could have introduced.
    """
    norm = path.replace(os.sep, "/")
    return norm.endswith("tests.rs") or "/tests/" in f"/{norm}"


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

    ADJACENCY IS THE BOUND, and it used to be weaker than it read (ledger 838).
    The backward walk SKIPPED blank lines rather than stopping at them, so a
    marker separated from `idx` by any number of blank lines still attached. That
    made a marker able to DRIFT: written for one item, it reattached with no
    warning to whatever ended up below it once its original target was edited
    away. A stale exemption is a silent green on a D67 gate — the one outcome
    this file cannot afford — and Rule B needs the hatch for `gateway`'s
    `tools/call`, so an unsound walk would have shipped alongside a rule that
    depends on it.

    So: the marker is on the item's OWN line, or on a contiguous unbroken run of
    comment lines directly above it. A blank line ends the run, and so does any
    line that is not a comment — an attribute (`#[...]`) already did. `///` is a
    comment for this purpose: a doc block above an item is that item's, and a
    marker written inside one belongs to it.

    Zero markers existed anywhere in the estate when this was tightened, checked
    across all 13 Rust repositories, so nothing was broken by tightening it.

    THE REASON IS NEVER JUDGED, only required to exist. A hook cannot know
    whether "both its paths record" is true; it can only make somebody write the
    sentence down where a reviewer reads it. So an exemption is a claim under
    review like any other line of the diff, not a fact this gate has checked.

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
        # A BLANK LINE STOPS THE WALK, which is the whole of the fix: `""` does
        # not start with `//`, so the same test now ends the run that used to
        # skip over it.
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
# `post(handle)` or `post(dispatch::handle)` in a `.route(...)`. The negative
# lookbehind on `.` is what keeps `headers.get(...)` and `.iter().any(...)` out:
# a METHOD call is not a route. The optional path lets a registration name a
# handler in another module, which is how a split writes it before the module
# grows a `routes()` of its own.
METHOD_ROUTER = re.compile(
    r"(?<![\w.])(?:get|post|put|delete|patch|head|options|trace|any)"
    r"\s*\(\s*((?:\w+\s*::\s*)*\w+)\s*\)"
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
# THE ONE CALL FORM CREDIT CAN TRAVEL THROUGH, anchored at the start of the text
# it is handed: a result position holds exactly one expression, so there is
# nothing to scan for. `self.name(` first, because `self` also reads as a bare
# word and the qualified branch would otherwise swallow it. `a::b::name(` carries
# a module hint; a bare `name(` carries none.
#
# This replaced a `finditer` over every call in the body. That search is what made
# crediting unsound: it reached a `Call::start` down any of a body's calls,
# including one on a path the caller never takes. An anchored match cannot.
RESULT_CALL = re.compile(
    r"\A(?:self\s*\.\s*(?P<method>\w+)"
    r"|(?P<path>(?:\w+\s*::\s*)+)?(?P<fn>\w+))\s*\("
)
# What may follow the call and still leave it the whole of the result: `.await`,
# whitespace, and a match arm's trailing comma. `?` IS ABSENT DELIBERATELY — it
# is a second exit, and a unit with two exits gets no credit.
TAIL_SUFFIX = re.compile(r"\A(?:\s|,|\.\s*await\b)*\Z")
# A control-flow expression reads as `name(` to a regex — `if (x) { .. }` — and a
# branch is not a delegation. Also the exits: a `return` or a `break` in a
# callee's prefix means its `Call::start` is not unconditional.
NOT_A_CALL = {
    "if",
    "while",
    "for",
    "match",
    "return",
    "fn",
    "let",
    "else",
    "as",
    "in",
    "move",
    "unsafe",
    "async",
    "await",
    "impl",
    "where",
    "dyn",
    "loop",
    "break",
    "continue",
}
BRANCH_KW = re.compile(r"\b(?:if|match|while|for|loop|else|return|break|continue)\b")
RETURN_KW = re.compile(r"\breturn\b")
SNAKE = re.compile(r"[a-z_][a-z0-9_]*")
CFG_TEST = re.compile(r"#\s*\[\s*cfg\s*\(\s*test\s*\)\s*\]")


def _blank_noncode(text: str) -> str:
    """`text` with string, char and comment CONTENTS replaced by spaces.

    Same length and same newlines, so an offset into one is an offset into the
    other. Every structural scan below runs on this and every substring that
    gets read or reported is taken from the original — which is how a brace in a
    comment or a comma in a string literal stops being able to move an arm
    boundary. `format!("unknown method: {other}")` is exactly that case, and it
    sits inside the arm this hook must classify correctly.

    It also decides what counts as a `Call::start`. Searching the raw text meant
    a doc comment MENTIONING `Call::start` satisfied the check — `iam-db`'s
    `service.rs` and two `build.rs` files carry exactly such a sentence — so the
    search runs on this text and a comment can no longer certify anything.
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


def _blank_cfg_test(blank: str) -> str:
    """`blank` with every inline `#[cfg(test)]` item blanked as well.

    An inline test module lives in a production file, so the path filter cannot
    see it — and a crate-wide name table that contained its functions would be
    wrong in both directions at once: a test double's `Call::start` could certify
    the production handler it shadows, and a test double named after a real
    handler would be an ambiguity that reddens a green tree.

    `#[cfg(test)] mod tests;` — the DECLARATION every service file here ends with
    — has no body to blank and is left alone; the file it names is caught by
    `_is_test_source`. `#[cfg(not(test))]` does not match, deliberately: that
    code ships.
    """
    out = list(blank)
    for m in CFG_TEST.finditer(blank):
        i = m.end()
        while i < len(blank) and blank[i] not in "{;":
            i += 1
        if i >= len(blank) or blank[i] == ";":
            continue
        end = _match_brace(blank, i)
        if end < 0:
            continue
        for k in range(m.start(), end):
            if out[k] != "\n":
                out[k] = " "
    return "".join(out)


def _functions(blank: str):
    """(name, decl_start, body_start, body_end) for every `fn` in the file.

    A LIST rather than a dict: one file may define two functions of the same
    name in two modules, and a dict silently kept the last.

    `decl_start` is the `fn` keyword rather than the opening brace, because that
    is where an exemption comment sits — above the signature, which for
    `tools_call` is five lines long.
    """
    out = []
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
            out.append((m.group(1), m.start(), brace, end))
    return out


# --------------------------------------------------------------------------
# The crate index — built once per crate, because that is now the unit.
# --------------------------------------------------------------------------

AMBIGUOUS = object()


class Fn:
    """One `fn` definition, and enough about it to be resolved and then read."""

    __slots__ = ("name", "path", "decl", "lo", "hi", "module")

    def __init__(self, name, path, decl, lo, hi, module):
        self.name = name
        self.path = path
        self.decl = decl
        self.lo = lo
        self.hi = hi
        self.module = module


class Crate:
    """Every module of one crate, indexed by name.

    THE RESOLUTION LADDER lives in `resolve`, and the reason it stops rather than
    guesses is in this module's docstring: a wrong follow is a false green, and a
    false green is the only outcome this hook cannot afford.
    """

    def __init__(self, root):
        self.root = root
        self.files = {}  # path -> (text, blank)
        self.fns = {}  # name -> [Fn]
        self.consts = {}  # name -> [(path, value)]
        self.sources = []  # scan order, deterministic
        self.has_router = False

    def add(self, path: str) -> None:
        if path in self.files or _is_test_source(path):
            return
        try:
            text = open(path, encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            return
        blank = _blank_cfg_test(_blank_noncode(text))
        self.files[path] = (text, blank)
        self.sources.append(path)
        module = _module_of(self.root, path)
        for name, decl, lo, hi in _functions(blank):
            self.fns.setdefault(name, []).append(Fn(name, path, decl, lo, hi, module))
        for name, value in CONST_STR.findall(text):
            self.consts.setdefault(name, []).append((path, value))
        if ROUTER.search(blank):
            self.has_router = True

    def resolve(self, name: str, module_hint, frm: str):
        """A callee, or None (not in this crate), or AMBIGUOUS (do not guess)."""
        cands = self.fns.get(name)
        if not cands:
            return None
        if module_hint:
            hit = [
                c
                for c in cands
                if c.module is not None
                and (c.module == module_hint or c.module.endswith("::" + module_hint))
            ]
            if len(hit) == 1:
                return hit[0]
            if len(hit) > 1:
                return AMBIGUOUS
            # No module of that name defines it. Fall through rather than refuse:
            # the qualifier may name a TYPE (`Self::`, `Iam::`), which this does
            # not model, and the rungs below are still sound.
        same = [c for c in cands if c.path == frm]
        if same:
            return same[0]
        if len(cands) == 1:
            return cands[0]
        return AMBIGUOUS

    def const_value(self, name: str, frm: str):
        """A `const NAME: &str`'s value, or None when the code must speak for it.

        Same-file first, then a value the whole crate agrees on. A DISAGREEMENT
        returns None and the caller keeps the identifier — a label is cosmetic
        and must never be able to fail a tree.
        """
        cands = self.consts.get(name)
        if not cands:
            return None
        same = [v for p, v in cands if p == frm]
        if same:
            return same[0]
        values = {v for _, v in cands}
        return values.pop() if len(values) == 1 else None


def _module_of(root, path: str):
    """`src/http/dispatch.rs` -> `http::dispatch`, or None when it is not a module.

    `src/lib.rs`, `src/main.rs` and any `mod.rs` name the module their directory
    is. A file outside `src/` — a `build.rs` — is not part of the crate's module
    tree, so it gets no name and no module qualifier can ever select it.
    """
    if root is None:
        return None
    rel = os.path.relpath(path, root).replace(os.sep, "/")
    if not rel.startswith("src/"):
        return None
    parts = rel[len("src/") :].split("/")
    leaf = parts[-1][:-3] if parts[-1].endswith(".rs") else parts[-1]
    parts = parts[:-1] if leaf in ("lib", "main", "mod") else parts[:-1] + [leaf]
    return "::".join(parts)


def _crate_root(path: str):
    """The nearest ancestor directory whose `Cargo.toml` declares a `[package]`.

    A workspace manifest is not a crate — `estate` carries one with two members —
    so the walk keeps going past it. The walk also stops at a `.git`: a
    repository is the widest thing this hook may read, and a machine's home
    directory is not an index.
    """
    d = os.path.dirname(os.path.abspath(path))
    while True:
        manifest = os.path.join(d, "Cargo.toml")
        if os.path.isfile(manifest):
            try:
                if re.search(r"^\s*\[package\]", open(manifest, encoding="utf-8").read(), re.M):
                    return d
            except OSError:
                pass
        if os.path.exists(os.path.join(d, ".git")):
            return None
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _one_spelling(paths):
    """`paths` as the walk below spells them: relative to the working directory.

    ONE FILE MUST HAVE ONE NAME IN THE INDEX. The crate walk yields paths
    relative to the working directory, which is how pre-commit passes them too —
    but a caller invoking this by hand with an absolute path would have indexed
    the same file twice under two spellings, and two identical definitions of one
    name is an AMBIGUOUS that reddens a green tree. Normalising here is cheaper
    than a rule about how the hook may be called.
    """
    cwd = os.getcwd()
    out, seen = [], set()
    for path in paths:
        rel = os.path.relpath(path, cwd) if os.path.isabs(path) else os.path.normpath(path)
        if rel not in seen:
            seen.add(rel)
            out.append(rel)
    return out


def _crates(paths):
    """Group `paths` into crates, each indexed ONCE.

    pre-commit hands over a list of changed files, and a crate-wide index read
    once per file would report every unit as many times as the crate has changed
    files. It would also make the numbers unreadable, which is how a measurement
    stops being one.

    A path with no crate around it is its own scope, which is exactly the
    file-local behaviour this hook had before ledger 824 — the safe answer for a
    stray `.rs` outside any package.
    """
    crates = {}
    order = []
    for path in _one_spelling(paths):
        root = _crate_root(path)
        key = root or os.path.abspath(path)
        if key not in crates:
            crates[key] = Crate(root)
            order.append(key)
            if root is not None:
                for dirpath, dirnames, filenames in os.walk(os.path.join(root, "src")):
                    dirnames[:] = sorted(d for d in dirnames if d != "target")
                    for name in sorted(filenames):
                        if name.endswith(".rs"):
                            full = os.path.join(dirpath, name)
                            crates[key].add(os.path.relpath(full, os.getcwd()))
        # A passed file outside `src/` still gets checked, as it did before.
        crates[key].add(path)
    return [(crates[k], list(crates[k].sources)) for k in order]


# --------------------------------------------------------------------------
# Following the code — the same search for both transports.
# --------------------------------------------------------------------------


def _match_bracket(text: str, open_idx: int) -> int:
    """Index just past the bracket closing the one at `open_idx`, or -1.

    Every bracket kind counts towards one depth, so a `(` closed after a `[` or a
    `{` inside it is still found. `_match_brace` above is the brace-only variant
    the structural scans use; this one is for reading ONE call's argument list,
    where the closing bracket is the end of the expression.
    """
    depth = 0
    for i in range(open_idx, len(text)):
        c = text[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                return i + 1
    return -1


def _inner_span(blank: str, lo: int, hi: int):
    """The unit's body with its braces removed, when it has any.

    TWO SHAPES REACH HERE and only one is braced. Every `fn`, and a match arm
    written `=> { .. }`, hands over the brace positions. An arm written as a bare
    expression — `DISCOVER => measured(DISCOVER, || discover(&id))` — has none,
    and in `gateway` that is the COMMON shape rather than an edge case: it is
    what both credited arms are written as.
    """
    if lo < len(blank) and blank[lo] == "{":
        return lo + 1, max(lo + 1, hi - 1)
    return lo, hi


def _top_segments(blank: str, lo: int, hi: int):
    """`blank[lo:hi]` cut at every `;` that sits at bracket depth zero.

    Depth counts every bracket kind, so a `;` inside a nested block, a closure
    body or a macro argument is not a cut. The last segment is the body's tail
    expression when the body has one, and empty when the body ends in `;`.
    """
    segs, start, depth, i = [], lo, 0, lo
    while i < hi:
        c = blank[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == ";" and depth == 0:
            segs.append((start, i))
            start = i + 1
        i += 1
    segs.append((start, hi))
    return segs


def _result_span(blank: str, lo: int, hi: int):
    """The unit's RESULT POSITION — (start, end) — or None when it has no single one.

    CONJUNCT 1 OF THE CREDITING RULE. A callee's `Call::start` may stand in for
    its caller's only when the call IS the caller's result: its tail expression,
    or its sole `return`. More than one way out of the unit means no credit,
    because a `Call` on one of them is not a `Call` on the others — which is
    exactly the loosening this replaces.

    So the answer is None when:

      - the body contains a `?`. That is an exit, and an invisible one.
      - the body contains more than one `return`, or one that is not its last
        statement. A `return` inside an `if` leaves by a second door.
      - the body ends in `;`, so there is no tail expression to be the result.
      - the result opens a block at depth zero — a `match`, an `if`, a `for`. Each
        arm or branch is its own path, and none of them is "the" result.

    Every one of those refusals is a REFUSAL TO CREDIT, never a verdict: the unit
    then has to open a `Call` in its own body. So an over-strict reading here
    costs a false red, which is loud, rather than a false green, which is silent —
    and ADR-0645 forbids only the second direction.
    """
    ilo, ihi = _inner_span(blank, lo, hi)
    body = blank[ilo:ihi]
    if "?" in body:
        return None
    segs = _top_segments(blank, ilo, ihi)
    returns = len(RETURN_KW.findall(body))
    if returns:
        if returns > 1:
            return None
        filled = [(a, b) for a, b in segs if blank[a:b].strip()]
        if not filled:
            return None
        s, e = filled[-1]
        if not blank[s:e].lstrip().startswith("return"):
            return None  # the sole `return` is not the last statement
        s = blank.index("return", s) + len("return")
    else:
        s, e = segs[-1]
        if not blank[s:e].strip():
            return None  # the body ends in `;` — no tail expression
    depth = 0
    for k in range(s, e):
        c = blank[k]
        if c in "([{":
            if c == "{" and depth == 0:
                return None  # a block, not a single result
            depth += 1
        elif c in ")]}":
            depth -= 1
    return s, e


def _result_call(blank: str, lo: int, hi: int):
    """(name, module_hint) for the ONE call in the unit's result position, or None.

    THE OUTERMOST CALL ONLY, which is the sound reading of "the call is the tail
    expression". `measured(DISCOVER, || discover(&id))` credits `measured` and
    never the closure's `discover`: the closure is an argument, and an argument is
    not the result.

    The forms are `name(..)`, `self.name(..)`, `Self::name(..)` and
    `a::b::name(..)`, with an optional `.await`. A method call on some other
    receiver is not followed, and neither is an associated function of a type this
    crate does not model — see the module docstring, and
    `RESOLVING A TYPE-QUALIFIED CALL BY ITS BARE NAME` below, which is a measured
    false green rather than a hypothesis: `iam` defines `fn new` in more than one
    module, so `Response::new(..)` reached the crate table under the name `new`.
    """
    span = _result_span(blank, lo, hi)
    if span is None:
        return None
    seg = blank[span[0] : span[1]].strip()
    m = RESULT_CALL.match(seg)
    if not m:
        return None
    name = m.group("method") or m.group("fn")
    if name in NOT_A_CALL:
        return None
    close = _match_bracket(seg, m.end() - 1)
    if close < 0 or not TAIL_SUFFIX.match(seg[close:]):
        return None  # something follows the call, so the call is not the result
    hint = None
    raw = m.group("path")
    if raw:
        parts = [x for x in raw.replace(" ", "").split("::") if x]
        parts = [x for x in parts if x not in ("crate", "super", "self", "Self")]
        if not all(SNAKE.fullmatch(x) for x in parts):
            return None  # a foreign type's associated function
        if parts:
            hint = parts[-1]
    return name, hint


def _opens_call_unconditionally(blank: str, lo: int, hi: int) -> bool:
    """Is `Call::start` the FIRST STATEMENT of `blank[lo:hi]`, before any branch?

    CONJUNCT 2 OF THE CREDITING RULE, and the reason it is not "at brace depth
    one" is `gateway`'s own two shapes. Depth is where an earlier attempt at this
    stopped, and it is measurably too weak:

      - `dispatch::measured` opens its `Call` as the first thing its body does,
        before the work it measures. Nothing it is handed can avoid that `Call`,
        so a caller whose whole result is `measured(..)` is instrumented BY it.
      - `dispatch::tools_call` opens its `Call` at the bottom of four statements,
        below an early `return` on the throttle path. Its `Call` is CONDITIONAL —
        and yet the `match` and `if let` blocks above it all open AND CLOSE, so
        the net bracket depth where its `Call::start` sits is one, exactly like
        `measured`'s. A depth test credits both and cannot tell them apart.

    Nothing before the `Call::start` may therefore be a statement boundary, a
    block, or an exit: no `;` at depth zero, no `{` at depth zero, no `?`, no
    control-flow keyword, and no `|` (a closure would put the `Call` somewhere
    that runs when somebody else decides, or never).

    A callee that fails this is not refused. Its CALLER is refused credit, and
    then has to open a `Call` in its own body — see `_result_span` on which
    direction an over-strict rule errs in.
    """
    ilo, ihi = _inner_span(blank, lo, hi)
    idx = blank.find(CALL, ilo, ihi)
    if idx < 0:
        return False
    prefix = blank[ilo:idx]
    if "?" in prefix or BRANCH_KW.search(prefix):
        return False
    depth = 0
    for c in prefix:
        if c in "([{":
            if c == "{" and depth == 0:
                return False
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0 and c in ";|":
            return False
    return True


def _instrumented(crate: Crate, path: str, lo: int, hi: int):
    """Is this unit instrumented — by itself, or by the one callee it cannot avoid?

    Returns (True, None) or (False, hint) where `hint` is (kind, name) naming why
    no credit was available. That is not a separate failure: the unit has already
    failed for want of a `Call::start`. It only makes the message say WHERE the
    rule stopped instead of implying the code is empty.

    ITS OWN BODY IS ACCEPTED ON ANY PATH, unchanged and deliberately: a handler
    that opens its `Call` inside an `if` has still made a decision about its own
    paths, and demanding otherwise would redden trees over somebody else's
    function.

    A CALLEE IS ONE HOP AND NEVER TWO. The recursion this replaces is what ledger
    838 measured as a loosening: it followed `guard`, found no `Call` it would
    accept there, then followed `guard`'s OWN callee `too_many` and accepted the
    `Call` on its throttle-refusal arm — so deleting a handler's own `Call::start`
    left the handler green two hops out. There is no chain in this estate to
    justify the depth: every gRPC wrapper opens its own `Call`, and `gateway`'s
    only credited units are two arms whose whole body is one call. One hop is also
    the tighter direction, which is the direction ADR-0645 allows.
    """
    blank = crate.files[path][1]
    if CALL in blank[lo:hi]:
        return True, None
    call = _result_call(blank, lo, hi)
    if call is None:
        return False, ("no-result", None)
    name, module_hint = call
    target = crate.resolve(name, module_hint, path)
    if target is AMBIGUOUS:
        return False, ("ambiguous", name)
    if target is None:
        # Only a snake_case name is worth naming as a dead end. `Some(..)` and
        # `Outcome { .. }` are constructors, not calls the rule failed to follow,
        # and reporting them as such makes the hint noise.
        return False, (("unfollowed", name) if SNAKE.fullmatch(name) else ("no-result", None))
    tblank = crate.files[target.path][1]
    if _opens_call_unconditionally(tblank, target.lo, target.hi):
        return True, None
    return False, ("conditional", name)


def _check(path: str, label: str, lo: int, hi: int, idx: int, crate: Crate):
    """The one verdict point, for both transports."""
    text = crate.files[path][0]
    ok, hint = _instrumented(crate, path, lo, hi)
    if ok:
        return
    reason = _exempt_reason(text, idx)
    if reason is not None:
        if not reason:
            yield f"{path}: `{label}` is marked exempt with no reason"
        return
    kind = hint[0] if hint else None
    if kind == "unfollowed":
        tail = f" (the call to `{hint[1]}` leaves this crate, so it cannot be credited)"
    elif kind == "ambiguous":
        tail = (
            f" (`{hint[1]}` is this unit's result, but is a name this crate defines in "
            "more than one module — qualify the call with its module)"
        )
    elif kind == "conditional":
        tail = (
            f" (`{hint[1]}` is this unit's result, but its `Call::start` is not its "
            "first statement, so it does not run on every path into it)"
        )
    else:
        tail = ""
    yield f"{path}: `{label}` opens no observe::Call{tail}"


# --------------------------------------------------------------------------
# gRPC handlers.
# --------------------------------------------------------------------------


def _grpc_handlers(blank: str):
    """(name, decl, body_start, body_end) for each handler in a service impl.

    Scoped to the impl blocks, not the file. The first version searched the whole
    file once a service impl appeared anywhere in it, so an ordinary `async fn`
    elsewhere — a helper, a constructor — was reported as an uninstrumented
    handler. That is a check firing on something it was never about, which is
    worse than one that misses: it teaches people the check is noise.

    EVERY impl block, not the first. `search` stopped at one, so a second service
    in one file went unchecked — and a file holding two is exactly what the
    500-line ceiling encourages.

    Structure comes off the BLANKED text now. It used to be counted on the raw
    lines, so a brace inside a doc comment could end a handler body early and the
    search would then look for `Call::start` in the wrong extent.
    """
    fns = _functions(blank)
    for impl in SERVICE_IMPL.finditer(blank):
        open_idx = blank.find("{", impl.end())
        if open_idx < 0:
            continue
        end = _match_brace(blank, open_idx)
        if end < 0:
            continue
        for name, decl, lo, hi in fns:
            if not open_idx < decl < end:
                continue
            head = blank.rfind("async", max(0, decl - 16), decl)
            if head < 0 or not ASYNC_FN.match(blank, head):
                continue
            yield name, decl, lo, hi


# --------------------------------------------------------------------------
# HTTP handlers.
# --------------------------------------------------------------------------


def _arms(text: str, blank: str, open_idx: int):
    """Yield (pattern_src, pattern_idx, body_span) for a match block's arms.

    Arm extents are found on the blanked text by depth, so a nested closure's
    `=>` and a struct literal's braces cannot end an arm early. An arm whose
    extent cannot be determined is not silently skipped — the caller reports the
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
            yield pattern, pat_idx, (j, body_end)
            i = body_end
            while i < end and (blank[i].isspace() or blank[i] == ","):
                i += 1
            arm_start = i
            continue
        i += 1


def _labels(pattern: str, crate: Crate, frm: str):
    """The bounded labels an arm serves, or None when it is a catch-all.

    A CATCH-ALL IS NOT A HANDLER (D67). `_` and a binding like `other` both mean
    "whatever the caller sent", and the only label available for such an arm is
    a string the caller invented — so requiring a record there would require a
    caller-mintable Prometheus series, which is the thing D67 forbids.

    Rust tells a const pattern from a binding by resolving the name, and this
    does the same with the CRATE's `const NAME: &str` table. That distinction is
    the whole classification: `DISCOVER` and `other` are both bare identifiers,
    and they are opposite answers. The table is crate-wide because `gateway`'s
    constants stayed in `src/http.rs` when the dispatch moved out (ledger 827).

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
            continue
        value = crate.const_value(part.split("::")[-1], frm)
        if value is not None:
            labels.append(value)
        elif re.fullmatch(r"[A-Z][A-Z0-9_]*", part) or "::" in part:
            # A const this crate does not declare, or one two modules disagree
            # about. Its value is not readable here, so the identifier is the
            # best name a failure can carry — never a failure of its own.
            labels.append(part)
        else:
            return None  # a binding: catch-all
    return labels or None


def http_failures(crate: Crate, path: str):
    """Every HTTP handler registered in `path` that is neither instrumented nor exempt.

    The ROUTER GATE IS THE CRATE'S, not the file's — see this module's docstring.
    A `.route(...)` is a registration wherever it is written, and the function it
    names is resolved across every module of the crate.
    """
    if not crate.has_router:
        return
    text, blank = crate.files[path]

    for reg in METHOD_ROUTER.finditer(blank):
        segs = [
            s
            for s in reg.group(1).replace(" ", "").split("::")
            if s and s not in ("crate", "super", "self")
        ]
        if not segs:
            continue
        name = segs[-1]
        hint = segs[-2] if len(segs) > 1 and SNAKE.fullmatch(segs[-2]) else None
        target = crate.resolve(name, hint, path)
        if target is None or target is AMBIGUOUS:
            reason = _exempt_reason(text, reg.start())
            if reason is None:
                what = (
                    "this crate defines in more than one module, so which one the "
                    "router reaches cannot be decided here"
                    if target is AMBIGUOUS
                    else "is not defined in this crate, so whether it is instrumented "
                    "cannot be checked here"
                )
                yield f"{path}: route handler `{name}` {what}"
            elif not reason:
                yield f"{path}: `{name}` is marked exempt with no reason"
            continue
        yield from _registered(crate, target)


def _registered(crate: Crate, target: Fn):
    """The units inside one route-registered function, wherever it lives."""
    text, blank = crate.files[target.path]
    dispatch = DISPATCH.search(blank, target.lo, target.hi)
    if not dispatch:
        # The ordinary REST shape: the registered function IS the handler.
        yield from _check(target.path, target.name, target.lo, target.hi, target.decl, crate)
        return
    open_idx = dispatch.end() - 1
    if _match_brace(blank, open_idx) < 0:
        yield f"{target.path}: the method dispatch in `{target.name}` could not be read"
        return
    for pattern, pat_idx, (lo, hi) in _arms(text, blank, open_idx):
        labels = _labels(pattern, crate, target.path)
        if labels is None:
            continue  # a catch-all is not a handler — see `_labels`
        for label in labels:
            yield from _check(target.path, label, lo, hi, pat_idx, crate)


# --------------------------------------------------------------------------


def main(paths):
    failures = []
    for crate, sources in _crates(paths):
        for path in sources:
            for name, decl, lo, hi in _grpc_handlers(crate.files[path][1]):
                failures.extend(_check(path, name, lo, hi, decl, crate))
            failures.extend(http_failures(crate, path))

    # A handler registered on two routes is one handler. Reporting it twice makes
    # a reader count instead of read.
    seen = set()
    unique = [f for f in failures if not (f in seen or seen.add(f))]

    if unique:
        print("observe-coverage: uninstrumented handlers\n")
        for f in unique:
            print(f"  {f}")
        print(
            "\nEvery handler must open a `Call::start(...)` (D67). The Call\n"
            "records on Drop, so an early return cannot lose the record — starting\n"
            "one is the whole requirement.\n\n"
            "A module that quietly stops emitting looks exactly like a module\n"
            "nobody called, which is the reading that would break D15.\n\n"
            "A handler that DELEGATES is instrumented by its callee, across the\n"
            "modules of a crate, on two conditions and no fewer:\n\n"
            "  1. the call is the handler's whole result — its tail expression or\n"
            "     its sole return. More than one way out means no credit.\n"
            "  2. the callee opens its `Call::start` as its FIRST statement, so\n"
            "     nothing the handler does can avoid it.\n\n"
            "A helper that records only its own refusals therefore instruments\n"
            "nothing; open a `Call` in the handler instead. Resolution refuses to\n"
            "guess between two same-named functions: qualify the call with its\n"
            "module.\n\n"
            "An omission that is deliberate is written down, above the arm or the\n"
            "function, WITH A REASON:\n\n"
            "    // observe-coverage: exempt — why this one records nothing\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
