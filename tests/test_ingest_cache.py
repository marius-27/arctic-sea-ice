"""Tests for the year-month chunk cache. No network: the downloader is a fake."""

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from arctic_sea_ice.ingest import ChunkCache, MonthChunk, SubsetRequest, month_chunks, sha256_of

DATASET = "OSISAF-GLO-SEAICE_CONC_TIMESERIES-NH-LA-OBS"
FIXED_TIME = "2026-11-25T15:00:00+00:00"

PERIOD = SubsetRequest(
    dataset_id=DATASET,
    version="202003",
    part="default",
    variables=("ice_conc", "status_flag"),
    start=date(2012, 8, 15),
    end=date(2012, 10, 3),
)


class FakeDownloader:
    """Writes a small file whose content identifies the request."""

    def __init__(self) -> None:
        self.requests: list[SubsetRequest] = []

    def download(self, request: SubsetRequest, output: Path) -> Path:
        self.requests.append(request)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(f"{request.start}..{request.end}".encode())
        return output


@pytest.fixture
def downloader() -> FakeDownloader:
    return FakeDownloader()


@pytest.fixture
def cache(tmp_path: Path, downloader: FakeDownloader) -> ChunkCache:
    return ChunkCache(tmp_path / "raw", downloader, clock=lambda: FIXED_TIME)


# --- Month chunks -----------------------------------------------------------------


def test_month_chunks_clip_the_first_and_last_month() -> None:
    chunks = month_chunks(date(2012, 8, 15), date(2012, 10, 3))

    assert chunks == [
        MonthChunk(2012, 8, date(2012, 8, 15), date(2012, 8, 31)),
        MonthChunk(2012, 9, date(2012, 9, 1), date(2012, 9, 30)),
        MonthChunk(2012, 10, date(2012, 10, 1), date(2012, 10, 3)),
    ]
    assert [c.label for c in chunks] == ["2012-08", "2012-09", "2012-10"]


def test_month_chunks_cross_the_year_and_handle_leap_february() -> None:
    chunks = month_chunks(date(2019, 12, 1), date(2020, 2, 29))

    assert [(c.label, c.end) for c in chunks] == [
        ("2019-12", date(2019, 12, 31)),
        ("2020-01", date(2020, 1, 31)),
        ("2020-02", date(2020, 2, 29)),
    ]


def test_month_chunks_of_a_single_day() -> None:
    assert month_chunks(date(2025, 9, 29), date(2025, 9, 29)) == [
        MonthChunk(2025, 9, date(2025, 9, 29), date(2025, 9, 29))
    ]


def test_month_chunks_reject_reversed_periods() -> None:
    with pytest.raises(ValueError, match="before start date"):
        month_chunks(date(2020, 2, 1), date(2020, 1, 1))


def test_full_core_period_has_one_chunk_per_month() -> None:
    assert len(month_chunks(date(1979, 1, 1), date(2024, 12, 31))) == 46 * 12


# --- Cache layout and metadata --------------------------------------------------------


def test_cache_path_layout(cache: ChunkCache, tmp_path: Path) -> None:
    request = replace(PERIOD, start=date(2012, 9, 1), end=date(2012, 9, 30))

    assert cache.path_for(request) == (
        tmp_path / "raw" / DATASET / "202003" / "default" / "2012" / f"{DATASET}_2012-09.nc"
    )


def test_cache_path_requires_a_single_month(cache: ChunkCache) -> None:
    with pytest.raises(ValueError, match="one calendar month"):
        cache.path_for(PERIOD)


def test_fetch_period_downloads_each_month_once(
    cache: ChunkCache, downloader: FakeDownloader
) -> None:
    results = cache.fetch_period(PERIOD)

    assert [(r.chunk.label, r.status) for r in results] == [
        ("2012-08", "downloaded"),
        ("2012-09", "downloaded"),
        ("2012-10", "downloaded"),
    ]
    assert [(r.start, r.end) for r in downloader.requests] == [
        (date(2012, 8, 15), date(2012, 8, 31)),
        (date(2012, 9, 1), date(2012, 9, 30)),
        (date(2012, 10, 1), date(2012, 10, 3)),
    ]


def test_metadata_records_provenance_and_checksum(cache: ChunkCache) -> None:
    (result, *_) = cache.fetch_period(PERIOD)
    metadata = json.loads(ChunkCache.metadata_path(result.path).read_text(encoding="utf-8"))

    assert metadata["request"] == {
        "dataset_id": DATASET,
        "version": "202003",
        "part": "default",
        "variables": ["ice_conc", "status_flag"],
        "start": "2012-08-15",
        "end": "2012-08-31",
        "minimum_latitude": None,
    }
    assert metadata["file"] == result.path.name
    assert metadata["size_bytes"] == result.path.stat().st_size
    assert metadata["sha256"] == sha256_of(result.path)
    assert metadata["downloaded_at"] == FIXED_TIME
    assert metadata["copernicusmarine_version"]


def test_second_run_uses_the_cache(cache: ChunkCache, downloader: FakeDownloader) -> None:
    cache.fetch_period(PERIOD)
    downloader.requests.clear()

    results = cache.fetch_period(PERIOD, verify=True)

    assert {r.status for r in results} == {"cached"}
    assert downloader.requests == []


def test_progress_callback_sees_every_month(cache: ChunkCache) -> None:
    seen: list[str] = []

    cache.fetch_period(PERIOD, on_result=lambda r: seen.append(f"{r.chunk.label}:{r.status}"))

    assert seen == ["2012-08:downloaded", "2012-09:downloaded", "2012-10:downloaded"]


# --- Cache invalidation ---------------------------------------------------------------


def _september(cache: ChunkCache) -> tuple[SubsetRequest, Path]:
    request = replace(PERIOD, start=date(2012, 9, 1), end=date(2012, 9, 30))
    cache.fetch(request, month_chunks(request.start, request.end)[0])
    return request, cache.path_for(request)


def test_missing_metadata_means_not_cached(cache: ChunkCache) -> None:
    request, path = _september(cache)
    ChunkCache.metadata_path(path).unlink()

    assert not cache.is_cached(request)


def test_corrupt_metadata_means_not_cached(cache: ChunkCache) -> None:
    request, path = _september(cache)
    ChunkCache.metadata_path(path).write_text("{not json", encoding="utf-8")

    assert not cache.is_cached(request)


def test_changed_request_means_not_cached(cache: ChunkCache) -> None:
    request, _ = _september(cache)

    assert cache.is_cached(request)
    assert not cache.is_cached(replace(request, variables=("ice_conc",)))


def test_size_mismatch_means_not_cached(cache: ChunkCache) -> None:
    request, path = _september(cache)
    path.write_bytes(path.read_bytes() + b"x")

    assert not cache.is_cached(request)


def test_verify_detects_changed_content_of_equal_size(cache: ChunkCache) -> None:
    request, path = _september(cache)
    content = path.read_bytes()
    path.write_bytes(bytes([content[0] ^ 1]) + content[1:])

    assert cache.is_cached(request)  # size check alone does not notice
    assert not cache.is_cached(request, verify=True)


def test_invalid_month_is_downloaded_again(cache: ChunkCache, downloader: FakeDownloader) -> None:
    request, path = _september(cache)
    path.write_bytes(b"corrupt")
    downloader.requests.clear()

    result = cache.fetch(request, month_chunks(request.start, request.end)[0])

    assert result.status == "downloaded"
    assert len(downloader.requests) == 1
    assert cache.is_cached(request, verify=True)
