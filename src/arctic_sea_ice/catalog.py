"""Discovery of the Copernicus Marine sea ice products used by the project.

The products come from the research plan (section 3). Their datasets and versions are
read live from the Copernicus Marine catalogue with ``copernicusmarine.describe``, so the
listing always reflects what the catalogue currently publishes. ``describe`` is a public
call and needs no credentials.

Run ``uv run python -m arctic_sea_ice.catalog`` to print the listing.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

# Product IDs from the research plan, section 3. NSIDC is an external reference and is
# not part of the Copernicus catalogue.
SEA_ICE_CONCENTRATION_PRODUCT = "SEAICE_GLO_SEAICE_L4_REP_OBSERVATIONS_011_009"
ARCTIC_REANALYSIS_PRODUCT = "ARCTIC_MULTIYEAR_PHY_ICE_002_016"
ARCTIC_ICE_PRODUCTS: tuple[str, ...] = (
    SEA_ICE_CONCENTRATION_PRODUCT,
    ARCTIC_REANALYSIS_PRODUCT,
)

# Global products also publish Southern Hemisphere datasets ("-SH-" or "south" in the
# dataset ID). They are outside the Arctic domain of this project.
_SOUTHERN_HEMISPHERE = re.compile(r"-SH-|south", re.IGNORECASE)

DescribeFunction = Callable[..., Any]


@dataclass(frozen=True)
class DatasetSummary:
    """One dataset of a product, with the catalogue versions available for it."""

    dataset_id: str
    title: str
    versions: tuple[str, ...]


@dataclass(frozen=True)
class ProductSummary:
    """One Copernicus Marine product and its Arctic datasets."""

    product_id: str
    title: str
    doi: str | None
    processing_level: str | None
    datasets: tuple[DatasetSummary, ...]


def is_arctic_dataset(dataset_id: str) -> bool:
    """Return False for Southern Hemisphere datasets, True otherwise."""
    return _SOUTHERN_HEMISPHERE.search(dataset_id) is None


def _copernicus_describe() -> DescribeFunction:
    # Imported lazily: the toolbox is slow to import and is only needed for live calls.
    import copernicusmarine

    describe: DescribeFunction = copernicusmarine.describe
    return describe


def list_arctic_products(
    product_ids: Iterable[str] = ARCTIC_ICE_PRODUCTS,
    *,
    describe: DescribeFunction | None = None,
) -> list[ProductSummary]:
    """Describe each product and keep only its Arctic datasets.

    ``describe`` defaults to ``copernicusmarine.describe``; tests pass a fake one.
    A product ID unknown to the catalogue raises ``LookupError`` instead of being skipped
    silently, so a renamed or retired product is noticed.
    """
    describe = describe or _copernicus_describe()
    summaries = []
    for product_id in product_ids:
        catalogue = describe(product_id=product_id, disable_progress_bar=True)
        matches = [p for p in catalogue.products if p.product_id == product_id]
        if not matches:
            raise LookupError(f"Product {product_id!r} not found in the Copernicus catalogue")
        product = matches[0]
        datasets = tuple(
            DatasetSummary(
                dataset_id=dataset.dataset_id,
                title=dataset.dataset_name,
                versions=tuple(version.label for version in dataset.versions),
            )
            for dataset in product.datasets
            if is_arctic_dataset(dataset.dataset_id)
        )
        summaries.append(
            ProductSummary(
                product_id=product.product_id,
                title=product.title,
                doi=product.digital_object_identifier,
                processing_level=product.processing_level,
                datasets=datasets,
            )
        )
    return summaries


def format_products(products: Iterable[ProductSummary]) -> str:
    """Render the product listing as plain text."""
    lines = []
    for product in products:
        lines.append(f"{product.product_id}  {product.title}")
        lines.append(f"  DOI: {product.doi or '-'}  level: {product.processing_level or '-'}")
        for dataset in product.datasets:
            versions = ", ".join(dataset.versions) or "-"
            lines.append(f"  - {dataset.dataset_id}  (versions: {versions})")
            lines.append(f"      {dataset.title}")
    return "\n".join(lines)


def main() -> None:
    print(format_products(list_arctic_products()))


if __name__ == "__main__":
    main()
