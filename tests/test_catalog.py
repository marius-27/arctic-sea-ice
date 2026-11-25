"""Tests for product discovery. No network: ``describe`` is replaced by a fake."""

from types import SimpleNamespace
from typing import Any

import pytest

from arctic_sea_ice import catalog
from arctic_sea_ice.catalog import (
    ARCTIC_ICE_PRODUCTS,
    DatasetSummary,
    ProductSummary,
    format_products,
    is_arctic_dataset,
    list_arctic_products,
)


def _dataset(dataset_id: str, *versions: str) -> SimpleNamespace:
    return SimpleNamespace(
        dataset_id=dataset_id,
        dataset_name=f"Title of {dataset_id}",
        versions=[SimpleNamespace(label=v) for v in versions],
    )


def _product(product_id: str, datasets: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(
        product_id=product_id,
        title=f"Title of {product_id}",
        digital_object_identifier="10.48670/test",
        processing_level="Level 4",
        datasets=datasets,
    )


# Mirrors the structure returned by the live catalogue (checked 2026-11-25).
FAKE_CATALOGUE = {
    "SEAICE_GLO_SEAICE_L4_REP_OBSERVATIONS_011_009": [
        _product(
            "SEAICE_GLO_SEAICE_L4_REP_OBSERVATIONS_011_009",
            [
                _dataset("OSISAF-GLO-SEAICE_CONC_TIMESERIES-NH-LA-OBS", "202003"),
                _dataset("OSISAF-GLO-SEAICE_CONC_TIMESERIES-SH-LA-OBS", "202003"),
                _dataset("osisaf_obs-si_glo_phy_sic-north_my_amsr_cdr_P1D-m", "202603"),
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


def test_list_keeps_only_arctic_datasets() -> None:
    products = list_arctic_products(describe=fake_describe)

    assert [p.product_id for p in products] == list(ARCTIC_ICE_PRODUCTS)
    assert [d.dataset_id for d in products[0].datasets] == [
        "OSISAF-GLO-SEAICE_CONC_TIMESERIES-NH-LA-OBS",
        "osisaf_obs-si_glo_phy_sic-north_my_amsr_cdr_P1D-m",
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


def test_main_prints_listing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(catalog, "_copernicus_describe", lambda: fake_describe)

    catalog.main()

    out = capsys.readouterr().out
    assert "SEAICE_GLO_SEAICE_L4_REP_OBSERVATIONS_011_009" in out
    assert "-SH-" not in out
