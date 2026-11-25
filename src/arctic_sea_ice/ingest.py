"""Authenticated download of Copernicus Marine subsets.

Downloads need a free Copernicus Marine account. Credentials are stored once, outside the
repository, with ``uv run copernicusmarine login``; they are never passed through this
code or written to the project.

The client checks the credentials before the first download, so a missing login fails
fast instead of waiting for an interactive password prompt. Each file is written under a
temporary name and renamed only when complete, so an interrupted download never leaves a
truncated file that looks valid. Transient network errors are retried with exponential
backoff.

Data are cached as one NetCDF file per dataset part and calendar month, each with a JSON
sidecar holding its provenance and SHA-256 checksum (``ChunkCache``). A rerun downloads
only the months that are missing or no longer match the request.

Run ``uv run python -m arctic_sea_ice.ingest --check-login`` to verify the login.
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import importlib
import json
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any, Literal, Protocol

logger = logging.getLogger(__name__)

LOGIN_HINT = (
    "Copernicus Marine credentials are missing or invalid. "
    "Run `uv run copernicusmarine login` once; the credentials are stored in your user "
    "profile, outside the repository."
)

# Light, lossless compression: sea ice grids are mostly fill values and zeros.
NETCDF_COMPRESSION_LEVEL = 1

LoginFunction = Callable[..., bool]
SubsetFunction = Callable[..., Any]


class CredentialsError(RuntimeError):
    """The Copernicus Marine credentials are missing or invalid."""


class DownloadError(RuntimeError):
    """A subset could not be downloaded."""


@dataclass(frozen=True)
class SubsetRequest:
    """One subset of one dataset part, over whole days from ``start`` to ``end``."""

    dataset_id: str
    version: str
    part: str
    variables: tuple[str, ...]
    start: date
    end: date
    minimum_latitude: float | None = None

    def __post_init__(self) -> None:
        if not self.variables:
            raise ValueError("A subset request needs at least one variable")
        if self.end < self.start:
            raise ValueError(f"End date {self.end} is before start date {self.start}")

    def subset_arguments(self, output: Path) -> dict[str, Any]:
        """Keyword arguments for ``copernicusmarine.subset`` writing to ``output``.

        The version and part are always pinned, so a new catalogue version is never
        downloaded silently (acceptance criterion A). ``raise_if_updating`` refuses data
        while the provider is rewriting the dataset.
        """
        return {
            "dataset_id": self.dataset_id,
            "dataset_version": self.version,
            "dataset_part": self.part,
            "variables": list(self.variables),
            "start_datetime": f"{self.start.isoformat()}T00:00:00",
            "end_datetime": f"{self.end.isoformat()}T23:59:59",
            "minimum_latitude": self.minimum_latitude,
            "output_directory": str(output.parent),
            "output_filename": output.name,
            "file_format": "netcdf",
            "netcdf_compression_level": NETCDF_COMPRESSION_LEVEL,
            "overwrite": True,
            "raise_if_updating": True,
            "disable_progress_bar": True,
        }

    def to_record(self) -> dict[str, Any]:
        """The request as plain values, as stored in the cache metadata."""
        return {
            "dataset_id": self.dataset_id,
            "version": self.version,
            "part": self.part,
            "variables": list(self.variables),
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "minimum_latitude": self.minimum_latitude,
        }


def _default_transient_errors() -> tuple[type[BaseException], ...]:
    """Network errors worth retrying: connection drops, timeouts, auth server outages."""
    import copernicusmarine

    # requests ships without type stubs; it is a dependency of the toolbox.
    requests_errors = importlib.import_module("requests.exceptions")
    return (
        ConnectionError,
        TimeoutError,
        copernicusmarine.CouldNotConnectToAuthenticationSystem,
        requests_errors.ConnectionError,
        requests_errors.Timeout,
        requests_errors.ChunkedEncodingError,
    )


def _partial_path(output: Path) -> Path:
    return output.with_name(f"{output.stem}.partial{output.suffix}")


class CopernicusClient:
    """Downloads subsets with the Copernicus Marine Toolbox.

    ``login``, ``subset``, ``sleep`` and ``transient_errors`` default to the real toolbox
    functions; tests replace them so that no network access is needed.
    """

    def __init__(
        self,
        *,
        login: LoginFunction | None = None,
        subset: SubsetFunction | None = None,
        max_attempts: int = 3,
        backoff_seconds: float = 10.0,
        sleep: Callable[[float], None] = time.sleep,
        transient_errors: tuple[type[BaseException], ...] | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if login is None or subset is None:
            import copernicusmarine

            login = login or copernicusmarine.login
            subset = subset or copernicusmarine.subset
        self._login: LoginFunction = login
        self._subset: SubsetFunction = subset
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds
        self._sleep = sleep
        self._transient_errors = transient_errors
        self._credentials_checked = False

    def check_credentials(self) -> None:
        """Raise ``CredentialsError`` unless the stored credentials are valid.

        The check runs once per client; it never prompts for a password.
        """
        if self._credentials_checked:
            return
        if not self._login(check_credentials_valid=True):
            raise CredentialsError(LOGIN_HINT)
        self._credentials_checked = True

    def download(self, request: SubsetRequest, output: Path) -> Path:
        """Download ``request`` to ``output`` (a ``.nc`` file) and return the path.

        The file is written as ``<name>.partial.nc`` and renamed when complete. Transient
        errors are retried up to ``max_attempts`` times, waiting ``backoff_seconds``,
        then twice as long after each further failure. Other errors propagate at once.
        """
        if output.suffix != ".nc":
            raise ValueError(f"Output must be a NetCDF (.nc) file, got {output.name}")
        self.check_credentials()
        transient = self._transient_errors or _default_transient_errors()
        output.parent.mkdir(parents=True, exist_ok=True)
        partial = _partial_path(output)

        for attempt in range(1, self._max_attempts + 1):
            try:
                self._subset(**request.subset_arguments(partial))
                break
            except transient as error:
                partial.unlink(missing_ok=True)
                if attempt == self._max_attempts:
                    raise DownloadError(
                        f"{request.dataset_id} {request.start}..{request.end}: "
                        f"failed after {attempt} attempts"
                    ) from error
                wait = self._backoff_seconds * 2 ** (attempt - 1)
                logger.warning(
                    "Attempt %d/%d failed (%s); retrying in %.0f s",
                    attempt,
                    self._max_attempts,
                    error,
                    wait,
                )
                self._sleep(wait)
            except BaseException:
                partial.unlink(missing_ok=True)
                raise

        if not partial.is_file():
            raise DownloadError(
                f"{request.dataset_id}: the toolbox reported success but wrote no file"
            )
        partial.replace(output)
        return output


# --- Year-month chunk cache ---------------------------------------------------


@dataclass(frozen=True)
class MonthChunk:
    """The part of one calendar month that falls inside a requested period."""

    year: int
    month: int
    start: date
    end: date

    @property
    def label(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"


def month_chunks(start: date, end: date) -> list[MonthChunk]:
    """Split ``start``..``end`` (inclusive) into calendar months.

    The first and last chunks are clipped to the period, so a period starting on
    25 October covers 25-31 October in its first chunk.
    """
    if end < start:
        raise ValueError(f"End date {end} is before start date {start}")
    chunks = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        first = date(year, month, 1)
        last = date(year, month, calendar.monthrange(year, month)[1])
        chunks.append(MonthChunk(year, month, max(first, start), min(last, end)))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return chunks


class Downloader(Protocol):
    def download(self, request: SubsetRequest, output: Path) -> Path: ...


@dataclass(frozen=True)
class CacheResult:
    """Outcome of fetching one month: ``downloaded`` now or already ``cached``."""

    chunk: MonthChunk
    path: Path
    status: Literal["downloaded", "cached"]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class ChunkCache:
    """Stores one NetCDF file per dataset part and calendar month under ``root``.

    Layout: ``<root>/<dataset_id>/<version>/<part>/<YYYY>/<dataset_id>_<YYYY-MM>.nc``,
    each file with a ``.json`` sidecar recording the request, the SHA-256 checksum, the
    size, the download time and the toolbox version (acceptance criterion A).

    A month counts as cached only if both files exist, the recorded request equals the
    current one and the size matches; with ``verify=True`` the checksum is recomputed too.
    Anything else is downloaded again, so changing the variables or the version in the
    configuration never reuses stale files. The sidecar is written after the data file,
    so an interruption between the two only causes a re-download.
    """

    def __init__(
        self,
        root: Path,
        downloader: Downloader,
        *,
        clock: Callable[[], str] = _utc_now,
    ) -> None:
        self.root = root
        self._downloader = downloader
        self._clock = clock

    def path_for(self, request: SubsetRequest) -> Path:
        if (request.start.year, request.start.month) != (request.end.year, request.end.month):
            raise ValueError("A cached request must lie within one calendar month")
        label = f"{request.start.year:04d}-{request.start.month:02d}"
        return (
            self.root
            / request.dataset_id
            / request.version
            / request.part
            / f"{request.start.year:04d}"
            / f"{request.dataset_id}_{label}.nc"
        )

    @staticmethod
    def metadata_path(data_path: Path) -> Path:
        return data_path.with_suffix(".json")

    def is_cached(self, request: SubsetRequest, *, verify: bool = False) -> bool:
        path = self.path_for(request)
        metadata_path = self.metadata_path(path)
        if not (path.is_file() and metadata_path.is_file()):
            return False
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return False
        if metadata.get("request") != request.to_record():
            return False
        if metadata.get("size_bytes") != path.stat().st_size:
            return False
        return not verify or metadata.get("sha256") == sha256_of(path)

    def fetch(
        self, request: SubsetRequest, chunk: MonthChunk, *, verify: bool = False
    ) -> CacheResult:
        path = self.path_for(request)
        if self.is_cached(request, verify=verify):
            return CacheResult(chunk, path, "cached")
        self._downloader.download(request, path)
        self._write_metadata(request, path)
        return CacheResult(chunk, path, "downloaded")

    def fetch_period(
        self,
        template: SubsetRequest,
        *,
        verify: bool = False,
        on_result: Callable[[CacheResult], None] | None = None,
    ) -> list[CacheResult]:
        """Fetch every month of ``template.start``..``template.end``, oldest first."""
        results = []
        for chunk in month_chunks(template.start, template.end):
            request = replace(template, start=chunk.start, end=chunk.end)
            result = self.fetch(request, chunk, verify=verify)
            if on_result is not None:
                on_result(result)
            results.append(result)
        return results

    def _write_metadata(self, request: SubsetRequest, path: Path) -> None:
        metadata = {
            "request": request.to_record(),
            "file": path.name,
            "size_bytes": path.stat().st_size,
            "sha256": sha256_of(path),
            "downloaded_at": self._clock(),
            "copernicusmarine_version": package_version("copernicusmarine"),
        }
        metadata_path = self.metadata_path(path)
        temporary = metadata_path.with_name(f"{metadata_path.name}.tmp")
        temporary.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        temporary.replace(metadata_path)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m arctic_sea_ice.ingest",
        description="Download Copernicus Marine sea ice data.",
    )
    parser.add_argument(
        "--check-login",
        action="store_true",
        help="verify the stored Copernicus Marine credentials and exit",
    )
    args = parser.parse_args(argv)

    if args.check_login:
        try:
            CopernicusClient().check_credentials()
        except CredentialsError as error:
            parser.exit(1, f"{error}\n")
        print("Copernicus Marine credentials are valid.")
        return
    parser.print_help()


if __name__ == "__main__":
    main()
