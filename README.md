# u4cast on Cloudera AI with Timestral

A web application that forecasts hundreds to thousands of time series at once
with Mistral's Timestral model, and keeps every forecast in Iceberg tables.
It runs inside Cloudera AI and installs itself as an AMP.

It is made for a demand forecasting team that does not write code. The
application does the bookkeeping that is hard to do by hand at that volume:
which forecast was issued when, which prediction belongs to which date and
which series, and how each forecast compared with what actually happened.

## New to Timestral? Start here

`notebooks/timestral_quickstart.ipynb` sends one series of sales to Timestral
and shows exactly what goes to the model and what comes back. The request is
numbers only: there is not a single date in it. Open it in a session of the
installed project and run it top to bottom. It takes about two minutes.

## Read this first

- **This is a reference example.** It is not something Cloudera runs in
  production.
- **The Timestral weights have their own license from Mistral.** That license
  is separate from the license of this project's code. The install downloads
  the weights from Hugging Face.
- **You are responsible for checking Mistral's license and complying with
  it** before you use the weights.

The code of this project is licensed under Apache 2.0; see `LICENSE`.

## What it does

1. **Add a dataset.** Browse to a table through one of the project's data
   connections (the data lake, or a Hive or Impala virtual warehouse), or
   upload a CSV file. Pick
   the date, the value to forecast and the columns that tell series apart.
   The frequency is detected and you confirm it.
2. **Select a slice.** Filter on any column, choose which columns make one
   series, and choose the history to use. The filter runs in the data lake.
3. **Create a forecast.** Choose how far ahead. All series go to the model
   together. The result is appended to an Iceberg table.
4. **Explore.** A chart of history, forecast and range, for the total or for
   one series found by search.
5. **Reissue.** Forecast the same series again from a later date. Every issue
   is kept, and the issues are shown side by side with what happened.
6. **Check accuracy.** The forecast is made again from past dates and scored
   against the actuals, per series, worst first.
7. **Download** any forecast as CSV, or query it with SQL.
8. **Put a forecast in production.** A Cloudera AI job then issues it again
   on a schedule, from the history as it stands that day. Each run is a new
   issue in the same table, and each issue is scored against the actuals
   as they arrive.

## Install

In Cloudera AI, create a project from this repository as an AMP. The install
asks for three settings:

| Setting | Default | What it is |
| --- | --- | --- |
| `HF_TOKEN` | empty | Optional Hugging Face access token for the weights download |
| `U4CAST_DATABASE` | `u4cast` | The database the application reads the demo data from and writes to |
| `U4CAST_MODEL_NAME` | `mistralai/Timestral-209M` | The Timestral model to serve |

The install then runs six steps:

1. Install the Python packages into a virtual environment, `.venv`.
2. Build the React front end.
3. Download the Timestral weights.
4. Ingest the demo data into Iceberg tables with Spark.
5. Deploy Timestral as a workbench model, `timestral`.
6. Start the application, `u4cast`.

It needs a workbench that can reach Hugging Face, GitHub, the npm registry
and the Iowa Data Hub, with a Spark data connection and the Spark and Hadoop
CLI runtime add-ons. The application and the model each take 4 vCPU and 8 GB.

## The demo data

Iowa liquor sales, 2022 through 2026: orders of spirits by Iowa stores,
published by the **State of Iowa, Department of Revenue, Alcoholic Beverages
Division** on the Iowa Data Hub under the Creative Commons Attribution 4.0
license (CC BY 4.0).

The install sums the order lines into two tables in which every series is
complete and all series have the same length:

- `iowa_liquor_monthly`: bottles, dollars and liters per month, county and
  item, for the county and item pairs that sold in every month.
- `iowa_liquor_weekly`: the same per week and item, statewide, for the items
  that sold in every week.

A total in these tables is a total of the kept series, not of all sales in
Iowa.

Mistral does not name the data Timestral was trained on, so overlap with this
public dataset cannot be ruled out. Keep that in mind when you read the
accuracy figures.

## Where things are stored

Everything is in Iceberg tables in one database:

| Table | One row per |
| --- | --- |
| `forecast_values_<dataset>` | forecast, series and future date |
| `forecasts` | forecast: its name, slice, history dates, horizon, model |
| `datasets` | saved dataset |

New forecasts are appended. Nothing is overwritten, and the application never
writes to a table of history.

## How it is built

| Folder | What is in it |
| --- | --- |
| `src/u4cast/` | The forecasting package: dates, data preparation, the model interface, forecasts, backtests, storage. It knows nothing about the web server. |
| `webapp/` | The web server (FastAPI). It calls the package and serves the front end. |
| `frontend/` | The front end (React, TypeScript). |
| `serving/` | The function Cloudera AI serves as the model endpoint, and the script that deploys it. |
| `ingest/` | The demo data download and the Spark ingest. |
| `amp/` | The six install steps. |
| `production/` | The job that issues a forecast in production, and a growing table to try it on. |
| `notebooks/` | The quick start: one series sent to Timestral, step by step. |
| `docs/decisions.md` | Why each thing is the way it is, with every measurement. |
| `docs/test-drive.md` | A walk through every screen, with what you should see at each step. `docs/sample-sales.csv` is a made-up file for trying the CSV upload. |

The model is called through one small interface. Two implementations exist:
the workbench model endpoint, which the application uses, and Timestral
loaded in the same process, chosen with `U4CAST_SERVING=in_process`.

The new forecast screen offers every workbench model of the project that
serves forecasts for this application. The install deploys one. To add the
smaller Timestral model, run `python3 serving/more_models_job.py` in a
session: it creates and runs a job that deploys it. Only models that are deployed
at the moment are offered.

## Develop

```
uv sync --all-extras --group frontend      # Python packages and Node
.venv/bin/pytest                           # tests; need no Spark and no weights
.venv/bin/ruff check . && .venv/bin/ruff format --check .
cd frontend && npm ci && npm run build     # with .venv/bin on the path
```

Tests marked `spark` run against a real data lake and those marked `weights`
run the real model. Both are skipped unless asked for:
`U4CAST_TEST_SPARK=1 pytest -m spark` and `U4CAST_TEST_WEIGHTS=1 pytest -m weights`.

After changing code in a running project: rebuild the front end if it
changed, then `python3 webapp/deploy_application.py` restarts the application,
and `python3 serving/deploy_workbench_model.py` rebuilds the model.

## Settings

All are environment variables, set on the project.

| Variable | Default | What it does |
| --- | --- | --- |
| `U4CAST_MODEL_NAME` | `mistralai/Timestral-209M` | The model to serve |
| `U4CAST_DATABASE` | `u4cast` | The database to read demo data from and write to |
| `U4CAST_SERVING` | the endpoint, when its key is set | `workbench` or `in_process` |
| `U4CAST_MODEL_ACCESS_KEY` | set by the model deploy step | Which workbench model to call |
| `U4CAST_BATCH_SIZE` | `500` | The most series in one model call |
| `U4CAST_MORE_MODELS` | the 28M model | Which further Timestral models `serving/more_models_job.py` deploys |
| `U4CAST_SPARK_MODE` | `local` | `local` runs Spark in the application's container; `cluster` uses workers |
| `U4CAST_SPARK_CONNECTION` | the project's first | The Spark data connection |
| `U4CAST_SPARK_DRIVER_MEMORY` | `2g` | Memory for Spark in the container |

## Not in this version

Fine-tuning, serving from Cloudera AI Inference Service, extra predictor
columns such as price or holidays, frequencies finer than daily, and models
other than Timestral. `docs/decisions.md` says why.
