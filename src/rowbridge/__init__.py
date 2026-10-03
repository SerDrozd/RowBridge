"""RowBridge package."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("rowbridge")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"
