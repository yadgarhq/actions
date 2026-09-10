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

BOTH TRANSPORTS NOW RUN THROUGH THE SAME SEARCH. The gRPC side used to be a
substring test on the wrapper's own text, which is what made `iam`'s delegation
invisible; it now follows calls exactly as the HTTP side does, and accepts the
same written-down exemption. Two rules for one question was the reason a split
could break one transport and not the other.

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

FITTED TO THE SHAPES THAT EXIST, DELIBERATELY. The calls this search follows are
a bare `name(...)`, `self.name(...)`, `Self::name(...)` and a module-qualified
`a::b::name(...)`. A method call on some other receiver — `bucket.start(...)` —
is NOT followed, even though the crate table would often resolve it, because that
is the form most likely to collide with a std method and certify a handler by
accident. When a repository delegates through a field rather than through `self`,
extend this with that real example in hand rather than an imagined one.
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

    Looked for on the line itself and on the `//` lines above it — but the
    backward walk SKIPS blank lines rather than stopping at them, so "above
    it" tolerates any number of blank lines between the marker and `idx`. It
    stops only at the first line that is neither blank nor `//`. So the
    marker CAN drift: if the item it was written for is edited away and
    something else ends up directly below it, separated only by blank lines
    and other comments, the reason reattaches to that something else with no
    warning. The bound this function actually enforces is "no non-comment
    code between the marker and the thing it exempts", not adjacency.

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
# The call forms the search follows. `self.name(` first, because `self` also
# reads as a bare word and the qualified branch would otherwise swallow it.
# `a::b::name(` carries a module hint; a bare `name(` carries none.
#
# AN ATTRIBUTE IS NOT A CALL, and `#[expect(...)]` reads exactly like one — four
# of them in `iam` were reported as the place the search gave up, which is how a
# useful hint becomes noise. The two lookbehinds are what tell `#[expect(` from
# `expect(`; `.expect(` was already excluded by the `.` in the first one.
CALL_SITE = re.compile(
    r"\bself\s*\.\s*(?P<method>\w+)\s*\("
    r"|(?<![\w:.])(?<!#\[)(?<!#!\[)(?P<path>(?:\w+\s*::\s*)+)?(?P<fn>\w+)\s*\("
)
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
}
SNAKE = re.compile(r"[a-z_][a-z0-9_]*")
CFG_TEST = re.compile(r"#\s*\[\s*cfg\s*\(\s*test\s*\)\s*\]")
# How deep the search follows a chain of calls. A cycle is already impossible —
# every callee is visited once — so this only bounds a pathological fan-out.
MAX_DEPTH = 16


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


def _call_sites(blank: str):
    """(name, module_hint) for every call this search is willing to follow.

    The forms are listed in this module's docstring. A method call on a receiver
    other than `self` is deliberately absent, and so is an associated function of
    a type this crate does not model: `Instant::now()`, `Response::new(...)`,
    `Duration::from_secs(...)`. Dropping those is not a convenience.

    RESOLVING A TYPE-QUALIFIED CALL BY ITS BARE NAME IS THE FALSE-GREEN THIS HOOK
    CANNOT AFFORD, measured while proving the mutation for ledger 824. `iam`
    defines `fn new` in more than one module, so `Response::new(...)` inside a
    handler reached the crate-wide table under the name `new` — which sent the
    search into an unrelated constructor and named it in the message. A
    constructor of somebody else's type is never where a `Call::start` lives, so
    the search does not go there. `Self::` and `self.` are stripped and DO
    resolve: those name this crate's own code.

    A qualifier that is a real crate type — `Iam::helper()` — is skipped too, and
    that is a miss rather than a hole: the unit fails for want of a `Call::start`
    it does not have, which is the safe direction.
    """
    for m in CALL_SITE.finditer(blank):
        name = m.group("method") or m.group("fn")
        if name in NOT_A_CALL:
            continue
        hint = None
        raw = m.group("path")
        if raw:
            segs = [s for s in raw.replace(" ", "").split("::") if s]
            segs = [s for s in segs if s not in ("crate", "super", "self", "Self")]
            if not all(SNAKE.fullmatch(s) for s in segs):
                continue  # a foreign type's associated function — see above
            if segs:
                hint = segs[-1]
        yield name, hint


def _opens_call(blank: str, lo: int, hi: int, top_only: bool) -> bool:
    """Does `blank[lo:hi]` open a `Call::start`, and does it do so on every path?

    A HELPER THAT INSTRUMENTS ONLY ITS REFUSALS DOES NOT INSTRUMENT ITS CALLER,
    and `top_only` is that rule. It is what stops crate-wide following from being
    a weakening rather than a fix, and it comes from two real shapes in `gateway`
    rather than from taste:

      - `dispatch::measured` opens its `Call` as the first statement of its body,
        on every path. Two label arms delegate to it and nothing else, and they
        are instrumented BY it — a `Call` the caller cannot avoid.
      - `gate::guard` opens one inside the arms of a `match` that decide a
        REFUSAL. Its success path opens none. `top_only` stops that nested
        `Call` from certifying `guard` ITSELF when `guard` is the callee being
        checked — this is the ONE hop this function bounds.

    **THIS RULE DOES NOT CLOSE THE CASE THAT MOTIVATED IT, and the residual is
    measured rather than assumed.** `guard` still calls `too_many`, and
    `too_many` opens its `Call` as the first statement of ITS OWN body — depth
    1, unconditional. `_instrumented`'s recursion follows `guard`'s callees
    after `_opens_call` returns False for `guard`, reaches `too_many` on the
    next hop, and `top_only` passes it there. So deleting `admin_create_user`'s
    own `Call::start` still leaves the handler green: the search never opens a
    `Call` inside `guard`, but it reaches one inside `guard`'s own callee two
    hops out. That is the silent stop-emitting this hook exists to catch, and
    `top_only` alone does not catch it — closing it needs a rule about whether
    a followed callee's `Call` sits in the caller's own result position, which
    is dataflow this function does not do.

    `top_only` is False for the unit's OWN body, unchanged: a handler that opens
    its `Call` inside an `if` has still made a decision about its own paths, and
    demanding otherwise would redden trees over a rule about somebody else's
    function.
    """
    i = blank.find(CALL, lo, hi)
    while i >= 0:
        if not top_only:
            return True
        # `lo` is the callee's opening brace, so depth 1 means "directly in the
        # body" — not inside a match arm, an `if`, or a closure.
        depth = 0
        for c in blank[lo:i]:
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
        if depth == 1:
            return True
        i = blank.find(CALL, i + 1, hi)
    return False


def _instrumented(crate: Crate, path: str, lo: int, hi: int, own=True, seen=None, depth=0):
    """Is `Call::start` reachable from `path[lo:hi]` through calls in this crate?

    Returns (True, None) or (False, hint) where `hint` is (kind, name) naming
    where the search stopped — a callee outside the crate, or a name the crate
    defines more than once. That is not a separate failure: the unit has already
    failed for want of a `Call::start`. It only makes the message say WHERE the
    search stopped instead of implying the code is empty.

    `own` distinguishes the unit's own body from a function it delegates to — see
    `_opens_call`, which is where that distinction is spent.

    `seen` is keyed by DEFINITION, not by name. Keying it by name was correct
    while the search was file-local and is not now: `gateway` defines three
    `routes`, and one visit would have blocked the other two.
    """
    blank = crate.files[path][1]
    if _opens_call(blank, lo, hi, top_only=not own):
        return True, None
    if depth >= MAX_DEPTH:
        return False, None
    seen = seen if seen is not None else set()
    hint = None
    for name, module_hint in _call_sites(blank[lo:hi]):
        target = crate.resolve(name, module_hint, path)
        if target is AMBIGUOUS:
            hint = hint or ("ambiguous", name)
            continue
        if target is None:
            # Only a snake_case name is worth naming as a dead end. `Some(..)`
            # and `Outcome { .. }` are constructors, not calls the search failed
            # to follow, and reporting them as such makes the hint noise.
            if SNAKE.fullmatch(name):
                hint = hint or ("unfollowed", name)
            continue
        key = (target.path, target.lo)
        if key in seen:
            continue
        seen.add(key)
        ok, deeper = _instrumented(
            crate, target.path, target.lo, target.hi, False, seen, depth + 1
        )
        if ok:
            return True, None
        hint = hint or deeper
    return False, hint


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
    tail = ""
    if hint and hint[0] == "unfollowed":
        tail = f" (the search could not follow the call to `{hint[1]}`)"
    elif hint and hint[0] == "ambiguous":
        tail = (
            f" (the search stopped at `{hint[1]}`, which this crate defines in more "
            "than one module — qualify the call with its module)"
        )
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
            "The search follows calls ACROSS the modules of a crate, so a handler\n"
            "that delegates is instrumented wherever its `Call::start` really is.\n"
            "It refuses to guess between two same-named functions: qualify the call\n"
            "with its module and the search follows it.\n\n"
            "An omission that is deliberate is written down, above the arm or the\n"
            "function, WITH A REASON:\n\n"
            "    // observe-coverage: exempt — why this one records nothing\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
