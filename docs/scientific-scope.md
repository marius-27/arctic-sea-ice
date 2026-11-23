# Scientific scope

This document summarises the scientific design of the project. The authoritative source
is the research plan, [`Proiect_1_Arctic_Sea_Ice_Research_Plan.pdf`](Proiect_1_Arctic_Sea_Ice_Research_Plan.pdf)
(in Romanian). Section numbers below refer to that plan.

> **No results yet.** This document describes what will be computed and how. It contains
> no results of this project. Published reference values (NSIDC) are listed in section 2
> of the research plan and will be imported with their provenance in the
> `feature/benchmark-nsidc` stage.

## 1. Research question and hypotheses

**Central question.** How has Arctic sea ice extent changed over 1979–2025, and to what
extent does the evolution differ between seasons and sub-regions?

2026 is included only as an extension, and only when data coverage and dataset version
are comparable.

| ID | Hypothesis |
|---|---|
| H1 | The mean September extent series shows a downward trend over the full satellite period. |
| H2 | The magnitude of the trend differs between September and March, and between Arctic basins. |
| H3 | The main conclusions remain similar under controls on the concentration threshold, the period and the product used. |

The hypotheses are tested, not assumed: the results may confirm, nuance or partly
contradict them.

## 2. Key definitions

| Term | Definition |
|---|---|
| Sea ice concentration (SIC) | Fraction of a grid cell covered by ice, in `[0, 1]` (some products store `0–100 %`; this is normalised during QC). |
| Sea ice **extent** | Total area of all valid ocean cells with SIC ≥ 15 %: `E(t) = Σ Aᵢ · 1(Cᵢ ≥ 0.15)`. |
| Sea ice **area** | Ice-covered area weighted by concentration: `A_ice(t) = Σ Aᵢ · Cᵢ`. |
| Daily minimum | The lowest value of the **daily** extent series in a year. |
| Monthly mean | The mean of the daily values within a calendar month. |

`Aᵢ` is the real area of grid cell `i` (from the product's projected grid) and `Cᵢ` its
concentration.

Extent and area are different quantities and are reported separately. A daily
minimum must never be compared with a monthly mean.

Missing observations are **not** treated as "no ice". Fill values, flags, land and invalid
cells are masked before any indicator is computed.

## 3. Data products

| Role | Product | Dataset / notes |
|---|---|---|
| Primary long satellite series | `SEAICE_GLO_SEAICE_L4_REP_OBSERVATIONS_011_009` | `OSISAF-GLO-SEAICE_CONC_TIMESERIES-NH-LA-OBS` (climate data record) plus the interim continuation for 2021–2025. Versions are checked in the catalogue. |
| Extension and sensitivity | Same OSI SAF product, AMSR branch | `osisaf_obs-si_glo_phy_sic-north_my_amsr_cdr_P1D-m` and `*_icdr_P1D-m`, from 2002. 2026 availability depends on the dataset. |
| Independent comparison | NSIDC Sea Ice Index | External reference with its own definitions and series; not provided by Copernicus. |
| Model–observation analysis (optional) | `ARCTIC_MULTIYEAR_PHY_ICE_002_016` | `cmems_mod_arc_phy_my_nextsim_P1M-m`. A model reanalysis, **not** an independent satellite observation. |

Dataset identifiers, variables, units and coverage are confirmed with the Copernicus
Marine Toolbox (`copernicusmarine describe`) in the `feature/catalog-discovery` stage
before any download.

### Periods and domain

- **Core period:** 1979–2025, with an explicit continuity check at the transition between
  the climate data record (CDR) and the interim record (ICDR).
- **Optional period:** a separate 2002–2026 series from the AMSR/ICDR family.
  Heterogeneous products are not merged without evaluation.
- **Domain:** the maritime Northern Hemisphere defined by the product grid, excluding
  land and invalid cells.

## 4. Methodology

1. **Inventory.** Record product and dataset IDs, variables, units, calendar, version,
   projection and temporal coverage. Save the metadata.
2. **Ingestion.** Download small temporal subsets (`copernicusmarine.subset`) or open
   datasets remotely. Cache locally with checksums.
3. **Quality control.** Handle fill values and flags, normalise SIC units, apply the
   ocean mask and the real cell areas.
4. **Indicators.** Daily extent and area; monthly means computed from the daily
   indicators; annual minima from the daily series.
5. **Climatology.** Monthly mean and standard deviation for the 1991–2020 baseline.
   Anomaly = observed value − baseline mean for the same month.
6. **Statistical inference.** OLS trend with confidence intervals, Theil–Sen
   estimator, Mann–Kendall test with treatment of autocorrelation. Comparison intervals
   are fixed in advance, not chosen after seeing the results.
7. **Sensitivity.** Concentration thresholds of 10 / 15 / 20 %, different grids and
   masks, missing coverage, aggregation method, and comparison with NSIDC.

## 5. Comparison with published values

The comparison with NSIDC follows these rules (research plan, section 6):

- Compare like with like: same indicator, definition, period, smoothing and coverage.
  A monthly mean is compared with a monthly mean; a daily minimum with a daily minimum.
- Report both the absolute and the percentage difference.
- A trend computed over 1979–2025 is not declared identical to a trend published for
  1979–2026.
- A different Copernicus product is **not** expected to reproduce NSIDC exactly, because
  algorithms, grids and masks differ. Differences are analysed, never adjusted away.

## 6. Acceptance criteria

| ID | Criterion |
|---|---|
| A | All results are computed from data actually downloaded, with source, version and access date recorded. |
| B | The difference between extent and area is explained and tested; units are consistent. |
| C | The final table contains the published benchmark, the project's own value, the period, the definition and the difference. |
| D | Results are reproduced automatically from configuration, with no hidden manual steps. |
| E | The report does not present a statistical relationship as evidence of causality. |

## 7. Risks and mitigation

| Risk | Mitigation |
|---|---|
| CDR → ICDR transition and sensor changes | Versioned metadata; discontinuity tests; separate series where needed. |
| Differences from NSIDC | Harmonised definitions, periods and averaging; analysis of the gap, not arbitrary adjustment. |
| Polar grid, cell areas and fill values | Tested cell areas and masks; visual inspection of maps. |
| Data volume | Yearly/monthly chunking, optional xarray + dask, caching, a small-subset configuration. |

## 8. Citation rule

The final report cites the Copernicus product, the dataset version, the period and the
access date. Values taken from scientific articles or agency analyses are attributed to
their authors.

## References

The full reference list, with links, is at the end of the research plan. Main data
sources:

- Copernicus Marine, OSI SAF Sea Ice Concentration, DOI
  [10.48670/moi-00136](https://doi.org/10.48670/moi-00136) —
  [product page](https://data.marine.copernicus.eu/product/SEAICE_GLO_SEAICE_L4_REP_OBSERVATIONS_011_009/services).
- Copernicus Marine, Arctic Ocean Sea Ice Reanalysis —
  [product page](https://data.marine.copernicus.eu/product/ARCTIC_MULTIYEAR_PHY_ICE_002_016/services).
- Copernicus Climate Change Service, [Sea ice indicator](https://climate.copernicus.eu/climate-indicators/sea-ice)
  (extent vs. area definitions).
- National Snow and Ice Data Center (NSIDC), [Sea Ice Today](https://nsidc.org/sea-ice-today)
  (Sea Ice Index analyses).
