# Repository guide

How the repository is organised and how work is done in it. For the science, see
[scientific-scope.md](scientific-scope.md).

## Layout

Current layout:

```text
arctic-sea-ice/
├── .github/workflows/ci.yml   CI: lock check, pre-commit, mypy, pytest
├── configs/data_manifest.yaml generated data manifest (datasets, versions, schema)
├── docs/                      research plan (PDF), scientific scope, this guide
├── src/arctic_sea_ice/        the Python package
├── tests/                     pytest test suite
├── .pre-commit-config.yaml    pre-commit hooks
├── pyproject.toml             project metadata, dependencies, tool configuration
└── uv.lock                    exact versions of all dependencies
```

Planned additions (research plan, section 9), created in the stage that first needs them:

| Path | Contents | Stage |
|---|---|---|
| `configs/` | `regions.geojson` | seasonal anomalies |
| `src/arctic_sea_ice/` | `ingest.py`, `qc.py`, `metrics.py`, `trends.py` | one module per stage |
| `notebooks/` | `01_explore.ipynb`, `02_research.ipynb` | exploration and research |
| `reports/` | `results.md`, `methods.md`, `comparison.csv` | benchmark and final report |
| `reports/figures/` | versioned figures (no raw data) | maps and dashboard |

> The research plan names the package `arctic_ice`. This repository uses
> `arctic_sea_ice`, matching the project name. The module names are unchanged.

## Branch plan

`main` is protected. Each stage is a short-lived branch merged through a pull request
after CI passes. Stages follow the order of their dependencies:

| # | Branch | Goal | Definition of done |
|---|---|---|---|
| 1 | `chore/project-bootstrap` | Initialisation | Installable repo; basic CI working. |
| 2 | `feature/catalog-discovery` | Identify the data | Reproducible data manifest. |
| 3 | `feature/data-ingestion` | Download data | Incremental downloads; no heavy files in Git. |
| 4 | `feature/ice-qc-grid` | Quality control | No-data is never confused with zero. |
| 5 | `feature/ice-indicators` | Physical indicators | Indicators pass tests with known values. |
| 6 | `feature/seasonal-anomalies` | Climatologies | Monthly series and anomalies exported. |
| 7 | `feature/trend-statistics` | Statistics | Trends and uncertainties documented. |
| 8 | `feature/benchmark-nsidc` | External validation | Comparison table with automatic warnings. |
| 9 | `feature/maps-dashboard` | Visualisation | Figures with units, legends and provenance. |
| 10 | `docs/research-report` | Final delivery | Complete report and release v1.0.0. |

The proposed commits for each stage are listed in the research plan, section 7.

## Workflow

1. Create the stage branch from an up-to-date `main`.
2. Make small, atomic, testable commits.
3. Push the branch and open a pull request into `main`.
4. CI must pass before merging.
5. Merge with a consistent strategy (squash or merge commit), then delete the branch.

## Commit messages

Commits follow [Conventional Commits](https://www.conventionalcommits.org/):
`<type>: <short imperative description>`.

| Type | Use for |
|---|---|
| `feat` | New functionality (e.g. a new indicator) |
| `fix` | Bug fix |
| `test` | Adding or changing tests only |
| `docs` | Documentation only |
| `chore` | Maintenance and project setup |
| `build` | Dependencies and packaging (`pyproject.toml`, `uv.lock`) |
| `ci` | CI configuration (`.github/workflows/`) |
| `refactor` | Code change that neither fixes a bug nor adds a feature |

## Dependencies

Dependencies are managed with uv only. Do not use `pip install` in the project environment.

- Runtime dependency: `uv add <package>`
- Development dependency: `uv add --dev <package>`
- Always commit `pyproject.toml` and `uv.lock` together.

Each stage adds only the dependencies it uses. Compatibility with Python 3.14 is
checked before a package is added.

## Quality checks

| Tool | Role | Configuration |
|---|---|---|
| Ruff | Linting and formatting | `[tool.ruff]` in `pyproject.toml` |
| mypy | Static type checking | `[tool.mypy]` in `pyproject.toml` |
| pytest + pytest-cov | Tests and coverage | `[tool.pytest.ini_options]`, `[tool.coverage.*]` |
| pre-commit | Runs file hygiene, Ruff and `uv-lock` hooks | `.pre-commit-config.yaml` |

The commands are listed in the [README](../README.md#development-commands).

CI (`.github/workflows/ci.yml`) runs on every pull request and on pushes to `main`. It
checks that `uv.lock` is in sync, installs the locked dependencies, runs all pre-commit
hooks, runs mypy and runs the tests with coverage.

## Data manifest

`configs/data_manifest.yaml` lists every Arctic dataset of the planned Copernicus Marine
products with its version, parts (`default` latitude-longitude grid and, where published,
`originalGrid` projected grid), coordinates, temporal coverage and variables with units.
It is generated from the live catalogue (`copernicusmarine describe`, no credentials
needed) and is never edited by hand:

```bash
uv run python -m arctic_sea_ice.catalog              # print the product listing
uv run python -m arctic_sea_ice.catalog --manifest   # regenerate configs/data_manifest.yaml
```

The manifest records the toolbox version and the retrieval date. Given the same catalogue,
regenerating it produces the same file, so `git diff` shows exactly what changed upstream.
`tests/test_data_manifest.py` checks the committed manifest offline: planned datasets
present, concentration units, grids and temporal coverage. Known gaps are encoded as
strict `xfail` tests, which fail as soon as the catalogue closes the gap.

## Data policy

- Downloaded data lives under `data/` and `.cache/`, which are ignored by Git.
- The `check-added-large-files` hook rejects files larger than 1 MB.
- Every dataset used is recorded with product ID, dataset ID, version and access date
  (acceptance criterion A).
- Copernicus Marine credentials are never committed. They are stored with
  `copernicusmarine login` or in a local `.env` file, which is ignored by Git.
- Reports and figures are committed; raw and intermediate data are not.

## Scientific integrity

- Results are produced only by running the pipeline on downloaded data; no value is
  written by hand or estimated without computation.
- Published reference values are always attributed to their source and access date.
- Statistical association is not presented as causation (acceptance criterion E).
