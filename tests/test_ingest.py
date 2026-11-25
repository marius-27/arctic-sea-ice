"""Tests for the download client. No network: login and subset are fakes."""

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from arctic_sea_ice import ingest
from arctic_sea_ice.ingest import (
    CopernicusClient,
    CredentialsError,
    DownloadError,
    SubsetRequest,
)

REQUEST = SubsetRequest(
    dataset_id="OSISAF-GLO-SEAICE_CONC_TIMESERIES-NH-LA-OBS",
    version="202003",
    part="default",
    variables=("ice_conc", "status_flag"),
    start=date(2012, 9, 1),
    end=date(2012, 9, 30),
)


class FakeToolbox:
    """Stands in for copernicusmarine.login and copernicusmarine.subset."""

    def __init__(self, *, valid: bool = True, failures: list[BaseException] | None = None):
        self.valid = valid
        self.failures = list(failures or [])
        self.login_calls = 0
        self.subset_calls: list[dict[str, Any]] = []

    def login(self, **kwargs: Any) -> bool:
        assert kwargs == {"check_credentials_valid": True}
        self.login_calls += 1
        return self.valid

    def subset(self, **kwargs: Any) -> None:
        self.subset_calls.append(kwargs)
        target = Path(kwargs["output_directory"]) / kwargs["output_filename"]
        if self.failures:
            target.write_bytes(b"trunc")  # a failure may leave a partial file behind
            raise self.failures.pop(0)
        target.write_bytes(b"CDF-data")


def _client(toolbox: FakeToolbox, waits: list[float] | None = None, **kwargs: Any):
    return CopernicusClient(
        login=toolbox.login,
        subset=toolbox.subset,
        sleep=(waits if waits is not None else []).append,
        transient_errors=(ConnectionError, TimeoutError),
        **kwargs,
    )


# --- Request --------------------------------------------------------------------


def test_request_pins_version_part_and_whole_days(tmp_path: Path) -> None:
    arguments = REQUEST.subset_arguments(tmp_path / "out.nc")

    assert arguments["dataset_version"] == "202003"
    assert arguments["dataset_part"] == "default"
    assert arguments["variables"] == ["ice_conc", "status_flag"]
    assert arguments["start_datetime"] == "2012-09-01T00:00:00"
    assert arguments["end_datetime"] == "2012-09-30T23:59:59"
    assert arguments["output_directory"] == str(tmp_path)
    assert arguments["output_filename"] == "out.nc"
    assert arguments["raise_if_updating"] is True
    assert arguments["file_format"] == "netcdf"


def test_request_never_carries_credentials(tmp_path: Path) -> None:
    arguments = REQUEST.subset_arguments(tmp_path / "out.nc")

    assert not {"username", "password", "credentials_file"} & arguments.keys()


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"variables": ()}, "at least one variable"),
        ({"end": date(2012, 8, 31)}, "before start date"),
    ],
)
def test_invalid_requests_are_rejected(changes: dict[str, Any], message: str) -> None:
    fields = REQUEST.__dict__ | changes

    with pytest.raises(ValueError, match=message):
        SubsetRequest(**fields)


# --- Credentials ----------------------------------------------------------------


def test_invalid_credentials_fail_before_any_download(tmp_path: Path) -> None:
    toolbox = FakeToolbox(valid=False)

    with pytest.raises(CredentialsError, match="copernicusmarine login"):
        _client(toolbox).download(REQUEST, tmp_path / "out.nc")

    assert toolbox.subset_calls == []


def test_credentials_are_checked_once_per_client(tmp_path: Path) -> None:
    toolbox = FakeToolbox()
    client = _client(toolbox)

    client.download(REQUEST, tmp_path / "a.nc")
    client.download(REQUEST, tmp_path / "b.nc")

    assert toolbox.login_calls == 1


# --- Download -------------------------------------------------------------------


def test_download_writes_through_a_partial_file(tmp_path: Path) -> None:
    toolbox = FakeToolbox()
    output = tmp_path / "sub" / "2012-09.nc"

    result = _client(toolbox).download(REQUEST, output)

    assert result == output
    assert output.read_bytes() == b"CDF-data"
    assert toolbox.subset_calls[0]["output_filename"] == "2012-09.partial.nc"
    assert not (output.parent / "2012-09.partial.nc").exists()


def test_transient_errors_are_retried_with_exponential_backoff(tmp_path: Path) -> None:
    toolbox = FakeToolbox(failures=[ConnectionError("reset"), TimeoutError("slow")])
    waits: list[float] = []

    _client(toolbox, waits, max_attempts=3, backoff_seconds=2.0).download(
        REQUEST, tmp_path / "out.nc"
    )

    assert len(toolbox.subset_calls) == 3
    assert waits == [2.0, 4.0]
    assert (tmp_path / "out.nc").read_bytes() == b"CDF-data"


def test_giving_up_raises_and_leaves_no_file(tmp_path: Path) -> None:
    toolbox = FakeToolbox(failures=[ConnectionError("down")] * 2)

    with pytest.raises(DownloadError, match="failed after 2 attempts"):
        _client(toolbox, max_attempts=2).download(REQUEST, tmp_path / "out.nc")

    assert list(tmp_path.iterdir()) == []


def test_other_errors_are_not_retried(tmp_path: Path) -> None:
    toolbox = FakeToolbox(failures=[ValueError("unknown variable")])

    with pytest.raises(ValueError, match="unknown variable"):
        _client(toolbox).download(REQUEST, tmp_path / "out.nc")

    assert len(toolbox.subset_calls) == 1
    assert list(tmp_path.iterdir()) == []


def test_success_without_a_file_is_an_error(tmp_path: Path) -> None:
    toolbox = FakeToolbox()

    with pytest.raises(DownloadError, match="wrote no file"):
        CopernicusClient(
            login=toolbox.login,
            subset=lambda **_: None,
            transient_errors=(ConnectionError,),
        ).download(REQUEST, tmp_path / "out.nc")


def test_output_must_be_netcdf(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"\.nc"):
        _client(FakeToolbox()).download(REQUEST, tmp_path / "out.zarr")


def test_max_attempts_must_be_positive() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        _client(FakeToolbox(), max_attempts=0)


def test_default_transient_errors_cover_network_failures() -> None:
    errors = ingest._default_transient_errors()

    assert ConnectionError in errors
    assert TimeoutError in errors
    assert any(e.__module__.startswith("requests") for e in errors)


# --- Command line ---------------------------------------------------------------


def test_check_login_reports_valid_credentials(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    toolbox = FakeToolbox()
    monkeypatch.setattr(
        ingest,
        "CopernicusClient",
        lambda: CopernicusClient(login=toolbox.login, subset=toolbox.subset),
    )

    ingest.main(["--check-login"])

    assert "credentials are valid" in capsys.readouterr().out


def test_check_login_exits_with_hint_when_invalid(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    toolbox = FakeToolbox(valid=False)
    monkeypatch.setattr(
        ingest,
        "CopernicusClient",
        lambda: CopernicusClient(login=toolbox.login, subset=toolbox.subset),
    )

    with pytest.raises(SystemExit) as exit_info:
        ingest.main(["--check-login"])

    assert exit_info.value.code == 1
    assert "copernicusmarine login" in capsys.readouterr().err
