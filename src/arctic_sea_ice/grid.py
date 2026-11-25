"""Real surface area of grid cells.

Sea ice extent and area are sums of cell areas (research plan, section 4), so each cell
must carry its true area on the Earth, not a constant.

* Latitude-longitude grids (the ``default`` part of the Copernicus Marine datasets): the
  area of the cell between latitudes phi_s..phi_n and longitudes lambda_w..lambda_e on a
  sphere of radius R is ``R^2 * (lambda_e - lambda_w) * (sin(phi_n) - sin(phi_s))``. Cells
  shrink towards the pole: a 0.2 degree cell covers about 379 km^2 at 40 N and less than
  1 km^2 next to the pole.
* Projected grids (the ``originalGrid`` part): cells of an **equal-area** projection,
  such as EASE-Grid 2.0 North (EPSG:6931) used by OSI SAF, all have the area dx * dy
  (625 km^2 for 25 km cells). Other projections distort areas and are rejected.

The sphere is the authalic sphere of the WGS 84 ellipsoid, whose radius gives the same
total surface as the ellipsoid; it is the standard choice for area computations on a
sphere. Areas are returned in km^2.
"""

from __future__ import annotations

import numpy as np
import xarray as xr

# Radius of the sphere with the same surface area as the WGS 84 ellipsoid.
AUTHALIC_EARTH_RADIUS_KM = 6371.0072

_SPACING_TOLERANCE = 1e-6
_LENGTH_IN_KM = {"km": 1.0, "m": 1e-3}


class GridError(ValueError):
    """The grid cannot be given exact cell areas."""


def _uniform_step(centers: np.ndarray, name: str) -> float:
    if centers.size < 2:
        raise GridError(f"{name}: at least two coordinate values are needed")
    steps = np.diff(centers)
    step = float(steps.mean())
    if not np.allclose(steps, step, rtol=0, atol=_SPACING_TOLERANCE * max(1.0, abs(step))):
        raise GridError(f"{name}: coordinate spacing is not uniform")
    return step


def _cell_edges(centers: np.ndarray, name: str) -> tuple[np.ndarray, np.ndarray]:
    """Lower and upper edge of each cell, halfway between neighbouring centres."""
    half = abs(_uniform_step(centers, name)) / 2
    return centers - half, centers + half


def latlon_cell_area(
    latitude: xr.DataArray,
    longitude: xr.DataArray,
    *,
    radius_km: float = AUTHALIC_EARTH_RADIUS_KM,
) -> xr.DataArray:
    """Cell areas (km^2) of a regular latitude-longitude grid, from the cell centres.

    Edges lie halfway between centres; latitude edges are clipped to +-90 degrees, so a
    cell centred on the pole is a half cell.
    """
    lat = np.asarray(latitude.values, dtype=np.float64)
    lon = np.asarray(longitude.values, dtype=np.float64)
    if np.any(np.abs(lat) > 90):
        raise GridError("latitude: values outside [-90, 90]")

    south, north = (np.clip(e, -90.0, 90.0) for e in _cell_edges(lat, "latitude"))
    band = np.abs(np.sin(np.radians(north)) - np.sin(np.radians(south)))
    width = np.radians(abs(_uniform_step(lon, "longitude")))
    if lon.size * width > 2 * np.pi * (1 + _SPACING_TOLERANCE):
        raise GridError("longitude: cells overlap (more than 360 degrees covered)")

    area = radius_km**2 * width * np.outer(band, np.ones(lon.size))
    return xr.DataArray(
        area,
        coords={latitude.name: latitude, longitude.name: longitude},
        dims=(latitude.name, longitude.name),
        name="cell_area",
        attrs={
            "long_name": "grid cell area",
            "units": "km2",
            "comment": f"Spherical cell area, authalic radius {radius_km} km",
        },
    )


def projected_cell_area(
    y: xr.DataArray,
    x: xr.DataArray,
    *,
    equal_area: bool,
) -> xr.DataArray:
    """Cell areas (km^2) of a regular projected grid with x/y in ``km`` or ``m``.

    ``equal_area`` must be True: only then is dx * dy the true area of every cell.
    """
    if not equal_area:
        raise GridError(
            "Projected cell areas are exact only for equal-area projections "
            "(e.g. EASE-Grid 2.0, EPSG:6931); pass equal_area=True only for those"
        )
    lengths = []
    for coordinate in (x, y):
        units = str(coordinate.attrs.get("units", "")).strip()
        if units not in _LENGTH_IN_KM:
            raise GridError(f"{coordinate.name}: units must be 'km' or 'm', got {units!r}")
        step = _uniform_step(np.asarray(coordinate.values, dtype=np.float64), str(coordinate.name))
        lengths.append(abs(step) * _LENGTH_IN_KM[units])

    area = np.full((y.size, x.size), lengths[0] * lengths[1])
    return xr.DataArray(
        area,
        coords={y.name: y, x.name: x},
        dims=(y.name, x.name),
        name="cell_area",
        attrs={
            "long_name": "grid cell area",
            "units": "km2",
            "comment": "dx * dy of an equal-area projection",
        },
    )


def cell_area(
    ds: xr.Dataset | xr.DataArray, *, equal_area_projection: bool = False
) -> xr.DataArray:
    """Cell areas (km^2) for the horizontal grid of ``ds``.

    Latitude-longitude grids are recognised by their ``latitude`` and ``longitude``
    dimensions; projected grids by ``xc``/``yc`` or ``x``/``y`` dimensions, which also
    require ``equal_area_projection=True``.
    """
    dims = set(ds.dims)
    if {"latitude", "longitude"} <= dims:
        return latlon_cell_area(ds["latitude"], ds["longitude"])
    for y_name, x_name in (("yc", "xc"), ("y", "x")):
        if {y_name, x_name} <= dims:
            return projected_cell_area(ds[y_name], ds[x_name], equal_area=equal_area_projection)
    raise GridError(f"No known horizontal grid in dimensions {sorted(map(str, dims))}")
