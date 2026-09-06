#!/usr/bin/env python3
# TASK 513's proto-pin report, run by the `proto` job of
# `.github/workflows/ci-pr.yaml` against the repository under review.
#
# A CHECKED-IN FILE RATHER THAN A HEREDOC, and that is not tidiness. actionlint
# hands every `run:` block to shellcheck over a pipe, and above 8192 bytes the
# write deadlocks: actionlint waits forever and the job burns its whole timeout
# (`hooks/run_block_size.py` measures this). This script was a 7785-byte block
# at `ci-pr.yaml:611` — 215 bytes under the 8000-byte gate, close enough that the
# next comment added to it would have tripped the gate or, worse, missed it and
# hung actionlint on every pull request in the estate (ledger 717). It was also
# undiffable, untestable and invisible to every linter, same as `d80_portability.py`
# above it in this same directory.
#
# It runs with the repository under review as the working directory, reading
# `PROTO_VERSION` and `PROTO_PATHS` from it. Nothing else is passed in.
import os, re, subprocess, sys, pathlib, filecmp, traceback

# TASK 513: nothing flagged that a repository's PROTO_VERSION had fallen behind
# the tag `proto` actually carries. The existing drift job answers "does the
# vendored copy match the pin"; it cannot answer "is the pin still current",
# because the pin is its own baseline.
#
# THE COMPARISON NEVER FAILS THE BUILD, and that is deliberate rather than timid:
#
#   * Being behind is a CHOICE until somebody bumps it. A newer tag adding an
#     RPC this module does not call is not a defect in this module, and failing
#     on it would turn every repository here red the moment `proto` is tagged.
#   * It needs the network and a remote tag listing. A hard gate that depends on
#     a third party being reachable fails for reasons that have nothing to do
#     with the change under review.
#
# The version job DERIVES AND WRITES now; this one still only reports,
# and the difference is whose repository the answer is about. A version
# is a statement about this repository, decided by this repository's
# own merged history. A proto pin being behind is a statement about
# `yadgarhq/proto`, and nothing here is entitled to act on that.
# Please do not "fix" this into a hard failure without reading the above.
#
# THAT PROMISE IS SCOPED TO THIS FILE'S OWN LOGIC, not to the step end to end.
# Every path through `report()` — behind, ahead, a pin that cannot be ordered,
# `proto` unreachable, `buf export` failing, or an unexpected exception in the
# comparison — is a statement about `yadgarhq/proto`, and every one of them
# leaves this module at exit 0. The whole body of `report()` runs inside the
# try/except below for exactly that reason: without it the promise would hold
# only for the failures somebody thought of, and a traceback in the comparison
# would redden `proto`, and through it `passed`, in a repository whose change
# had nothing to do with protos.
#
# IT DOES NOT COVER THIS FILE BEING UNREACHABLE. That is a statement about
# THIS workflow, not about `yadgarhq/proto`, and it is refused rather than
# swallowed: the `run:` step in `ci-pr.yaml` that calls this file exits 1 if
# `$dir/proto_pin_report.py` is missing, instead of silently skipping the
# report. A gate that cannot reach its own logic must not pass — the same
# shape ledger 720 has already found more than once — and that check lives in
# the shell wrapper, outside the try/except below, on purpose.

PROTO_REPO = "https://github.com/yadgarhq/proto.git"
out, warnings = [], []


def w(line=""):
    out.append(line)


def sh(*a):
    return subprocess.run(a, capture_output=True, text=True)


def semver(tag):
    m = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", tag)
    return tuple(int(x) for x in m.groups()) if m else None


def report():
    pin = pathlib.Path("PROTO_VERSION").read_text().strip()

    # A PIN THIS STEP CANNOT ORDER. `semver()` returns None for anything that is
    # not `vN.N.N` — a bare `1.6.0`, a branch name, a commit. Comparing None to
    # a tuple raises, and the traceback would redden a job that promises not to
    # fail. The drift check above still validates such a pin; only the
    # is-it-current question needs an ordering.
    if semver(pin) is None:
        w(f"`PROTO_VERSION` is `{pin}`, which is not a `vN.N.N` tag.")
        w()
        w(
            "This step orders tags to say whether the pin is behind, and it has "
            "no ordering for that value. The drift check above still verifies "
            "the vendored copy against it."
        )
        return

    r = sh("git", "ls-remote", "--tags", "--refs", PROTO_REPO, "v*")
    tags = [t for t in re.findall(r"refs/tags/(\S+)", r.stdout) if semver(t)]

    if r.returncode != 0 or not tags:
        w(f"Could not list tags on `{PROTO_REPO}`, so there is nothing to compare.")
        return

    latest = max(tags, key=semver)
    if latest == pin:
        w(
            f"`PROTO_VERSION` is `{pin}`, which is the newest tag on `proto`. "
            f"Nothing to report."
        )
        return

    w(f"`PROTO_VERSION` is **`{pin}`**; `proto` is tagged **`{latest}`**.")
    w()

    if semver(latest) < semver(pin):
        w("The pin is AHEAD of every tag this job can see, which is stranger")
        w("than being behind: it names something `proto` has not published.")
        warnings.append(f"PROTO_VERSION {pin} is ahead of the newest proto tag {latest}")
        return

    paths = [
        l.strip()
        for l in pathlib.Path("PROTO_PATHS").read_text().splitlines()
        if l.strip() and not l.strip().startswith("#")
    ]
    args = []
    for p in paths:
        args += ["--path", p]

    for tag, dest in ((pin, "/tmp/pp-pin"), (latest, "/tmp/pp-latest")):
        sh("rm", "-rf", dest)
        e = sh("buf", "export", f"{PROTO_REPO}#tag={tag}", *args, "-o", dest)
        if e.returncode != 0:
            w(f"`buf export` at `{tag}` failed, so the contents were not compared:")
            w()
            w("```")
            w(e.stderr.strip()[:800])
            w("```")
            return

    def tree(root):
        base = pathlib.Path(root)
        return {str(p.relative_to(base)) for p in base.rglob("*") if p.is_file()}

    a, b = tree("/tmp/pp-pin"), tree("/tmp/pp-latest")
    # THREE BUCKETS, because two of them are not drift. `buf export` follows the
    # import graph, so a newer tag can pull in a file the pin's closure never
    # had — gateway/PROTO_PATHS documents exactly that happening at v1.6.0,
    # where yadgar/iam/v1 began importing yadgar/telemetry/v1. A flat `diff -r`
    # reports that as drift and reads alarming. Only files present in BOTH and
    # differing are a change to a contract this module already vendors.
    changed = sorted(
        f
        for f in a & b
        if not filecmp.cmp(f"/tmp/pp-pin/{f}", f"/tmp/pp-latest/{f}", shallow=False)
    )
    added, dropped = sorted(b - a), sorted(a - b)

    w("What the newer tag would change **in this module's own closure**:")
    w()
    w("| bucket | files | means |")
    w("| --- | --- | --- |")
    w(
        f"| contract drift | {len(changed)} | a file this module already "
        f"vendors, whose text differs |"
    )
    w(
        f"| closure growth | {len(added)} | a file `buf export` reaches "
        f"only at the newer tag, because an import was added upstream |"
    )
    w(
        f"| closure shrink | {len(dropped)} | a file the newer closure no "
        f"longer reaches |"
    )
    w()
    for label, files in (
        ("Contract drift", changed),
        ("Closure growth", added),
        ("Closure shrink", dropped),
    ):
        if files:
            w(f"**{label}:** " + ", ".join(f"`{f}`" for f in files))
            w()

    if changed:
        warnings.append(
            f"PROTO_VERSION is {pin} while proto is tagged {latest}, and "
            f"{len(changed)} vendored file(s) differ between them: "
            + ", ".join(changed)
        )
        w(
            "**The pin being behind is not harmless here.** The files above "
            "differ, so this module is built against an older text of a "
            "contract that has since been changed. Read the diff, then bump "
            "`PROTO_VERSION` and re-run `make proto`."
        )
    else:
        warnings.append(
            f"PROTO_VERSION is {pin} while proto is tagged {latest}; no "
            f"vendored file differs, so the pin is behind but harmless today"
        )
        w(
            "**Behind, but harmless today** — no file this module vendors "
            "differs between the two tags. That is a fact about these tags, "
            "not a guarantee about the next one."
        )
    w()


w("## Proto pin (task 513) — reported, never enforced")
w()
try:
    report()
except Exception:
    # The promise is structural, so an unhandled error is REPORTED rather than
    # raised. Loudly, because a step that quietly measures nothing is the
    # failure this whole change exists to argue against.
    w("This step hit an unexpected error and reported it instead of failing:")
    w()
    w("```")
    w(traceback.format_exc().strip()[:1500])
    w("```")
    warnings.append("the proto pin step errored; see the job summary")
w()
w("Nothing was bumped. This step reports; a human decides (D64).")

summary = "\n".join(out)
sp = os.environ.get("GITHUB_STEP_SUMMARY")
if sp:
    with open(sp, "a") as fh:
        fh.write(summary + "\n")
print(summary)
for m in warnings:
    print("::warning::" + m)
sys.exit(0)
