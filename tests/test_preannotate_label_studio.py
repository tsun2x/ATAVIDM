from pathlib import Path

import pytest

from scripts.preannotate_label_studio import PreannotationError, detection_results, validate_model_roster


CANONICAL = [
    "car", "van", "jeepney", "tricycle", "autorickshaw",
    "bus", "truck", "pickup_truck", "motorcycle", "bicycle",
]


def test_converts_detection_to_unaccepted_label_studio_prediction() -> None:
    """Catches pixel/percent conversion errors or accidental accepted annotations."""
    task = detection_results(
        image_name="cam01__frame_00000001__ms_000000000200.jpg",
        source_group="cam01",
        width=200,
        height=100,
        detections=[{"class_id": 7, "confidence": 0.8, "xyxy": [20, 10, 100, 70]}],
        names=CANONICAL,
        model_version="predecessor-best",
    )

    assert "annotations" not in task
    assert task["data"]["review_required"] is True
    result = task["predictions"][0]["result"][0]
    assert result["score"] == 0.8
    assert result["value"] == {
        "x": 10.0, "y": 10.0, "width": 40.0, "height": 60.0,
        "rotation": 0, "rectanglelabels": ["pickup_truck"],
    }


def test_rejects_checkpoint_roster_mismatch() -> None:
    """Catches applying predictions from a model with incompatible class IDs."""
    with pytest.raises(PreannotationError, match="checkpoint class roster"):
        validate_model_roster({0: "car", 1: "truck"}, CANONICAL)
