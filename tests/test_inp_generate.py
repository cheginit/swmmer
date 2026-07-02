"""Coverage + behavioral tests for SWMMInputGenerator.generate across all sections."""

from __future__ import annotations

from pathlib import Path

from swmmer import SWMMInputGenerator, SWMMOptions, plot


def _full_generator() -> SWMMInputGenerator:
    """A generator with every simple section populated (empty props use defaults)."""
    return SWMMInputGenerator(
        title="Coverage Model",
        options=SWMMOptions(start_date="01/01/2000", end_date="01/02/2000"),
        raingages={"RG1": {}},
        subcatchments={"S1": {}},
        subareas={"S1": {}},
        infiltration={"S1": {}},
        junctions={"J1": {}},
        outfalls={"O1": {}},
        storage={"ST1": {}},
        conduits={"C1": {}},
        pumps={"P1": {}},
        orifices={"OR1": {}},
        weirs={"W1": {}},
        outlets={"OL1": {}},
        xsections={"C1": {}},
        losses={"C1": {}},
        inflows={"J1": {}},
        dwf={"J1": {}},
        pollutants={"TSS": {}},
        landuses={"L1": {}},
        aquifers={"A1": {}},
        groundwater={"S1": {}},
        coverages={"S1": {"L1": 100.0}},
        loadings={"S1": {"TSS": 0.0}},
        buildup={"L1": {"TSS": {}}},
        washoff={"L1": {"TSS": {}}},
        coordinates={"J1": {"x": 0.0, "y": 0.0}, "O1": {"x": 100.0, "y": 0.0}},
        vertices={"C1": [(0.0, 0.0), (50.0, 10.0), (100.0, 0.0)]},
        polygons={"S1": [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]},
    )


def test_generate_writes_all_sections(tmp_path: Path):
    out = _full_generator().generate(tmp_path / "full.inp")
    text = out.read_text()
    for section in (
        "TITLE",
        "OPTIONS",
        "RAINGAGES",
        "SUBCATCHMENTS",
        "SUBAREAS",
        "INFILTRATION",
        "JUNCTIONS",
        "OUTFALLS",
        "STORAGE",
        "CONDUITS",
        "PUMPS",
        "ORIFICES",
        "WEIRS",
        "OUTLETS",
        "XSECTIONS",
        "LOSSES",
        "INFLOWS",
        "DWF",
        "POLLUTANTS",
        "LANDUSES",
        "COVERAGES",
        "LOADINGS",
        "BUILDUP",
        "WASHOFF",
        "AQUIFERS",
        "GROUNDWATER",
        "COORDINATES",
        "VERTICES",
        "Polygons",
    ):
        assert f"[{section}]" in text, f"missing [{section}]"
    assert "Coverage Model" in text


def test_generate_roundtrips_through_read_inp(tmp_path: Path):
    """A generated inp parses back to the same nodes/links via plot.read_inp."""
    out = _full_generator().generate(tmp_path / "rt.inp")
    data = plot.read_inp(out)
    assert set(data.nodes) == {"J1", "O1"}
    assert "C1" in data.links
    assert data.nodes["O1"]["node_type"] == "outfall"


def test_empty_generator_needs_dates(tmp_path: Path):
    """An OPTIONS section without dates is rejected (guards a common mistake)."""
    import pytest

    with pytest.raises(ValueError, match="dates"):
        SWMMInputGenerator().generate(tmp_path / "nodate.inp")


def test_generate_only_populates_nonempty_sections(tmp_path: Path):
    """Empty sections are skipped, so a minimal model has no CONDUITS block."""
    gen = SWMMInputGenerator(options=SWMMOptions(start_date="01/01/2000", end_date="01/01/2000"))
    text = gen.generate(tmp_path / "min.inp").read_text()
    assert "[JUNCTIONS]" not in text
    assert "[CONDUITS]" not in text
    assert "[OPTIONS]" in text
