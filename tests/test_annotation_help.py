from __future__ import annotations

from pathlib import Path


def test_annotation_help_is_authenticated_and_links_both_guides(client, enforcer_client):
    import app as flask_app

    with flask_app.app.test_client() as anonymous:
        assert anonymous.get("/annotation-help").status_code in (302, 401)

    response = enforcer_client.get("/annotation-help")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'sandbox="allow-scripts"' in html
    assert "Annotation Basics visual guide" in html
    assert "Overlapping Annotations visual guide" in html
    assert "detailed written annotation guide" in html


def test_annotation_help_document_route_is_authenticated_and_allowlisted(client, enforcer_client):
    import app as flask_app

    allowed = (
        "testing/2026-10-13/annotation-visual-guide/index.html",
        "testing/2026-10-13/annotation-visual-guide/overlap-use-cases.html",
        "testing/2026-10-10/ANNOTATION-GUIDE.md",
    )
    for path in allowed:
        with flask_app.app.test_client() as anonymous:
            assert anonymous.get(f"/annotation-help/documents/{path}").status_code in (302, 401)
        response = enforcer_client.get(f"/annotation-help/documents/{path}")
        assert response.status_code == 200, path
        assert response.data

    assert enforcer_client.get("/annotation-help/documents/README.md").status_code == 404


def test_visual_guide_covers_each_editor_category_and_type():
    root = Path(__file__).resolve().parents[1]
    editor = (root / "static/js/zone_editor.js").read_text(encoding="utf-8")
    basics = (root / "docs/testing/2026-10-13/annotation-visual-guide/index.html").read_text(encoding="utf-8")
    zone_types = (root / "core/zone_config.py").read_text(encoding="utf-8")

    category_types = {
        "lanes": ("active_lane",),
        "flow_arrows": ("lane_flow",),
        "threshold_lines": ("threshold", "counting_line", "no_entry_threshold"),
        "markings": ("double_solid", "single_solid", "solid_broken", "restricted_lane_boundary", "generic_marking"),
        "signs": ("no_entry", "no_left_turn", "no_right_turn", "no_u_turn", "no_overtaking", "generic_sign"),
        "activity_regions": ("passenger_activity", "boarding_alighting"),
    }
    for kind, types in category_types.items():
        assert f'key: "{kind}"' in editor
        assert f"key:'{kind}'" in basics
        for type_key in types:
            assert f'key: "{type_key}"' in editor
            assert f"key:'{type_key}'" in basics

    for type_key in ("no_parking", "pedestrian_crossing", "truck_ban_zone", "loading_unloading", "restricted_lane"):
        assert f'"{type_key}"' in zone_types
        assert f"key:'{type_key}'" in basics

    assert "ArrowRight" in basics and "ArrowLeft" in basics
    assert "camera-perspective road" in basics
