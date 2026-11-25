"""Tests for the download configuration and the download command. No network."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, ClassVar

import pytest
import yaml

from arctic_sea_ice import ingest
from arctic_sea_ice.ingest import (
    IngestConfig,
    Series,
    SubsetRequest,
    check_against_manifest,
    clip,
    load_ingest_config,
    manifest_problems,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "configs" / "ingest.yaml"
MANIFEST_PATH = PROJECT_ROOT / "configs" / "data_manifest.yaml"

CDR = "OSISAF-GLO-SEAICE_CONC_TIMESERIES-NH-LA-OBS"


def _manifest() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    return loaded


def _write(tmp_path: Path, content: dict[str, Any]) -> Path:
    path = tmp_path / "ingest.yaml"
    path.write_text(yaml.safe_dump(content), encoding="utf-8")
    return path


def _series_fields(**changes: Any) -> dict[str, Any]:
    return {
        "dataset_id": CDR,
        "version": "202003",
        "part": "default",
        "variables": ["ice_conc", "status_flag"],
        "start": "2012-09-01",
        "end": "2012-10-31",
    } | changes


def _series(**changes: Any) -> Series:
    fields = _series_fields(**changes)
    return ingest._parse_series("test", fields)


# --- The committed configuration ------------------------------------------------------


def test_committed_config_matches_the_manifest() -> None:
    check_against_manifest(load_ingest_config(CONFIG_PATH), _manifest())


def test_committed_config_covers_the_core_period_without_gap() -> None:
    cdr, icdr = load_ingest_config(CONFIG_PATH).series

    assert (cdr.name, icdr.name) == ("cdr", "icdr")
    assert cdr.request.start == date(1979, 1, 1)
    assert cdr.request.end == date(2020, 12, 31)
    assert icdr.request.start == date(2021, 1, 1)
    assert icdr.request.end >= date(2024, 12, 31)


def test_committed_config_downloads_concentration_and_flags() -> None:
    for series in load_ingest_config(CONFIG_PATH).series:
        assert series.request.variables == ("ice_conc", "status_flag")


# --- Loading ----------------------------------------------------------------------


def test_load_parses_dates_and_paths(tmp_path: Path) -> None:
    path = _write(tmp_path, {"data_directory": "somewhere", "series": {"a": _series_fields()}})

    config = load_ingest_config(path)

    assert config.data_directory == Path("somewhere")
    assert config.series[0].request.start == date(2012, 9, 1)
    assert config.series[0].request.variables == ("ice_conc", "status_flag")


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ({"data_directory": "x"}, "non-empty 'series'"),
        ({"series": {"a": _series_fields()}, "extra": 1}, "unknown top-level fields"),
        ({"series": {"a": _series_fields(colour="blue")}}, "unknown fields"),
        ({"series": {"a": {"dataset_id": CDR}}}, "missing fields"),
        ({"series": {"a": _series_fields(start="1st of May")}}, "a.start must be a date"),
        ({"series": {"a": ["not", "a", "mapping"]}}, "must be a mapping"),
    ],
)
def test_load_rejects_malformed_configuration(
    tmp_path: Path, content: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        load_ingest_config(_write(tmp_path, content))


def test_select_series_by_name() -> None:
    config = IngestConfig(Path("d"), (_series(), ingest._parse_series("b", _series_fields())))

    assert [s.name for s in config.select(["b"])] == ["b"]
    assert len(config.select(None)) == 2
    with pytest.raises(ValueError, match="Unknown series"):
        config.select(["nope"])


# --- Manifest check ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"dataset_id": "NOT_A_DATASET"}, "dataset not in the manifest"),
        ({"version": "199901"}, "version 199901 not in the manifest"),
        ({"part": "originalGrid"}, "part 'originalGrid' not in the manifest"),
        ({"variables": ["ice_conc", "sst"]}, "variables ['sst'] not in the manifest"),
        ({"start": "1978-01-01"}, "outside coverage 1978-10-25..2020-12-31"),
        ({"end": "2021-06-30"}, "outside coverage"),
    ],
)
def test_manifest_problems_are_reported(changes: dict[str, Any], message: str) -> None:
    problems = manifest_problems(_series(**changes), _manifest())

    assert len(problems) == 1
    assert message in problems[0]


def test_consistent_series_has_no_problems() -> None:
    assert manifest_problems(_series(), _manifest()) == []


def test_check_lists_every_problem() -> None:
    config = IngestConfig(
        Path("d"),
        (_series(part="originalGrid"), _series(variables=["sst"])),
    )

    with pytest.raises(ValueError, match="does not match") as error:
        check_against_manifest(config, _manifest())

    assert str(error.value).count("test (") == 2


# --- Clipping -----------------------------------------------------------------------


REQUEST = SubsetRequest(
    CDR, "202003", "default", ("ice_conc",), date(2010, 1, 1), date(2020, 12, 31)
)


def test_clip_restricts_to_the_overlap() -> None:
    clipped = clip(REQUEST, date(2012, 9, 1), date(2025, 1, 1))

    assert clipped is not None
    assert (clipped.start, clipped.end) == (date(2012, 9, 1), date(2020, 12, 31))


def test_clip_without_limits_keeps_the_request() -> None:
    assert clip(REQUEST, None, None) == REQUEST


def test_clip_without_overlap_is_none() -> None:
    assert clip(REQUEST, date(2021, 1, 1), None) is None


# --- Command line -------------------------------------------------------------------


class FakeClient:
    instances: ClassVar[list[FakeClient]] = []

    def __init__(self) -> None:
        self.requests: list[SubsetRequest] = []
        FakeClient.instances.append(self)

    def download(self, request: SubsetRequest, output: Path) -> Path:
        self.requests.append(request)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"x" * 1000)
        return output


@pytest.fixture
def offline_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    FakeClient.instances = []
    monkeypatch.setattr(ingest, "CopernicusClient", FakeClient)
    return _write(
        tmp_path,
        {
            "data_directory": str(tmp_path / "raw"),
            "series": {
                "cdr": _series_fields(start="2020-11-15", end="2020-12-31"),
                "icdr": _series_fields(
                    dataset_id="OSISAF-GLO-SEAICE_CONC_CONT_TIMESERIES-NH-LA-OBS",
                    start="2021-01-01",
                    end="2021-01-31",
                ),
            },
        },
    )


def _run(config: Path, *extra: str) -> None:
    ingest.main(["--download", "--config", str(config), "--manifest", str(MANIFEST_PATH), *extra])


def test_download_fetches_all_series_by_month(
    offline_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(offline_config)

    (client,) = FakeClient.instances
    assert [(r.start, r.end) for r in client.requests] == [
        (date(2020, 11, 15), date(2020, 11, 30)),
        (date(2020, 12, 1), date(2020, 12, 31)),
        (date(2021, 1, 1), date(2021, 1, 31)),
    ]
    out = capsys.readouterr().out
    assert "Done: 3 downloaded, 0 already cached" in out


def test_second_download_uses_the_cache(
    offline_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(offline_config)
    _run(offline_config, "--verify")

    assert FakeClient.instances[1].requests == []
    assert "Done: 0 downloaded, 3 already cached" in capsys.readouterr().out


def test_small_subset_by_dates_and_series(
    offline_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(offline_config, "--series", "cdr", "icdr", "--start", "2020-12-10", "--end", "2020-12-20")

    (client,) = FakeClient.instances
    assert [(r.start, r.end) for r in client.requests] == [(date(2020, 12, 10), date(2020, 12, 20))]
    assert "icdr: no overlap with the requested period, skipped" in capsys.readouterr().out


def test_configuration_errors_exit_before_downloading(
    offline_config: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = _write(tmp_path, {"series": {"cdr": _series_fields(version="199901")}})

    with pytest.raises(SystemExit) as exit_info:
        _run(bad)

    assert exit_info.value.code == 1
    assert "version 199901 not in the manifest" in capsys.readouterr().err
    assert FakeClient.instances == []


def test_invalid_date_argument_is_rejected(offline_config: Path) -> None:
    with pytest.raises(SystemExit):
        _run(offline_config, "--start", "September")


@pytest.mark.parametrize(
    ("size", "text"),
    [(2_700_000, "2.7 MB"), (999_000_000, "999.0 MB"), (9_480_000_000, "9.48 GB")],
)
def test_format_size(size: int, text: str) -> None:
    assert ingest.format_size(size) == text


def test_no_action_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    ingest.main([])

    assert "--download" in capsys.readouterr().out
