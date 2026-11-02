"""rendermill: render narrated slide videos from HTML + a slide list.

Version is read from installed package metadata so it can never drift from
pyproject.toml (it did: pyproject said 0.8.0 while this hardcoded 0.7.0).
Falls back to the pyproject value when running from a source tree.
"""
from __future__ import annotations

from pathlib import Path


def _version_from_pyproject() -> str:
    """Read `[project] version` straight out of pyproject.toml.

    Only used when the package is not installed (e.g. a bare source checkout
    with no dist-info). Deliberately avoids tomllib so it works on 3.10 too.
    """
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    try:
        in_project = False
        for line in pyproject.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("["):
                in_project = stripped == "[project]"
                continue
            if in_project and stripped.startswith("version"):
                _, _, val = stripped.partition("=")
                return val.strip().strip('"').strip("'")
    except OSError:
        pass
    return "0.0.0"


try:  # installed (normal case)
    from importlib.metadata import PackageNotFoundError, version as _pkg_version

    try:
        __version__ = _pkg_version("rendermill")
    except PackageNotFoundError:
        __version__ = _version_from_pyproject()
except ImportError:  # pragma: no cover - very old interpreters
    __version__ = _version_from_pyproject()

__all__ = ["__version__"]