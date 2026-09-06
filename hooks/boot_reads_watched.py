#!/usr/bin/env python3
"""ADR-0523: a file read at boot is WATCHED, or it is declared UNWATCHED with a reason.

LEDGER 726. ADR-0523 makes a process exit when the security material it read at
boot is replaced underneath it. Every service builds that set in one expression,
`rotate::watch_set`, and every service's `tests/assembly.rs` asserts the set
against a LITERAL path list. That test kills one mutant and is blind to its
opposite:

  * DELETE a member from `watch_set` and the literal list no longer matches. RED.
  * ADD a file to the boot path and watch nothing. The literal list still
    matches `watch_set`, because `watch_set` did not change. GREEN.

Nothing in the estate compares the files a service READS at boot against the
files it WATCHES. The assembly test asserts the watch set against itself.

THE CLASS IS ALREADY REALISED. `yadgarhq/iam` reads `encryption.key` and
`blind-index.key` at boot -- `src/main.rs` `Keys::from_env`, through
`src/crypto.rs` `read_key` -- and neither is a member of `iam::rotate::watch_set`,
whose five materials are the listener, the upstream, the broker credential, the
enrolment CA and the mounted configuration document. Every rotation test in that
repository is green.

WHAT THIS GATE IS, AND WHY IT IS NOT "THE SAME FAILURE WITH AN EXTRA STEP".
The obvious detector -- a second list, in a test, naming the files boot reads --
fails exactly where the first one does: an author who forgets the watch-set
member forgets the list entry too, and the suite stays green. The direction of
the default is the whole difference.

  * With a second list, FORGETTING IS GREEN.
  * Here, FORGETTING IS RED. An unmarked filesystem read is a violation. The
    author cannot reach a passing state without saying, at the read site, which
    material carries this file into the watch set -- or that it is not carried,
    and why.

The marker is not a list. It lives on the line that does the reading, it is
written in the same edit that adds the read, and its ABSENCE is the failure.

WHERE EACH SIDE OF THE COMPARISON COMES FROM (ADR-0599). A check whose two
sides are derived from one source can never fail, and this estate has now
measured that class five times. The sides here:

  * Side A is the CALL EXPRESSION in the repository's own `src/`, found by
    scanning source text. It is what the author actually wrote, not a list of
    what they meant.
  * Side B is `src/rotate.rs` -- the set of `impl Material for T` blocks and the
    `watch_set` signature. A `WATCHED` marker must NAME one of them.

Neither is computed from the other. A read marked `ADR-0523-WATCHED: Foo` where
`src/rotate.rs` has no `Foo` is refused, so the marker cannot be satisfied by
inventing a name at the read site.

THE RESIDUAL HOLE, STATED PLAINLY. A marker naming a REAL material for the
WRONG path passes: `ADR-0523-WATCHED: ServerTls` on a read of an unrelated key
is not detected. That is lying rather than forgetting, and this gate closes
forgetting. Closing lying needs the mechanism this gate is the cheap stand-in
for: hoist the file-reading half of boot into a library function, route every
read through a shim that records the path in a ledger, and assert in
`tests/assembly.rs` that the ledger and `watch_set` agree. That comparison has
no author-written middle term at all. It does not exist today because no test in
any of these repositories can reach `fn main`, and building it is a boot
refactor in six repositories rather than a hook.

THE EXCLUSION IS PART OF THE DESIGN, NOT A CONCESSION. A gate with no declared
exclusion is a gate that gets deleted the first time it fires on a file somebody
deliberately does not watch -- and `iam`'s two crypto keys are exactly that file,
with the ruling on them reserved. `ADR-0523-UNWATCHED: <reason>` records the
decision where the next reader of that line will find it, and
`git grep ADR-0523-UNWATCHED` answers "which boot reads are unwatched, and why"
in one command. That is the property `ADR-0569-EXCEPTION` is built for, applied
here.

TWO FLOORS AND A FAIL-CLOSED, because there are three ways this can go blind and
they are independent.

  * No Rust source root, or no `.rs` file under one. A gate that searched
    nothing proves nothing (`no_compiled_in_defaults.sh`'s rule, unchanged).
  * No read site judged. Every repository in this estate reads at least its
    database password or its listener key at boot; a run that judged zero found
    none of them, which means the classifier below no longer recognises the
    shape the code is written in.
  * AN UNCLASSIFIED FILESYSTEM TOKEN REFUSES THE RUN. Every `fs::NAME`,
    `File::NAME` and `OpenOptions` in scanned source must be a known read, a
    known write, or a known type mention. A repository that starts reading
    through a call this file does not know would otherwise pass silently, having
    inspected everything except the thing that changed. Refusing is how the gate
    stays honest as the code moves; the fix is one entry in a table below.

`language: script` AND STDLIB ONLY, for the reason `certificate_usages.py`,
`complexity.py`, `observe_coverage.py` and `run_block_size.py` all record:
`language: python` makes pre-commit pip-install this repository, which is not a
package, and the failure surfaces in the CONSUMING repository where nothing
explains it. Under `language: script` the working directory is the consumer's
root, which is what lets the walk below see the consumer's tree.

That forces a hand-written scan of Rust rather than a parser. Two traps make the
naive version wrong, and both are in the real corpus:

  * BRACES INSIDE STRING LITERALS. `tracing::info!("{}", x)` and every format
    string in these repositories carry `{`, so counting braces over raw text to
    find the end of a `#[cfg(test)]` module drifts within a few hundred lines and
    then skips, or fails to skip, an arbitrary span. Strings and comments are
    blanked to spaces FIRST, newlines preserved, so line numbers survive and the
    depth count is over code alone.
  * TEST CODE READS FILES CONSTANTLY. `src/crypto/tests.rs`,
    `src/service/tests.rs` and inline `#[cfg(test)] mod tests` blocks write and
    read fixtures on every run. Scanning them would make the gate's output
    entirely noise, which is how a gate gets an exemption list and stops meaning
    anything.

`scripts/tests/test_boot_reads_watched.py` asserts the blanking pass against
both traps, and asserts the REFUSALS rather than only the pass -- a gate fed
conforming trees alone proves it returns 0, which `true` also does.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# A repository adopting this gate must have a watch set for a marker to name.
ROTATE = Path("src") / "rotate.rs"

# `fs::NAME` calls that read file CONTENT. Each needs a marker.
READ_FS = {"read", "read_to_string", "read_dir"}

# `fs::NAME` calls that do not read content, and the type names in the same
# namespace. Listed rather than inferred: an unknown name refuses the run.
WRITE_FS = {
    "write",
    "create_dir",
    "create_dir_all",
    "remove_file",
    "remove_dir",
    "remove_dir_all",
    "rename",
    "copy",
    "set_permissions",
    "hard_link",
    "soft_link",
}
META_FS = {
    "metadata",
    "symlink_metadata",
    "canonicalize",
    "read_link",
    "exists",
    "try_exists",
}
TYPE_FS = {
    "File",
    "OpenOptions",
    "Metadata",
    "Permissions",
    "DirEntry",
    "ReadDir",
    "FileType",
    "DirBuilder",
}

# `File::NAME`. `open` and `options` reach a read; `create` and `create_new` do
# not. UNLIKE `fs::`, AN UNKNOWN NAME HERE IS IGNORED RATHER THAN REFUSED, and
# the asymmetry is measured rather than a preference: `yadgar-lifecycle` exports
# its own `rotate::File` -- the watch-set descriptor -- whose constructors are
# `File::read` and `File::certificate`, and every service's `src/rotate.rs` calls
# them. Refusing an unknown `File::NAME` reddens all six repositories on a name
# collision with nothing wrong. The hole that opens is narrow and shallow:
# `std::fs::File`'s associated-function set is the standard library's, so it
# cannot grow because somebody edited a repository here.
READ_FILE = {"open", "options"}

# `use std::fs::read;` would let `read(path)` do the work with no `fs::` at the
# call site, and a bare `read(` is far too common a name to match on. Importing
# the FUNCTION is refused so the call always names its module -- which is also
# how every one of these repositories already writes it.
FS_IMPORT = re.compile(r"\buse\s+(?:[A-Za-z_][A-Za-z0-9_]*::)*fs::(\{[^}]*\}|[A-Za-z_][A-Za-z0-9_]*)")

# `OpenOptions::new()` builds a handle that MAY read, and the mode is decided by
# a builder call this scan does not follow. Counted as a read: the conservative
# direction is the one that asks for a marker.
OPEN_OPTIONS = re.compile(r"\bOpenOptions::new\s*\(")

FS_CALL = re.compile(r"\bfs::([A-Za-z_][A-Za-z0-9_]*)")
FILE_CALL = re.compile(r"\bFile::([A-Za-z_][A-Za-z0-9_]*)")

WATCHED = re.compile(r"ADR-0523-WATCHED:\s*([A-Za-z_][A-Za-z0-9_]*)")
UNWATCHED = re.compile(r"ADR-0523-UNWATCHED:\s*(\S.*)")

# How far above a read a marker may sit. A read is often the tail of a `let`
# whose line above is code, so the marker goes on the read's own line or in the
# comment block over the statement. Ten lines covers a doc comment; a marker is
# consumed by the NEAREST read below it, so one marker can never cover two.
LOOKBACK = 10

# A reason short enough to be a placeholder is not a reason. `iam`'s is a
# paragraph; this floor only refuses `ADR-0523-UNWATCHED: todo`.
MIN_REASON = 24

IMPL_MATERIAL = re.compile(r"\bimpl(?:\s*<[^>]*>)?\s+Material\s+for\s+([A-Za-z_][A-Za-z0-9_]*)")
WATCH_SET_FN = re.compile(r"\bfn\s+watch_set\b")

# MATCHED AT A POSITION, NEVER AGAINST A SLICE. `re.match(p, text[i:])` copies
# the tail of the file on every character it inspects, which is quadratic and
# turns a 60 KB `main.rs` into a hang rather than a slow run -- measured on
# `yadgarhq/gateway`, where the first draft of this file did not finish.
RAW_STRING = re.compile(r'r(#*)"')
CHAR_LITERAL = re.compile(r"'(?:\\.|[^\\'])'")


def blank_noncode(text: str) -> str:
    """Replace string, char and comment CONTENT with spaces, keeping every newline.

    Line numbers and every brace that is really a brace survive. Handles `//`,
    `/* */` (nested, as Rust nests them), `"..."` with escapes, `r"..."`,
    `r#"..."#` at any hash count, and `'a'` without mistaking the lifetime `'a`
    for an unterminated char literal.
    """
    out = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i))
            i = j
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            depth, j = 1, i + 2
            out.append("  ")
            while j < n and depth:
                if text.startswith("/*", j):
                    depth += 1
                    out.append("  ")
                    j += 2
                elif text.startswith("*/", j):
                    depth -= 1
                    out.append("  ")
                    j += 2
                else:
                    out.append("\n" if text[j] == "\n" else " ")
                    j += 1
            i = j
        elif c == "r" and (m := RAW_STRING.match(text, i)):
            close = '"' + m.group(1)
            j = text.find(close, m.end())
            j = n if j == -1 else j + len(close)
            out.append("".join("\n" if ch == "\n" else " " for ch in text[i:j]))
            i = j
        elif c == '"':
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == '"':
                    j += 1
                    break
                j += 1
            out.append("".join("\n" if ch == "\n" else " " for ch in text[i:j]))
            i = j
        elif c == "'":
            # `'a'` and `'\n'` are literals; `'a` alone is a lifetime and the
            # next quote may be a hundred lines away.
            m = CHAR_LITERAL.match(text, i)
            if m:
                out.append(" " * (m.end() - i))
                i = m.end()
            else:
                out.append(c)
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def test_spans(code: str) -> list[tuple[int, int]]:
    """Line ranges (1-based, inclusive) of every `#[cfg(test)]` item.

    The attribute's item is found by taking the first `{` after it and following
    the depth back to zero, over BLANKED code so a format string cannot move it.
    An item with no brace -- `#[cfg(test)] use ...;` -- ends at its semicolon.
    """
    spans = []
    lines = code.splitlines()
    starts = [i for i, line in enumerate(lines) if re.search(r"#\[cfg\((?:[^)]*\b)?test\b", line)]
    for start in starts:
        depth, j, opened = 0, start, False
        while j < len(lines):
            for ch in lines[j]:
                if ch == "{":
                    depth += 1
                    opened = True
                elif ch == "}":
                    depth -= 1
            if opened and depth <= 0:
                break
            if not opened and ";" in lines[j] and j > start:
                break
            j += 1
        spans.append((start + 1, min(j, len(lines) - 1) + 1))
    return spans


def in_span(line: int, spans: list[tuple[int, int]]) -> bool:
    return any(lo <= line <= hi for lo, hi in spans)


def source_files() -> tuple[list[Path], list[str]]:
    """Every non-test `.rs` file under a source root, plus the roots searched."""
    roots = [d for d in (Path("src"), *sorted(Path(".").glob("crates/*/src"))) if d.is_dir()]
    files = []
    for root in roots:
        for path in sorted(root.rglob("*.rs")):
            parts = set(path.parts)
            if "tests" in parts or path.name == "tests.rs":
                continue
            files.append(path)
    return files, [str(r) for r in roots]


def classify(code: str, spans: list[tuple[int, int]]) -> tuple[list[int], list[tuple[int, str]]]:
    """Read-site line numbers, and every token the tables above do not cover."""
    reads, unknown = [], []
    for lineno, line in enumerate(code.splitlines(), start=1):
        if in_span(lineno, spans):
            continue
        for match in FS_CALL.finditer(line):
            name = match.group(1)
            if name in READ_FS:
                reads.append(lineno)
            elif name in WRITE_FS or name in META_FS or name in TYPE_FS:
                continue
            else:
                unknown.append((lineno, f"fs::{name}"))
        for match in FILE_CALL.finditer(line):
            if match.group(1) in READ_FILE:
                reads.append(lineno)
        if OPEN_OPTIONS.search(line):
            reads.append(lineno)
        if imported := FS_IMPORT.search(line):
            names = {n.strip() for n in imported.group(1).strip("{}").split(",")}
            for name in sorted(names & READ_FS):
                unknown.append(
                    (lineno, f"use ...fs::{name} (import the module, not the function)")
                )
    return sorted(set(reads)), unknown


def markers(raw: str, spans: list[tuple[int, int]]) -> list[tuple[int, str, str]]:
    """Every marker in the file, as (line, kind, payload). Read from RAW text.

    Markers live in comments, which the blanking pass erases -- so this side of
    the scan reads the original and the call side reads the blanked view.
    """
    found = []
    for lineno, line in enumerate(raw.splitlines(), start=1):
        if in_span(lineno, spans):
            continue
        if m := WATCHED.search(line):
            found.append((lineno, "watched", m.group(1)))
        elif m := UNWATCHED.search(line):
            found.append((lineno, "unwatched", m.group(1).strip()))
    return found


def watch_set_names() -> set[str]:
    """Every name a `WATCHED` marker may legitimately claim.

    The `impl Material for T` blocks in `src/rotate.rs`, plus the identifiers in
    the `watch_set` signature -- which is where `Path` and `Configuration` live,
    whose `Material` impls belong to `yadgar-lifecycle` rather than to the
    service. THIS IS SIDE B, and it is read out of a different file from the one
    the marker sits in.
    """
    text = blank_noncode(ROTATE.read_text(encoding="utf-8"))
    names = set(IMPL_MATERIAL.findall(text))
    match = WATCH_SET_FN.search(text)
    if match:
        tail = text[match.start() :]
        end = tail.find("{")
        names |= set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", tail[: end if end > 0 else 0]))
    return names


def report(problems: list[str]) -> int:
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    print("", file=sys.stderr)
    print(
        "ADR-0523: every file a service reads at boot is watched, or is declared",
        file=sys.stderr,
    )
    print("  unwatched with the reason recorded at the read.", file=sys.stderr)
    print("", file=sys.stderr)
    print("  Mark the read with ONE of:", file=sys.stderr)
    print(
        "    // ADR-0523-WATCHED: <Material>    -- named in src/rotate.rs",
        file=sys.stderr,
    )
    print(
        "    // ADR-0523-UNWATCHED: <why not>   -- a sentence, not a token",
        file=sys.stderr,
    )
    return 1


def main() -> int:
    files, roots = source_files()
    if not roots:
        print(
            "ADR-0523 gate: NO RUST SOURCE ROOT FOUND. Looked for src/ and\n"
            f"  crates/*/src beneath {Path.cwd()}, and none exist. This is a\n"
            "  failure and not a pass: a gate that searches nothing proves nothing.",
            file=sys.stderr,
        )
        return 1
    if not files:
        print(
            f"ADR-0523 gate: MATCHED 0 NON-TEST RUST FILES under {' '.join(roots)}.\n"
            "  The source roots exist but hold no file this gate would judge, so\n"
            "  this run checked nothing.",
            file=sys.stderr,
        )
        return 1
    if not ROTATE.is_file():
        print(
            f"ADR-0523 gate: {ROTATE} DOES NOT EXIST. A repository adopting this\n"
            "  gate declares its watch set there, and a WATCHED marker names a\n"
            "  material out of that file. With no watch set there is nothing for\n"
            "  a marker to name and this gate cannot judge anything.",
            file=sys.stderr,
        )
        return 1

    names = watch_set_names()
    if not names:
        print(
            f"ADR-0523 gate: {ROTATE} DECLARES NO MATERIAL AND NO watch_set.\n"
            "  Side B of this comparison is empty, so every WATCHED marker would\n"
            "  be refused and every UNWATCHED one accepted -- a verdict that says\n"
            "  nothing about the watch set.",
            file=sys.stderr,
        )
        return 1

    problems: list[str] = []
    judged = 0

    for path in files:
        raw = path.read_text(encoding="utf-8")
        code = blank_noncode(raw)
        spans = test_spans(code)
        reads, unknown = classify(code, spans)
        for lineno, token in unknown:
            problems.append(
                f"{path}:{lineno}: UNCLASSIFIED FILESYSTEM ACCESS `{token}`. This gate "
                "refuses rather than ignoring it: a read this scan does not recognise "
                "would pass unjudged, and a gate that inspected everything except the "
                "thing that changed is worse than no gate. Either write the call as "
                "`fs::<known>` or add the name to the tables in "
                "hooks/boot_reads_watched.py."
            )
        available = markers(raw, spans)
        for read in reads:
            judged += 1
            candidates = [m for m in available if read - LOOKBACK <= m[0] <= read]
            if not candidates:
                problems.append(
                    f"{path}:{read}: reads a file with no ADR-0523 marker. Say which "
                    "material carries it into rotate::watch_set, or that it is not "
                    "carried and why."
                )
                continue
            marker = max(candidates, key=lambda m: m[0])
            available.remove(marker)
            _, kind, payload = marker
            if kind == "watched" and payload not in names:
                problems.append(
                    f"{path}:{read}: ADR-0523-WATCHED names `{payload}`, which "
                    f"{ROTATE} does not declare. A marker may only name a material "
                    "the watch set actually folds in."
                )
            if kind == "unwatched" and len(payload) < MIN_REASON:
                problems.append(
                    f"{path}:{read}: ADR-0523-UNWATCHED gives a {len(payload)}-character "
                    f"reason. A declared exclusion is read by whoever meets this line "
                    f"next; write at least {MIN_REASON} characters saying why."
                )

    print(
        f"ADR-0523 gate: {len(files)} non-test file(s) under {' '.join(roots)}, "
        f"{judged} filesystem read(s) judged against {len(names)} name(s) in {ROTATE}."
    )

    # THE PROBLEMS COME BEFORE THE FLOOR, and the order is not cosmetic. An
    # unclassified access is the reason a run judges nothing, so reporting the
    # floor first prints "the classifier no longer recognises this code" and
    # swallows the line that says which call it did not recognise.
    if problems:
        print("", file=sys.stderr)
        print("ADR-0523 VIOLATION", file=sys.stderr)
        return report(problems)

    if judged == 0:
        print(
            f"ADR-0523 gate: JUDGED 0 READS across {len(files)} file(s) under "
            f"{' '.join(roots)}.\n"
            "  Every service in this estate reads at least a credential or a\n"
            "  listener key at boot, so a run that found none means the classifier\n"
            "  no longer recognises the shape this code is written in.",
            file=sys.stderr,
        )
        return 1
    print("ADR-0523 gate: every read is watched or declared unwatched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
