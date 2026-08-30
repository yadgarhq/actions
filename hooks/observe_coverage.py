#!/usr/bin/env python3
"""observe-coverage — every RPC handler must be instrumented (D67, D59).

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
"""

import re
import sys

# An RPC handler in a tonic service impl: `async fn name(&self, ...)`.
HANDLER = re.compile(r"^\s*async fn (\w+)\s*\(\s*$|^\s*async fn (\w+)\s*\(&self", re.M)
# The impl blocks we care about — a generated tonic server trait.
SERVICE_IMPL = re.compile(r"impl\s+\w*Service\s+for\s+\w+")


def handlers_in(text: str):
    """Yield (name, body) for each handler inside a service impl."""
    if not SERVICE_IMPL.search(text):
        return
    lines = text.splitlines()
    for i, line in enumerate(lines):
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


def main(paths):
    failures = []
    for path in paths:
        try:
            text = open(path, encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            continue
        for name, body in handlers_in(text):
            if "Call::start" not in body:
                failures.append(f"{path}: `{name}` opens no observe::Call")

    if failures:
        print("observe-coverage: uninstrumented RPC handlers\n")
        for f in failures:
            print(f"  {f}")
        print(
            "\nEvery RPC handler must open a `Call::start(...)` (D67). The Call\n"
            "records on Drop, so an early return cannot lose the record — starting\n"
            "one is the whole requirement.\n\n"
            "A module that quietly stops emitting looks exactly like a module\n"
            "nobody called, which is the reading that would break D15."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
