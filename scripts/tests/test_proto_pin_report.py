"""What `proto_pin_report.py` reports, pinned so it cannot quietly stop reporting it.

LEDGER 717. The script was a 7785-byte heredoc inside `ci-pr.yaml`'s `proto`
job — 215 bytes under the 8000-byte `run_block_size` gate, and nothing ran it
before this file: `grep` across `scripts/tests/` for the module name returned
zero hits. That is the same gap `test_d80_portability.py` closed for its
sibling extraction, one directory over.

THIS GATE NEVER FAILS, BY DESIGN, and that changes what a red case looks like.
Every path through `report()` — a non-semver pin, an unreachable `proto`,
`buf export` failing, a pin ahead of every tag, a pin behind with real drift,
an outright exception reading `PROTO_VERSION` — exits 0. So "red twin" here
does not mean a nonzero exit; it means the ONE message a scenario must produce,
paired against a scenario built to prove the gate did not produce it by
accident. A pin reported "behind but harmless" must not also say "not harmless
here", and the reverse.

STUBBED `git` AND `buf`, never the real network. Same reasoning as
`test_d80_portability.py`'s stub `helm`: the gate's contract with each binary is
argv-in, stdout/files-out, and a stub honouring that contract is a complete
substitute for testing the gate. `STUB_GIT_TAGS` (comma-separated) and
`STUB_BUF_SPEC` (JSON, keyed by tag) drive them from the environment, so one
pair of stub scripts covers every scenario below without touching the real
`yadgarhq/proto`.

Run: python3 -m pytest scripts/tests/ -q
"""

import json
import os
import subprocess
import sys
from pathlib import Path

GATE = Path(__file__).resolve().parents[1] / "proto_pin_report.py"
REPO = Path(__file__).resolve().parents[2]

# A stand-in for `git`. Only `ls-remote --tags --refs <repo> v*` is modelled —
# the one invocation the gate makes. `STUB_GIT_FAIL=1` makes it fail the way an
# unreachable `proto` would; otherwise it prints one `refs/tags/<tag>` line per
# entry in `STUB_GIT_TAGS`, exactly the shape `git ls-remote` emits.
STUB_GIT = """
import os, sys
if len(sys.argv) < 2 or sys.argv[1] != "ls-remote":
    sys.stderr.write("stub git: only ls-remote is modelled\\n")
    sys.exit(2)
if os.environ.get("STUB_GIT_FAIL") == "1":
    sys.stderr.write("stub git: could not resolve host\\n")
    sys.exit(128)
tags = [t for t in os.environ.get("STUB_GIT_TAGS", "").split(",") if t]
for t in tags:
    sys.stdout.write("0000000000000000000000000000000000000000\\trefs/tags/%s\\n" % t)
sys.exit(0)
"""

# A stand-in for `buf`. Only `export <repo>#tag=<tag> [--path P]... -o DEST` is
# modelled. `STUB_BUF_SPEC` is a JSON object keyed by tag:
#   {"fail": true}                    -> exit 1, matching a real export failure
#   {"files": {"a.proto": "text"}}    -> writes each file under DEST
# A tag absent from the spec writes nothing (an empty closure).
STUB_BUF = """
import json, os, pathlib, sys
argv = sys.argv[1:]
if not argv or argv[0] != "export":
    sys.stderr.write("stub buf: only export is modelled\\n")
    sys.exit(2)
target = argv[1]
tag = target.rsplit("#tag=", 1)[1]
dest = argv[argv.index("-o") + 1]
spec = json.loads(os.environ.get("STUB_BUF_SPEC", "{}")).get(tag, {})
if spec.get("fail"):
    sys.stderr.write("stub buf: export failed at %s\\n" % tag)
    sys.exit(1)
pathlib.Path(dest).mkdir(parents=True, exist_ok=True)
for name, text in spec.get("files", {}).items():
    path = pathlib.Path(dest) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
sys.exit(0)
"""


def stub_bin(tmp_path):
    """Write the stub `git` and `buf` and return the directory holding them."""
    binaries = tmp_path / "stub-bin"
    binaries.mkdir(exist_ok=True)
    for name, body in (("git", STUB_GIT), ("buf", STUB_BUF)):
        script = binaries / name
        script.write_text("#!" + sys.executable + "\n" + body)
        script.chmod(0o755)
    return binaries


def repo(tmp_path, version=None, paths=("a.proto",)):
    """A repository under review: `PROTO_VERSION` and `PROTO_PATHS`, no more."""
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    if version is not None:
        (root / "PROTO_VERSION").write_text(version)
    (root / "PROTO_PATHS").write_text("\n".join(paths) + "\n")
    return root


def run(root, tmp_path, git_tags="", git_fail=False, buf_spec=None, summary=None):
    # STUB-BIN FIRST, then the real PATH, so `git`/`buf` resolve to the stubs
    # while `rm` still resolves to a real binary — nix installs coreutils
    # somewhere neither `/usr/bin` nor `/bin` names, so hardcoding either would
    # pass on a stock Ubuntu runner and fail here.
    environment = {
        "PATH": str(stub_bin(tmp_path)) + ":" + os.environ.get("PATH", "/usr/bin:/bin"),
        "STUB_GIT_TAGS": git_tags,
        "STUB_BUF_SPEC": json.dumps(buf_spec or {}),
    }
    if git_fail:
        environment["STUB_GIT_FAIL"] = "1"
    if summary:
        environment["GITHUB_STEP_SUMMARY"] = str(summary)
    return subprocess.run(
        [sys.executable, str(GATE)],
        capture_output=True,
        text=True,
        cwd=str(root),
        env=environment,
    )


# -------------------------------------------------- it NEVER fails the build


def test_every_scenario_below_exits_zero(tmp_path):
    """THE STRUCTURAL PROMISE. Asserted once here; every other test repeats it."""
    root = repo(tmp_path, version="not-a-tag")
    result = run(root, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


# ------------------------------------------------------------- the pin itself


def test_a_non_semver_pin_is_named_and_not_ordered(tmp_path):
    root = repo(tmp_path, version="task")
    result = run(root, tmp_path)
    assert "is not a `vN.N.N` tag" in result.stdout
    # THE RED TWIN: it must not claim to have compared anything it did not.
    assert "Nothing to report" not in result.stdout


def test_an_unreachable_proto_is_reported_not_raised(tmp_path):
    root = repo(tmp_path, version="v1.5.0")
    result = run(root, tmp_path, git_fail=True)
    assert "Could not list tags" in result.stdout


def test_no_matching_tags_is_the_same_as_unreachable(tmp_path):
    """`git ls-remote` can succeed and still return nothing this gate can order —
    a repository with no `vN.N.N` tag reads the same as an unreachable one."""
    root = repo(tmp_path, version="v1.5.0")
    result = run(root, tmp_path, git_tags="not-a-tag,also-not-one")
    assert "Could not list tags" in result.stdout


def test_pin_already_latest_reports_nothing_to_compare(tmp_path):
    root = repo(tmp_path, version="v1.6.0")
    result = run(root, tmp_path, git_tags="v1.5.0,v1.6.0")
    assert "the newest tag on `proto`. Nothing to report." in result.stdout
    # THE RED TWIN: no drift table, because nothing was exported to build one.
    assert "closure growth" not in result.stdout


def test_pin_ahead_of_every_tag_warns_rather_than_compares(tmp_path):
    """THE STRANGER CASE. A pin naming something `proto` has not published."""
    root = repo(tmp_path, version="v9.9.9")
    result = run(root, tmp_path, git_tags="v1.5.0,v1.6.0")
    assert "AHEAD of every tag" in result.stdout
    assert "::warning::PROTO_VERSION v9.9.9 is ahead" in result.stdout


# ------------------------------------------------------------- buf export


def test_a_failed_export_is_reported_with_the_tag_and_stderr(tmp_path):
    root = repo(tmp_path, version="v1.5.0")
    result = run(
        root,
        tmp_path,
        git_tags="v1.5.0,v1.6.0",
        buf_spec={"v1.5.0": {"fail": True}},
    )
    assert "`buf export` at `v1.5.0` failed" in result.stdout
    assert "stub buf: export failed at v1.5.0" in result.stdout


# ----------------------------------------------------- the three-bucket drift


def test_behind_but_identical_is_harmless_not_alarming(tmp_path):
    """THE RED TWIN of the case below: same drift machinery, opposite verdict."""
    root = repo(tmp_path, version="v1.5.0")
    result = run(
        root,
        tmp_path,
        git_tags="v1.5.0,v1.6.0",
        buf_spec={
            "v1.5.0": {"files": {"a.proto": "same"}},
            "v1.6.0": {"files": {"a.proto": "same"}},
        },
    )
    assert "Behind, but harmless today" in result.stdout
    assert "not harmless here" not in result.stdout
    assert "::warning::" in result.stdout
    assert "harmless today" in result.stdout


def test_behind_with_real_drift_is_not_harmless(tmp_path):
    root = repo(tmp_path, version="v1.5.0")
    result = run(
        root,
        tmp_path,
        git_tags="v1.5.0,v1.6.0",
        buf_spec={
            "v1.5.0": {"files": {"a.proto": "old text"}},
            "v1.6.0": {"files": {"a.proto": "new text"}},
        },
    )
    assert "not harmless here" in result.stdout
    assert "contract drift | 1" in result.stdout
    assert "`a.proto`" in result.stdout
    # THE RED TWIN.
    assert "Behind, but harmless today" not in result.stdout


def test_closure_growth_is_not_read_as_drift(tmp_path):
    """THE ANTI-FALSE-ALARM CASE. A file the newer tag reaches through a new
    import is CLOSURE GROWTH, not a change to a contract this module vendors —
    `gateway/PROTO_PATHS` at v1.6.0 is the real instance this distinguishes."""
    root = repo(tmp_path, version="v1.5.0")
    result = run(
        root,
        tmp_path,
        git_tags="v1.5.0,v1.6.0",
        buf_spec={
            "v1.5.0": {"files": {"a.proto": "same"}},
            "v1.6.0": {"files": {"a.proto": "same", "b.proto": "new"}},
        },
    )
    assert "contract drift | 0" in result.stdout
    assert "closure growth | 1" in result.stdout
    assert "`b.proto`" in result.stdout
    # THE RED TWIN: growth alone is harmless, so it must not read as drift.
    assert "not harmless here" not in result.stdout


def test_closure_shrink_is_reported_as_its_own_bucket(tmp_path):
    root = repo(tmp_path, version="v1.5.0")
    result = run(
        root,
        tmp_path,
        git_tags="v1.5.0,v1.6.0",
        buf_spec={
            "v1.5.0": {"files": {"a.proto": "same", "c.proto": "gone"}},
            "v1.6.0": {"files": {"a.proto": "same"}},
        },
    )
    assert "closure shrink | 1" in result.stdout
    assert "`c.proto`" in result.stdout


# --------------------------------------------------- the structural try/except


def test_a_missing_proto_version_is_reported_not_raised(tmp_path):
    """No `PROTO_VERSION` at all — `report()` raises `FileNotFoundError`, and the
    promise is that this becomes a report rather than a nonzero exit."""
    root = repo(tmp_path, version=None)
    result = run(root, tmp_path)
    assert result.returncode == 0
    assert "hit an unexpected error and reported it instead of failing" in result.stdout
    assert "FileNotFoundError" in result.stdout
    assert "::warning::the proto pin step errored" in result.stdout


def test_this_repository_has_no_proto_pin_today(tmp_path):
    """LEDGER 717's OWN QUESTION, asked of the real tree. `yadgarhq/actions`
    vendors no proto, so this reports the same missing-file error as the
    fixture above rather than comparing anything."""
    result = run(REPO, tmp_path)
    assert result.returncode == 0
    assert "hit an unexpected error and reported it instead of failing" in result.stdout


# --------------------------------------------------------- GITHUB_STEP_SUMMARY


def test_the_summary_is_appended_to_github_step_summary(tmp_path):
    root = repo(tmp_path, version="v1.6.0")
    summary = tmp_path / "summary.md"
    summary.write_text("# earlier step\n")
    result = run(root, tmp_path, git_tags="v1.5.0,v1.6.0", summary=summary)
    text = summary.read_text()
    assert text.startswith("# earlier step\n")
    assert "## Proto pin (task 513)" in text
    # THE EARLIER STEP'S OUTPUT IS PRESERVED, never overwritten — this step
    # only appends. What follows it is exactly this step's own stdout.
    assert text[len("# earlier step\n") :].strip() == result.stdout.strip()
