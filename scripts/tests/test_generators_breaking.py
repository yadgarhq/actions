"""ADR-1258 / yaadgaar 199: every bot PR-body generator answers `## Breaking?` with `no`.

A generated body is a dependency re-pin, a hook adoption or a toolchain bump,
and none of those carries a `!` Changelog bullet, so the only answer that
agrees with its own Changelog is `no`. The first non-blank line under the
heading is the answer; anything after it is the reason.

Each body is built from the real fixture template through the generator's own
test helper, so this reads exactly what each generator writes.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pr_body  # noqa: E402
import repin  # noqa: E402
import rust_propagate  # noqa: E402
import rust_release  # noqa: E402
import test_fan_out  # noqa: E402
import test_repin  # noqa: E402

TEMPLATE = Path(__file__).resolve().parent / "fixtures" / "pull_request_template.md"


def test_every_generator_is_listed():
    assert sorted(bodies()) == sorted(GENERATORS)


def bodies():
    template = TEMPLATE.read_text()
    return {
        "repin": test_repin.generated_body(template),
        "fan_out": test_fan_out.generated_body(template=template),
        "rust_propagate": rust_propagate.body(template, "1.91.0", "o/r", ["Cargo.toml"]),
        "rust_release": rust_release.body(template, "bump", "1.91.0", "1.90.0", "sha256:" + "a" * 64),
        "rust_release_end": rust_release.body(template, "end", "1.91.0", "1.90.0", None),
    }


def test_breaking_is_a_required_heading_for_every_generator():
    assert "Breaking?" in repin.REQUIRED


def test_the_fixture_template_carries_the_breaking_section():
    titles = [title for _, title in repin.headings(TEMPLATE.read_text())]
    assert "Breaking?" in titles
    assert titles.index("Breaking?") == titles.index("Changelog") + 1


GENERATORS = ["repin", "fan_out", "rust_propagate", "rust_release", "rust_release_end"]


@pytest.mark.parametrize("name", GENERATORS)
def test_every_generator_answers_breaking_with_no(name):
    found = pr_body.sections(bodies()[name])
    assert "Breaking?" in found, name
    lines = [line.strip() for line in found["Breaking?"] if line.strip()]
    assert lines, name
    assert lines[0] == "no", (name, lines[0])
    assert len(lines) >= 2, f"{name}: `no` carries a one-sentence reason"


def test_a_template_without_breaking_is_refused_by_every_generator():
    template = TEMPLATE.read_text().replace("## Breaking?", "## Something else")
    with pytest.raises(repin.Refusal):
        test_repin.generated_body(template)
    with pytest.raises(repin.Refusal):
        test_fan_out.generated_body(template=template)
    with pytest.raises(rust_propagate.Refusal):
        rust_propagate.body(template, "1.91.0", "o/r", ["Cargo.toml"])
    with pytest.raises(rust_release.Refusal):
        rust_release.body(template, "bump", "1.91.0", "1.90.0", "sha256:" + "a" * 64)


def test_the_extra_section_passes_the_gate_as_it_stands():
    # This lands BEFORE `pr_body.REQUIRED` grows the heading, so the gate on
    # `main` must accept a body that carries it as one more section.
    body = test_repin.generated_body(TEMPLATE.read_text())
    assert "## Breaking?" in body
    for text, wrapped in ((body, False), (test_repin.wrap(body), True)):
        problems, bump, count = pr_body.review(text, wrapped=wrapped)
        assert problems == []
        assert bump == "patch" and count >= 1
