"""Tests for grid cell areas against values known analytically."""

import numpy as np
import pytest
import xarray as xr

from arctic_sea_ice.grid import (
    AUTHALIC_EARTH_RADIUS_KM,
    GridError,
    cell_area,
    latlon_cell_area,
    projected_cell_area,
)

R = AUTHALIC_EARTH_RADIUS_KM
EARTH_SURFACE_KM2 = 4 * np.pi * R**2  # about 510.07 million km^2


def _coord(name: str, values, units: str | None = None) -> xr.DataArray:
    attrs = {"units": units} if units is not None else {}
    return xr.DataArray(np.asarray(values, dtype=float), dims=name, name=name, attrs=attrs)


def _latlon(lat_step: float, lon_step: float, lat_min: float = -90.0, lat_max: float = 90.0):
    lat = np.arange(lat_min + lat_step / 2, lat_max, lat_step)
    lon = np.arange(-180.0, 180.0, lon_step)
    return _coord("latitude", lat), _coord("longitude", lon)


# --- Latitude-longitude grids --------------------------------------------------------


@pytest.mark.parametrize(("lat_step", "lon_step"), [(0.2, 0.2), (0.25, 0.3), (1.0, 2.0)])
def test_global_grid_sums_to_the_earth_surface(lat_step: float, lon_step: float) -> None:
    area = latlon_cell_area(*_latlon(lat_step, lon_step))

    assert float(area.sum()) == pytest.approx(EARTH_SURFACE_KM2, rel=1e-9)


def test_authalic_radius_gives_the_known_earth_surface() -> None:
    # The WGS 84 ellipsoid has a surface of about 510.07 million km^2.
    assert abs(EARTH_SURFACE_KM2 - 510.07e6) < 0.01e6


def test_area_north_of_40n_matches_the_spherical_cap() -> None:
    area = latlon_cell_area(*_latlon(0.2, 0.2, lat_min=40.0))
    cap = 2 * np.pi * R**2 * (1 - np.sin(np.radians(40.0)))

    assert float(area.sum()) == pytest.approx(cap, rel=1e-9)


def test_single_cell_matches_the_closed_form() -> None:
    area = latlon_cell_area(_coord("latitude", [40.0, 40.2]), _coord("longitude", [0.0, 0.2]))
    expected = R**2 * np.radians(0.2) * (np.sin(np.radians(40.1)) - np.sin(np.radians(39.9)))

    assert float(area.isel(latitude=0, longitude=0)) == pytest.approx(expected, rel=1e-12)
    # About 22.24 km (0.2 degrees of latitude) x 17.04 km (0.2 degrees of longitude at 40 N).
    assert float(area.isel(latitude=0, longitude=0)) == pytest.approx(378.9, abs=0.1)


def test_cells_shrink_towards_the_pole() -> None:
    area = latlon_cell_area(*_latlon(0.2, 0.2, lat_min=40.0)).isel(longitude=0)

    assert bool((area.diff("latitude") < 0).all())
    assert float(area[-1]) < 1.0  # last row, centred at 89.9 N


def test_cell_centred_on_the_pole_is_a_half_cell() -> None:
    lat = _coord("latitude", [89.5, 89.75, 90.0])
    area = latlon_cell_area(lat, _coord("longitude", np.arange(-180, 180, 0.3)))
    cap = 2 * np.pi * R**2 * (1 - np.sin(np.radians(89.375)))

    assert float(area.sum()) == pytest.approx(cap, rel=1e-9)


def test_descending_latitudes_give_positive_areas() -> None:
    lat = _coord("latitude", [60.0, 59.8, 59.6])

    area = latlon_cell_area(lat, _coord("longitude", [0.0, 0.2]))

    assert bool((area > 0).all())


def test_area_keeps_the_grid_coordinates() -> None:
    lat, lon = _latlon(1.0, 1.0, lat_min=40.0)

    area = latlon_cell_area(lat, lon)

    assert area.dims == ("latitude", "longitude")
    assert area.attrs["units"] == "km2"
    np.testing.assert_array_equal(area["latitude"], lat)


@pytest.mark.parametrize(
    ("lat", "lon", "message"),
    [
        ([40.0, 40.2, 40.5], [0.0, 0.2], "latitude: coordinate spacing is not uniform"),
        ([40.0], [0.0, 0.2], "at least two"),
        ([40.0, 91.0], [0.0, 1.0], r"outside \[-90, 90\]"),
        ([40.0, 41.0], np.arange(0, 400, 1.0), "more than 360"),
    ],
)
def test_irregular_or_invalid_grids_are_rejected(lat, lon, message: str) -> None:
    with pytest.raises(GridError, match=message):
        latlon_cell_area(_coord("latitude", lat), _coord("longitude", lon))


# --- Projected grids ------------------------------------------------------------------


def test_ease2_25km_cells_are_625_km2() -> None:
    # OSI SAF originalGrid: yc descending from 5387.5 km, steps of 25 km.
    yc = _coord("yc", np.arange(5387.5, -5400, -25.0), "km")
    xc = _coord("xc", np.arange(-5387.5, 5400, 25.0), "km")

    area = projected_cell_area(yc, xc, equal_area=True)

    assert area.shape == (432, 432)
    assert bool((area == 625.0).all())


def test_projected_metres_are_converted_to_km2() -> None:
    area = projected_cell_area(
        _coord("y", [0.0, 3000.0], "m"), _coord("x", [0.0, 3000.0, 6000.0], "m"), equal_area=True
    )

    assert float(area[0, 0]) == pytest.approx(9.0)


def test_projection_must_be_declared_equal_area() -> None:
    with pytest.raises(GridError, match="equal-area"):
        projected_cell_area(
            _coord("y", [0, 25], "km"), _coord("x", [0, 25], "km"), equal_area=False
        )


def test_projected_units_must_be_lengths() -> None:
    with pytest.raises(GridError, match="units must be"):
        projected_cell_area(
            _coord("y", [0, 25], "deg"), _coord("x", [0, 25], "km"), equal_area=True
        )


# --- Dispatch ---------------------------------------------------------------------


def test_cell_area_of_a_latlon_dataset() -> None:
    lat, lon = _latlon(1.0, 1.0, lat_min=40.0)
    ds = xr.Dataset(
        {"ice_conc": (("time", "latitude", "longitude"), np.zeros((1, lat.size, lon.size)))},
        coords={"latitude": lat, "longitude": lon},
    )

    assert cell_area(ds).equals(latlon_cell_area(lat, lon))


def test_cell_area_of_a_projected_dataset() -> None:
    ds = xr.Dataset(
        {"ice_conc": (("yc", "xc"), np.zeros((2, 2)))},
        coords={"yc": _coord("yc", [25, 0], "km"), "xc": _coord("xc", [0, 25], "km")},
    )

    assert float(cell_area(ds, equal_area_projection=True)[0, 0]) == 625.0
    with pytest.raises(GridError, match="equal-area"):
        cell_area(ds)


def test_unknown_grid_is_rejected() -> None:
    ds = xr.Dataset({"v": (("row", "col"), np.zeros((2, 2)))})

    with pytest.raises(GridError, match="No known horizontal grid"):
        cell_area(ds)
