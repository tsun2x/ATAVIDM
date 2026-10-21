"""Pure geometry and model-contract tests for offline pickup evaluation."""

from __future__ import annotations

import pytest

from scripts.evaluate_pickup_detail import (
    map_crop_box_to_source,
    normalize_class_names,
    padded_pickup_crop,
)


def test_context_crop_includes_area_above_pickup_and_clips_to_frame():
    crop = padded_pickup_crop((100, 100, 200, 160), 320, 240)
    assert crop == {"x1": 80, "y1": 55, "x2": 220, "y2": 169, "clipped": False}

    clipped = padded_pickup_crop((5, 12, 55, 42), 100, 100)
    assert clipped["y1"] == 0
    assert clipped["clipped"] is True


def test_crop_predictions_map_back_to_source_frame_coordinates():
    crop = {"x1": 80, "y1": 55, "x2": 220, "y2": 169, "clipped": False}
    assert map_crop_box_to_source((3, 4, 23, 34), crop, 320, 240) == (
        83.0, 59.0, 103.0, 89.0
    )


@pytest.mark.parametrize(
    "names",
    [
        {0: "car", 1: "person"},
        {0: "pickup_truck", 2: "person"},
        {0: "person", 1: "person", 2: "pickup_truck"},
    ],
)
def test_model_contract_requires_unique_contiguous_person_and_pickup_classes(names):
    with pytest.raises(ValueError):
        normalize_class_names(names)


def test_class_map_preserves_id_order():
    assert normalize_class_names({1: "person", 0: "pickup_truck"}) == (
        "pickup_truck", "person"
    )
