"""Coverage tests for SWMMResults accessors not exercised by the e2e suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from swmmer import (
    LinkAttr,
    NodeAttr,
    SubcatchAttr,
    SWMMResults,
    find_engine,
    find_output_lib,
)

_OUT = Path(__file__).parent / "data" / "swmmer" / "test_example1.out"


def _engine_available() -> bool:
    try:
        find_engine()
        find_output_lib()
    except FileNotFoundError:
        return False
    return True


pytestmark = pytest.mark.skipif(not _engine_available(), reason="SWMM engine/library not available")


@pytest.fixture
def res():
    with SWMMResults(_OUT) as r:
        yield r


def test_time_axes_aligned(res: SWMMResults):
    n = len(res.times_hours)
    assert n > 0
    assert len(res.times) == n
    assert len(res.times64) == n
    assert res.times_hours[0] < res.times_hours[-1]


def test_named_node_and_link_helpers(res: SWMMResults):
    node = res.node_names[0]
    link = res.link_names[0]
    shape = res.times_hours.shape
    assert res.node_volume(node).shape == shape
    assert res.node_inflow(node).shape == shape
    assert res.node_flooding(node).shape == shape
    assert res.link_flow(link).shape == shape


def test_subcatchment_series(res: SWMMResults):
    sub = res.subcatchment_names[0]
    series = res.subcatchment_series(sub, SubcatchAttr.RUNOFF)
    assert series.shape == res.times_hours.shape
    assert (series >= 0).all()


def test_snapshot_readers(res: SWMMResults):
    """Single-period snapshots across all elements / all attributes."""
    assert res.node_attribute(NodeAttr.DEPTH, 0).shape == (len(res.node_names),)
    assert res.link_attribute(LinkAttr.FLOW, 0).shape == (len(res.link_names),)
    # per-element, all-attributes vectors at a period
    assert res.node_result(res.node_names[0], 0).size > 0
    assert res.link_result(res.link_names[0], 0).size > 0
    assert res.system_result(0).size > 0


def test_to_pandas_link_and_subcatchment(res: SWMMResults):
    pd = pytest.importorskip("pandas")
    df_link = res.to_pandas(LinkAttr.FLOW, element_type="link")
    assert list(df_link.columns) == res.link_names
    assert len(df_link) == len(res.times64)
    df_sub = res.to_pandas(SubcatchAttr.RUNOFF, element_type="subcatchment")
    assert isinstance(df_sub, pd.DataFrame)
    assert len(df_sub) == len(res.times64)


def test_to_pandas_rejects_bad_element_type(res: SWMMResults):
    with pytest.raises(ValueError, match="element_type"):
        res.to_pandas(NodeAttr.DEPTH, element_type="bogus")


def test_to_xarray_system_and_subcatchment(res: SWMMResults):
    pytest.importorskip("xarray")
    ds_sys = res.to_xarray("system")
    assert ds_sys.sizes
    ds_sub = res.to_xarray("subcatchment")
    assert ds_sub.sizes
