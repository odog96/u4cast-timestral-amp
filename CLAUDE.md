# u4cast on Cloudera AI with Timestral

## What this project is

A Cloudera AI application that forecasts many time series at once using
Mistral's Timestral model. It is packaged as an AMP (Accelerator for Machine
Learning Projects, Cloudera's format for a project that installs and starts
itself inside Cloudera AI). It will be open source.

It replaces u4cast, a 2021-2022 forecasting web app. The old code is reference
only. Do not copy code from it. See `docs/decisions.md` for what the old app did.

- Owner: Oliver Zarate
- First demo: 27 November 2026
- Demo data: Iowa liquor sales, a retail dataset with many series
- Purpose of the demo: show Cloudera AI plus Timestral as a demand forecasting
  solution, and give Oliver exposure as its creator

## Goal

A demand forecasting team that does not write code can forecast hundreds to
thousands of series and keep track of all of them. The application does the
bookkeeping that is hard to do by hand at that volume:

- **Volume.** Hundreds to thousands of series are forecast at once.
- **Reissued forecasts.** The same data is forecast again as new data arrives,
  for example every month. Forecasts issued at different times overlap: several
  of them predict the same future date. Every forecast is kept, and every
  predicted value can be traced to the forecast that produced it.
- **Time handling.** The application handles dates so the user does not:
  different frequencies, a history window the user can choose, a horizon the
  user can choose, and gaps in the data.
- **Interface quality.** The interface must be top quality: a designed,
  polished product, not a default-looking internal tool. Oliver's reference is
  the quality of his own spending app, with different colors.

The reasons behind every requirement, the open questions and the deferred items
are in this file, which loads with this one:

@docs/decisions.md

## Rules for Claude Code sessions

1. When implementation changes a decision, adds one, or answers an open
   question, add a dated line to the section "Changes made during
   implementation" in `docs/decisions.md`, with what changed and why, and commit
   it in the same commit as the code. Oliver plans in a separate Claude
   conversation that only sees this repo, so anything not written there is lost.
2. Do not add scope beyond the requirements below. If something looks missing,
   add it to "Open questions" in `docs/decisions.md` and ask Oliver.
3. Items marked "proposed" were suggested by Claude and not yet confirmed by
   Oliver. Build them the proposed way, but keep them easy to change.
4. Never commit secrets, access tokens, model weights, generated outputs or
   compiled files.

## Requirements for version one

### Model

- Timestral only, size `mistralai/Timestral-209M`. No ARIMA, Prophet or
  exponential smoothing code.
- Install: `pip install git+https://github.com/mistralai/timestral.git`
  (needs Python 3.10 or later and torch 2.4 or later). There is no PyPI package.
- Load: `TimestralForecaster.from_pretrained("mistralai/Timestral-209M")`.
- The model name is a setting read from an environment variable, so the model
  can be upgraded without a code change.
- Runs on CPU. Do not require a GPU.
- No training and no fine-tuning. The model forecasts from the history passed in
  at prediction time.

### How the model is served

- All forecasting code calls the model through one small interface: a method
  that takes histories and a horizon and returns forecasts. Nothing else in the
  code knows where the model runs.
- Version one has two implementations of that interface:
  1. **Cloudera AI Workbench model.** Timestral is deployed as a model in
     Cloudera AI Workbench, meaning a Python function the workbench serves as a
     web endpoint. An AMP step creates and deploys it through Cloudera AI's
     API. The application calls that endpoint. This is the one the demo uses.
  2. **In the same process.** The model is loaded directly. Used by the first
     build checks, by local runs, and as the fallback if the workbench model
     endpoint cannot carry the volume.
- Leave room for a third implementation that calls Cloudera AI Inference
  Service, Cloudera's separate service for serving models. Do not build it now.
- Send series to the endpoint in batches. Find the workbench model endpoint's
  limits on request size and request time first, and size the batches to fit.

### Data in

- Two sources: an Iceberg table (Iceberg is the table format of Cloudera's
  data lake), and CSV upload.
- The demo data is ingested into an Iceberg table with Spark by an AMP step.
- The user picks the date column, the target column (the value to forecast)
  and the dimension columns (for example store, category, SKU).
- Frequencies: daily, weekly, monthly, quarterly. Nothing finer than daily.
  Detect the frequency from the dates and ask the user to confirm it.
- Each unique combination of dimension values is one series, identified by
  `ts_id`. Build `ts_id` from the dimension values themselves, so a series has
  the same `ts_id` in every forecast. Do not use a running number.
- Put every series on one regular date grid for the frequency. A date with no
  data inside a series' history becomes NaN (the marker for a missing number),
  not zero. Timestral accepts NaN as missing.
- All series in a dataset must have the same history length. A series that
  does not is left out and listed for the user.
- Minimum history per series is a setting. Default: 24 time steps (proposed),
  so two years of monthly data passes. Timestral states no minimum.
- The user can choose the history window, meaning the first and last date of
  history a forecast uses. The default is all of it.

### Forecasting

- Forecast many series per model call. Never loop one call per series.
- The user picks the horizon (the number of future periods to forecast).
- Timestral takes numbers only. This application creates the future dates from
  the dataset's frequency.
- Keep three values per future date: the median as the forecast, and the 0.1
  and 0.9 quantiles as the low and high of the range. A quantile is a value the
  actual is expected to fall below that share of the time. Read the available
  levels from `forecast.quantile_levels`; do not assume them.

### How forecasts are stored

Everything is stored in Iceberg tables. History and forecasts are in separate
tables. The application never writes to a history table.

- **Forecast values table**, one per dataset. One row per forecast, series and
  future date. Columns: `forecast_id`, `ts_id`, the dataset's dimension
  columns, `date`, `forecast`, `low`, `high`, and `step` (how many periods
  after the end of history the row is). New forecasts are appended. Nothing is
  overwritten.
- **Forecasts table.** One row per forecast: `forecast_id`, `forecast_name`,
  the dataset, target column, dimension columns, frequency, the first and last
  date of history used (`history_start_date`, `history_end_date`), horizon,
  model name, `kind`, creation time.
- **Datasets table.** One row per saved dataset: name, source table, column
  roles, frequency.
- Reissuing a forecast creates a new `forecast_id` under the same
  `forecast_name`. Example: 100 SKUs forecast monthly for the next 12 months
  gives 1,200 rows per issue. The issues of January, February and March each
  hold a row for June. `forecast_id` and `history_end_date` tell them apart.
- CSV download of any forecast is an extra option, not the main output.

### Backtesting

- A backtest forecasts from a past cutoff date and compares the result with
  what actually happened. Support several cutoffs: first cutoff, step between
  cutoffs, and horizon. Nothing is retrained.
- Store a backtest as forecasts whose `kind` is `backtest` and whose
  `history_end_date` is the cutoff, in the same tables.
- Report per series and in total: MAE (average absolute error), RMSE (square
  root of the average squared error), MAPE (average absolute error as a
  percent of the actual), and WAPE (sum of absolute errors divided by sum of
  actuals). MAPE cannot be computed when an actual is zero; WAPE can.
- Show one reference comparison that needs no training: repeat the value from
  one season earlier.

### User interface

- A Python web server built with FastAPI (a Python library for building web
  APIs) that also serves a front end built with React (a JavaScript library
  for building web interfaces). One process for the application.
- Interface quality is a goal. See "Goal".
- Screens, in order: add a dataset, pick its columns, create a forecast, explore
  the result, download.
- Explore shows a chart of history, forecast and range, either for the total of
  all series or for one combination of dimension values.
- With many series, give a searchable picker for one series and a table of
  backtest error by series, worst first.
- For a date that several forecasts predicted, show those predictions side by
  side with what actually happened.
- The measure of success Oliver gave: easy and intuitive to use, and the
  application does the manual work of lining up forecasts with periods,
  history and SKUs.

### Platform and packaging

- Runs as a Cloudera AI application. Public cloud first.
- No login, no user accounts, no Django, no Postgres, no PHP. Access control is
  Cloudera AI's own sign-in.
- AMP packaging: a `.project-metadata.yaml` file in the repo root whose steps
  install dependencies, build the front end, download the model weights, ingest
  the demo data, deploy the workbench model, and start the application.
- Downloading the weights from Hugging Face needs a signed-in account. Take the
  access token from an environment variable the user fills in when installing
  the AMP.
- The application must listen on the port Cloudera AI assigns (environment
  variable `CDSW_APP_PORT`, address `127.0.0.1`). Verify this in the target
  environment before relying on it.
- The README and the AMP description must say three things: the Timestral
  weights have their own license from Mistral, separate from this repo's code
  license; each user is responsible for checking and complying with it; and
  this project is a reference example, not something Cloudera runs in
  production.

## Engineering standards

- Python 3.11. Dependencies in `pyproject.toml` with a lock file.
- The forecasting code is a package under `src/` that knows nothing about the
  web server or the front end. The web server calls the package.
- Type hints on public functions. `ruff` for linting and formatting.
  `logging`, not `print`.
- `pytest` tests for data preparation, building the array of series, future
  date generation, backtest cutoffs, metrics and the stored table layout.
  Tests use a stand-in model that returns fixed numbers, so they run without
  downloading weights.
- No file written to a path shared between users or forecasts.

## Order of work

1. The checks below.
2. The forecasting package with its tests.
3. The storage layer and the demo data ingest.
4. The web server and front end.
5. The AMP packaging.

## First build step: check what is not yet verified

Run these and record the results in `docs/decisions.md`:

1. Install Timestral and forecast a small sample on CPU.
2. Print `forecast.quantile_levels` and the output shapes.
3. Confirm NaN inside a history is accepted.
4. Time one call for 500 series and for 5,000 series of about 200 steps.
5. Backtest a few series with only 24 steps of history and look at the error.
6. In Cloudera AI: the request size and time limits of a workbench model
   endpoint, whether the runtime can build a React front end (Node available),
   and how a Spark session reads and writes Iceberg tables.

## Not in version one

Fine-tuning, serving from Cloudera AI Inference Service, anomaly adjustment,
extra predictor columns, on-premises deployment, frequencies finer than daily,
and any model other than Timestral. `docs/decisions.md` says why.
