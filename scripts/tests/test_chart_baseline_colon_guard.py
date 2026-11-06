"""A guard over a plain scalar holding a mapping indicator is refused (yaadgaar 622).

Helm refuses `toggle: 1 x: 0` ("mapping values are not allowed in this context"),
measured 2026-11-06 on helm v4.3.0 and v3.18.4. The gate read the toggle as the
string "1 x: 0", which is on, and judged the if branch alone. A colon followed by
NBSP or IDEO is text to Helm, and the gate judges that toggle as on.
"""

from test_chart_baseline import DEPLOYMENT, VALUES, findings_of, run, swap, toggle_if_else, write_chart

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


# TWO GUARDED preSTOP SLEEPS, in two containers. A 5s one behind `short` and a 30s
# one behind `long`; with `short` on, the first renders whatever `long` reads as.
ONE_PRESTOP = """\
          lifecycle:
            {{- with .Values.preStopSleepSeconds }}
            preStop:
              sleep:
                seconds: {{ . }}
            {{- end }}
"""
TWO_PRESTOPS = """\
          lifecycle:
            {{- if .Values.short }}
            preStop:
              sleep:
                seconds: 5
            {{- end }}
        - name: sidecar
          image: example
          lifecycle:
            {{- if .Values.long }}
            preStop:
              sleep:
                seconds: 30
            {{- end }}
"""


def two_prestops(long):
    """`short: true` and `long: <long>`, with the grace period at 35."""
    values = swap(VALUES, "replicaCount: 2", "short: true" + chr(10) + "long: " + long + chr(10) + "replicaCount: 2")
    return dict(templates__deployment_yaml=swap(DEPLOYMENT, ONE_PRESTOP, TWO_PRESTOPS), values_yaml=values)


def test_an_unmodelled_prestop_guard_beside_a_rendered_one_is_refused(tmp_path):
    """RED on actions#211's first head (a0b471c): `long: 1 x: 0` became unmodelled,
    and `prestop_sleep` checked an unmodelled guard only when NO preStop rendered.
    The 5s sleep rendered, the 30s one was dropped unpriced, and 35s passed. The
    gate before #211 read `long` as on and refused 35 against a floor of 60s."""
    for index, long in enumerate(("1 x: 0", "x:")):
        root = tmp_path / f"b{index}"
        root.mkdir()
        write_chart(root, **two_prestops(long))
        result = run(root)
        assert result.returncode == 1, (repr(long), result.stdout)
        assert "cannot tell whether the `preStop` sleep renders" in findings_of(result), (repr(long), result.stdout)


def test_an_anchored_prestop_guard_beside_a_rendered_one_is_refused(tmp_path):
    """RED on a26ba23 and on a0b471c (pre-existing): Helm reads `long: &a 1` as 1 and
    renders both sleeps, so the floor is 60s. The anchor is unmodelled, and the gate
    dropped the 30s sleep because the 5s one rendered."""
    root = tmp_path / "anchor"
    root.mkdir()
    write_chart(root, **two_prestops("&a 1"))
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "cannot tell whether the `preStop` sleep renders" in findings_of(result), result.stdout


def test_two_modelled_prestop_guards_price_the_longest_sleep(tmp_path):
    """The control: with `long: 1` both sleeps render, and the 30s one sets the floor."""
    root = tmp_path / "on"
    root.mkdir()
    write_chart(root, **two_prestops("1"))
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "below the floor of 60s" in findings_of(result), result.stdout
