from __future__ import annotations

import pytest

from build_v9_merged_derivative import MergeError, convert_label_line


def test_convert_label_line_remaps_box_without_changing_geometry() -> None:
    # Catches copying archive-local class IDs directly into the canonical schema.
    line, was_polygon = convert_label_line("0 0.5 0.4 0.2 0.1", ["bus"], ["car", "bus"])

    assert line == "1 0.5 0.4 0.2 0.1"
    assert was_polygon is False


def test_convert_label_line_encloses_polygon_in_rectangular_box() -> None:
    # Catches passing segmentation coordinates into a YOLO detection dataset.
    line, was_polygon = convert_label_line(
        "0 0.1 0.2 0.7 0.2 0.7 0.8 0.1 0.8",
        ["helmet_nut_shell"],
        ["helmet_nut_shell"],
    )

    assert line == "0 0.4 0.5 0.6 0.6"
    assert was_polygon is True


def test_convert_label_line_rejects_noncanonical_class() -> None:
    # Catches silently dropping or inventing a mapping for an unsupported class.
    with pytest.raises(MergeError, match="noncanonical"):
        convert_label_line("0 0.5 0.5 0.2 0.2", ["other"], ["bus"])
