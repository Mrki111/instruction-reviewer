from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("instruction-reviewer")
except PackageNotFoundError:  # editable install or source tree without metadata
    __version__ = "0+unknown"

