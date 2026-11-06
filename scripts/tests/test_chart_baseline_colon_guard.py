"""A guard over a plain scalar holding a mapping indicator is refused (yaadgaar 622).

Helm refuses `toggle: 1 x: 0` ("mapping values are not allowed in this context"),
measured 2026-11-06 on helm v4.3.0 and v3.18.4. The gate read the toggle as the
string "1 x: 0", which is on, and judged the if branch alone. A colon followed by
NBSP or IDEO is text to Helm, and the gate judges that toggle as on.
"""

from test_chart_baseline import findings_of, run, toggle_if_else, write_chart

NBSP = chr(0xA0)
IDEO = chr(0x3000)
TAB = chr(9)

REFUSED_TOGGLES = ["1 x: 0", "1" + NBSP + "x: 0", "1 x:" + TAB + "0", "x:"]


def test_a_guard_over_a_mapping_indicator_is_refused_in_either_branch(tmp_path):
    """RED on afc39f8: the (35, 29) order exited 0, and the (29, 35) order was
    refused with the floor finding rather than "does not model"."""
    for index, toggle in enumerate(REFUSED_TOGGLES):
        for if_branch, else_branch in ((35, 29), (29, 35)):
            root = tmp_path / f"m{index}-{if_branch}"
            root.mkdir()
            write_chart(root, **toggle_if_else(toggle, if_branch, else_branch))
            result = run(root)
            assert result.returncode == 1, (repr(toggle), if_branch, result.stdout)
            assert "does not model" in findings_of(result), (repr(toggle), result.stdout)


def test_a_colon_then_a_unicode_space_is_a_toggle_that_is_on(tmp_path):
    """The boundary: Helm reads `1 x:<NBSP>0` as a string, which is on, so the gate
    judges the if branch."""
    for index, space in enumerate((NBSP, IDEO)):
        toggle = "1 x:" + space + "0"
        for if_branch, else_branch, expected in ((35, 29, 0), (29, 35, 1)):
            root = tmp_path / f"u{index}-{if_branch}"
            root.mkdir()
            write_chart(root, **toggle_if_else(toggle, if_branch, else_branch))
            result = run(root)
            assert result.returncode == expected, (repr(toggle), if_branch, result.stdout)
            if expected:
                assert "below the floor of 35s" in findings_of(result), (repr(toggle), result.stdout)
    root = tmp_path / "plain"
    root.mkdir()
    write_chart(root, **toggle_if_else("1 x:0", 35, 29))
    result = run(root)
    assert result.returncode == 0, result.stdout
