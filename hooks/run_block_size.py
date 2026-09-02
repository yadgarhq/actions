#!/usr/bin/env python3
"""A mechanical ceiling on the size of a `run:` block in a workflow.

WHY THIS EXISTS, and it is a measurement rather than a preference. actionlint
pipes every `run:` block to shellcheck and does not drain the pipe while it
writes. Above 8192 bytes the write never completes: actionlint hangs, and the
job it runs in burns its entire timeout. Measured 2026-09-02 with actionlint
1.7.12, deterministic across three runs at every size:

    7940 bytes   ok ok ok
    8180 bytes   HANG HANG HANG
   63980 bytes   HANG HANG HANG

The D80 gate was a 20499-byte block, so NO workflow in this repository could be
linted at all. That is how the actionlint hook came to report Passed having run
nothing. The block is now `scripts/d80_portability.py`, and this hook is what
stops the next one from arriving.

WHY A CEILING RATHER THAN A NOTE IN A COMMENT. The failure is silent and
expensive: the workflows here run in every repository, so one oversized block
means a 360-minute timeout on every pull request everywhere. A comment asking
people to remember does not survive a rushed fix. A number that fails the
commit does.

WHY 8000 AND NOT 8192. Headroom. actionlint prepends a short prelude to what it
sends, so the byte the pipe sees is a little larger than the block itself, and a
limit set exactly at the cliff would pass locally and hang in CI.

WHAT TO DO WHEN IT FAILS: move the body into a checked-in script and call it.
That is worth doing on its own merits — a heredoc is undiffable, untestable and
invisible to every linter.

Stdlib only, on purpose: the same reason `hooks/observe_coverage.py` is. A hook
that needs PyYAML fails on a machine that does not have it, and the failure
reads as a broken hook rather than a missing package.
"""

import re
import sys

LIMIT = 8000

# `run: |`, `run: |-`, `run: >`, and the indentation-indicator forms. A `run:`
# whose value is a plain or quoted scalar on the same line cannot approach the
# limit, so only block scalars are measured.
BLOCK = re.compile(r"^(?P<indent>\s*)-?\s*run:\s*[|>][-+]?\d*\s*$")


def blocks(lines):
    """Yield (line_number, byte_length) for every block scalar under a `run:`.

    The body of a block scalar is the run of following lines that are blank or
    indented deeper than the `run:` key itself. That is the YAML rule, and it is
    the whole rule for this shape.

    THE SIZE IS THE DEDENTED SIZE, because that is the string YAML produces and
    therefore the string actionlint writes into shellcheck's pipe. Counting the
    file's indentation instead inflates a 7513-byte block to 9283 and fails a
    workflow that lints perfectly well.
    """
    i = 0
    while i < len(lines):
        m = BLOCK.match(lines[i])
        if not m:
            i += 1
            continue
        start = i
        depth = len(m.group("indent"))
        i += 1
        body = []
        while i < len(lines):
            line = lines[i]
            if line.strip() and len(line) - len(line.lstrip()) <= depth:
                break
            body.append(line)
            i += 1
        # Trailing blank lines belong to whatever comes next, not to the block.
        while body and not body[-1].strip():
            body.pop()
        content = [b for b in body if b.strip()]
        strip = min((len(b) - len(b.lstrip()) for b in content), default=0)
        yield start + 1, sum(len(b[strip:]) + 1 for b in body)


def main(paths):
    checked = 0
    over = []
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        for line_no, size in blocks(lines):
            checked += 1
            if size > LIMIT:
                over.append((path, line_no, size))

    # SAY WHAT WAS MEASURED. A hook that finds nothing because it matched
    # nothing looks exactly like a hook that found nothing wrong, and this
    # repository has already been bitten by that difference three times.
    print(f"run_block_size: {checked} `run:` block(s) measured, limit {LIMIT} bytes")
    if checked == 0 and paths:
        print("run_block_size: no block scalar found — check the pattern, not the files")

    for path, line_no, size in over:
        print(f"{path}:{line_no}: run: block is {size} bytes, over the {LIMIT} limit.")
        print("    actionlint deadlocks above 8192. Move the body into a checked-in")
        print("    script under scripts/ and call it from the workflow.")
    return 1 if over else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
