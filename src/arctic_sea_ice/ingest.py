"""Authenticated download of Copernicus Marine subsets.

Downloads need a free Copernicus Marine account. Credentials are stored once, outside the
repository, with ``uv run copernicusmarine login``; they are never passed through this
code or written to the project.

The client checks the credentials before the first download, so a missing login fails
fast instead of waiting for an interactive password prompt. Each file is written under a
temporary name and renamed only when complete, so an interrupted download never leaves a
truncated file that looks valid. Transient network errors are retried with exponential
backoff.

Run ``uv run python -m arctic_sea_ice.ingest --check-login`` to verify the login.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

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
