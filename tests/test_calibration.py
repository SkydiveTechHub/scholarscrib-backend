import math

import pytest

from app.services.learning.calibration import (
    BUCKETS,
    LOW_SAMPLE,
    PredictionRow,
    calibration_report,
)


def row(predicted, correct, confidence=0.5, difficulty="INTERMEDIATE"):
    return PredictionRow(predicted, correct, confidence, difficulty)


def test_empty_input_gives_zeroed_report_and_ten_empty_buckets():
    report = calibration_report([])
    assert report["overall"]["n"] == 0
    assert report["overall"]["brier"] == 0
    assert report["overall"]["logLoss"] == 0
    assert len(report["buckets"]) == BUCKETS
    assert all(bucket["n"] == 0 and bucket["lowSample"] for bucket in report["buckets"])
    assert report["buckets"][0]["observed"] is None
    assert report["byDifficulty"] == {}


def test_known_brier_and_log_loss():
    report = calibration_report([row(0.8, True), row(0.8, False)])
    overall = report["overall"]
    assert overall["n"] == 2
    assert overall["baseRate"] == pytest.approx(0.5)
    assert overall["meanPredicted"] == pytest.approx(0.8)
    assert overall["brier"] == pytest.approx(0.34)
    assert overall["baselineBrier"] == pytest.approx(0.25)
    assert overall["logLoss"] == pytest.approx(-(math.log(0.8) + math.log(0.2)) / 2)
    assert overall["baselineLogLoss"] == pytest.approx(math.log(2))


def test_extreme_predictions_keep_log_loss_finite():
    report = calibration_report([row(0.0, True), row(1.0, False)])
    assert math.isfinite(report["overall"]["logLoss"])
    assert report["overall"]["logLoss"] > 0


def test_bucket_edges():
    report = calibration_report(
        [row(0.0, False), row(0.1, True), row(0.9999, True), row(1.0, True)]
    )
    buckets = report["buckets"]
    assert buckets[0]["n"] == 1
    assert buckets[1]["n"] == 1
    assert buckets[9]["n"] == 2
    assert buckets[9]["observed"] == 1.0
    assert buckets[9]["lower"] == pytest.approx(0.9)
    assert buckets[9]["upper"] == pytest.approx(1.0)


def test_low_sample_flag_flips_at_the_threshold():
    few = calibration_report([row(0.55, True)] * (LOW_SAMPLE - 1))
    enough = calibration_report([row(0.55, True)] * LOW_SAMPLE)
    assert few["buckets"][5]["lowSample"] is True
    assert enough["buckets"][5]["lowSample"] is False


def test_evidence_split_uses_the_confidence_floor():
    report = calibration_report(
        [row(0.5, True, confidence=0.2), row(0.5, False, confidence=0.35)]
    )
    assert report["byEvidence"]["belowFloor"]["n"] == 1
    assert report["byEvidence"]["atOrAboveFloor"]["n"] == 1


def test_difficulty_split_names_missing_difficulty_unknown():
    report = calibration_report(
        [row(0.5, True, difficulty="BASIC"), row(0.5, True, difficulty=None)]
    )
    assert set(report["byDifficulty"]) == {"BASIC", "UNKNOWN"}
