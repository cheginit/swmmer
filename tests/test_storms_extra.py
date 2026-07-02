"""Behavioral tests for the NRCS hyetograph builder and duration parsing."""

from __future__ import annotations

from datetime import datetime

import pytest

from swmmer import build_nrcs_hyetograph, duration_hours


@pytest.mark.parametrize(
    ("label", "hours"),
    [("5-min", 5 / 60), ("30-min", 0.5), ("2-hr", 2.0), ("6-hour", 6.0), ("1-day", 24.0)],
)
def test_duration_hours_units(label: str, hours: float):
    assert duration_hours(label) == pytest.approx(hours)


def test_duration_hours_unrecognized_unit():
    with pytest.raises(ValueError, match="Unrecognized duration label"):
        duration_hours("5-widgets")


def test_hyetograph_conserves_and_terminates():
    hyeto = build_nrcs_hyetograph(150.0, storm_type="II", duration="24-hr", dt_min=6)
    assert len(hyeto.times) == len(hyeto.intensity)
    assert (hyeto.intensity >= 0).all()
    assert hyeto.intensity[-1] == 0.0  # rain stops cleanly


def test_hyetograph_accepts_datetime_start():
    start = datetime(2021, 6, 1, 12, 0)
    hyeto = build_nrcs_hyetograph(100.0, start=start)
    assert hyeto.times[0] == start


def test_hyetograph_inch_unit_scales_depth():
    mm = build_nrcs_hyetograph(254.0, rain_dat_unit="MM")
    inch = build_nrcs_hyetograph(254.0, rain_dat_unit="IN")  # 254 mm -> 10 in of depth
    assert inch.intensity.max() == pytest.approx(mm.intensity.max() / 25.4, rel=1e-6)


def test_hyetograph_rejects_indivisible_timestep():
    with pytest.raises(ValueError, match="divisor"):
        build_nrcs_hyetograph(100.0, duration="24-hr", dt_min=7)  # 1440 / 7 is not integer


def test_hyetograph_non_24h_duration_is_stretched():
    hyeto = build_nrcs_hyetograph(50.0, duration="12-hr", dt_min=6)
    span_h = (hyeto.times[-1] - hyeto.times[0]).total_seconds() / 3600.0
    assert span_h == pytest.approx(12.0)
