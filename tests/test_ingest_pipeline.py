"""End-to-end download scenarios with a mocked toolbox: client + retries + month cache.

The real ``CopernicusClient`` and ``ChunkCache`` are used; only ``copernicusmarine.login``
and ``copernicusmarine.subset`` are replaced. Each fake subset call is also checked
against the signature of the real ``copernicusmarine.subset``, so a renamed or removed
toolbox argument fails here instead of at download time.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import copernicusmarine
import pytest

from arctic_sea_ice.ingest import (
    ChunkCache,
    CopernicusClient,
    CredentialsError,
    DownloadError,
    SubsetRequest,
)

PERIOD = SubsetRequest(
    dataset_id="OSISAF-GLO-SEAICE_CONC_TIMESERIES-NH-LA-OBS",
    version="202003",
    part="default",
    variables=("ice_conc", "status_flag"),
    start=date(2012, 7, 1),
    end=date(2012, 10, 31),
)
MONTHS = ["2012-07", "2012-08", "2012-09", "2012-10"]
REAL_SUBSET = inspect.signature(copernicusmarine.subset)


class MockToolbox:
    """Fake login and subset. ``failures`` maps a month label to errors raised in turn."""

    def __init__(
        self,
        *,
        valid: bool = True,
        failures: dict[str, list[BaseException]] | None = None,
    ) -> None:
        self.valid = valid
        self.failures = {month: list(errors) for month, errors in (failures or {}).items()}
        self.calls: list[str] = []

    def login(self, **kwargs: Any) -> bool:
        return self.valid

    def subset(self, **kwargs: Any) -> None:
        REAL_SUBSET.bind(**kwargs)  # raises TypeError on an unknown argument
        month = kwargs["start_datetime"][:7]
        self.calls.append(month)
        target = Path(kwargs["output_directory"]) / kwargs["output_filename"]
        target.write_bytes(b"partial")
        pending = self.failures.get(month)
        if pending:
            raise pending.pop(0)
        target.write_bytes(f"netcdf {kwargs['start_datetime']}..{kwargs['end_datetime']}".encode())


def _cache(tmp_path: Path, toolbox: MockToolbox, waits: list[float] | None = None) -> ChunkCache:
    client = CopernicusClient(
        login=toolbox.login,
        subset=toolbox.subset,
        max_attempts=3,
        backoff_seconds=1.0,
        sleep=(waits if waits is not None else []).append,
        transient_errors=(ConnectionError, TimeoutError),
    )
    return ChunkCache(tmp_path / "raw", client, clock=lambda: "2026-11-25T15:00:00+00:00")


def _files(tmp_path: Path) -> list[str]:
    return sorted(p.name for p in (tmp_path / "raw").rglob("*") if p.is_file())


def _complete_months(tmp_path: Path) -> list[str]:
    return sorted(name[-10:-3] for name in _files(tmp_path) if name.endswith(".nc"))


def _run(cache: ChunkCache) -> list[tuple[str, str]]:
    return [(r.chunk.label, r.status) for r in cache.fetch_period(PERIOD)]


def test_requests_match_the_real_toolbox_signature(tmp_path: Path) -> None:
    toolbox = MockToolbox()

    _run(_cache(tmp_path, toolbox))

    assert toolbox.calls == MONTHS


def test_flaky_network_is_retried_and_the_period_completes(tmp_path: Path) -> None:
    toolbox = MockToolbox(failures={"2012-08": [ConnectionError("reset"), TimeoutError("slow")]})
    waits: list[float] = []

    results = _run(_cache(tmp_path, toolbox, waits))

    assert results == [(m, "downloaded") for m in MONTHS]
    assert toolbox.calls == ["2012-07", "2012-08", "2012-08", "2012-08", "2012-09", "2012-10"]
    assert waits == [1.0, 2.0]
    assert not [name for name in _files(tmp_path) if ".partial" in name]


def test_interrupted_run_resumes_with_the_missing_months(tmp_path: Path) -> None:
    toolbox = MockToolbox(failures={"2012-09": [KeyboardInterrupt()]})

    with pytest.raises(KeyboardInterrupt):
        _run(_cache(tmp_path, toolbox))

    # Months before the interruption are complete; nothing partial is left behind.
    assert _complete_months(tmp_path) == ["2012-07", "2012-08"]
    assert not [name for name in _files(tmp_path) if ".partial" in name]

    toolbox.calls.clear()
    results = _run(_cache(tmp_path, toolbox))

    assert results == [
        ("2012-07", "cached"),
        ("2012-08", "cached"),
        ("2012-09", "downloaded"),
        ("2012-10", "downloaded"),
    ]
    assert toolbox.calls == ["2012-09", "2012-10"]


def test_persistent_failure_stops_and_a_later_run_resumes(tmp_path: Path) -> None:
    toolbox = MockToolbox(failures={"2012-08": [ConnectionError("down")] * 3})
    waits: list[float] = []

    with pytest.raises(DownloadError, match="failed after 3 attempts"):
        _run(_cache(tmp_path, toolbox, waits))

    assert waits == [1.0, 2.0]
    assert _complete_months(tmp_path) == ["2012-07"]

    toolbox.calls.clear()
    results = _run(_cache(tmp_path, toolbox))

    assert [status for _, status in results] == ["cached", "downloaded", "downloaded", "downloaded"]
    assert toolbox.calls == ["2012-08", "2012-09", "2012-10"]


def test_error_that_is_not_transient_is_not_retried(tmp_path: Path) -> None:
    toolbox = MockToolbox(failures={"2012-07": [ValueError("variable not found")]})
    waits: list[float] = []

    with pytest.raises(ValueError, match="variable not found"):
        _run(_cache(tmp_path, toolbox, waits))

    assert toolbox.calls == ["2012-07"]
    assert waits == []
    assert _files(tmp_path) == []


def test_invalid_login_downloads_nothing(tmp_path: Path) -> None:
    toolbox = MockToolbox(valid=False)

    with pytest.raises(CredentialsError):
        _run(_cache(tmp_path, toolbox))

    assert toolbox.calls == []
    assert not (tmp_path / "raw").exists() or _files(tmp_path) == []


def test_every_downloaded_month_has_matching_provenance(tmp_path: Path) -> None:
    results = _cache(tmp_path, MockToolbox()).fetch_period(PERIOD)

    for result in results:
        metadata = json.loads(ChunkCache.metadata_path(result.path).read_text(encoding="utf-8"))
        assert metadata["request"]["start"] == result.chunk.start.isoformat()
        assert metadata["request"]["end"] == result.chunk.end.isoformat()
        assert metadata["size_bytes"] == result.path.stat().st_size


@pytest.mark.parametrize(
    "change",
    [
        lambda r: replace(r, variables=("ice_conc",)),
        lambda r: replace(r, version="202603"),
    ],
    ids=["fewer-variables", "new-version"],
)
def test_changed_configuration_downloads_again(
    tmp_path: Path, change: Callable[[SubsetRequest], SubsetRequest]
) -> None:
    toolbox = MockToolbox()
    cache = _cache(tmp_path, toolbox)
    cache.fetch_period(PERIOD)
    toolbox.calls.clear()

    results = cache.fetch_period(change(PERIOD))

    assert {r.status for r in results} == {"downloaded"}
    assert toolbox.calls == MONTHS
