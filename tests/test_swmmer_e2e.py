"""End-to-end tests for ``swmmer`` against EPA SWMM's own Example 1 fixtures.

``tests/data/swmmer/test_example1.{inp,out}`` are copied verbatim from the EPA
SWMM repository's test suite (Stormwater-Management-Model/tests, public domain).
The reference values asserted here are the same ones EPA's C++ ``test_output``
suite checks against that ``.out`` file, so this exercises the full
``run_swmm`` -> ``SWMMResults`` path against an independent oracle.

These need the native ``runswmm`` / ``libswmm-output`` artifacts, so they skip
when the engine is not installed.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from swmmer import (
    LinkAttr,
    NodeAttr,
    SubcatchAttr,
    SWMMResults,
    SystemAttr,
    find_engine,
    find_output_lib,
    run_swmm,
)

_DATA = Path(__file__).parent / "data" / "swmmer"
_OUT = _DATA / "test_example1.out"
_INP = _DATA / "test_example1.inp"


def _engine_available() -> bool:
    """True when the SWMM engine is resolvable (bundled in the wheel or on PATH)."""
    try:
        find_engine()
        find_output_lib()
    except FileNotFoundError:
        return False
    return True


_HAS_ENGINE = _engine_available()

pytestmark = pytest.mark.skipif(not _HAS_ENGINE, reason="runswmm/libswmm-output not installed")

# Reference series for Example 1 (periods 0..10), from EPA's test_output.cpp.
_SYSTEM_RUNOFF_0_10 = np.array(
    [
        0.0,
        6.216825,
        13.030855,
        24.252975,
        14.172027,
        4.1949716,
        0.322329,
        0.056010,
        0.024938,
        0.012474,
        0.00766089,
    ]
)
_SUBCATCH1_RUNOFF_0_10 = np.array(
    [
        0.0,
        1.2438242,
        2.5639679,
        4.524055,
        2.5115132,
        0.69808137,
        0.040894926,
        0.011605669,
        0.00509294,
        0.0027438672,
        0.00167188,
    ]
)


def test_read_reference_out_metadata():
    """Project size / units / timing match EPA's reference for Example 1."""
    with SWMMResults(_OUT) as res:
        assert (res.n_subcatch, res.n_node, res.n_link) == (8, 14, 13)
        assert res.flow_units == "CFS"
        assert res.report_step == 3600
        assert res.n_periods == 36
        assert res.start_date == datetime(1998, 1, 1)
        assert len(res.node_names) == 14
        assert res.node_names[1] == "10"


def test_read_reference_out_time_axis():
    """The time axes (read from the file's stored dates) follow SWMM's convention."""
    with SWMMResults(_OUT) as res:
        # Uniform 1-hour reporting grid; first period one step after the start.
        np.testing.assert_allclose(res.times_hours, np.arange(1, 37, dtype=float), atol=1e-6)
        assert len(res.times) == 36
        assert res.times[0] == datetime(1998, 1, 1, 1, 0)
        assert res.times[-1] == datetime(1998, 1, 2, 12, 0)
        assert res.version == 51000  # engine version that wrote the fixture


def test_read_reference_out_system_series():
    """Total runoff series matches EPA's reference values."""
    with SWMMResults(_OUT) as res:
        series = res.system_series(SystemAttr.RUNOFF)
        assert series.shape == (36,)
        np.testing.assert_allclose(series[:11], _SYSTEM_RUNOFF_0_10, rtol=1e-3, atol=1e-4)


def test_read_reference_out_subcatchment_series():
    """Runoff for subcatchment index 1 matches EPA's reference values."""
    with SWMMResults(_OUT) as res:
        name = res.subcatchment_names[1]
        series = res.subcatchment_series(name, SubcatchAttr.RUNOFF)
        np.testing.assert_allclose(series[:11], _SUBCATCH1_RUNOFF_0_10, rtol=1e-3, atol=1e-4)


def test_read_reference_out_unknown_element_raises():
    """An unknown node name is a clear KeyError, not a crash."""
    with SWMMResults(_OUT) as res, pytest.raises(KeyError, match="not found"):
        res.node_series("does-not-exist", NodeAttr.DEPTH)


def test_run_inp_then_read_results(tmp_path):
    """Running Example 1 reproduces the reference structure and hydrology."""
    inp = tmp_path / "example1.inp"
    inp.write_text(_INP.read_text())

    rpt, out = run_swmm(inp)
    assert rpt.is_file()
    assert out.is_file()

    with SWMMResults(out) as res:
        assert (res.n_subcatch, res.n_node, res.n_link) == (8, 14, 13)
        assert res.n_periods == 36
        # Engine output reproduces EPA's reference runoff on the rising limb +
        # peak; the near-zero recession tail is left out (it drifts across
        # engine versions, which is hydrology, not a swmmer concern).
        series = res.system_series(SystemAttr.RUNOFF)
        np.testing.assert_allclose(series[:7], _SYSTEM_RUNOFF_0_10[:7], rtol=1e-2, atol=1e-3)
        assert series.argmax() == 3  # peak at the 4th reporting period
        assert series.max() == pytest.approx(24.25, rel=1e-2)
        # Every node has a full-length depth series.
        depth = res.node_series(res.node_names[0], NodeAttr.DEPTH)
        assert depth.shape == (36,)


def test_run_writes_outputs_next_to_input(tmp_path):
    """Default rpt/out land beside the input with matching stems."""
    inp = tmp_path / "model" / "example1.inp"
    inp.parent.mkdir()
    inp.write_text(_INP.read_text())

    rpt, out = run_swmm(inp)
    assert rpt == inp.with_suffix(".rpt")
    assert out == inp.with_suffix(".out")


# --- cross-element snapshots + per-element results --------------------------

# All attributes for subcatchment index 1 at period 1, from EPA's test_output.cpp.
_SUBCATCH1_RESULT_P1 = np.array(
    [0.5, 0.0, 0.0, 0.125, 1.2438242, 0.0, 0.0, 0.0, 33.481991, 6.6963983]
)


def test_subcatch_result_matches_reference():
    """All-attributes-at-one-element matches EPA's reference (incl. RUNOFF at index 4)."""
    with SWMMResults(_OUT) as res:
        result = res.subcatchment_result(res.subcatchment_names[1], 1)
        np.testing.assert_allclose(result[:10], _SUBCATCH1_RESULT_P1, rtol=1e-3, atol=1e-3)


def test_node_attribute_snapshot_shape_and_consistency():
    """A snapshot has one value per node and agrees with the per-node series."""
    with SWMMResults(_OUT) as res:
        period = 5
        snap = res.node_attribute(NodeAttr.DEPTH, period)
        assert snap.shape == (res.n_node,)
        # snapshot[k] == the k-th node's depth series at that period
        for k, name in enumerate(res.node_names):
            assert snap[k] == pytest.approx(res.node_series(name, NodeAttr.DEPTH)[period])


def test_link_attribute_snapshot_consistency():
    """Link snapshot aligns to link_names and matches the per-link series."""
    with SWMMResults(_OUT) as res:
        period = 3
        snap = res.link_attribute(LinkAttr.FLOW, period)
        assert snap.shape == (res.n_link,)
        name = res.link_names[0]
        assert snap[0] == pytest.approx(res.link_series(name, LinkAttr.FLOW)[period])


def test_result_matches_series_at_period():
    """A per-element result equals the matching attribute's series at that period."""
    with SWMMResults(_OUT) as res:
        name = res.subcatchment_names[1]
        result = res.subcatchment_result(name, 7)
        runoff_series = res.subcatchment_series(name, SubcatchAttr.RUNOFF)
        assert result[int(SubcatchAttr.RUNOFF)] == pytest.approx(runoff_series[7])


def test_system_result_shape():
    with SWMMResults(_OUT) as res:
        assert res.system_result(0).shape[0] >= len(SystemAttr)


def test_attribute_period_out_of_range_raises():
    with SWMMResults(_OUT) as res:
        with pytest.raises(IndexError, match="out of range"):
            res.node_attribute(NodeAttr.DEPTH, res.n_periods)
        with pytest.raises(IndexError, match="out of range"):
            res.node_attribute(NodeAttr.DEPTH, -1)


def test_result_unknown_element_raises():
    with SWMMResults(_OUT) as res, pytest.raises(KeyError, match="not found"):
        res.node_result("nope", 0)


def test_results_are_native_float32():
    """Result arrays keep SWMM's native 32-bit float (no widening to f64)."""
    with SWMMResults(_OUT) as res:
        assert res.system_series(SystemAttr.RUNOFF).dtype == np.float32
        assert res.node_attribute(NodeAttr.DEPTH, 0).dtype == np.float32
        assert res.node_result(res.node_names[0], 0).dtype == np.float32


def test_times64_matches_times_and_is_compact():
    """times64 is a datetime64[s] array equal to the per-period datetimes."""
    with SWMMResults(_OUT) as res:
        assert res.times64.dtype == np.dtype("datetime64[s]")
        assert res.times64.shape == (res.n_periods,)
        assert np.array_equal(res.times64, np.array(res.times, dtype="datetime64[s]"))
        # cached_property returns the same object on repeat access
        assert res.times is res.times


# --- to_pandas / to_xarray (optional deps; present in this env) --------------


def test_to_pandas_node_panel():
    with SWMMResults(_OUT) as res:
        df = res.to_pandas(NodeAttr.DEPTH, "node")
        assert df.shape == (res.n_periods, res.n_node)
        assert df.values.dtype == np.float32  # native, not widened
        assert type(df.index).__name__ == "DatetimeIndex"
        assert list(df.columns) == res.node_names
        n0 = res.node_names[0]
        np.testing.assert_allclose(df[n0].to_numpy(), res.node_series(n0, NodeAttr.DEPTH))


def test_to_pandas_system_single_column():
    with SWMMResults(_OUT) as res:
        df = res.to_pandas(SystemAttr.RUNOFF, "system")
        assert df.shape == (res.n_periods, 1)
        assert list(df.columns) == ["RUNOFF"]


def test_to_pandas_invalid_element_type_raises():
    with SWMMResults(_OUT) as res, pytest.raises(ValueError, match="element_type"):
        res.to_pandas(NodeAttr.DEPTH, "nodes")  # typo


def test_to_xarray_node_cube():
    with SWMMResults(_OUT) as res:
        ds = res.to_xarray("node")
        assert dict(ds.sizes) == {"time": res.n_periods, "node": res.n_node}
        assert list(ds.data_vars) == [a.name for a in NodeAttr]
        assert set(ds.coords) == {"time", "node"}
        n0 = res.node_names[0]
        np.testing.assert_allclose(
            ds["DEPTH"].sel(node=n0).to_numpy(), res.node_series(n0, NodeAttr.DEPTH)
        )


def test_to_xarray_subset_of_attrs():
    with SWMMResults(_OUT) as res:
        ds = res.to_xarray("link", attrs=[LinkAttr.FLOW])
        assert list(ds.data_vars) == ["FLOW"]
        assert dict(ds.sizes) == {"time": res.n_periods, "link": res.n_link}


def test_optional_dependency_missing_raises(monkeypatch):
    """A missing pandas/xarray gives a clear install hint, not a bare ImportError."""
    import swmmer.run as run_mod

    def _boom(_name):
        raise ImportError(_name)

    monkeypatch.setattr(run_mod.importlib, "import_module", _boom)
    with SWMMResults(_OUT) as res, pytest.raises(ImportError, match="optional dependency"):
        res.to_pandas(NodeAttr.DEPTH, "node")
