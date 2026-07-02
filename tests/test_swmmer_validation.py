"""Trust-boundary validation tests for the public ``swmmer`` API.

Most checks are pre-flight (they fire before the native engine / C library is
touched), so they run without ``runswmm`` installed.  The few that need the
engine are guarded by :data:`_HAS_ENGINE`.
"""

from __future__ import annotations

import pytest

from swmmer import (
    SWMMInputGenerator,
    SWMMOptions,
    SWMMResults,
    build_nrcs_hyetograph,
    find_engine,
    find_output_lib,
    run_swmm,
    write_rain_dat,
)


def _engine_available() -> bool:
    """True when the SWMM engine is resolvable (bundled in the wheel or on PATH)."""
    try:
        find_engine()
        find_output_lib()
    except FileNotFoundError:
        return False
    return True


_HAS_ENGINE = _engine_available()


# --- storms -----------------------------------------------------------------


@pytest.mark.parametrize("depth", [-1.0, -0.001, float("nan"), float("inf")])
def test_build_hyetograph_rejects_bad_depth(depth: float):
    with pytest.raises(ValueError, match="total_depth_mm"):
        build_nrcs_hyetograph(depth)


def test_build_hyetograph_rejects_bad_unit():
    with pytest.raises(ValueError, match="rain_dat_unit"):
        build_nrcs_hyetograph(100.0, rain_dat_unit="CM")


def test_build_hyetograph_rejects_bad_storm_type():
    with pytest.raises(ValueError, match="storm_type"):
        build_nrcs_hyetograph(100.0, storm_type="IV")


def test_build_hyetograph_rejects_unparseable_start():
    with pytest.raises(ValueError, match="ISO-8601"):
        build_nrcs_hyetograph(100.0, start="not-a-date")


def test_build_hyetograph_zero_depth_is_allowed():
    """Zero is a valid (if degenerate) depth -- all-zero intensities."""
    hyeto = build_nrcs_hyetograph(0.0)
    assert (hyeto.intensity == 0).all()


def test_write_rain_dat_creates_missing_parent(tmp_path):
    hyeto = build_nrcs_hyetograph(50.0, dt_min=6)
    target = tmp_path / "nested" / "deeper" / "storm.dat"
    out = write_rain_dat(hyeto, target)
    assert out.is_file()
    assert target.parent.is_dir()


def test_write_rain_dat_to_directory_raises(tmp_path):
    hyeto = build_nrcs_hyetograph(50.0, dt_min=6)
    with pytest.raises(IsADirectoryError):
        write_rain_dat(hyeto, tmp_path)


# --- SWMMOptions ------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("flow_units", "LITERS"),
        ("flow_routing", "DYNAMIC"),
        ("infiltration", "GREEN"),
        ("link_offsets", "OFFSET"),
        ("inertial_damping", "SOME"),
        ("normal_flow_limited", "NEITHER"),
        ("force_main_equation", "MANNING"),
        ("surcharge_method", "PREISSMANN"),
    ],
)
def test_swmmoptions_rejects_bad_literal(field: str, bad: str):
    with pytest.raises(ValueError, match=field):
        SWMMOptions(**{field: bad})


def test_swmmoptions_accepts_valid_and_none_surcharge():
    opts = SWMMOptions(flow_units="CMS", surcharge_method=None)
    assert opts.flow_units == "CMS"
    assert opts.surcharge_method is None


def test_swmmoptions_time_defaults_applied():
    opts = SWMMOptions(start_date="01/01/2000", end_date="01/02/2000")
    assert opts.start_time == "00:00:00"
    assert opts.end_time == "23:59:00"
    assert opts.report_start_date == "01/01/2000"


def test_from_rain_data_rejects_empty():
    with pytest.raises(ValueError, match="empty"):
        SWMMOptions.from_rain_data([])


def test_to_inp_section_requires_dates():
    with pytest.raises(ValueError, match="dates must be set"):
        SWMMOptions().to_inp_section()


# --- SWMMInputGenerator.generate -------------------------------------------


def _dated_generator() -> SWMMInputGenerator:
    return SWMMInputGenerator(options=SWMMOptions(start_date="01/01/2000", end_date="01/01/2000"))


def test_generate_creates_missing_parent_and_writes(tmp_path):
    target = tmp_path / "model" / "run1" / "m.inp"
    out = _dated_generator().generate(target)
    assert out.is_file()
    text = out.read_text()
    assert "[TITLE]" in text
    assert "[OPTIONS]" in text


def test_generate_to_directory_raises(tmp_path):
    with pytest.raises(IsADirectoryError):
        _dated_generator().generate(tmp_path)


# --- run_swmm (pre-flight, no engine needed) -------------------------------


def test_run_swmm_missing_input_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="SWMM input file not found"):
        run_swmm(tmp_path / "does_not_exist.inp")


def test_run_swmm_input_is_directory_raises(tmp_path):
    with pytest.raises(IsADirectoryError):
        run_swmm(tmp_path)


def test_run_swmm_bad_engine_raises(tmp_path):
    inp = tmp_path / "m.inp"
    inp.write_text("[TITLE]\nx\n")
    with pytest.raises(FileNotFoundError, match="runswmm engine not found"):
        run_swmm(inp, engine=tmp_path / "no_such_engine")


def test_run_swmm_creates_output_dir_before_engine(tmp_path):
    """Output dirs are prepared before the engine runs, so a deep ``out`` works."""
    inp = tmp_path / "m.inp"
    inp.write_text("[TITLE]\nx\n")
    outdir = tmp_path / "results" / "deep"
    # The (bogus) engine check fires *after* the output dir is created.
    with pytest.raises(FileNotFoundError, match="runswmm engine"):
        run_swmm(inp, out=outdir / "m.out", engine=tmp_path / "no_such_engine")
    assert outdir.is_dir()


def test_find_engine_raises_when_absent(monkeypatch):
    # Defeat both discovery paths: the bundled-in-package engine and PATH.
    monkeypatch.setattr("swmmer.run._bundled_engine", lambda: None)
    monkeypatch.setattr("swmmer.run.shutil.which", lambda _name: None)
    with pytest.raises(FileNotFoundError, match="runswmm not found"):
        find_engine()


# --- SWMMResults (pre-flight, no engine needed) ----------------------------


def test_swmmresults_missing_output_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="SWMM output"):
        SWMMResults(tmp_path / "missing.out")


def test_swmmresults_output_is_directory_raises(tmp_path):
    with pytest.raises(IsADirectoryError):
        SWMMResults(tmp_path)


def test_swmmresults_bad_output_lib_raises(tmp_path):
    out = tmp_path / "fake.out"
    out.write_bytes(b"\x00")  # exists so the .out check passes; lib check then fires
    with pytest.raises(FileNotFoundError, match="libswmm-output library not found"):
        SWMMResults(out, output_lib=tmp_path / "no_such_lib.so")


# --- engine-gated discovery -------------------------------------------------


@pytest.mark.skipif(not _HAS_ENGINE, reason="runswmm engine not installed")
def test_find_engine_and_output_lib_when_installed():
    engine = find_engine()
    assert engine.is_file()
    lib = find_output_lib()
    assert lib.is_file()
