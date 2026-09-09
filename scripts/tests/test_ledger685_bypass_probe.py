"""TEMPORARY. Reddens `precommit` in CI so ledger 685's bypass can be constructed.

DELETE THIS FILE. It exists for the length of one pull request run, to produce a
failing `ci / passed` on a commit and then let a text-only edit try to supersede
it. Reverted in the commit after the measurement.

It fails only where `GITHUB_ACTIONS` is set, so the local hook chain stays green
and the red is created without `--no-verify`.
"""

import os


def test_temporary_ledger_685_probe():
    assert os.environ.get("GITHUB_ACTIONS") != "true", (
        "TEMPORARY ledger 685 probe: this failure is deliberate. It exists to "
        "put a red `ci / passed` on this commit so that a text-only edit can be "
        "shown failing to supersede it. Delete this file."
    )
