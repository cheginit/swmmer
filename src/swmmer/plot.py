"""Matplotlib plots of a SWMM model: network map and longitudinal profile.

Parses geometry straight from a SWMM ``.inp`` (:func:`read_inp`) and draws it
with matplotlib only — no geopandas/shapely — so the sole extra dependency is
matplotlib (``pip install swmmer[plot]``).  Node/link/subcatchment values (e.g.
peak flow or depth from :class:`swmmer.SWMMResults`) can be overlaid as colors.
"""

from __future__ import annotations

__lazy_modules__ = ["collections", "re", "swmmer._paths"]

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, cast

from swmmer._paths import resolve_input_file

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from matplotlib.axes import Axes
    from matplotlib.figure import Figure

NodeType = Literal["junction", "outfall", "storage"]
LinkType = Literal["conduit", "pump", "orifice", "weir", "outlet"]

# (marker, face color, size) per node type / (color, linewidth) per link type.
_NODE_STYLE: dict[str, tuple[str, str, float]] = {
    "junction": ("o", "#3498db", 45),
    "outfall": ("^", "#e74c3c", 90),
    "storage": ("s", "#2ecc71", 70),
}
_LINK_STYLE: dict[str, tuple[str, float]] = {
    "conduit": ("#7f8c8d", 1.8),
    "pump": ("#d35400", 2.5),
    "orifice": ("#e67e22", 2.0),
    "weir": ("#16a085", 2.5),
    "outlet": ("#9b59b6", 2.0),
}

_Rows = dict[str, list[list[str]]]
_Coords = dict[str, tuple[float, float]]


@dataclass
class InpData:
    """Parsed geometry of a SWMM ``.inp``.

    ``nodes[name]`` has ``x, y, elevation, node_type, depth_max``;
    ``links[name]`` has ``from_node, to_node, link_type, length, vertices``
    (a list of ``(x, y)`` including both end nodes); ``subcatchments[name]`` has
    ``outlet, area, polygon``; ``xsections[name]`` has ``shape, height``.
    """

    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    links: dict[str, dict[str, Any]] = field(default_factory=dict)
    subcatchments: dict[str, dict[str, Any]] = field(default_factory=dict)
    xsections: dict[str, dict[str, Any]] = field(default_factory=dict)


def _sections(text: str) -> _Rows:
    """Split ``.inp`` text into ``{SECTION: [tokenized non-comment rows]}``."""
    out: _Rows = {}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        header = re.match(r"^\[(\w+)\]", line)
        if header:
            current = cast("str", header.group(1)).upper()
            out[current] = []
        elif current and line and not line.startswith(";"):
            out[current].append(line.split())
    return out


def _parse_coords(sec: _Rows) -> tuple[_Coords, dict[str, list[tuple[float, float]]]]:
    """Parse [COORDINATES] into node points and [VERTICES] into per-link points."""
    coords: _Coords = {
        r[0]: (float(r[1]), float(r[2])) for r in sec.get("COORDINATES", []) if len(r) >= 3
    }
    verts: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for r in sec.get("VERTICES", []):
        if len(r) >= 3:
            verts[r[0]].append((float(r[1]), float(r[2])))
    return coords, verts


def _parse_nodes(sec: _Rows, coords: _Coords) -> dict[str, dict[str, Any]]:
    """Parse junction/outfall/storage rows that carry coordinates."""
    nodes: dict[str, dict[str, Any]] = {}
    for section, node_type in (
        ("JUNCTIONS", "junction"),
        ("OUTFALLS", "outfall"),
        ("STORAGE", "storage"),
    ):
        for r in sec.get(section, []):
            if len(r) >= 2 and r[0] in coords:
                x, y = coords[r[0]]
                depth = float(r[2]) if node_type != "outfall" and len(r) > 2 else 0.0
                nodes[r[0]] = {
                    "x": x,
                    "y": y,
                    "elevation": float(r[1]),
                    "node_type": node_type,
                    "depth_max": depth,
                }
    return nodes


def _parse_links(
    sec: _Rows, coords: _Coords, verts: dict[str, list[tuple[float, float]]]
) -> dict[str, dict[str, Any]]:
    """Parse conduits + special links, attaching each polyline's vertices."""
    links: dict[str, dict[str, Any]] = {}
    for r in sec.get("CONDUITS", []):
        if len(r) >= 4:
            links[r[0]] = {
                "from_node": r[1],
                "to_node": r[2],
                "link_type": "conduit",
                "length": float(r[3]),
                "vertices": [],
            }
    for section, link_type in (
        ("PUMPS", "pump"),
        ("ORIFICES", "orifice"),
        ("WEIRS", "weir"),
        ("OUTLETS", "outlet"),
    ):
        for r in sec.get(section, []):
            if len(r) >= 3:
                links[r[0]] = {
                    "from_node": r[1],
                    "to_node": r[2],
                    "link_type": link_type,
                    "length": 0.0,
                    "vertices": [],
                }
    for name, link in links.items():
        a, b = link["from_node"], link["to_node"]
        if a in coords and b in coords:
            link["vertices"] = [coords[a], *verts.get(name, []), coords[b]]
    return links


def _parse_subcatchments(sec: _Rows) -> dict[str, dict[str, Any]]:
    """Parse subcatchments and attach their [Polygons] rings."""
    subs: dict[str, dict[str, Any]] = {}
    for r in sec.get("SUBCATCHMENTS", []):
        if len(r) >= 3:
            subs[r[0]] = {"outlet": r[2], "area": float(r[3]) if len(r) > 3 else 0.0, "polygon": []}
    polys: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for r in sec.get("POLYGONS", []):
        if len(r) >= 3:
            polys[r[0]].append((float(r[1]), float(r[2])))
    for name, poly in polys.items():
        if name in subs:
            subs[name]["polygon"] = poly
    return subs


def read_inp(path: str | Path) -> InpData:
    """Parse node/link/subcatchment geometry from a SWMM ``.inp`` file.

    Parameters
    ----------
    path : str or Path
        Path to the SWMM input file.

    Returns
    -------
    InpData
        Parsed geometry, ready for :func:`plot_network` / :func:`plot_profile`.

    """
    text = resolve_input_file(path, what="SWMM input file").read_text(
        encoding="utf-8", errors="replace"
    )
    sec = _sections(text)
    coords, verts = _parse_coords(sec)
    return InpData(
        nodes=_parse_nodes(sec, coords),
        links=_parse_links(sec, coords, verts),
        subcatchments=_parse_subcatchments(sec),
        xsections={
            r[0]: {"shape": r[1], "height": float(r[2])}
            for r in sec.get("XSECTIONS", [])
            if len(r) >= 3
        },
    )


def _require_mpl() -> Any:
    """Import matplotlib.pyplot, with an actionable error if it is missing."""
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        msg = "plotting requires matplotlib (pip install swmmer[plot])"
        raise ImportError(msg) from exc
    return plt


def _fig_ax(plt: Any, ax: Axes | None, figsize: tuple[float, float]) -> tuple[Figure, Axes]:
    """Return the figure/axes to draw on, creating a new pair if ``ax`` is None."""
    if ax is None:
        return plt.subplots(figsize=figsize)
    return cast("Figure", ax.get_figure()), ax


def _colorbar(ax: Axes, mappable: Any, label: str) -> None:
    """Attach a shrunk colorbar for ``mappable`` to ``ax``'s figure."""
    cast("Figure", ax.get_figure()).colorbar(mappable, ax=ax, shrink=0.6, label=label)


def _draw_subcatchments(
    ax: Axes, data: InpData, values: Mapping[str, float] | None, cmap: str
) -> None:
    from matplotlib.collections import PatchCollection
    from matplotlib.patches import Polygon

    polys = [(n, s) for n, s in data.subcatchments.items() if s["polygon"]]
    if not polys:
        return
    patches = [Polygon(s["polygon"], closed=True) for _, s in polys]
    if values is not None:
        pc = PatchCollection(patches, cmap=cmap, alpha=0.55, edgecolor="darkgray", linewidths=0.5)
        pc.set_array([float(values.get(n, float("nan"))) for n, _ in polys])
        ax.add_collection(pc)
        _colorbar(ax, pc, "subcatchment")
    else:
        ax.add_collection(
            PatchCollection(
                patches, facecolor="lightgray", alpha=0.35, edgecolor="darkgray", linewidths=0.5
            )
        )
    # dashed tie line from each subcatchment centroid to its outlet node
    for _name, s in polys:
        xs = [p[0] for p in s["polygon"]]
        ys = [p[1] for p in s["polygon"]]
        cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
        outlet = data.nodes.get(s["outlet"])
        if outlet is not None:
            ax.plot(
                [cx, outlet["x"]],
                [cy, outlet["y"]],
                "--",
                color="gray",
                alpha=0.4,
                lw=0.5,
                zorder=1,
            )


def _draw_links(ax: Axes, data: InpData, values: Mapping[str, float] | None, cmap: str) -> None:
    from matplotlib.collections import LineCollection

    if values is not None:
        segs = [s["vertices"] for s in data.links.values() if s["vertices"]]
        vals = [float(values.get(n, float("nan"))) for n, s in data.links.items() if s["vertices"]]
        if segs:
            lc = LineCollection(segs, cmap=cmap, linewidths=2.0, zorder=2)
            lc.set_array(vals)
            ax.add_collection(lc)
            _colorbar(ax, lc, "link")
        return
    for link_type, (color, lw) in _LINK_STYLE.items():
        segs = [
            s["vertices"]
            for s in data.links.values()
            if s["vertices"] and s["link_type"] == link_type
        ]
        if segs:
            ax.add_collection(
                LineCollection(segs, colors=color, linewidths=lw, zorder=2, label=link_type)
            )


def _draw_nodes(
    ax: Axes, data: InpData, values: Mapping[str, float] | None, cmap: str, labels: bool
) -> None:
    for node_type, (marker, color, size) in _NODE_STYLE.items():
        items = [(n, s) for n, s in data.nodes.items() if s["node_type"] == node_type]
        if not items:
            continue
        xs = [s["x"] for _, s in items]
        ys = [s["y"] for _, s in items]
        if values is not None:
            sc = ax.scatter(
                xs,
                ys,
                c=[float(values.get(n, float("nan"))) for n, _ in items],
                cmap=cmap,
                marker=marker,
                s=size,
                edgecolor="black",
                linewidths=0.5,
                zorder=4,
            )
            if node_type == "junction":
                _colorbar(ax, sc, "node")
        else:
            ax.scatter(
                xs,
                ys,
                c=color,
                marker=marker,
                s=size,
                edgecolor="black",
                linewidths=0.5,
                zorder=4,
                label=node_type,
            )
    if labels:
        for name, s in data.nodes.items():
            ax.annotate(
                name, (s["x"], s["y"]), textcoords="offset points", xytext=(3, 3), fontsize=7
            )


def plot_network(
    inp: str | Path | InpData,
    *,
    node_values: Mapping[str, float] | None = None,
    link_values: Mapping[str, float] | None = None,
    subcatch_values: Mapping[str, float] | None = None,
    show_subcatchments: bool = True,
    labels: bool = False,
    cmap: str = "viridis",
    title: str = "SWMM network",
    figsize: tuple[float, float] = (11, 9),
    ax: Axes | None = None,
) -> tuple[Figure, Axes]:
    """Draw the SWMM network: subcatchments, links and nodes.

    Pass ``node_values`` / ``link_values`` / ``subcatch_values`` (name -> value,
    e.g. peak flow from :class:`swmmer.SWMMResults`) to color elements and add a
    colorbar. Returns the matplotlib ``(figure, axes)``.
    """
    data = inp if isinstance(inp, InpData) else read_inp(inp)
    plt = _require_mpl()
    fig, ax = _fig_ax(plt, ax, figsize)

    if show_subcatchments:
        _draw_subcatchments(ax, data, subcatch_values, cmap)
    _draw_links(ax, data, link_values, cmap)
    _draw_nodes(ax, data, node_values, cmap, labels)

    ax.set_aspect("equal")
    ax.set_axis_off()
    ax.margins(0.05)
    ax.set_title(title, fontsize=13, fontweight="bold")
    if not (node_values or link_values or subcatch_values):
        ax.legend(loc="best", framealpha=0.9, fontsize=8)
    return fig, ax


def _downstream_path(data: InpData, start: str, end: str) -> list[str]:
    """Breadth-first downstream path of node names from ``start`` to ``end``."""
    graph: dict[str, list[str]] = defaultdict(list)
    for link in data.links.values():
        graph[link["from_node"]].append(link["to_node"])
    queue: list[list[str]] = [[start]]
    seen: set[str] = set()
    while queue:
        path = queue.pop(0)
        node = path[-1]
        if node == end:
            return path
        if node in seen:
            continue
        seen.add(node)
        queue.extend([*path, nxt] for nxt in graph[node] if nxt not in seen)
    msg = f"no downstream path from {start!r} to {end!r}"
    raise ValueError(msg)


def plot_profile(
    inp: str | Path | InpData,
    start_node: str,
    end_node: str,
    *,
    figsize: tuple[float, float] = (13, 5),
    ax: Axes | None = None,
) -> tuple[Figure, Axes]:
    """Draw the invert/crown/ground longitudinal profile ``start`` → ``end``.

    Follows the pipe network downstream from ``start_node`` to ``end_node`` and
    plots ground surface, conduit crown and invert against chainage.
    """
    data = inp if isinstance(inp, InpData) else read_inp(inp)
    plt = _require_mpl()
    path = _downstream_path(data, start_node, end_node)

    link_by_ends = {
        (link["from_node"], link["to_node"]): (name, link) for name, link in data.links.items()
    }
    station = 0.0
    stations, invert, crown, ground = [], [], [], []
    for i, name in enumerate(path):
        node = data.nodes[name]
        crown_elev = node["elevation"]
        if i > 0:
            found = link_by_ends.get((path[i - 1], name))
            if found is not None:
                lname, link = found
                station += link["length"]
                if lname in data.xsections:
                    crown_elev = node["elevation"] + data.xsections[lname]["height"]
        stations.append(station)
        invert.append(node["elevation"])
        crown.append(crown_elev)
        ground.append(node["elevation"] + node["depth_max"])

    fig, ax = _fig_ax(plt, ax, figsize)
    bottom = min(invert) - 0.1 * (max(ground) - min(invert) + 1.0)
    ax.fill_between(stations, ground, crown, color="#c49b98", alpha=0.5, label="soil")
    ax.fill_between(stations, invert, bottom, color="#c49b98", alpha=0.5)
    ax.fill_between(stations, crown, invert, color="#b0b0b0", alpha=0.6, label="conduit")
    ax.plot(stations, ground, color="#8b4513", lw=1.5, label="ground")
    ax.plot(stations, invert, "k-", lw=2, label="invert")
    for sta, name in zip(stations, path):
        ax.axvline(sta, color="gray", ls=":", alpha=0.5, lw=0.8)
        ax.text(sta, max(ground), name, rotation=90, va="bottom", ha="right", fontsize=8)
    ax.set_xlim(stations[0], stations[-1])
    ax.set_ylim(bottom=bottom)
    ax.set_xlabel("station (length units)")
    ax.set_ylabel("elevation (length units)")
    ax.set_title(f"profile: {start_node} → {end_node}", fontsize=12, fontweight="bold")
    ax.legend(loc="best", framealpha=0.9, fontsize=8)
    ax.grid(visible=True, alpha=0.3, ls="--")
    return fig, ax
