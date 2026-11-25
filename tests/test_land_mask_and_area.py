"""Validation of the land mask and grid areas, together.

Synthetic tests run everywhere. Tests marked ``data`` read monthly files downloaded into
``data/raw`` and are skipped when those files are absent (as in CI); run them locally
after ``python -m arctic_sea_ice.ingest --download``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from arctic_sea_ice.grid import AUTHALIC_EARTH_RADIUS_KM, cell_area
from arctic_sea_ice.qc import DOMAIN_MINIMUM_LATITUDE, StatusFlag, normalize

R = AUTHALIC_EARTH_RADIUS_KM
RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
CDR = "OSISAF-GLO-SEAICE_CONC_TIMESERIES-NH-LA-OBS"
ICDR = "OSISAF-GLO-SEAICE_CONC_CONT_TIMESERIES-NH-LA-OBS"


def _cap_km2(lower_edge_deg: float) -> float:
    """Area of the spherical cap north of ``lower_edge_deg``."""
    return 2 * np.pi * R**2 * (1 - np.sin(np.radians(lower_edge_deg)))


# --- Synthetic grids ------------------------------------------------------------------


def _synthetic(step: float, land_rows: slice) -> xr.Dataset:
    """One day on a global latitude-longitude grid, 50 % ice everywhere, land in a band."""
    lat = np.arange(-90 + step / 2, 90, step)
    lon = np.arange(-180, 180, step)
    shape = (1, lat.size, lon.size)
    conc = np.full(shape, 50.0)
    flags = np.zeros(shape, dtype=np.float32)
    conc[0, land_rows, :] = np.nan
    flags[0, land_rows, :] = StatusFlag.LAND
    return xr.Dataset(
        {
            "ice_conc": (("time", "latitude", "longitude"), conc, {"units": "%"}),
            "status_flag": (("time", "latitude", "longitude"), flags),
        },
        coords={"time": [np.datetime64("2012-09-01", "ns")], "latitude": lat, "longitude": lon},
    )


def test_ocean_area_is_the_domain_cap_minus_land() -> None:
    # 1 degree grid; land between 70 N and 80 N (rows centred 70.5..79.5).
    ds = _synthetic(1.0, slice(160, 170))
    area = cell_area(ds)

    ocean = float(area.where(normalize(ds)["ocean"].isel(time=0)).sum())
    land = _cap_km2(70.0) - _cap_km2(80.0)

    assert ocean == pytest.approx(_cap_km2(DOMAIN_MINIMUM_LATITUDE) - land, rel=1e-9)


def test_concentration_weighted_area_uses_real_cell_areas() -> None:
    ds = _synthetic(0.5, slice(0, 0))
    area = cell_area(ds)
    sic = normalize(ds)["sic"].isel(time=0)

    ice_area = float((area * sic.fillna(0)).sum())

    assert ice_area == pytest.approx(0.5 * _cap_km2(DOMAIN_MINIMUM_LATITUDE), rel=1e-9)


def test_land_never_contributes_area_even_with_a_value() -> None:
    ds = _synthetic(1.0, slice(160, 170))
    ds["ice_conc"][0, 160:170, :] = 100.0  # a value on land must still be ignored
    area = cell_area(ds)

    sic = normalize(ds)["sic"].isel(time=0)

    assert float(area.where(sic.notnull()).sel(latitude=slice(70, 80)).sum()) == 0.0


# --- Downloaded data --------------------------------------------------------------


def _month(dataset_id: str, year: int, month: int) -> Path:
    path = (
        RAW / dataset_id / "202003" / "default" / f"{year}" / f"{dataset_id}_{year}-{month:02d}.nc"
    )
    if not path.is_file():
        pytest.skip(f"{path.name} not downloaded")
    return path


def _open(path: Path) -> xr.Dataset:
    return xr.open_dataset(path)


@pytest.mark.data
@pytest.mark.parametrize(("dataset_id", "year", "month"), [(CDR, 2020, 12), (ICDR, 2021, 1)])
def test_land_cells_never_carry_a_concentration(dataset_id: str, year: int, month: int) -> None:
    with _open(_month(dataset_id, year, month)) as ds:
        land = (ds["status_flag"].fillna(0).astype("int32") & StatusFlag.LAND) != 0

        assert bool(land.any())
        assert bool(ds["ice_conc"].where(land).isnull().all())


@pytest.mark.data
@pytest.mark.parametrize(("dataset_id", "year", "month"), [(CDR, 2020, 12), (ICDR, 2021, 1)])
def test_land_and_lake_mask_is_constant_within_a_month(
    dataset_id: str, year: int, month: int
) -> None:
    with _open(_month(dataset_id, year, month)) as ds:
        mask = (
            ds["status_flag"].fillna(0).astype("int32") & (StatusFlag.LAND | StatusFlag.LAKE)
        ) != 0

        assert bool((mask == mask.isel(time=0)).all())


@pytest.mark.data
@pytest.mark.parametrize(("dataset_id", "year", "month"), [(CDR, 2020, 12), (ICDR, 2021, 1)])
def test_real_grid_covers_the_domain_cap(dataset_id: str, year: int, month: int) -> None:
    with _open(_month(dataset_id, year, month)) as ds:
        area = cell_area(ds)
        domain = normalize(ds).attrs["minimum_latitude"]
        step = float(abs(ds["latitude"][1] - ds["latitude"][0]))
        in_domain = area.where(ds["latitude"] >= domain - 1e-6)

        # The domain starts half a cell below 40 N, at the lower edge of the 40 N row.
        assert float(in_domain.sum()) == pytest.approx(_cap_km2(domain - step / 2), rel=1e-4)


@pytest.mark.data
def test_both_records_cover_the_same_ocean_at_the_transition() -> None:
    oceans = []
    for dataset_id, year, month in ((CDR, 2020, 12), (ICDR, 2021, 1)):
        with _open(_month(dataset_id, year, month)) as ds:
            ocean = normalize(ds)["ocean"].isel(time=-1 if dataset_id == CDR else 0)
            oceans.append(float(cell_area(ds).where(ocean).sum()))

    cdr, icdr = oceans
    # Different grids (0.2 x 0.2 vs 0.25 x 0.3 degrees) and coastlines: within 0.5 %.
    assert cdr == pytest.approx(icdr, rel=5e-3)
    # About 43 million km2 of ocean north of 40 N.
    assert 40e6 < cdr < 46e6
