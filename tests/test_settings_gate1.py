"""Gate 1 — Settings presentation and atomic rule-parameter validation."""

from __future__ import annotations

import math

import pytest

from core.detection_config import (
    DEFAULT_RULE_PARAMETERS,
    VIOLATION_ILLEGAL_TERMINAL,
    VIOLATION_OBSTRUCTION,
)
from core.rule_parameter_validation import (
    RuleParameterValidationError,
    validate_rule_parameters,
)
from core.video_processor import load_rule_parameters
from core.violation_config import save_enabled_violations
from database import db


@pytest.fixture
def admin_client(client):
    """Logged-in admin client against the isolated temporary database."""
    client.post("/login", data={"username": "admin", "password": "admin123"})
    return client


class TestRuleParameterValidation:
    def test_valid_payload_coerces_numbers(self):
        out = validate_rule_parameters(
            {
                "obstruction_dwell_sec": "12.5",
                "crossing_block_sec": 4,
                "loading_dwell_sec": "8",
                "frame_skip": "3",
                "confidence_threshold": "0.55",
                "lane_flow_degrees": 90,
                "flow_tolerance_degrees": 45,
            }
        )
        assert out["obstruction_dwell_sec"] == 12.5
        assert out["crossing_block_sec"] == 4.0
        assert out["loading_dwell_sec"] == 8.0
        assert out["frame_skip"] == 3
        assert out["confidence_threshold"] == 0.55

    def test_rejects_nan_infinity_and_malformed(self):
        for raw in (float("nan"), float("inf"), "-inf", "abc", "", None, True):
            with pytest.raises(RuleParameterValidationError):
                validate_rule_parameters({"obstruction_dwell_sec": raw})

    def test_rejects_out_of_range(self):
        with pytest.raises(RuleParameterValidationError):
            validate_rule_parameters({"obstruction_dwell_sec": 0.5})
        with pytest.raises(RuleParameterValidationError):
            validate_rule_parameters({"frame_skip": 0})
        with pytest.raises(RuleParameterValidationError):
            validate_rule_parameters({"frame_skip": 11})
        with pytest.raises(RuleParameterValidationError):
            validate_rule_parameters({"confidence_threshold": 0.2})
        with pytest.raises(RuleParameterValidationError):
            validate_rule_parameters({"lane_flow_degrees": 400})
        with pytest.raises(RuleParameterValidationError):
            validate_rule_parameters({"flow_tolerance_degrees": 5})

    def test_atomic_multi_key_failure_lists_errors(self):
        with pytest.raises(RuleParameterValidationError) as exc:
            validate_rule_parameters(
                {
                    "obstruction_dwell_sec": -1,
                    "crossing_block_sec": "bad",
                }
            )
        message = str(exc.value)
        assert "obstruction_dwell_sec" in message
        assert "crossing_block_sec" in message


class TestSettingsRouteGate1:
    def test_settings_page_renders_dwell_values_and_links(self, admin_client):
        db.set_settings(
            {
                "obstruction_dwell_sec": "11.5",
                "crossing_block_sec": "4.5",
                "loading_dwell_sec": "9.0",
            }
        )
        response = admin_client.get("/settings")
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'data-setting-value="obstruction_dwell_sec"' in html
        assert ">11.5<" in html or ">11.5</strong>" in html
        assert 'data-setting-value="crossing_block_sec"' in html
        assert 'data-setting-value="loading_dwell_sec"' in html
        assert 'href="#param-obstruction-dwell"' in html
        assert 'href="#param-crossing-block"' in html
        assert 'href="#param-loading-dwell"' in html
        assert 'id="param-obstruction-dwell"' in html
        assert 'id="param-crossing-block"' in html
        assert 'id="param-loading-dwell"' in html
        assert 'id="detection-rule-parameters"' in html

    def test_valid_save_and_reload(self, admin_client):
        payload = {
            "obstruction_dwell_sec": 14.0,
            "crossing_block_sec": 5.0,
            "loading_dwell_sec": 11.0,
            "confidence_threshold": 0.65,
            "frame_skip": 3,
            "stopping_dwell_sec": DEFAULT_RULE_PARAMETERS["stopping_dwell_sec"],
            "parking_dwell_sec": DEFAULT_RULE_PARAMETERS["parking_dwell_sec"],
            "truck_ban_start": "06:00",
            "truck_ban_end": "09:00",
            "lane_flow_degrees": 90,
            "flow_tolerance_degrees": 60,
        }
        response = admin_client.post("/api/settings", json=payload)
        assert response.status_code == 200
        body = response.get_json()
        assert body["success"] is True

        loaded = load_rule_parameters()
        assert loaded["obstruction_dwell_sec"] == 14.0
        assert loaded["crossing_block_sec"] == 5.0
        assert loaded["loading_dwell_sec"] == 11.0
        assert loaded["confidence_threshold"] == 0.65
        assert loaded["frame_skip"] == 3

        get_resp = admin_client.get("/api/settings")
        assert get_resp.status_code == 200
        settings = get_resp.get_json()["settings"]
        assert settings["obstruction_dwell_sec"] == 14.0
        assert settings["loading_dwell_sec"] == 11.0

    def test_atomic_rejection_preserves_prior_values(self, admin_client):
        admin_client.post(
            "/api/settings",
            json={
                "obstruction_dwell_sec": 13.0,
                "crossing_block_sec": 3.5,
                "loading_dwell_sec": 8.5,
                "confidence_threshold": 0.60,
                "frame_skip": 2,
                "stopping_dwell_sec": 10.0,
                "parking_dwell_sec": 30.0,
                "truck_ban_start": "06:00",
                "truck_ban_end": "09:00",
                "lane_flow_degrees": 90,
                "flow_tolerance_degrees": 60,
            },
        )
        before = load_rule_parameters()

        bad = admin_client.post(
            "/api/settings",
            json={
                "obstruction_dwell_sec": 20.0,
                "crossing_block_sec": float("nan"),
                "loading_dwell_sec": 99.0,
            },
        )
        assert bad.status_code == 400
        assert bad.get_json()["success"] is False

        after = load_rule_parameters()
        assert after["obstruction_dwell_sec"] == before["obstruction_dwell_sec"]
        assert after["crossing_block_sec"] == before["crossing_block_sec"]
        assert after["loading_dwell_sec"] == before["loading_dwell_sec"]
        assert not math.isnan(after["crossing_block_sec"])

    def test_disabling_violation_preserves_dwell_values(self, admin_client):
        admin_client.post(
            "/api/settings",
            json={
                "obstruction_dwell_sec": 16.0,
                "crossing_block_sec": 6.0,
                "loading_dwell_sec": 12.0,
                "confidence_threshold": 0.60,
                "frame_skip": 2,
                "stopping_dwell_sec": 10.0,
                "parking_dwell_sec": 30.0,
                "truck_ban_start": "06:00",
                "truck_ban_end": "09:00",
                "lane_flow_degrees": 90,
                "flow_tolerance_degrees": 60,
                "enabled_violations": [
                    VIOLATION_OBSTRUCTION,
                    VIOLATION_ILLEGAL_TERMINAL,
                ],
            },
        )
        # Disable both rules while resubmitting the same dwell values (UI behavior).
        response = admin_client.post(
            "/api/settings",
            json={
                "obstruction_dwell_sec": 16.0,
                "crossing_block_sec": 6.0,
                "loading_dwell_sec": 12.0,
                "confidence_threshold": 0.60,
                "frame_skip": 2,
                "stopping_dwell_sec": 10.0,
                "parking_dwell_sec": 30.0,
                "truck_ban_start": "06:00",
                "truck_ban_end": "09:00",
                "lane_flow_degrees": 90,
                "flow_tolerance_degrees": 60,
                "enabled_violations": [],
            },
        )
        assert response.status_code == 200
        assert response.get_json()["enabled_violations"] == []
        loaded = load_rule_parameters()
        assert loaded["obstruction_dwell_sec"] == 16.0
        assert loaded["crossing_block_sec"] == 6.0
        assert loaded["loading_dwell_sec"] == 12.0

    def test_html_static_contains_dwell_anchors(self):
        html = open("templates/settings.html", encoding="utf-8").read()
        assert 'data-setting-value="obstruction_dwell_sec"' in html
        assert 'data-setting-value="crossing_block_sec"' in html
        assert 'data-setting-value="loading_dwell_sec"' in html
        assert 'href="#param-obstruction-dwell"' in html
        assert 'id="param-loading-dwell"' in html
