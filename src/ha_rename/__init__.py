"""Bulk-rename Home Assistant entities via the WebSocket API."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("ha-rename")
except PackageNotFoundError:  # pragma: no cover - only when run from an uninstalled tree
    __version__ = "0.0.0"
