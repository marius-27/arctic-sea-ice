# Data provenance and licences

Where the data used by this project come from, how each download is recorded, and the
licence terms that apply. Acceptance criterion A of the research plan requires every
result to be traceable to the source, version and access date of the data it was
computed from.

## Sources

The series downloaded by the pipeline are configured in
[`configs/ingest.yaml`](../configs/ingest.yaml). Their identifiers and coverage come from
the data manifest, [`configs/data_manifest.yaml`](../configs/data_manifest.yaml), which is
generated from the Copernicus Marine catalogue.

| Series | Copernicus Marine dataset | Version | Original product | Period used |
|---|---|---|---|---|
| `cdr` | `OSISAF-GLO-SEAICE_CONC_TIMESERIES-NH-LA-OBS` | 202003 | EUMETSAT OSI SAF `osi-450-a`, Sea Ice Concentration Climate Data Record v3 (SMMR, SSM/I, SSMIS) | 1979-01-01 to 2020-12-31 |
| `icdr` | `OSISAF-GLO-SEAICE_CONC_CONT_TIMESERIES-NH-LA-OBS` | 202003 | EUMETSAT OSI SAF `osi-430-a`, Interim Climate Data Record v3 (SSMIS) | 2021-01-01 to 2025-09-29 |

Both datasets belong to the Copernicus Marine product
`SEAICE_GLO_SEAICE_L4_REP_OBSERVATIONS_011_009`, DOI
[10.48670/moi-00136](https://doi.org/10.48670/moi-00136). The original OSI SAF records
have their own DOIs:

- CDR `osi-450-a`: [10.15770/EUM_SAF_OSI_0013](https://doi.org/10.15770/EUM_SAF_OSI_0013)
- ICDR `osi-430-a`: [10.15770/EUM_SAF_OSI_0014](https://doi.org/10.15770/EUM_SAF_OSI_0014)

Both series are downloaded from the `default` part (a regular latitude-longitude grid),
with the variables `ice_conc` (sea ice concentration, %) and `status_flag` (bit flags for
land, lakes, filtering and interpolation).

Reference values from the NSIDC Sea Ice Index are not downloaded by the pipeline. They are
imported, with their source and access date, in the `feature/benchmark-nsidc` stage.

## How each download is recorded

Data are downloaded month by month into `data/raw/`, which is ignored by Git:

```text
data/raw/<dataset_id>/<version>/<part>/<YYYY>/<dataset_id>_<YYYY-MM>.nc
data/raw/<dataset_id>/<version>/<part>/<YYYY>/<dataset_id>_<YYYY-MM>.json
```

The JSON file next to each month records:

| Field | Meaning |
|---|---|
| `request` | dataset ID, version, part, variables, first and last day, latitude limit |
| `file`, `size_bytes`, `sha256` | the data file and its SHA-256 checksum |
| `downloaded_at` | UTC time of the download, i.e. the access date |
| `copernicusmarine_version` | version of the Copernicus Marine Toolbox used |

The version and part are pinned in every request, so a new catalogue version is never
downloaded silently. Months whose recorded request no longer matches the configuration
are downloaded again. `--verify` recomputes the checksums of cached months.

The NetCDF files keep the global attributes written by the producer (title, DOI, licence,
algorithm, source sensors, product version), so each file also documents itself.

## Reproducing the download

```bash
uv run copernicusmarine login                  # once; credentials stay outside the repository
uv run python -m arctic_sea_ice.ingest --check-login
uv run python -m arctic_sea_ice.ingest --download
```

The complete download is about 10 GB. For a small subset, limit the period, for example
`--start 2012-09-01 --end 2012-09-30`, or the series, for example `--series icdr`.

## Licences

### Copernicus Marine Service

The products are provided under the
[Copernicus Marine Service licence](https://marine.copernicus.eu/user-corner/service-commitments-and-licence),
free of charge. It allows derived products ("Value Added Products or Derivative Work")
for any purpose and the redistribution of products in their original form. The products
are provided without any warranty as to their quality or accuracy.

Required acknowledgements (licence, article 2.4):

- Figures, tables and other derived products:
  *"Generated using E.U. Copernicus Marine Service Information;
  [https://doi.org/10.48670/moi-00136](https://doi.org/10.48670/moi-00136)"*
- Publications, including the final report of this project:
  *"This study has been conducted using E.U. Copernicus Marine Service Information;
  [https://doi.org/10.48670/moi-00136](https://doi.org/10.48670/moi-00136)"*

### EUMETSAT OSI SAF

The licence attribute of the OSI SAF files states that all intellectual property rights
belong to EUMETSAT, that use is granted to every user free of charge, and that the credit
**"Copyright EUMETSAT"** must be shown under each product shown. EUMETSAT gives no
warranty about the products and does not guarantee their continuity or availability.

### In this repository

- Raw data are never committed: `data/` is ignored by Git and files over 1 MB are
  rejected by a pre-commit hook.
- Every figure and table derived from these data carries both credits: the Copernicus
  Marine sentence with the DOI, and "Copyright EUMETSAT".
- Copernicus Marine credentials are never committed.
