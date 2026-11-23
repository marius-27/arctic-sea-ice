# Arctic Sea Ice Decline Analyzer

A reproducible Python analysis of Arctic sea ice **extent** and **area** over the
satellite era (core period 1979–2025), built on Copernicus Marine satellite products
and validated against the published NSIDC Sea Ice Index.

The project investigates long-term change, seasonal differences (September vs. March)
and regional differences between Arctic basins. The planned pipeline is:

```text
data discovery → ingestion → quality control → indicators → statistics → maps → report
```

> **Status:** implementation plan. No results have been computed yet. Any number in this
> repository that is not produced by the pipeline is a published reference value and is
> attributed to its source.

## Documentation

| Document | Contents |
|---|---|
| [Scientific scope](docs/scientific-scope.md) | Research question, hypotheses, definitions, data products, methodology, acceptance criteria |
| [Repository guide](docs/repository-guide.md) | Repository layout, branch plan, commit conventions, data policy, CI |
| [Research plan (PDF, Romanian)](docs/Proiect_1_Arctic_Sea_Ice_Research_Plan.pdf) | The original research plan; the primary reference for this project |

## Requirements

- Python 3.14
- [uv](https://docs.astral.sh/uv/) for environment and dependency management

## Getting started

```bash
git clone https://github.com/marius-27/arctic-sea-ice.git
cd arctic-sea-ice
uv sync            # creates .venv and installs the locked dependencies
```

## Development commands

| Task | Command |
|---|---|
| Run the tests | `uv run python -m pytest` |
| Tests with coverage | `uv run python -m pytest --cov` |
| Lint | `uv run ruff check .` |
| Check formatting | `uv run ruff format --check .` |
| Type check | `uv run python -m mypy` |
| All pre-commit hooks | `uv run pre-commit run --all-files` |
| Verify the lock file | `uv lock --check` |

The same checks run in GitHub Actions on every pull request
(see [`.github/workflows/ci.yml`](.github/workflows/ci.yml)).

### Windows note

If Windows Smart App Control (or a similar application control policy) is enabled, it can
block unsigned executables created inside `.venv\Scripts` and the `_ctypes` module of
uv-managed Python builds. Use the `python -m <tool>` form shown above. mypy and
pre-commit need `ctypes` on Windows; if they are blocked locally, they are still verified
in CI. Using the signed Python build from python.org avoids the problem.

## Data

Satellite data is downloaded locally and is **never committed** to Git. See the data policy
in the [repository guide](docs/repository-guide.md#data-policy).
