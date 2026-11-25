"""Quality control of sea ice concentration fields.

Turns the concentration delivered by Copernicus Marine into a clean fraction in [0, 1]
on the ocean domain of the study, keeping missing observations missing.

Rules (research plan, section 4; checked on downloaded OSI SAF files, 2026-11-25):

* Units are read from the ``units`` attribute: ``%`` (OSI SAF, 0-100) is divided by 100,
  ``1`` (fraction, e.g. neXtSIM) is kept. Values outside the valid range beyond rounding
  noise stop the processing instead of being clipped silently.
* ``status_flag`` is a bit mask. Cells flagged as land (bit 1) or lake (bit 2) are not
  ocean and are excluded. The flags are read for every day, because the producer's
  land and lake masks change between months (lakes are flagged in winter only).
* The study domain is limited to latitudes >= 40 N, the southern edge of the interim
  record (2021 onwards), so that both records cover the same area.
* An ocean cell without a concentration value is **missing**: it stays NaN and is
  counted, never turned into zero (no-data is not open water). Zero concentration set
  by the producer's filters (open water, climatological maximum) is a real zero.
* Values estimated by spatial or temporal interpolation are kept and marked.
"""

from __future__ import annotations

from enum import IntFlag

import numpy as np
import xarray as xr

# Southern edge of the study domain: the interim record starts at 40 N.
DOMAIN_MINIMUM_LATITUDE = 40.0

# Concentrations may exceed 1 by floating-point noise (e.g. 100.00000000000003 %).
RANGE_TOLERANCE = 1e-6

# Stored latitudes carry floating-point noise too: the 1979-2020 record stores 40 N as
# 39.999999999999915. Without a tolerance that whole row would leave the domain in one
# record but not in the other.
LATITUDE_TOLERANCE = 1e-6


class StatusFlag(IntFlag):
    """Bits of the OSI SAF ``status_flag`` variable (``flag_masks`` / ``flag_meanings``)."""

    LAND = 1
    LAKE = 2
    OPEN_WATER_FILTERED = 4
    LAND_SPILL_OVER = 8
    HIGH_T2M = 16
    SPATIAL_INTERP = 32
    TEMPORAL_INTERP = 64
    MAX_ICE_CLIMO = 128


NOT_OCEAN = StatusFlag.LAND | StatusFlag.LAKE
INTERPOLATED = StatusFlag.SPATIAL_INTERP | StatusFlag.TEMPORAL_INTERP


class QCError(ValueError):
    """The data do not satisfy an assumption of the quality control."""


def fraction_scale(units: str | None) -> float:
    """Factor converting a concentration in ``units`` to a fraction in [0, 1]."""
    normalized = (units or "").strip().lower()
    if normalized in {"%", "percent"}:
        return 0.01
    if normalized in {"1", "fraction"}:
        return 1.0
    raise QCError(f"Unknown concentration units {units!r}; expected '%' or '1'")


def _check_range(sic: xr.DataArray) -> None:
    if not bool(sic.notnull().any()):
        return
    low, high = float(sic.min()), float(sic.max())
    if low < -RANGE_TOLERANCE or high > 1 + RANGE_TOLERANCE:
        raise QCError(f"Concentration outside [0, 1] after unit conversion: {low}..{high}")


def _has_flag(flags: xr.DataArray, mask: int) -> xr.DataArray:
    return (flags & mask) != 0


def normalize(
    ds: xr.Dataset,
    *,
    concentration: str = "ice_conc",
    flags: str | None = "status_flag",
    minimum_latitude: float = DOMAIN_MINIMUM_LATITUDE,
) -> xr.Dataset:
    """Normalise the concentration and classify every cell of every day.

    Returns a dataset on the same coordinates with:

    ``sic``
        concentration as a fraction in [0, 1] (float32); NaN outside the ocean domain
        and where the observation is missing.
    ``ocean``
        True for cells in the study domain that are neither land nor lake.
    ``missing``
        True for ocean cells without a valid concentration.
    ``interpolated``
        True for ocean cells whose value was estimated by interpolation.
    """
    if "latitude" not in ds.coords:
        raise QCError("A 'latitude' coordinate is needed to apply the study domain")
    raw = ds[concentration]
    sic = raw.astype("float64") * fraction_scale(raw.attrs.get("units"))
    _check_range(sic)
    sic = sic.clip(0.0, 1.0)

    if flags is not None:
        # A cell without a flag value carries no land/lake information.
        flag_values = ds[flags].fillna(0).astype("int32")
        excluded = _has_flag(flag_values, NOT_OCEAN)
        interpolated = _has_flag(flag_values, INTERPOLATED)
    else:
        excluded = xr.zeros_like(sic, dtype=bool)
        interpolated = xr.zeros_like(sic, dtype=bool)

    in_domain = ds["latitude"] >= minimum_latitude - LATITUDE_TOLERANCE
    ocean = (in_domain & ~excluded).broadcast_like(sic)
    sic = sic.where(ocean)
    missing = ocean & sic.isnull()

    sic = sic.astype("float32")
    sic.attrs = {
        "long_name": "sea ice concentration",
        "standard_name": "sea_ice_area_fraction",
        "units": "1",
        "source_units": str(raw.attrs.get("units")),
        "comment": "NaN: outside the ocean domain or no valid observation (never zero)",
    }
    return xr.Dataset(
        {
            "sic": sic,
            "ocean": ocean,
            "missing": missing,
            "interpolated": ocean & sic.notnull() & interpolated,
        },
        attrs={"minimum_latitude": minimum_latitude},
    )


def daily_summary(normalized: xr.Dataset) -> xr.Dataset:
    """Per-day cell counts: ocean, missing and interpolated cells, and missing fraction."""
    spatial = [d for d in normalized["ocean"].dims if d != "time"]
    ocean = normalized["ocean"].sum(spatial)
    missing = normalized["missing"].sum(spatial)
    return xr.Dataset(
        {
            "ocean_cells": ocean,
            "missing_cells": missing,
            "interpolated_cells": normalized["interpolated"].sum(spatial),
            "missing_fraction": (missing / ocean.where(ocean > 0)).astype(np.float64),
        }
    )
