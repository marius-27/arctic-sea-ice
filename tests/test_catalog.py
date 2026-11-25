"""Tests for product discovery and the data manifest.

No network: ``describe`` is replaced by a fake catalogue that mirrors the structure of
the live Copernicus Marine catalogue (checked 2026-11-25).
"""

from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from arctic_sea_ice import catalog
from arctic_sea_ice.catalog import (
    ARCTIC_ICE_PRODUCTS,
    DatasetSummary,
    ProductSummary,
    build_manifest,
    format_products,
    is_arctic_dataset,
    list_arctic_products,
    write_manifest,
)

EPOCH_MS = "milliseconds since 1970-01-01 00:00:00Z (no leap seconds)"
DAY_MS = 86_400_000.0
JAN_1_2021_MS = 1_609_459_200_000.0


def _coordinate(coordinate_id: str, axis: str, unit: str, **fields: Any) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "minimum_value": None,
        "maximum_value": None,
        "step": None,
        "values": None,
    }
    return SimpleNamespace(
        coordinate_id=coordinate_id, axis=axis, coordinate_unit=unit, **(defaults | fields)
    )


REGULAR_TIME = _coordinate(
    "time",
    "t",
    EPOCH_MS,
    minimum_value=JAN_1_2021_MS,
    maximum_value=JAN_1_2021_MS + 2 * DAY_MS,
    step=DAY_MS,
)
# Irregular axis given as explicit values (as for the 1978-2020 record).
LISTED_TIME = _coordinate("time", "t", EPOCH_MS, values=[JAN_1_2021_MS + 2 * DAY_MS, JAN_1_2021_MS])
LATITUDE = _coordinate(
    "latitude", "y", "", minimum_value=40.0, maximum_value=90.0, step=0.2499999999999993
)
LONGITUDE = _coordinate("longitude", "x", "", minimum_value=-180.0, maximum_value=179.7, step=0.3)
YC = _coordinate("yc", "y", "km", minimum_value=5387.5, maximum_value=-5387.5, step=-25.0)
XC = _coordinate("xc", "x", "km", minimum_value=-5387.5, maximum_value=5387.5, step=25.0)


def _variable(
    name: str, units: str | None, coordinates: list[SimpleNamespace], bbox: list[float]
) -> SimpleNamespace:
    return SimpleNamespace(
        short_name=name,
        standard_name="sea_ice_area_fraction" if name == "ice_conc" else None,
        units=units,
        bbox=bbox,
        coordinates=coordinates,
    )


def _part(name: str, coordinates: list[SimpleNamespace]) -> SimpleNamespace:
    bbox = [-180.0, 40.0, 179.7, 90.0]
    schema = [
        _variable("ice_conc", "%", coordinates, bbox),
        _variable("status_flag", "", coordinates, bbox),
    ]
    return SimpleNamespace(
        name=name,
        released_date=None,
        retired_date=None,
        arco_updated_date="2025-10-15T07:34:20.060Z",
        url_metadata=f"https://example.org/{name}/dataset.stac.json",
        services=[
            # "original-files" lists variables without coordinates; it must be skipped.
            SimpleNamespace(service_name="original-files", variables=[]),
            SimpleNamespace(service_name="arco-geo-series", variables=schema),
            SimpleNamespace(service_name="wmts", variables=[]),
        ],
    )


def _dataset(dataset_id: str, *versions: str, parts: list[SimpleNamespace] | None = None):
    return SimpleNamespace(
        dataset_id=dataset_id,
        dataset_name=f"Title of {dataset_id}",
        versions=[
            SimpleNamespace(label=v, parts=parts or [_part("default", [REGULAR_TIME])])
            for v in versions
        ],
    )


def _product(product_id: str, datasets: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(
        product_id=product_id,
        title=f"Title of {product_id}",
        digital_object_identifier="10.48670/test",
        processing_level="Level 4",
        datasets=datasets,
    )


ICDR_PARTS = [
    _part("default", [REGULAR_TIME, LATITUDE, LONGITUDE]),
    _part("originalGrid", [REGULAR_TIME, YC, XC]),
]

FAKE_CATALOGUE = {
    "SEAICE_GLO_SEAICE_L4_REP_OBSERVATIONS_011_009": [
        _product(
            "SEAICE_GLO_SEAICE_L4_REP_OBSERVATIONS_011_009",
            [
                _dataset(
                    "OSISAF-GLO-SEAICE_CONC_TIMESERIES-NH-LA-OBS",
                    "202003",
                    parts=[_part("default", [LISTED_TIME, LATITUDE, LONGITUDE])],
                ),
                _dataset("OSISAF-GLO-SEAICE_CONC_TIMESERIES-SH-LA-OBS", "202003"),
                _dataset(
                    "OSISAF-GLO-SEAICE_CONC_CONT_TIMESERIES-NH-LA-OBS", "202003", parts=ICDR_PARTS
                ),
                _dataset("osisaf_obs-si_glo_phy_sic-south_my_amsr_cdr_P1D-m", "202603"),
            ],
        )
    ],
    "ARCTIC_MULTIYEAR_PHY_ICE_002_016": [
        _product(
            "ARCTIC_MULTIYEAR_PHY_ICE_002_016",
            [_dataset("cmems_mod_arc_phy_my_nextsim_P1M-m", "202411")],
        )
    ],
}


def fake_describe(*, product_id: str, **_: Any) -> SimpleNamespace:
    return SimpleNamespace(products=FAKE_CATALOGUE.get(product_id, []))


def _manifest() -> dict[str, Any]:
    return build_manifest(describe=fake_describe, retrieved_on=date(2026, 11, 25))


def _dataset_entry(manifest: dict[str, Any], dataset_id: str) -> dict[str, Any]:
    return next(d for d in manifest["datasets"] if d["dataset_id"] == dataset_id)


# --- Product listing ----------------------------------------------------------


def test_planned_products_are_listed() -> None:
    assert ARCTIC_ICE_PRODUCTS == (
        "SEAICE_GLO_SEAICE_L4_REP_OBSERVATIONS_011_009",
        "ARCTIC_MULTIYEAR_PHY_ICE_002_016",
    )


@pytest.mark.parametrize(
    ("dataset_id", "expected"),
    [
        ("OSISAF-GLO-SEAICE_CONC_TIMESERIES-NH-LA-OBS", True),
        ("OSISAF-GLO-SEAICE_CONC_CONT_TIMESERIES-NH-LA-OBS", True),
        ("osisaf_obs-si_glo_phy_sic-north_my_amsr_icdr_P1D-m", True),
        ("cmems_mod_arc_phy_my_nextsim_P1M-m", True),
        ("OSISAF-GLO-SEAICE_CONC_TIMESERIES-SH-LA-OBS", False),
        ("osisaf_obs-si_glo_phy_sic-south_my_amsr_cdr_P1D-m", False),
    ],
)
def test_is_arctic_dataset(dataset_id: str, expected: bool) -> None:
    assert is_arctic_dataset(dataset_id) is expected


def test_list_keeps_only_arctic_datasets_sorted_by_id() -> None:
    products = list_arctic_products(describe=fake_describe)

    assert [p.product_id for p in products] == list(ARCTIC_ICE_PRODUCTS)
    assert [d.dataset_id for d in products[0].datasets] == [
        "OSISAF-GLO-SEAICE_CONC_CONT_TIMESERIES-NH-LA-OBS",
        "OSISAF-GLO-SEAICE_CONC_TIMESERIES-NH-LA-OBS",
    ]
    assert products[1].datasets == (
        DatasetSummary(
            dataset_id="cmems_mod_arc_phy_my_nextsim_P1M-m",
            title="Title of cmems_mod_arc_phy_my_nextsim_P1M-m",
            versions=("202411",),
        ),
    )


def test_list_copies_product_metadata() -> None:
    product = list_arctic_products(["ARCTIC_MULTIYEAR_PHY_ICE_002_016"], describe=fake_describe)[0]

    assert product.title == "Title of ARCTIC_MULTIYEAR_PHY_ICE_002_016"
    assert product.doi == "10.48670/test"
    assert product.processing_level == "Level 4"


def test_unknown_product_raises() -> None:
    with pytest.raises(LookupError, match="NOT_A_PRODUCT"):
        list_arctic_products(["NOT_A_PRODUCT"], describe=fake_describe)


def test_format_products_lists_ids_and_versions() -> None:
    product = ProductSummary(
        product_id="P",
        title="Product title",
        doi=None,
        processing_level=None,
        datasets=(DatasetSummary("D", "Dataset title", ("202003", "202603")),),
    )

    text = format_products([product])

    assert "P  Product title" in text
    assert "DOI: -  level: -" in text
    assert "- D  (versions: 202003, 202603)" in text
    assert "Dataset title" in text


# --- Data manifest ------------------------------------------------------------


def test_manifest_records_provenance() -> None:
    manifest = _manifest()

    assert manifest["retrieved_on"] == "2026-11-25"
    assert manifest["copernicusmarine_version"]
    assert "copernicusmarine.describe" in manifest["source"]


def test_manifest_lists_only_arctic_datasets() -> None:
    ids = [d["dataset_id"] for d in _manifest()["datasets"]]

    assert ids == [
        "OSISAF-GLO-SEAICE_CONC_CONT_TIMESERIES-NH-LA-OBS",
        "OSISAF-GLO-SEAICE_CONC_TIMESERIES-NH-LA-OBS",
        "cmems_mod_arc_phy_my_nextsim_P1M-m",
    ]


def test_written_lists_of_values_stay_on_one_line(tmp_path: Path) -> None:
    path = tmp_path / "manifest.yaml"

    write_manifest(_manifest(), path)

    text = path.read_text(encoding="utf-8")
    assert "dimensions: [time, latitude, longitude]" in text
    assert "bbox: [-180.0, 40.0, 179.7, 90.0]" in text


def test_manifest_keeps_every_part_and_identifies_its_grid() -> None:
    dataset = _dataset_entry(_manifest(), "OSISAF-GLO-SEAICE_CONC_CONT_TIMESERIES-NH-LA-OBS")
    parts = dataset["versions"][0]["parts"]

    assert dataset["product_doi"] == "10.48670/test"
    assert dataset["versions"][0]["version"] == "202003"
    assert [(p["part"], p["grid"]) for p in parts] == [
        ("default", "latitude-longitude"),
        ("originalGrid", "projected"),
    ]
    assert parts[1]["metadata_url"] == "https://example.org/originalGrid/dataset.stac.json"


def test_manifest_variable_schema_comes_from_arco_service() -> None:
    dataset = _dataset_entry(_manifest(), "OSISAF-GLO-SEAICE_CONC_CONT_TIMESERIES-NH-LA-OBS")
    part = dataset["versions"][0]["parts"][0]

    assert part["variables"][0] == {
        "name": "ice_conc",
        "standard_name": "sea_ice_area_fraction",
        "units": "%",
        "dimensions": ["time", "latitude", "longitude"],
    }
    # Empty units in the catalogue are recorded as missing, not as an empty string.
    assert part["variables"][1]["units"] is None
    assert part["bbox"] == [-180.0, 40.0, 179.7, 90.0]


def test_manifest_converts_regular_time_axis_to_dates() -> None:
    part = _dataset_entry(_manifest(), "OSISAF-GLO-SEAICE_CONC_CONT_TIMESERIES-NH-LA-OBS")[
        "versions"
    ][0]["parts"][0]
    time = part["coordinates"][0]

    assert time == {
        "id": "time",
        "axis": "t",
        "units": EPOCH_MS,
        "minimum": "2021-01-01",
        "maximum": "2021-01-03",
        "step_days": 1.0,
        "count": None,
    }


def test_manifest_summarises_listed_time_values() -> None:
    part = _dataset_entry(_manifest(), "OSISAF-GLO-SEAICE_CONC_TIMESERIES-NH-LA-OBS")["versions"][
        0
    ]["parts"][0]
    time = part["coordinates"][0]

    assert (time["minimum"], time["maximum"], time["count"]) == ("2021-01-01", "2021-01-03", 2)
    assert time["step_days"] is None


def test_manifest_rounds_floating_point_noise() -> None:
    part = _dataset_entry(_manifest(), "OSISAF-GLO-SEAICE_CONC_CONT_TIMESERIES-NH-LA-OBS")[
        "versions"
    ][0]["parts"][0]
    latitude = next(c for c in part["coordinates"] if c["id"] == "latitude")

    assert latitude["step"] == 0.25
    assert latitude["units"] is None


def test_manifest_rejects_inconsistent_coordinates() -> None:
    part = _part("default", [REGULAR_TIME])
    other_time = _coordinate("time", "t", EPOCH_MS, minimum_value=0.0, maximum_value=DAY_MS)
    part.services[1].variables[1].coordinates = [other_time]
    product = _product("P", [_dataset("D", "1", parts=[part])])

    with pytest.raises(ValueError, match="'time' differs"):
        build_manifest(["P"], describe=lambda **_: SimpleNamespace(products=[product]))


def test_manifest_requires_a_schema_service() -> None:
    part = _part("default", [REGULAR_TIME])
    part.services = [SimpleNamespace(service_name="original-files", variables=[])]
    product = _product("P", [_dataset("D", "1", parts=[part])])

    with pytest.raises(LookupError, match="no service with a variable schema"):
        build_manifest(["P"], describe=lambda **_: SimpleNamespace(products=[product]))


def test_manifest_is_deterministic(tmp_path: Path) -> None:
    first, second = tmp_path / "a.yaml", tmp_path / "b.yaml"

    write_manifest(_manifest(), first)
    write_manifest(_manifest(), second)

    assert first.read_bytes() == second.read_bytes()


def test_written_manifest_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "configs" / "data_manifest.yaml"
    manifest = _manifest()

    write_manifest(manifest, path)

    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Generated by: uv run python -m arctic_sea_ice.catalog --manifest")
    assert yaml.safe_load(text) == manifest


# --- Command line -------------------------------------------------------------


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(catalog, "_copernicus_describe", lambda: fake_describe)


@pytest.mark.usefixtures("offline")
def test_main_prints_listing(capsys: pytest.CaptureFixture[str]) -> None:
    catalog.main([])

    out = capsys.readouterr().out
    assert "SEAICE_GLO_SEAICE_L4_REP_OBSERVATIONS_011_009" in out
    assert "-SH-" not in out


@pytest.mark.usefixtures("offline")
def test_main_writes_manifest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "manifest.yaml"

    catalog.main(["--manifest", str(path)])

    assert yaml.safe_load(path.read_text(encoding="utf-8"))["datasets"]
    assert "Wrote 3 datasets" in capsys.readouterr().out
