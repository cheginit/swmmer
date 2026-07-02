"""swmmer: a numpy-only toolkit for EPA SWMM file and engine I/O.

Write a SWMM ``.inp`` (:class:`SWMMInputGenerator` + element defaults), build an
NRCS design-storm rain ``.dat`` (:func:`build_nrcs_hyetograph`,
:func:`write_rain_dat`), run the ``runswmm`` engine and read the binary ``.out``
(:func:`run_swmm`, :class:`SWMMResults`).  numpy is the only third-party
dependency; the engine runner additionally needs the native ``runswmm`` /
``libswmm-output`` artifacts on the system.
"""

from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from swmmer import plot, storms
    from swmmer.defaults import (
        AquiferDefaults,
        BuildupDefaults,
        ConduitDefaults,
        CoordinateDefaults,
        EvaporationOptions,
        GroundwaterDefaults,
        InfiltrationCurveNumberDefaults,
        InfiltrationGreenAmptDefaults,
        InfiltrationHortonDefaults,
        InflowDefaults,
        JunctionDefaults,
        LanduseDefaults,
        LossDefaults,
        OrificeDefaults,
        OutfallDefaults,
        OutletDefaults,
        PollutantDefaults,
        PumpDefaults,
        RaingageDefaults,
        ReportOptions,
        StorageDefaults,
        SubareaDefaults,
        SubcatchmentDefaults,
        SWMMOptions,
        WashoffDefaults,
        WeirDefaults,
        XSectionDefaults,
    )
    from swmmer.inp import SWMMInputGenerator
    from swmmer.run import (
        LinkAttr,
        NodeAttr,
        SubcatchAttr,
        SWMMResults,
        SystemAttr,
        find_engine,
        find_output_lib,
        run_swmm,
    )
    from swmmer.storms import Hyetograph, build_nrcs_hyetograph, duration_hours, write_rain_dat

try:
    __version__ = version("swmmer")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

# ---------------------------------------------------------------------------
# Lazy public API: heavy imports are deferred until first access.  The
# TYPE_CHECKING block above gives pyright/mypy full visibility without
# executing any imports at runtime.
# ---------------------------------------------------------------------------

_LAZY_IMPORTS: dict[str, tuple[str, str | None]] = {
    # Submodules (consumers do ``from swmmer import storms`` / ``plot``)
    "storms": ("swmmer.storms", None),
    "plot": ("swmmer.plot", None),
    # Element defaults + options
    "AquiferDefaults": ("swmmer.defaults", "AquiferDefaults"),
    "BuildupDefaults": ("swmmer.defaults", "BuildupDefaults"),
    "ConduitDefaults": ("swmmer.defaults", "ConduitDefaults"),
    "CoordinateDefaults": ("swmmer.defaults", "CoordinateDefaults"),
    "EvaporationOptions": ("swmmer.defaults", "EvaporationOptions"),
    "GroundwaterDefaults": ("swmmer.defaults", "GroundwaterDefaults"),
    "InfiltrationCurveNumberDefaults": ("swmmer.defaults", "InfiltrationCurveNumberDefaults"),
    "InfiltrationGreenAmptDefaults": ("swmmer.defaults", "InfiltrationGreenAmptDefaults"),
    "InfiltrationHortonDefaults": ("swmmer.defaults", "InfiltrationHortonDefaults"),
    "InflowDefaults": ("swmmer.defaults", "InflowDefaults"),
    "JunctionDefaults": ("swmmer.defaults", "JunctionDefaults"),
    "LanduseDefaults": ("swmmer.defaults", "LanduseDefaults"),
    "LossDefaults": ("swmmer.defaults", "LossDefaults"),
    "OrificeDefaults": ("swmmer.defaults", "OrificeDefaults"),
    "OutfallDefaults": ("swmmer.defaults", "OutfallDefaults"),
    "OutletDefaults": ("swmmer.defaults", "OutletDefaults"),
    "PollutantDefaults": ("swmmer.defaults", "PollutantDefaults"),
    "PumpDefaults": ("swmmer.defaults", "PumpDefaults"),
    "RaingageDefaults": ("swmmer.defaults", "RaingageDefaults"),
    "ReportOptions": ("swmmer.defaults", "ReportOptions"),
    "StorageDefaults": ("swmmer.defaults", "StorageDefaults"),
    "SubareaDefaults": ("swmmer.defaults", "SubareaDefaults"),
    "SubcatchmentDefaults": ("swmmer.defaults", "SubcatchmentDefaults"),
    "SWMMOptions": ("swmmer.defaults", "SWMMOptions"),
    "WashoffDefaults": ("swmmer.defaults", "WashoffDefaults"),
    "WeirDefaults": ("swmmer.defaults", "WeirDefaults"),
    "XSectionDefaults": ("swmmer.defaults", "XSectionDefaults"),
    # INP generation
    "SWMMInputGenerator": ("swmmer.inp", "SWMMInputGenerator"),
    # Engine runner + binary .out reader
    "LinkAttr": ("swmmer.run", "LinkAttr"),
    "NodeAttr": ("swmmer.run", "NodeAttr"),
    "SubcatchAttr": ("swmmer.run", "SubcatchAttr"),
    "SWMMResults": ("swmmer.run", "SWMMResults"),
    "SystemAttr": ("swmmer.run", "SystemAttr"),
    "find_engine": ("swmmer.run", "find_engine"),
    "find_output_lib": ("swmmer.run", "find_output_lib"),
    "run_swmm": ("swmmer.run", "run_swmm"),
    # Design-storm hyetographs
    "Hyetograph": ("swmmer.storms", "Hyetograph"),
    "build_nrcs_hyetograph": ("swmmer.storms", "build_nrcs_hyetograph"),
    "duration_hours": ("swmmer.storms", "duration_hours"),
    "write_rain_dat": ("swmmer.storms", "write_rain_dat"),
}


__all__ = [
    "AquiferDefaults",
    "BuildupDefaults",
    "ConduitDefaults",
    "CoordinateDefaults",
    "EvaporationOptions",
    "GroundwaterDefaults",
    "Hyetograph",
    "InfiltrationCurveNumberDefaults",
    "InfiltrationGreenAmptDefaults",
    "InfiltrationHortonDefaults",
    "InflowDefaults",
    "JunctionDefaults",
    "LanduseDefaults",
    "LinkAttr",
    "LossDefaults",
    "NodeAttr",
    "OrificeDefaults",
    "OutfallDefaults",
    "OutletDefaults",
    "PollutantDefaults",
    "PumpDefaults",
    "RaingageDefaults",
    "ReportOptions",
    "SWMMInputGenerator",
    "SWMMOptions",
    "SWMMResults",
    "StorageDefaults",
    "SubareaDefaults",
    "SubcatchAttr",
    "SubcatchmentDefaults",
    "SystemAttr",
    "WashoffDefaults",
    "WeirDefaults",
    "XSectionDefaults",
    "__version__",
    "build_nrcs_hyetograph",
    "duration_hours",
    "find_engine",
    "find_output_lib",
    "plot",
    "run_swmm",
    "storms",
    "write_rain_dat",
]


def __dir__() -> list[str]:
    return __all__


def __getattr__(name: str) -> object:
    if name in _LAZY_IMPORTS:
        module_path, attr = _LAZY_IMPORTS[name]
        import importlib

        mod = importlib.import_module(module_path)
        val = mod if attr is None else getattr(mod, attr)
        globals()[name] = val
        return val
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


# ---------------------------------------------------------------------------
# Eager-import override: set EAGER_IMPORT=1 (any non-"0"/non-empty value) to
# load all lazy members immediately.  Useful in CI and for profiling.
# ---------------------------------------------------------------------------
if os.environ.get("EAGER_IMPORT", "") not in ("", "0"):
    for _name in _LAZY_IMPORTS:
        __getattr__(_name)
