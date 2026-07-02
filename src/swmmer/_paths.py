"""Internal helpers for validating user-supplied input/output paths.

These centralize the trust-boundary checks the public API performs on file
arguments so a clear, early error is raised instead of a cryptic failure deep
inside the engine, the C library, or a file write.
"""

from __future__ import annotations

__lazy_modules__ = ["pathlib"]

from pathlib import Path


def resolve_input_file(path: str | Path, *, what: str = "file") -> Path:
    """Return ``path`` as a :class:`~pathlib.Path`, requiring an existing file.

    Parameters
    ----------
    path : str or Path
        The path to validate.
    what : str, default "file"
        Human label used in the error message (e.g. ``"SWMM input file"``).

    Returns
    -------
    Path
        The validated path.

    Raises
    ------
    FileNotFoundError
        If ``path`` does not exist.
    IsADirectoryError
        If ``path`` is a directory rather than a file.

    """
    p = Path(path)
    if not p.exists():
        msg = f"{what} not found: {p}"
        raise FileNotFoundError(msg)
    if p.is_dir():
        msg = f"{what} is a directory, not a file: {p}"
        raise IsADirectoryError(msg)
    return p


def prepare_output_file(path: str | Path, *, what: str = "output file") -> Path:
    """Return ``path`` as a :class:`~pathlib.Path`, creating its parent directory.

    Parameters
    ----------
    path : str or Path
        The output path to prepare.
    what : str, default "output file"
        Human label used in error messages.

    Returns
    -------
    Path
        The validated path, with its parent directory created if needed.

    Raises
    ------
    IsADirectoryError
        If ``path`` itself is an existing directory.
    OSError
        If the parent directory cannot be created (e.g. a parent component is a
        file, or the location is not writable).

    """
    p = Path(path)
    if p.is_dir():
        msg = f"{what} path is a directory: {p}"
        raise IsADirectoryError(msg)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        msg = f"cannot create the output directory for {what} {p}: {exc}"
        raise OSError(msg) from exc
    return p
