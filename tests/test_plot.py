"""Behavioral tests for swmmer.plot (parsing + matplotlib rendering)."""

from __future__ import annotations

from pathlib import Path

import matplotlib as mpl
import pytest

mpl.use("Agg")  # headless, no display needed
import matplotlib.pyplot as plt

from swmmer import plot

_INP = Path(__file__).parent / "data" / "swmmer" / "test_example1.inp"


@pytest.fixture
def data() -> plot.InpData:
    return plot.read_inp(_INP)


def test_read_inp_geometry(data: plot.InpData):
    assert len(data.subcatchments) == 8
    assert len(data.nodes) == 14
    assert len(data.links) == 13
    # the single outfall (node 18) is typed and carries coordinates
    outfalls = [n for n, s in data.nodes.items() if s["node_type"] == "outfall"]
    assert outfalls == ["18"]
    node = data.nodes["18"]
    assert {"x", "y", "elevation", "node_type", "depth_max"} <= node.keys()
    # every conduit gets a polyline (its two end coordinates at least)
    assert all(len(link["vertices"]) >= 2 for link in data.links.values())
    # subcatchment polygons parsed from the [Polygons] section
    assert any(s["polygon"] for s in data.subcatchments.values())


def test_read_inp_accepts_str_path():
    assert plot.read_inp(str(_INP)).nodes


def test_read_inp_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        plot.read_inp(tmp_path / "nope.inp")


def test_plot_network_default(data: plot.InpData):
    fig, ax = plot.plot_network(data)
    assert isinstance(fig, plt.Figure)
    # subcatchment patches, conduit lines and node scatters are all drawn
    assert ax.collections
    assert ax.get_title()
    plt.close(fig)


def test_plot_network_accepts_path():
    fig, ax = plot.plot_network(_INP, labels=True, show_subcatchments=False)
    assert ax.collections
    plt.close(fig)


def test_plot_network_value_overlays_add_colorbars(data: plot.InpData):
    node_v = dict.fromkeys(data.nodes, 1.0)
    link_v = dict.fromkeys(data.links, 2.0)
    sub_v = dict.fromkeys(data.subcatchments, 3.0)
    fig, _ax = plot.plot_network(
        data, node_values=node_v, link_values=link_v, subcatch_values=sub_v
    )
    # each value overlay adds a colorbar axes, so the figure has extra axes
    assert len(fig.axes) > 1
    plt.close(fig)


def test_plot_network_reuses_given_ax(data: plot.InpData):
    fig, ax = plt.subplots()
    out_fig, out_ax = plot.plot_network(data, ax=ax)
    assert out_ax is ax
    assert out_fig is fig
    plt.close(fig)


def test_plot_profile(data: plot.InpData):
    outfall = next(n for n, s in data.nodes.items() if s["node_type"] == "outfall")
    downstream = {link["to_node"] for link in data.links.values()}
    headwater = next(
        link["from_node"] for link in data.links.values() if link["from_node"] not in downstream
    )
    fig, ax = plot.plot_profile(data, headwater, outfall)
    assert ax.lines  # ground / invert lines
    assert outfall in ax.get_title()
    plt.close(fig)


def test_plot_profile_no_path_raises(data: plot.InpData):
    with pytest.raises(ValueError, match="no downstream path"):
        plot.plot_profile(data, "18", "9")  # outfall has no downstream path to a headwater


def test_downstream_path_trivial(data: plot.InpData):
    assert plot._downstream_path(data, "18", "18") == ["18"]
