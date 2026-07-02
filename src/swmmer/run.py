"""Run a SWMM model and read its binary results without pyswmm / swmm-toolkit.

Runs an EPA SWMM ``.inp`` with the standalone ``runswmm`` engine (located on
``PATH``) and reads the binary ``.out`` directly from the EPA SWMM output C
library (``libswmm-output``) through :mod:`ctypes`.  No compiled Python bindings
are involved, so it sidesteps the swmm-toolkit wheel / macOS code-signing
problems entirely.

The attribute enums and the by-name element lookup mirror the official EPA
``epaswmm`` package's ``Output`` API (which wraps this same C library via
Cython); the only deliberate difference is that the series accessors return
NumPy arrays aligned to :attr:`SWMMResults.times` rather than
``{datetime: value}`` maps.

Examples
--------
Results are exposed per element *by name*, so downstream code can pull just the
series it needs for a specific node or link::

    from swmmer import SWMMResults, SystemAttr, run_swmm

    rpt, out = run_swmm("model.inp")
    with SWMMResults(out) as res:
        t = res.times_hours                              # report-step time axis
        q = res.system_series(SystemAttr.OUTFALL_FLOW)   # total outfall discharge

"""

from __future__ import annotations

__lazy_modules__ = [
    "collections",
    "collections.abc",
    "ctypes",
    "functools",
    "importlib",
    "numpy",
    "pathlib",
    "shutil",
    "subprocess",
    "swmmer._paths",
]

import ctypes as ct
import importlib
import shutil
import subprocess
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from enum import IntEnum
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self, cast

import numpy as np

from swmmer._paths import prepare_output_file, resolve_input_file

if TYPE_CHECKING:
    from numpy.typing import NDArray

# SWMM stores dates as days since this epoch (the Delphi/Excel-1900 convention).
_SWMM_EPOCH = datetime(1899, 12, 30)  # noqa: DTZ001 - SWMM dates are naive/local


def _swmm_date_to_datetime(date_num: float) -> datetime:
    """Convert a SWMM date (days since :data:`_SWMM_EPOCH`) to a naive ``datetime``.

    The day fraction is rounded to whole seconds (SWMM's reporting resolution),
    avoiding the sub-second float drift a raw ``timedelta(days=date_num)`` incurs.
    """
    days = int(date_num)
    seconds = round((date_num - days) * 86400.0)
    return _SWMM_EPOCH + timedelta(days=days, seconds=seconds)


class NodeAttr(IntEnum):
    """Node result attributes (``SMO_nodeAttribute``)."""

    DEPTH = 0  # water depth above invert (ft or m)
    HEAD = 1  # hydraulic head / water-surface elevation (ft or m)
    VOLUME = 2  # stored + ponded volume (ft3 or m3) -- storage nodes
    LATERAL_INFLOW = 3  # runoff + external inflow (flow units)
    TOTAL_INFLOW = 4  # lateral + upstream inflow (flow units) -- node inflow
    FLOODING = 5  # surface flooding / overflow (flow units)


class LinkAttr(IntEnum):
    """Link result attributes (``SMO_linkAttribute``)."""

    FLOW = 0  # flow rate (flow units) -- e.g. conduit / outlet discharge
    DEPTH = 1  # flow depth (ft or m)
    VELOCITY = 2  # flow velocity (ft/s or m/s)
    VOLUME = 3  # stored volume (ft3 or m3)
    CAPACITY = 4  # fraction of conduit filled (-)


class SubcatchAttr(IntEnum):
    """Subcatchment result attributes (``SMO_subcatchAttribute``)."""

    RAINFALL = 0
    SNOW_DEPTH = 1
    EVAP_LOSS = 2
    INFIL_LOSS = 3
    RUNOFF = 4  # runoff flow (flow units)
    GW_OUTFLOW = 5
    GW_ELEV = 6
    SOIL_MOISTURE = 7


class SystemAttr(IntEnum):
    """System-wide result attributes (``SMO_systemAttribute``)."""

    AIR_TEMP = 0
    RAINFALL = 1
    SNOW_DEPTH = 2
    EVAP_INFIL_LOSS = 3
    RUNOFF = 4
    DRY_WEATHER_INFLOW = 5
    GROUNDWATER_INFLOW = 6
    RDII_INFLOW = 7
    DIRECT_INFLOW = 8
    TOTAL_LATERAL_INFLOW = 9
    FLOODING = 10  # total flooding across all nodes (flow units)
    OUTFALL_FLOW = 11  # total flow leaving via outfalls (flow units)
    STORAGE = 12  # total stored volume (ft3 or m3)
    EVAP_RATE = 13


# SMO_elementType
_SUBCATCH, _NODE, _LINK = 0, 1, 2
# SMO_time
_REPORT_STEP, _NUM_PERIODS = 0, 1

_FLOW_UNITS = ("CFS", "GPM", "MGD", "CMS", "LPS", "MLD")  # SMO_flowUnits order

# element_type -> (attribute enum, names property, snapshot C function).  System
# is handled separately (it has attributes but no named elements).
_ELEMENT_INFO: dict[str, tuple[type[IntEnum], str, str]] = {
    "subcatchment": (SubcatchAttr, "subcatchment_names", "SMO_getSubcatchAttribute"),
    "node": (NodeAttr, "node_names", "SMO_getNodeAttribute"),
    "link": (LinkAttr, "link_names", "SMO_getLinkAttribute"),
}


def _require(module: str) -> Any:
    """Import an optional dependency, with an install hint if it is missing."""
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        msg = f"this requires the optional dependency {module!r} (pip install swmmer[{module}])"
        raise ImportError(msg) from exc


def _bundled_engine() -> Path | None:
    """Locate the ``runswmm`` bundled in the installed package, if present.

    Handles both layouts: a regular wheel install (``_engine`` sits next to
    this module) and a scikit-build-core *editable* install (the engine is
    placed in site-packages while ``__file__`` redirects to the source tree, so
    it is resolved from the installed package RECORD instead).

    The ``_engine`` path filter is essential: the package also installs a
    ``runswmm`` console-script launcher, and matching it here would make the
    launcher resolve (and exec) itself — infinite recursion.
    """
    here = Path(__file__).resolve().parent / "_engine" / "bin"
    for name in ("runswmm", "runswmm.exe"):
        candidate = here / name
        if candidate.is_file():
            return candidate
    from importlib.metadata import PackageNotFoundError, files

    try:
        record = files("swmmer")
    except PackageNotFoundError:
        return None
    for entry in record or []:
        if entry.name in ("runswmm", "runswmm.exe") and "_engine" in entry.parts:
            located = Path(entry.locate())
            if located.is_file():
                return located
    return None


def find_engine() -> Path:
    """Locate the ``runswmm`` executable.

    Searches the engine bundled inside the installed package
    (``swmmer/_engine/bin``, built into the wheel by scikit-build-core) first,
    then falls back to ``PATH`` (e.g. a conda-provided engine).

    Returns
    -------
    Path
        Path to the ``runswmm`` executable.

    Raises
    ------
    FileNotFoundError
        If ``runswmm`` is neither bundled in the package nor on ``PATH``.

    """
    bundled = _bundled_engine()
    if bundled is not None:
        return bundled
    exe = shutil.which("runswmm")
    if exe is None:
        msg = "runswmm not found in the swmmer package (swmmer/_engine/bin) or on PATH"
        raise FileNotFoundError(msg)
    return Path(exe)


def _runswmm_cli() -> None:  # pyright: ignore[reportUnusedFunction]  # console-script entry point
    """Console-script shim: forward CLI args to the bundled ``runswmm`` engine.

    Installed as the ``runswmm`` entry point so the bundled engine — which lives
    inside the package and is otherwise not on ``PATH`` — can be invoked directly
    from the shell, e.g. ``runswmm model.inp model.rpt model.out``.
    """
    import sys

    proc = subprocess.run([str(find_engine()), *sys.argv[1:]], check=False)  # noqa: S603
    sys.exit(proc.returncode)


def find_output_lib(engine: Path | None = None) -> Path:
    """Locate the ``libswmm-output`` shared library.

    Parameters
    ----------
    engine : Path, optional
        Path to the ``runswmm`` executable; auto-located via :func:`find_engine`
        when omitted.  The library is looked up in ``../lib`` (Unix) and next to
        the engine (Windows) relative to it.

    Returns
    -------
    Path
        Path to the platform ``libswmm-output`` shared library.

    Raises
    ------
    FileNotFoundError
        If no matching library is found alongside the engine.

    """
    engine = engine or find_engine()
    # Unix installs the shared library under ../lib; Windows puts the DLL in the
    # runtime dir (bin/) next to runswmm.exe, so search both.
    search = (engine.parent.parent / "lib", engine.parent)
    for directory in search:
        for pattern in ("libswmm-output.dylib", "libswmm-output.so", "swmm-output.dll"):
            hits = list(directory.glob(pattern))
            if hits:
                return hits[0]
    locations = " or ".join(str(d) for d in search)
    msg = f"libswmm-output not found under {locations}"
    raise FileNotFoundError(msg)


def run_swmm(
    inp: str | Path,
    rpt: str | Path | None = None,
    out: str | Path | None = None,
    *,
    engine: str | Path | None = None,
    quiet: bool = True,
) -> tuple[Path, Path]:
    """Run a SWMM input file with the ``runswmm`` engine.

    Parameters
    ----------
    inp : str or Path
        Path to the SWMM input (``.inp``) file.
    rpt : str or Path, optional
        Report-file path; defaults to ``inp`` with a ``.rpt`` suffix.
    out : str or Path, optional
        Binary-output path; defaults to ``inp`` with a ``.out`` suffix.
    engine : str or Path, optional
        Path to the ``runswmm`` executable; auto-located via :func:`find_engine`
        when omitted.
    quiet : bool, default True
        Capture the engine's stdout/stderr instead of streaming it.

    Returns
    -------
    tuple of Path
        The ``(report, output)`` file paths.

    Raises
    ------
    FileNotFoundError
        If ``inp`` (or an explicit ``engine``) does not exist.
    IsADirectoryError
        If ``inp`` is a directory.
    RuntimeError
        If the engine exits with a non-zero return code.

    """
    inp = resolve_input_file(inp, what="SWMM input file")
    rpt = prepare_output_file(rpt or inp.with_suffix(".rpt"), what="SWMM report file")
    out = prepare_output_file(out or inp.with_suffix(".out"), what="SWMM output file")
    engine = resolve_input_file(engine, what="runswmm engine") if engine else find_engine()
    proc = subprocess.run(  # noqa: S603 - engine path is resolved, args are file paths
        [str(engine), str(inp), str(rpt), str(out)],
        check=False,
        capture_output=quiet,
        text=True,
    )
    if proc.returncode != 0:
        tail = (proc.stdout or "")[-2000:] if quiet else ""
        msg = f"runswmm failed (rc={proc.returncode}) on {inp}\n{tail}"
        raise RuntimeError(msg)
    return rpt, out


class SWMMResults:
    """Reader for a SWMM binary ``.out`` file via ``libswmm-output`` (ctypes).

    Open it as a context manager so the native file handle is always released.

    Parameters
    ----------
    out_path : str or Path
        Path to the SWMM binary output (``.out``) file.
    output_lib : str or Path, optional
        Path to the ``libswmm-output`` shared library; auto-located via
        :func:`find_output_lib` when omitted.

    Notes
    -----
    All result arrays are SWMM's native 32-bit float (``REAL4``); dates are its
    native 64-bit (``REAL8``).  Reads are lazy and stream from disk: no method
    loads the whole file, so an arbitrarily large ``.out`` reads in a few MB of
    RAM (one element series, or one period snapshot, at a time).  Two scale tips:
    prefer ``times_hours`` / ``times64`` over ``times`` for long runs (``times``
    builds one Python ``datetime`` per period); and for bulk extraction across
    many elements, iterate periods with the ``*_attribute`` snapshots (contiguous
    reads) rather than calling ``*_series`` per element (one strided pass over the
    file each).  Materializing the full ``elements x periods`` matrix can exhaust
    memory -- that is on the caller, not the reader.

    Attributes
    ----------
    n_subcatch, n_node, n_link : int
        Element counts in the output file.
    flow_units : str
        Flow-unit label, one of ``CFS``/``GPM``/``MGD``/``CMS``/``LPS``/``MLD``.
    report_step : int
        Seconds between reporting periods.
    n_periods : int
        Number of reporting periods.
    start_date : datetime
        Simulation start time.

    Examples
    --------
    >>> with SWMMResults(out_path) as res:
    ...     series = res.node_series("J1", NodeAttr.TOTAL_INFLOW)

    """

    # -- internals -------------------------------------------------------
    def _bind(self) -> None:
        """Declare ``argtypes`` for the ``libswmm-output`` functions used here."""
        lib = self._lib
        p_int, p_float, p_char, p_double = (
            ct.POINTER(ct.c_int),
            ct.POINTER(ct.c_float),
            ct.POINTER(ct.c_char_p),
            ct.POINTER(ct.c_double),
        )
        lib.SMO_init.argtypes = [ct.POINTER(ct.c_void_p)]
        lib.SMO_open.argtypes = [ct.c_void_p, ct.c_char_p]
        lib.SMO_close.argtypes = [ct.c_void_p]
        lib.SMO_getProjectSize.argtypes = [ct.c_void_p, ct.POINTER(p_int), p_int]
        lib.SMO_getUnits.argtypes = [ct.c_void_p, ct.POINTER(p_int), p_int]
        lib.SMO_getTimes.argtypes = [ct.c_void_p, ct.c_int, p_int]
        lib.SMO_getStartDate.argtypes = [ct.c_void_p, ct.POINTER(ct.c_double)]
        lib.SMO_getElementName.argtypes = [ct.c_void_p, ct.c_int, ct.c_int, p_char, p_int]
        # (handle, elementIndex, attr, startPeriod, endPeriod, float**, int*)
        series_sig = [
            ct.c_void_p,
            ct.c_int,
            ct.c_int,
            ct.c_int,
            ct.c_int,
            ct.POINTER(p_float),
            p_int,
        ]
        lib.SMO_getNodeSeries.argtypes = series_sig
        lib.SMO_getLinkSeries.argtypes = series_sig
        lib.SMO_getSubcatchSeries.argtypes = series_sig
        lib.SMO_getSystemSeries.argtypes = [
            ct.c_void_p,
            ct.c_int,
            ct.c_int,
            ct.c_int,
            ct.POINTER(p_float),
            p_int,
        ]
        lib.SMO_getVersion.argtypes = [ct.c_void_p, p_int]
        lib.SMO_getDateSeries.argtypes = [
            ct.c_void_p,
            ct.c_int,
            ct.c_int,
            ct.POINTER(p_double),
            p_int,
        ]
        # (handle, periodIndex, attr-or-elementIndex, float**, int*) -- the
        # attribute (one attr, all elements) and result (one element, all attrs)
        # calls share this signature.
        snapshot_sig = [ct.c_void_p, ct.c_int, ct.c_int, ct.POINTER(p_float), p_int]
        lib.SMO_getSubcatchAttribute.argtypes = snapshot_sig
        lib.SMO_getNodeAttribute.argtypes = snapshot_sig
        lib.SMO_getLinkAttribute.argtypes = snapshot_sig
        lib.SMO_getSubcatchResult.argtypes = snapshot_sig
        lib.SMO_getNodeResult.argtypes = snapshot_sig
        lib.SMO_getLinkResult.argtypes = snapshot_sig
        lib.SMO_getSystemResult.argtypes = snapshot_sig
        lib.SMO_freeMemory.argtypes = [ct.c_void_p]

    def _check(self, rc: int, what: str) -> None:
        """Raise ``RuntimeError`` when a ``libswmm-output`` call returns non-zero."""
        if rc != 0:
            msg = f"{what} failed (rc={rc}) for {self._path}"
            raise RuntimeError(msg)

    def _int_array(self, fn: Callable[..., int]) -> list[int]:
        """Call a function returning an ``int*``/length pair and copy it to a list."""
        arr, length = ct.POINTER(ct.c_int)(), ct.c_int()
        self._check(
            fn(self._handle, ct.byref(arr), ct.byref(length)), getattr(fn, "__name__", "fn")
        )
        vals = [int(arr[i]) for i in range(length.value)]
        self._lib.SMO_freeMemory(arr)
        return vals

    def __init__(self, out_path: str | Path, output_lib: str | Path | None = None) -> None:
        self._path = resolve_input_file(out_path, what="SWMM output (.out) file")
        lib_path = (
            resolve_input_file(output_lib, what="libswmm-output library")
            if output_lib
            else find_output_lib()
        )
        self._lib = ct.CDLL(str(lib_path))
        self._bind()
        self._handle = ct.c_void_p()
        self._check(self._lib.SMO_init(ct.byref(self._handle)), "SMO_init")
        self._check(self._lib.SMO_open(self._handle, str(self._path).encode()), "SMO_open")
        sizes = self._int_array(self._lib.SMO_getProjectSize)
        self.n_subcatch, self.n_node, self.n_link = sizes[0], sizes[1], sizes[2]
        units = self._int_array(self._lib.SMO_getUnits)
        self.flow_units = _FLOW_UNITS[units[1]] if len(units) > 1 else "?"
        step = ct.c_int()
        self._lib.SMO_getTimes(self._handle, _REPORT_STEP, ct.byref(step))
        self.report_step = step.value  # seconds between reporting periods
        nper = ct.c_int()
        self._lib.SMO_getTimes(self._handle, _NUM_PERIODS, ct.byref(nper))
        self.n_periods = nper.value
        date = ct.c_double()
        self._lib.SMO_getStartDate(self._handle, ct.byref(date))
        self._start_date_num = date.value  # SWMM date (days since epoch) of the run start
        self.start_date = _swmm_date_to_datetime(date.value)
        self._names: dict[int, dict[str, int]] = {}
        self._dates: NDArray[np.float64] | None = None

    def close(self) -> None:
        """Close the native output-file handle (idempotent)."""
        if getattr(self, "_handle", None) is not None:
            self._lib.SMO_close(self._handle)
            self._handle = None

    @property
    def version(self) -> int:
        """The SWMM engine version that wrote the output file."""
        ver = ct.c_int()
        self._check(self._lib.SMO_getVersion(self._handle, ct.byref(ver)), "SMO_getVersion")
        return ver.value

    def _check_period(self, period: int) -> None:
        """Raise ``IndexError`` if ``period`` is not a valid reporting-period index."""
        if not 0 <= period < self.n_periods:
            msg = f"period {period} out of range [0, {self.n_periods - 1}]"
            raise IndexError(msg)

    def _date_nums(self) -> NDArray[np.float64]:
        """Return the per-period SWMM dates (days since epoch) read from the file (cached)."""
        if self._dates is None:
            arr, length = ct.POINTER(ct.c_double)(), ct.c_int()
            self._check(
                self._lib.SMO_getDateSeries(
                    self._handle, 0, self.n_periods - 1, ct.byref(arr), ct.byref(length)
                ),
                "SMO_getDateSeries",
            )
            self._dates = np.ctypeslib.as_array(arr, shape=(length.value,)).astype(np.float64)
            self._lib.SMO_freeMemory(arr)
        return self._dates

    def _to_array(self, arr: object, length: int) -> NDArray[np.float32]:
        """Copy a native ``float*``/length buffer into an owned ``float32`` array.

        SWMM stores results as 32-bit floats (``REAL4``), so this preserves the
        native dtype -- no widening to ``float64``.
        """
        vals = np.ctypeslib.as_array(arr, shape=(length,)).astype(np.float32)
        self._lib.SMO_freeMemory(arr)
        return vals

    def _value_array(
        self, fn: Callable[..., int], a: int, b: int, what: str
    ) -> NDArray[np.float32]:
        """Read a ``float*``/length result from a ``(handle, a, b, **out, *n)`` call."""
        arr, length = ct.POINTER(ct.c_float)(), ct.c_int()
        self._check(fn(self._handle, a, b, ct.byref(arr), ct.byref(length)), what)
        return self._to_array(arr, length.value)

    # -- time axis (cached; one native date read shared by all three) ----
    @cached_property
    def times_hours(self) -> NDArray[np.float64]:
        """Reporting times as hours from the simulation start (length ``n_periods``).

        Read from the file's stored per-period dates (``SMO_getDateSeries``), so it
        reflects the actual reporting grid: SWMM interpolates its variable routing
        step onto the fixed reporting step, so the grid is uniform, but this reads
        it rather than assuming it.  Cheap and cached -- prefer this (or
        :attr:`times64`) over :attr:`times` for long simulations.
        """
        return (self._date_nums() - self._start_date_num) * 24.0

    @cached_property
    def times64(self) -> NDArray[np.datetime64]:
        """Reporting timestamps as a ``datetime64[s]`` array (length ``n_periods``).

        The low-memory, vectorized alternative to :attr:`times`: ~8 bytes per
        period instead of a Python :class:`~datetime.datetime` object each.
        """
        seconds = np.rint(self._date_nums() * 86400.0).astype("int64")
        return np.datetime64("1899-12-30", "s") + seconds.astype("timedelta64[s]")

    @cached_property
    def times(self) -> list[datetime]:
        """Reporting timestamps as :class:`~datetime.datetime` objects.

        Read from the file's stored per-period dates (authoritative); the first
        period is one ``report_step`` after the start.  This materializes one
        Python object per period -- for long runs prefer :attr:`times_hours` or
        :attr:`times64`.

        Returns
        -------
        list of datetime
            One timestamp per reporting period.

        """
        return [_swmm_date_to_datetime(d) for d in self._date_nums().tolist()]

    def _name_index(self, element_type: int, count: int) -> dict[str, int]:
        """Build (and cache) the ``{element name: index}`` map for an element type."""
        if element_type not in self._names:
            mapping: dict[str, int] = {}
            for i in range(count):
                name_p, size = ct.c_char_p(), ct.c_int()
                self._check(
                    self._lib.SMO_getElementName(
                        self._handle, element_type, i, ct.byref(name_p), ct.byref(size)
                    ),
                    "SMO_getElementName",
                )
                mapping[cast("bytes", name_p.value).decode()] = i
                self._lib.SMO_freeMemory(ct.cast(name_p, ct.c_void_p))
            self._names[element_type] = mapping
        return self._names[element_type]

    # -- element names (cached; built once from the name index) ----------
    @cached_property
    def node_names(self) -> list[str]:
        """Node names in the output file, in element order."""
        return list(self._name_index(_NODE, self.n_node))

    @cached_property
    def link_names(self) -> list[str]:
        """Link names in the output file, in element order."""
        return list(self._name_index(_LINK, self.n_link))

    @cached_property
    def subcatchment_names(self) -> list[str]:
        """Subcatchment names in the output file, in element order."""
        return list(self._name_index(_SUBCATCH, self.n_subcatch))

    def _series(
        self, fn: Callable[..., int], element_type: int, name: str, attr: int
    ) -> NDArray[np.float32]:
        """Read an element's attribute series by name via one of the ``*Series`` calls."""
        index = self._name_index(
            element_type, getattr(self, ("n_subcatch", "n_node", "n_link")[element_type])
        )
        if name not in index:
            kind = ("subcatchment", "node", "link")[element_type]
            msg = f"{kind} {name!r} not found in {self._path.name}"
            raise KeyError(msg)
        arr, length = ct.POINTER(ct.c_float)(), ct.c_int()
        self._check(
            fn(
                self._handle,
                index[name],
                attr,
                0,
                self.n_periods - 1,
                ct.byref(arr),
                ct.byref(length),
            ),
            getattr(fn, "__name__", "series"),
        )
        return self._to_array(arr, length.value)

    # -- series by name --------------------------------------------------
    def node_series(self, name: str, attr: NodeAttr) -> NDArray[np.float32]:
        """Return the time series of an attribute for a node.

        Parameters
        ----------
        name : str
            Node name as written in the ``.inp``.
        attr : NodeAttr
            Result attribute to read.

        Returns
        -------
        NDArray[np.float32]
            Series of length ``n_periods``.

        Raises
        ------
        KeyError
            If ``name`` is not a node in the output file.

        """
        return self._series(self._lib.SMO_getNodeSeries, _NODE, name, int(attr))

    def link_series(self, name: str, attr: LinkAttr) -> NDArray[np.float32]:
        """Return the time series of an attribute for a link.

        Parameters
        ----------
        name : str
            Link name as written in the ``.inp``.
        attr : LinkAttr
            Result attribute to read.

        Returns
        -------
        NDArray[np.float32]
            Series of length ``n_periods``.

        Raises
        ------
        KeyError
            If ``name`` is not a link in the output file.

        """
        return self._series(self._lib.SMO_getLinkSeries, _LINK, name, int(attr))

    def subcatchment_series(self, name: str, attr: SubcatchAttr) -> NDArray[np.float32]:
        """Return the time series of an attribute for a subcatchment.

        Parameters
        ----------
        name : str
            Subcatchment name as written in the ``.inp``.
        attr : SubcatchAttr
            Result attribute to read.

        Returns
        -------
        NDArray[np.float32]
            Series of length ``n_periods``.

        Raises
        ------
        KeyError
            If ``name`` is not a subcatchment in the output file.

        """
        return self._series(self._lib.SMO_getSubcatchSeries, _SUBCATCH, name, int(attr))

    def system_series(self, attr: SystemAttr) -> NDArray[np.float32]:
        """Return a system-wide attribute time series (e.g. total outfall flow).

        Parameters
        ----------
        attr : SystemAttr
            System-wide result attribute to read.

        Returns
        -------
        NDArray[np.float32]
            Series of length ``n_periods``.

        """
        arr, length = ct.POINTER(ct.c_float)(), ct.c_int()
        self._check(
            self._lib.SMO_getSystemSeries(
                self._handle, int(attr), 0, self.n_periods - 1, ct.byref(arr), ct.byref(length)
            ),
            "SMO_getSystemSeries",
        )
        return self._to_array(arr, length.value)

    # -- snapshots: one attribute, one period, all elements -------------
    # Returned arrays are aligned to the matching ``*_names`` ordering, so
    # ``dict(zip(res.node_names, res.node_attribute(attr, p)))`` maps name->value.
    def subcatchment_attribute(self, attr: SubcatchAttr, period: int) -> NDArray[np.float32]:
        """Every subcatchment's value of ``attr`` at reporting ``period``.

        Parameters
        ----------
        attr : SubcatchAttr
            Result attribute to read.
        period : int
            Reporting-period index in ``[0, n_periods - 1]``.

        Returns
        -------
        NDArray[np.float32]
            One value per subcatchment, aligned to :attr:`subcatchment_names`.

        """
        self._check_period(period)
        return self._value_array(
            self._lib.SMO_getSubcatchAttribute, period, int(attr), "SMO_getSubcatchAttribute"
        )

    def node_attribute(self, attr: NodeAttr, period: int) -> NDArray[np.float32]:
        """Every node's value of ``attr`` at reporting ``period`` (aligned to :attr:`node_names`).

        Parameters
        ----------
        attr : NodeAttr
            Result attribute to read.
        period : int
            Reporting-period index in ``[0, n_periods - 1]``.

        Returns
        -------
        NDArray[np.float32]
            One value per node, aligned to :attr:`node_names`.

        """
        self._check_period(period)
        return self._value_array(
            self._lib.SMO_getNodeAttribute, period, int(attr), "SMO_getNodeAttribute"
        )

    def link_attribute(self, attr: LinkAttr, period: int) -> NDArray[np.float32]:
        """Every link's value of ``attr`` at reporting ``period`` (aligned to :attr:`link_names`).

        Parameters
        ----------
        attr : LinkAttr
            Result attribute to read.
        period : int
            Reporting-period index in ``[0, n_periods - 1]``.

        Returns
        -------
        NDArray[np.float32]
            One value per link, aligned to :attr:`link_names`.

        """
        self._check_period(period)
        return self._value_array(
            self._lib.SMO_getLinkAttribute, period, int(attr), "SMO_getLinkAttribute"
        )

    # -- results: all attributes for one element at one period ----------
    def _element_result(
        self,
        fn: Callable[..., int],
        element_type: int,
        count: int,
        name: str,
        period: int,
        what: str,
    ) -> NDArray[np.float32]:
        """Resolve ``name`` to its index and read all of its attributes at ``period``."""
        self._check_period(period)
        index = self._name_index(element_type, count)
        if name not in index:
            kind = ("subcatchment", "node", "link")[element_type]
            msg = f"{kind} {name!r} not found in {self._path.name}"
            raise KeyError(msg)
        return self._value_array(fn, period, index[name], what)

    def subcatchment_result(self, name: str, period: int) -> NDArray[np.float32]:
        """All attributes for one subcatchment at ``period`` (in :class:`SubcatchAttr` order).

        Parameters
        ----------
        name : str
            Subcatchment name.
        period : int
            Reporting-period index in ``[0, n_periods - 1]``.

        Returns
        -------
        NDArray[np.float32]
            One value per attribute (any pollutant concentrations follow the
            built-in attributes).

        """
        return self._element_result(
            self._lib.SMO_getSubcatchResult,
            _SUBCATCH,
            self.n_subcatch,
            name,
            period,
            "SMO_getSubcatchResult",
        )

    def node_result(self, name: str, period: int) -> NDArray[np.float32]:
        """All attributes for one node at ``period`` (in :class:`NodeAttr` order).

        Parameters
        ----------
        name : str
            Node name.
        period : int
            Reporting-period index in ``[0, n_periods - 1]``.

        Returns
        -------
        NDArray[np.float32]
            One value per attribute (any pollutant concentrations follow the
            built-in attributes).

        """
        return self._element_result(
            self._lib.SMO_getNodeResult, _NODE, self.n_node, name, period, "SMO_getNodeResult"
        )

    def link_result(self, name: str, period: int) -> NDArray[np.float32]:
        """All attributes for one link at ``period`` (in :class:`LinkAttr` order).

        Parameters
        ----------
        name : str
            Link name.
        period : int
            Reporting-period index in ``[0, n_periods - 1]``.

        Returns
        -------
        NDArray[np.float32]
            One value per attribute (any pollutant concentrations follow the
            built-in attributes).

        """
        return self._element_result(
            self._lib.SMO_getLinkResult, _LINK, self.n_link, name, period, "SMO_getLinkResult"
        )

    def system_result(self, period: int) -> NDArray[np.float32]:
        """All system-wide attributes at ``period`` (in :class:`SystemAttr` order).

        Parameters
        ----------
        period : int
            Reporting-period index in ``[0, n_periods - 1]``.

        Returns
        -------
        NDArray[np.float32]
            One value per system attribute.

        """
        self._check_period(period)
        return self._value_array(self._lib.SMO_getSystemResult, period, 0, "SMO_getSystemResult")

    # -- convenience accessors ------------------------------------------
    def node_volume(self, node: str) -> NDArray[np.float32]:
        """Stored-volume series for a storage node.

        Parameters
        ----------
        node : str
            Storage-node name.

        Returns
        -------
        NDArray[np.float32]
            Stored volume per reporting period (flow-unit volume units).

        """
        return self.node_series(node, NodeAttr.VOLUME)

    def node_inflow(self, node: str) -> NDArray[np.float32]:
        """Total inflow (lateral + upstream) to a node.

        Parameters
        ----------
        node : str
            Node name.

        Returns
        -------
        NDArray[np.float32]
            Total inflow per reporting period (flow units).

        """
        return self.node_series(node, NodeAttr.TOTAL_INFLOW)

    def node_flooding(self, node: str) -> NDArray[np.float32]:
        """Surface flooding / overflow rate at a node.

        Parameters
        ----------
        node : str
            Node name.

        Returns
        -------
        NDArray[np.float32]
            Flooding rate per reporting period (flow units).

        """
        return self.node_series(node, NodeAttr.FLOODING)

    def link_flow(self, link: str) -> NDArray[np.float32]:
        """Flow rate through a link, e.g. a conduit, orifice, or weir.

        Parameters
        ----------
        link : str
            Link name.

        Returns
        -------
        NDArray[np.float32]
            Flow rate per reporting period (flow units).

        """
        return self.link_series(link, LinkAttr.FLOW)

    # -- export to pandas / xarray (optional dependencies) --------------
    def _panel(self, element_type: str, attr: int) -> tuple[list[str], NDArray[np.float32]]:
        """Build a ``(n_periods, n_elements)`` panel for one attribute via period snapshots.

        Reads one contiguous period record per call (good I/O locality) and fills
        a preallocated ``float32`` array -- the efficient axis for a full panel.
        """
        _, names_attr, fn_name = _ELEMENT_INFO[element_type]
        names: list[str] = getattr(self, names_attr)
        fn = getattr(self._lib, fn_name)
        out = np.empty((self.n_periods, len(names)), dtype=np.float32)
        for period in range(self.n_periods):
            out[period] = self._value_array(fn, period, attr, fn_name)
        return names, out

    def to_pandas(
        self,
        attr: NodeAttr | LinkAttr | SubcatchAttr | SystemAttr,
        element_type: str = "node",
    ) -> Any:
        """Tabulate one attribute as a pandas :class:`~pandas.DataFrame`.

        Requires the optional ``pandas`` dependency.  Materializes the full
        ``n_periods x n_elements`` panel in memory.

        Parameters
        ----------
        attr : NodeAttr | LinkAttr | SubcatchAttr | SystemAttr
            Result attribute to read (must match ``element_type``).
        element_type : {"node", "link", "subcatchment", "system"}, default "node"
            Element class to tabulate.

        Returns
        -------
        pandas.DataFrame
            Index is :attr:`times64`; columns are the element names.  For
            ``"system"`` (no elements) the result is a single-column frame.

        """
        if element_type != "system" and element_type not in _ELEMENT_INFO:
            msg = f"element_type must be one of {(*_ELEMENT_INFO, 'system')}, got {element_type!r}"
            raise ValueError(msg)
        pd = _require("pandas")
        if element_type == "system":
            series = self.system_series(cast("SystemAttr", attr))
            return pd.DataFrame({attr.name: series}, index=self.times64)
        names, panel = self._panel(element_type, int(attr))
        return pd.DataFrame(panel, index=self.times64, columns=names)

    def to_xarray(
        self,
        element_type: str = "node",
        attrs: Sequence[NodeAttr | LinkAttr | SubcatchAttr | SystemAttr] | None = None,
    ) -> Any:
        """Export an element class as an xarray :class:`~xarray.Dataset`.

        Requires the optional ``xarray`` dependency.  One data variable per
        attribute over dims ``(time, <element_type>)``; materializes one panel
        per attribute in memory.

        Parameters
        ----------
        element_type : {"node", "link", "subcatchment", "system"}, default "node"
            Element class to export.
        attrs : sequence of attribute enums, optional
            Attributes to include; defaults to every attribute of ``element_type``.

        Returns
        -------
        xarray.Dataset
            ``coords`` are ``time`` (:attr:`times64`) and the element names.

        """
        if element_type != "system" and element_type not in _ELEMENT_INFO:
            msg = f"element_type must be one of {(*_ELEMENT_INFO, 'system')}, got {element_type!r}"
            raise ValueError(msg)
        xr = _require("xarray")
        if element_type == "system":
            chosen = list(SystemAttr) if attrs is None else list(attrs)
            data_vars = {
                a.name: ("time", self.system_series(cast("SystemAttr", a))) for a in chosen
            }
            return xr.Dataset(data_vars, coords={"time": self.times64})
        enum = _ELEMENT_INFO[element_type][0]
        chosen = list(enum) if attrs is None else list(attrs)
        names: list[str] = []
        data_vars = {}
        for a in chosen:
            names, panel = self._panel(element_type, int(a))
            data_vars[a.name] = (["time", element_type], panel)
        return xr.Dataset(data_vars, coords={"time": self.times64, element_type: names})

    # -- context manager -------------------------------------------------
    def __enter__(self) -> Self:
        """Enter the context manager, returning the open handle."""
        return self

    def __exit__(self, *_exc: object) -> None:
        """Exit the context manager, closing the underlying SWMM handle."""
        self.close()
