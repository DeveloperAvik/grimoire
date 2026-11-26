"""grimoire: catalog and launcher for security tools installed on the host."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("grimoire")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0+unknown"
