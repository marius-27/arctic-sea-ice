"""Validation of the committed data manifest (configs/data_manifest.yaml).

The manifest is generated from the live Copernicus Marine catalogue. These tests read the
committed file, without network access, and check that the datasets planned in the
research plan (section 3) are present with the units, grids and temporal coverage the
next stages rely on. When the manifest is regenerated and the catalogue has changed,
these tests show what changed.
"""

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from arctic_sea_ice.catalog import ARCTIC_ICE_PRODUCTS, is_arctic_dataset, write_manifest

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "configs" / "data_manifest.yaml"

# Research plan, section 3 (dataset IDs as published in the catalogue).
CDR = "OSISAF-GLO-SEAICE_CONC_TIMESERIES-NH-LA-OBS"
ICDR = "OSISAF-GLO-SEAICE_CONC_CONT_TIMESERIES-NH-LA-OBS"
AMSR_CDR = "osisaf_obs-si_glo_phy_sic-north_my_amsr_cdr_P1D-m"
AMSR_ICDR = "osisaf_obs-si_glo_phy_sic-north_my_amsr_icdr_P1D-m"
NEXTSIM_MONTHLY = "cmems_mod_arc_phy_my_nextsim_P1M-m"
PLANNED_DATASETS = {CDR, ICDR, AMSR_CDR, AMSR_ICDR, NEXTSIM_MONTHLY}

CORE_PERIOD_START = date(1979, 1, 1)
CORE_PERIOD_END = date(2025, 12, 31)

CONCENTRATION = "sea_ice_area_fraction"
OSI_SAF_DATASETS = [CDR, ICDR, AMSR_CDR, AMSR_ICDR]


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    with MANIFEST_PATH.open(encoding="utf-8") as f:
        loaded: dict[str, Any] = yaml.safe_load(f)
    return loaded


def _dataset(manifest: dict[str, Any], dataset_id: str) -> dict[str, Any]:
    matches = [d for d in manifest["datasets"] if d["dataset_id"] == dataset_id]
    assert len(matches) == 1, f"{dataset_id} should appear exactly once"
    return matches[0]


def _all_parts(manifest: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [
        (f"{d['dataset_id']}/{v['version']}/{p['part']}", p)
        for d in manifest["datasets"]
        for v in d["versions"]
        for p in v["parts"]
    ]


def _part(manifest: dict[str, Any], dataset_id: str, part: str = "default") -> dict[str, Any]:
    (version,) = _dataset(manifest, dataset_id)["versions"]
    return next(p for p in version["parts"] if p["part"] == part)


def _time(part: dict[str, Any]) -> dict[str, Any]:
    (time,) = [c for c in part["coordinates"] if c["axis"] == "t"]
    return time


def _time_range(manifest: dict[str, Any], dataset_id: str) -> tuple[date, date]:
    time = _time(_part(manifest, dataset_id))
    return date.fromisoformat(time["minimum"]), date.fromisoformat(time["maximum"])


# --- Provenance and completeness ------------------------------------------------


def test_manifest_records_provenance(manifest: dict[str, Any]) -> None:
    assert date.fromisoformat(manifest["retrieved_on"])
    assert manifest["copernicusmarine_version"]


def test_manifest_is_unedited_generator_output(manifest: dict[str, Any], tmp_path: Path) -> None:
    # Rewriting the parsed manifest must give the committed bytes: no hand edits.
    rewritten = tmp_path / "data_manifest.yaml"
    write_manifest(manifest, rewritten)

    assert rewritten.read_bytes() == MANIFEST_PATH.read_bytes()


def test_manifest_covers_planned_products(manifest: dict[str, Any]) -> None:
    assert {d["product_id"] for d in manifest["datasets"]} == set(ARCTIC_ICE_PRODUCTS)


def test_planned_datasets_are_present(manifest: dict[str, Any]) -> None:
    present = {d["dataset_id"] for d in manifest["datasets"]}

    missing = PLANNED_DATASETS - present

    assert not missing, f"Planned datasets missing from the catalogue: {sorted(missing)}"


def test_only_arctic_datasets(manifest: dict[str, Any]) -> None:
    southern = [
        d["dataset_id"] for d in manifest["datasets"] if not is_arctic_dataset(d["dataset_id"])
    ]

    assert not southern


def test_each_dataset_pins_one_version(manifest: dict[str, Any]) -> None:
    for dataset in manifest["datasets"]:
        versions = [v["version"] for v in dataset["versions"]]
        assert len(versions) == 1 and versions[0], dataset["dataset_id"]


# --- Schema: dimensions, grids, units ------------------------------------------


def test_variable_dimensions_are_declared_coordinates(manifest: dict[str, Any]) -> None:
    for name, part in _all_parts(manifest):
        declared = {c["id"] for c in part["coordinates"]}
        for variable in part["variables"]:
            assert set(variable["dimensions"]) <= declared, f"{name}: {variable['name']}"


def test_every_part_has_one_time_axis(manifest: dict[str, Any]) -> None:
    for name, part in _all_parts(manifest):
        times = [c for c in part["coordinates"] if c["axis"] == "t"]
        assert len(times) == 1, name


def test_every_part_lies_in_the_northern_hemisphere(manifest: dict[str, Any]) -> None:
    for name, part in _all_parts(manifest):
        _, lat_min, _, lat_max = part["bbox"]
        assert 0 < lat_min < lat_max <= 90.05, name


def test_grids_have_the_expected_horizontal_coordinates(manifest: dict[str, Any]) -> None:
    for name, part in _all_parts(manifest):
        horizontal = {c["id"]: c for c in part["coordinates"] if c["axis"] in ("x", "y")}
        if part["grid"] == "latitude-longitude":
            assert set(horizontal) == {"latitude", "longitude"}, name
        else:
            # Projected grids are given in km (OSI SAF) or m (neXtSIM).
            assert len(horizontal) == 2, name
            assert {c["units"] for c in horizontal.values()} <= {"km", "m"}, name


def test_every_part_has_one_concentration_variable(manifest: dict[str, Any]) -> None:
    for name, part in _all_parts(manifest):
        concentration = [v for v in part["variables"] if v["standard_name"] == CONCENTRATION]
        assert len(concentration) == 1, name


@pytest.mark.parametrize(
    ("dataset_id", "variable", "units"),
    [(dataset_id, "ice_conc", "%") for dataset_id in OSI_SAF_DATASETS]
    + [(NEXTSIM_MONTHLY, "siconc", "1")],
)
def test_concentration_units(
    manifest: dict[str, Any], dataset_id: str, variable: str, units: str
) -> None:
    # OSI SAF stores percent (0-100), neXtSIM a fraction (0-1): QC must normalise both.
    for part in _dataset(manifest, dataset_id)["versions"][0]["parts"]:
        (concentration,) = [v for v in part["variables"] if v["standard_name"] == CONCENTRATION]
        assert (concentration["name"], concentration["units"]) == (variable, units)


def test_osi_saf_interim_records_offer_the_original_projected_grid(
    manifest: dict[str, Any],
) -> None:
    for dataset_id in (ICDR, AMSR_CDR, AMSR_ICDR):
        grid = _part(manifest, dataset_id, "originalGrid")
        steps = sorted(abs(c["step"]) for c in grid["coordinates"] if c["axis"] in ("x", "y"))
        assert grid["grid"] == "projected"
        assert steps == [25.0, 25.0], dataset_id


# --- Temporal coverage -----------------------------------------------------------


def test_cdr_starts_before_the_core_period(manifest: dict[str, Any]) -> None:
    start, end = _time_range(manifest, CDR)

    assert start <= CORE_PERIOD_START
    assert end == date(2020, 12, 31)


def test_cdr_time_axis_is_irregular(manifest: dict[str, Any]) -> None:
    # Every other day in the SMMR era and a 1987-1988 gap: fewer values than days.
    time = _time(_part(manifest, CDR))
    start, end = _time_range(manifest, CDR)

    assert time["step_days"] is None
    assert 0 < time["count"] < (end - start).days + 1


def test_interim_record_continues_the_cdr_without_gap(manifest: dict[str, Any]) -> None:
    _, cdr_end = _time_range(manifest, CDR)
    icdr_start, _ = _time_range(manifest, ICDR)

    assert icdr_start == cdr_end + timedelta(days=1)
    assert _time(_part(manifest, ICDR))["step_days"] == 1.0


def test_amsr_interim_record_continues_the_amsr_cdr(manifest: dict[str, Any]) -> None:
    _, cdr_end = _time_range(manifest, AMSR_CDR)
    icdr_start, _ = _time_range(manifest, AMSR_ICDR)

    assert icdr_start == cdr_end + timedelta(days=1)


def test_interim_record_covers_the_2025_september_minimum(manifest: dict[str, Any]) -> None:
    _, end = _time_range(manifest, ICDR)

    assert end >= date(2025, 9, 29)


@pytest.mark.xfail(
    reason="ICDR ends 2025-09-29 (catalogue 2026-11-25); Oct-Dec 2025 is missing. "
    "This test starts passing, and fails as XPASS, once the catalogue extends it.",
    strict=True,
)
def test_interim_record_covers_the_whole_core_period(manifest: dict[str, Any]) -> None:
    _, end = _time_range(manifest, ICDR)

    assert end >= CORE_PERIOD_END
