"""Post-build smoketest for a *packaged* ``swmmer`` wheel.

cibuildwheel runs this against every repaired wheel (see ``[tool.cibuildwheel]``
in ``pyproject.toml``), in a fresh venv with only the wheel and its single hard
dependency (numpy) installed. It proves the thing that a build can actually
botch: that the bundled EPA SWMM engine and its shared libraries were compiled,
vendored, and repaired correctly, so the engine runs and its binary ``.out`` is
readable. A failure here fails the wheels job, which blocks publish.

Deliberately numpy-only (no pytest, no pandas/xarray/matplotlib): the pure
Python paths are identical across wheels and covered by the editable test suite
in ``test.yml``; only the compiled engine varies per platform/arch, so that is
all this checks. Run it by hand with ``python tests/smoketest.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

# EPA SWMM Example 1 ships beside this script in tests/data; cibuildwheel runs
# {package}/tests/smoketest.py with {package} = the source checkout, so this
# sibling path always resolves.
_INP = Path(__file__).resolve().parent / "data" / "swmmer" / "test_example1.inp"


def main() -> int:
    """Run Example 1 through the installed wheel's engine and validate the results."""
    import tempfile

    import swmmer
    from swmmer import (
        NodeAttr,
        SWMMResults,
        SystemAttr,
        find_engine,
        find_output_lib,
        run_swmm,
    )

    # 1. the compiled artifacts resolve from the *installed* package (not src/).
    engine = find_engine()
    outlib = find_output_lib()
    print(f"swmmer {swmmer.__version__}")
    print(f"  engine: {engine}")
    print(f"  outlib: {outlib}")
    if not engine.is_file():
        raise SystemExit(f"bundled engine missing: {engine}")
    if not outlib.is_file():
        raise SystemExit(f"bundled output library missing: {outlib}")

    # 2. the engine actually runs a model and writes a readable .out. Run in a
    #    temp dir so the read-only fixture tree stays clean.
    with tempfile.TemporaryDirectory() as td:
        inp = Path(td) / "example1.inp"
        inp.write_text(_INP.read_text())
        rpt, out = run_swmm(inp)
        if not (rpt.is_file() and out.is_file()):
            raise SystemExit("run_swmm did not produce rpt/out files")

        # 3. libswmm-output loads the .out and returns real numbers. Assert the
        #    structure exactly (proves the parse) and the peak loosely (proves
        #    the engine computed, without pinning cross-version hydrology).
        with SWMMResults(out) as res:
            counts = (res.n_subcatch, res.n_node, res.n_link)
            if counts != (8, 14, 13):
                raise SystemExit(f"unexpected element counts {counts}, expected (8, 14, 13)")
            if res.n_periods != 36:
                raise SystemExit(f"unexpected n_periods {res.n_periods}, expected 36")
            runoff = res.system_series(SystemAttr.RUNOFF)
            peak = float(runoff.max())
            print(f"  peak system runoff: {peak:.2f} {res.flow_units}")
            if not (20.0 < peak < 28.0):  # EPA reference peak is ~24.25 CFS
                raise SystemExit(f"peak runoff {peak:.2f} outside sane range (20, 28)")
            depth = res.node_series(res.node_names[0], NodeAttr.DEPTH)
            if depth.shape != (36,):
                raise SystemExit(f"node depth series shape {depth.shape}, expected (36,)")

    print("SMOKETEST OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
