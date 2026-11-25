"""Tests for concentration quality control on small synthetic grids.

The flag values and their combinations mirror those found in downloaded OSI SAF files.
"""

import numpy as np
import pytest
import xarray as xr

from arctic_sea_ice.qc import (
    DOMAIN_MINIMUM_LATITUDE,
    QCError,
    StatusFlag,
    daily_summary,
    fraction_scale,
    normalize,
)

LAND = int(StatusFlag.LAND)
LAKE = int(StatusFlag.LAKE)
FILTERED = int(StatusFlag.OPEN_WATER_FILTERED)
CLIMO = int(StatusFlag.MAX_ICE_CLIMO)
SPATIAL = int(StatusFlag.SPATIAL_INTERP)
TEMPORAL = int(StatusFlag.TEMPORAL_INTERP)

NAN = np.nan


def _dataset(conc, flags, *, latitude=(38.0, 60.0), units="%"):
    """Two days on a 2 x 3 grid: rows are latitudes, columns longitudes."""
    conc = np.asarray(conc, dtype=float)
    flags = np.asarray(flags, dtype=np.float32)
    coords = {
        "time": np.array(["2012-09-01", "2012-09-02"], dtype="datetime64[ns]"),
        "latitude": list(latitude),
        "longitude": [0.0, 10.0, 20.0],
    }
    dims = ("time", "latitude", "longitude")
    return xr.Dataset(
        {
            "ice_conc": xr.DataArray(conc, dims=dims, attrs={"units": units}),
            "status_flag": xr.DataArray(flags, dims=dims),
        },
        coords=coords,
    )


# Day 1 (north row): ice 80 %, real zero from a filter, missing ocean value.
# Day 2 (north row): lake with ice, land, interpolated ice.
# South row (38 N) lies outside the study domain on both days.
CONC = [
    [[50.0, 0.0, NAN], [80.0, 0.0, NAN]],
    [[50.0, 0.0, NAN], [30.0, NAN, 45.0]],
]
FLAGS = [
    [[0, CLIMO, 0], [0, FILTERED, 0]],
    [[0, CLIMO, 0], [LAKE, LAND, SPATIAL | TEMPORAL]],
]


@pytest.fixture
def result() -> xr.Dataset:
    return normalize(_dataset(CONC, FLAGS))


def _north(da: xr.DataArray, day: int) -> list:
    return da.isel(time=day).sel(latitude=60.0).values.tolist()


# --- Units --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("units", "scale"),
    [("%", 0.01), ("percent", 0.01), ("1", 1.0), ("fraction", 1.0), (" % ", 0.01)],
)
def test_fraction_scale(units: str, scale: float) -> None:
    assert fraction_scale(units) == scale


@pytest.mark.parametrize("units", [None, "", "m", "0-100"])
def test_unknown_units_are_rejected(units: str | None) -> None:
    with pytest.raises(QCError, match="Unknown concentration units"):
        fraction_scale(units)


def test_percent_is_converted_to_fraction(result: xr.Dataset) -> None:
    assert _north(result["sic"], 0)[:2] == pytest.approx([0.8, 0.0])
    assert result["sic"].attrs["units"] == "1"
    assert result["sic"].attrs["source_units"] == "%"
    assert result["sic"].dtype == np.float32


def test_fraction_units_are_kept() -> None:
    ds = _dataset(np.asarray(CONC) / 100, FLAGS, units="1")

    assert _north(normalize(ds)["sic"], 0)[0] == pytest.approx(0.8)


def test_rounding_noise_above_100_percent_is_clipped() -> None:
    conc = np.full((2, 2, 3), 100.00000000000003)

    sic = normalize(_dataset(conc, np.zeros((2, 2, 3))))["sic"]

    assert float(sic.max()) == 1.0


@pytest.mark.parametrize("bad", [100.5, -0.5, 1000.0])
def test_values_outside_the_valid_range_stop_processing(bad: float) -> None:
    conc = np.full((2, 2, 3), 50.0)
    conc[0, 1, 0] = bad

    with pytest.raises(QCError, match=r"outside \[0, 1\]"):
        normalize(_dataset(conc, np.zeros((2, 2, 3))))


def test_fraction_data_mislabelled_as_percent_still_in_range_is_accepted() -> None:
    # A fraction labelled "%" would become 0..0.01: the range check alone cannot catch
    # it, which is why the units attribute is checked against the data manifest.
    sic = normalize(_dataset(np.asarray(CONC) / 100, FLAGS, units="%"))["sic"]

    assert float(sic.max()) <= 0.01


# --- No-data is never zero ----------------------------------------------------------


def test_missing_ocean_value_stays_missing(result: xr.Dataset) -> None:
    assert np.isnan(_north(result["sic"], 0)[2])
    assert _north(result["missing"], 0) == [False, False, True]


def test_zero_from_producer_filters_is_a_real_zero(result: xr.Dataset) -> None:
    assert _north(result["sic"], 0)[1] == 0.0
    assert not _north(result["missing"], 0)[1]
    assert _north(result["ocean"], 0)[1]


def test_land_and_lake_are_not_ocean(result: xr.Dataset) -> None:
    assert _north(result["ocean"], 1) == [False, False, True]
    # The lake had ice (30 %): it must not count as sea ice.
    assert np.isnan(_north(result["sic"], 1)[0])
    assert _north(result["missing"], 1)[:2] == [False, False]


def test_interpolated_values_are_kept_and_marked(result: xr.Dataset) -> None:
    assert _north(result["sic"], 1)[2] == pytest.approx(0.45)
    assert _north(result["interpolated"], 1) == [False, False, True]


def test_cells_south_of_the_domain_are_excluded(result: xr.Dataset) -> None:
    south = result.sel(latitude=38.0)

    assert not bool(south["ocean"].any())
    assert bool(south["sic"].isnull().all())
    assert not bool(south["missing"].any())
    assert result.attrs["minimum_latitude"] == DOMAIN_MINIMUM_LATITUDE


def test_domain_limit_is_configurable() -> None:
    result = normalize(_dataset(CONC, FLAGS), minimum_latitude=30.0)

    assert result["sic"].isel(time=0).sel(latitude=38.0).values[0] == pytest.approx(0.5)


def test_flags_are_read_per_day(result: xr.Dataset) -> None:
    # The same cell is ocean on day 1 and lake on day 2.
    assert result["ocean"].sel(latitude=60.0, longitude=0.0).values.tolist() == [True, False]


def test_missing_flag_values_carry_no_land_information() -> None:
    flags = np.asarray(FLAGS, dtype=np.float32)
    flags[0, 1, 0] = np.nan

    result = normalize(_dataset(CONC, flags))

    assert _north(result["ocean"], 0)[0]
    assert _north(result["sic"], 0)[0] == pytest.approx(0.8)


def test_without_flags_every_domain_cell_is_ocean() -> None:
    ds = _dataset(CONC, FLAGS).drop_vars("status_flag")

    result = normalize(ds, flags=None)

    assert bool(result["ocean"].sel(latitude=60.0).all())
    assert _north(result["missing"], 1) == [False, True, False]


def test_latitude_is_required() -> None:
    ds = _dataset(CONC, FLAGS).rename({"latitude": "yc"})

    with pytest.raises(QCError, match="latitude"):
        normalize(ds)


# --- Daily summary ------------------------------------------------------------------


def test_daily_summary_counts(result: xr.Dataset) -> None:
    summary = daily_summary(result)

    assert summary["ocean_cells"].values.tolist() == [3, 1]
    assert summary["missing_cells"].values.tolist() == [1, 0]
    assert summary["interpolated_cells"].values.tolist() == [0, 1]
    assert summary["missing_fraction"].values.tolist() == pytest.approx([1 / 3, 0.0])


def test_daily_summary_without_ocean_has_undefined_fraction() -> None:
    ds = _dataset(CONC, np.full((2, 2, 3), LAND))

    fraction = daily_summary(normalize(ds))["missing_fraction"]

    assert bool(fraction.isnull().all())
