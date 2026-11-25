"""Discovery of the Copernicus Marine sea ice products used by the project.

The products come from the research plan (section 3). Their datasets, versions and
variable schemas are read live from the Copernicus Marine catalogue with
``copernicusmarine.describe``, so the listing and the data manifest always reflect what
the catalogue currently publishes. ``describe`` is a public call and needs no credentials.

Usage::

    uv run python -m arctic_sea_ice.catalog                      # print the listing
    uv run python -m arctic_sea_ice.catalog --manifest configs/data_manifest.yaml
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any

import yaml

# Product IDs from the research plan, section 3. NSIDC is an external reference and is
# not part of the Copernicus catalogue.
SEA_ICE_CONCENTRATION_PRODUCT = "SEAICE_GLO_SEAICE_L4_REP_OBSERVATIONS_011_009"
ARCTIC_REANALYSIS_PRODUCT = "ARCTIC_MULTIYEAR_PHY_ICE_002_016"
ARCTIC_ICE_PRODUCTS: tuple[str, ...] = (
    SEA_ICE_CONCENTRATION_PRODUCT,
    ARCTIC_REANALYSIS_PRODUCT,
)

DEFAULT_MANIFEST_PATH = Path("configs") / "data_manifest.yaml"

# Global products also publish Southern Hemisphere datasets ("-SH-" or "south" in the
# dataset ID). They are outside the Arctic domain of this project.
_SOUTHERN_HEMISPHERE = re.compile(r"-SH-|south", re.IGNORECASE)

# Services that publish the full variable schema (coordinates included). The
# "original-files" service lists variables without coordinates.
_SCHEMA_SERVICES = ("arco-geo-series", "arco-time-series")

# Time coordinates in the catalogue are given in milliseconds since the Unix epoch.
_EPOCH_MILLISECONDS = "milliseconds since 1970-01-01"
_MILLISECONDS_PER_DAY = 86_400_000

# Coordinate values are rounded to remove floating-point noise from the catalogue
# (e.g. 0.1999999999999993); six decimals keep well below the grid resolution.
_DECIMALS = 6

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


def _describe_products(product_ids: Iterable[str], describe: DescribeFunction | None) -> list[Any]:
    """Return the catalogue entry of each product, failing loudly on unknown IDs."""
    describe = describe or _copernicus_describe()
    products = []
    for product_id in product_ids:
        catalogue = describe(product_id=product_id, disable_progress_bar=True)
        matches = [p for p in catalogue.products if p.product_id == product_id]
        if not matches:
            raise LookupError(f"Product {product_id!r} not found in the Copernicus catalogue")
        products.append(matches[0])
    return products


def _arctic_datasets(product: Any) -> list[Any]:
    """Arctic datasets sorted by ID, so the output does not depend on catalogue order."""
    arctic = [d for d in product.datasets if is_arctic_dataset(d.dataset_id)]
    return sorted(arctic, key=lambda d: d.dataset_id)


# --- Product listing ----------------------------------------------------------


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
    return [
        ProductSummary(
            product_id=product.product_id,
            title=product.title,
            doi=product.digital_object_identifier,
            processing_level=product.processing_level,
            datasets=tuple(
                DatasetSummary(
                    dataset_id=dataset.dataset_id,
                    title=dataset.dataset_name,
                    versions=tuple(version.label for version in dataset.versions),
                )
                for dataset in _arctic_datasets(product)
            ),
        )
        for product in _describe_products(product_ids, describe)
    ]


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


# --- Data manifest ------------------------------------------------------------


def _round(value: Any) -> Any:
    return round(value, _DECIMALS) if isinstance(value, float) else value


def _epoch_ms_to_iso(value: Any) -> Any:
    if not isinstance(value, int | float):
        return value
    return datetime.fromtimestamp(value / 1000, tz=UTC).date().isoformat()


def _coordinate_entry(coordinate: Any) -> dict[str, Any]:
    """Summarise one coordinate: range, step and, for listed values, their count.

    Time values in epoch milliseconds are converted to ISO dates and the step to days.
    Coordinates given as an explicit list of values (irregular time axes) report the
    first and last value and the count, with ``step`` set to ``None``.
    """
    values = coordinate.values
    minimum = min(values) if values else coordinate.minimum_value
    maximum = max(values) if values else coordinate.maximum_value
    step = coordinate.step
    units = coordinate.coordinate_unit or None

    if units and units.startswith(_EPOCH_MILLISECONDS):
        minimum, maximum = _epoch_ms_to_iso(minimum), _epoch_ms_to_iso(maximum)
        step_entry: dict[str, Any] = {
            "step_days": None if step is None else _round(step / _MILLISECONDS_PER_DAY)
        }
    else:
        step_entry = {"step": _round(step)}

    return {
        "id": coordinate.coordinate_id,
        "axis": coordinate.axis,
        "units": units,
        "minimum": _round(minimum),
        "maximum": _round(maximum),
        **step_entry,
        "count": len(values) if values else None,
    }


def _schema_service(part: Any) -> Any:
    services = {getattr(s.service_name, "value", s.service_name): s for s in part.services}
    for name in _SCHEMA_SERVICES:
        if name in services:
            return services[name]
    raise LookupError(f"Part {part.name!r} has no service with a variable schema")


def _part_entry(part: Any) -> dict[str, Any]:
    """Describe one part (e.g. ``default`` or ``originalGrid``) of a dataset version.

    All variables of a part share the same coordinates and bounding box in the current
    catalogue; a disagreement raises ``ValueError`` so that it is examined, not hidden.
    """
    service = _schema_service(part)
    coordinates: dict[str, dict[str, Any]] = {}
    bboxes = set()
    variables = []
    for variable in service.variables:
        bboxes.add(tuple(_round(v) for v in variable.bbox or ()))
        for coordinate in variable.coordinates:
            entry = _coordinate_entry(coordinate)
            known = coordinates.setdefault(entry["id"], entry)
            if known != entry:
                raise ValueError(
                    f"Coordinate {entry['id']!r} differs between variables in part {part.name!r}"
                )
        variables.append(
            {
                "name": variable.short_name,
                "standard_name": variable.standard_name or None,
                "units": variable.units or None,
                "dimensions": [c.coordinate_id for c in variable.coordinates],
            }
        )
    if len(bboxes) > 1:
        raise ValueError(f"Variables of part {part.name!r} have different bounding boxes")

    grid = "latitude-longitude" if {"latitude", "longitude"} <= coordinates.keys() else "projected"
    return {
        "part": part.name,
        "grid": grid,
        "bbox": list(bboxes.pop()) if bboxes else None,
        "released_date": part.released_date,
        "retired_date": part.retired_date,
        "arco_updated_date": part.arco_updated_date,
        "metadata_url": part.url_metadata,
        "coordinates": list(coordinates.values()),
        "variables": variables,
    }


def _dataset_entry(product: Any, dataset: Any) -> dict[str, Any]:
    return {
        "product_id": product.product_id,
        "product_doi": product.digital_object_identifier,
        "dataset_id": dataset.dataset_id,
        "title": dataset.dataset_name,
        "versions": [
            {
                "version": version.label,
                "parts": [_part_entry(part) for part in version.parts],
            }
            for version in dataset.versions
        ],
    }


def build_manifest(
    product_ids: Iterable[str] = ARCTIC_ICE_PRODUCTS,
    *,
    describe: DescribeFunction | None = None,
    retrieved_on: date | None = None,
) -> dict[str, Any]:
    """Build the data manifest: every Arctic dataset with its versions and schema.

    The manifest records where the information came from (toolbox version and retrieval
    date, acceptance criterion A). Given the same catalogue and date, the output is
    identical, so regenerating it shows exactly what changed upstream.
    """
    products = _describe_products(product_ids, describe)
    return {
        "source": "Copernicus Marine catalogue (copernicusmarine.describe)",
        "copernicusmarine_version": package_version("copernicusmarine"),
        "retrieved_on": (retrieved_on or datetime.now(UTC).date()).isoformat(),
        "datasets": [
            _dataset_entry(product, dataset)
            for product in products
            for dataset in _arctic_datasets(product)
        ],
    }


class _ManifestDumper(yaml.SafeDumper):
    """Writes lists of plain values on one line (``[time, latitude, longitude]``)."""


def _represent_list(dumper: yaml.SafeDumper, data: list[Any]) -> yaml.Node:
    flat = all(not isinstance(item, dict | list) for item in data)
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=flat)


_ManifestDumper.add_representer(list, _represent_list)


def write_manifest(manifest: dict[str, Any], path: Path) -> None:
    """Write the manifest as YAML, keeping the key order of ``build_manifest``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Generated by: uv run python -m arctic_sea_ice.catalog --manifest\n"
        "# Do not edit by hand; regenerate it instead.\n"
    )
    body = yaml.dump(
        manifest, Dumper=_ManifestDumper, sort_keys=False, allow_unicode=True, width=100
    )
    path.write_text(header + body, encoding="utf-8", newline="\n")


# --- Command line -------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m arctic_sea_ice.catalog",
        description="List the Arctic sea ice products or write the data manifest.",
    )
    parser.add_argument(
        "--manifest",
        nargs="?",
        const=DEFAULT_MANIFEST_PATH,
        type=Path,
        metavar="PATH",
        help=f"write the data manifest (default path: {DEFAULT_MANIFEST_PATH.as_posix()})",
    )
    args = parser.parse_args(argv)

    if args.manifest is None:
        print(format_products(list_arctic_products()))
        return
    manifest = build_manifest()
    write_manifest(manifest, args.manifest)
    print(f"Wrote {len(manifest['datasets'])} datasets to {args.manifest.as_posix()}")


if __name__ == "__main__":
    main()
