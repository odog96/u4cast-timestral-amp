# u4cast on Cloudera AI with Timestral

## What this project is

A Cloudera AI application that forecasts many time series in one run using
Mistral's Timestral model. It is packaged as an AMP (Accelerator for Machine
Learning Projects, Cloudera's format for a project that installs and starts
itself inside Cloudera AI). It will be open source.

It replaces u4cast, a 2021-2022 forecasting web app. The old code is reference
only. Do not copy code from it. See `docs/decisions.md` for what the old app did.

- Owner: Oliver Zarate
- First demo: 27 November 2026
- Flagship demo: retail demand forecasting with hundreds to thousands of SKUs
- Purpose of the demo: show Cloudera AI plus Timestral as a demand forecasting
  solution, and give Oliver exposure as its creator

## Goal

A demand forecasting team that does not write code can forecast hundreds to
thousands of series and keep track of all of them. The application does the
bookkeeping that is hard to do by hand at that volume:

- **Volume.** Hundreds to thousands of series are forecast in one run.
- **Rolling forecasts.** The same dataset is forecast again as new data
  arrives. Every run is kept. For every predicted value the application
  records which run produced it, which series it belongs to, which date it is
  for, and where that run's history ended.
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
3. Items marked "proposed" below were suggested by Claude and not yet confirmed
   by Oliver. Build them the proposed way, but keep them easy to change.
4. Never commit secrets, model weights, generated outputs or compiled files.

## Requirements for version one

### Model

- Timestral only. No ARIMA, Prophet or exponential smoothing code.
- Install: `pip install git+https://github.com/mistralai/timestral.git`
  (needs Python 3.10 or later and torch 2.4 or later). There is no PyPI package.
- Load: `TimestralForecaster.from_pretrained("mistralai/Timestral-209M")`.
  Sizes on Hugging Face: `Timestral-5M`, `-28M`, `-123M`, `-209M`.
- Model size is a setting read from an environment variable. Proposed default:
  `mistralai/Timestral-209M`.
- Runs on CPU. Do not require a GPU.
- No training and no fine-tuning. The model forecasts from the history passed in
  at prediction time. Timestral's public code is inference only.
- Put the model behind a small interface with one method that takes histories
  and a horizon and returns forecasts, so another model can be added later
  without touching the rest of the code.

### Data in

- Two sources: CSV upload, and reading an Iceberg table. Build CSV first.
- The user picks the date column, the target column (the value to forecast),
  the dimension columns (for example store, category, SKU), and the frequency
  (daily, weekly, monthly or quarterly).
- Each unique combination of dimension values is one series. Each series gets
  an identifier in a column named `ts_id`.
- Put every series on one regular date grid for the chosen frequency. A date
  with no data inside a series' history becomes NaN (the marker for a missing
  number), not zero. Timestral accepts NaN as missing.
- All series in a dataset must have the same history length. A series that does
  not is left out of the run and listed for the user (the "left out and listed"
  behavior is proposed).
- Minimum history per series: 32 time steps (proposed). Timestral states no
  minimum; it reads history in blocks of 32 steps.
- Detect the frequency from the dates and ask the user to confirm it
  (proposed).
- The user can choose the history window, meaning the first and last date of
  history a forecast uses (proposed). The default is all of it.

### Forecasting

- Forecast many series per model call:
  `forecaster.forecast(context, prediction_length=h)` where `context` is an
  array with one row per series. Never loop one call per series.
- The user picks the horizon (the number of future periods to forecast).
- Timestral takes numbers only. This application creates the future dates from
  the dataset's frequency.
- Keep three values per future date: the median as the forecast, and a low and
  a high quantile as the range. A quantile is a value the actual is expected to
  fall below that share of the time. Proposed range: 0.1 and 0.9. Read the
  available levels from `forecast.quantile_levels`; do not assume them.

### Backtesting

- A backtest forecasts from a past cutoff date and compares the result with
  what actually happened. Support several cutoffs: first cutoff, step between
  cutoffs, and horizon.
- A backtest is the same model call with each history cut off at the cutoff
  date. Nothing is retrained.
- Report MAE, RMSE and MAPE per series and in total. MAE is the average
  absolute error, RMSE is the square root of the average squared error, and
  MAPE is the average absolute error as a percent of the actual.
- Also report WAPE, the sum of absolute errors divided by the sum of actuals
  (proposed). MAPE cannot be computed when an actual is zero, which is common
  in retail data.
- Show one reference comparison that needs no training: repeat the value from
  one season earlier (proposed).

### Data out

- One forecast table per run: `ts_id`, the dimension columns, date, forecast,
  low, high, and an identifier for the run.
- Two more columns so rolling forecasts can be lined up (proposed):
  `history_end_date`, the last date of history the run used, and `step`, how
  many periods ahead of that date the row is.
- Write the forecast table to an Iceberg table. CSV download is an extra
  option, not the main output.

### Saved objects

- Dataset: a named input with its column roles and frequency.
- Forecast: a named set of choices that belongs to one dataset: target,
  dimension columns, horizon and history window.
- Run: one execution of a forecast, with its history end date, model size and
  creation time. Running the same forecast again after new data arrives adds a
  run; it does not replace the earlier one (proposed).
- The user can list, open and delete all three.

### User interface

- One application that the AMP installs and starts as a single process.
- Interface quality is a goal, not a nice-to-have. See "Goal".
- The framework is not decided. Do not start interface work until Oliver
  decides it. See open question 1 in `docs/decisions.md`.
- Screens, in order: add a dataset, pick its columns, create a forecast, explore
  the result, download.
- Explore shows a chart of history, forecast and range, either for the total of
  all series or for one combination of dimension values.
- With many series, give a searchable picker for one series and a table of
  backtest error by series, worst first.
- For a date that several runs predicted, show those predictions side by side
  with what actually happened (proposed).
- The measure of success Oliver gave: easy and intuitive to use, and the
  application does the manual work of lining up forecasts with periods,
  history and SKUs.

### Platform and packaging

- Runs as a Cloudera AI application. Public cloud first.
- No login, no user accounts, no Django, no Postgres, no PHP. Access control is
  Cloudera AI's own sign-in.
- AMP packaging: a `.project-metadata.yaml` file in the repo root whose steps
  install dependencies, download the model weights, and start the application.
- The application must listen on the port Cloudera AI assigns (environment
  variable `CDSW_APP_PORT`, address `127.0.0.1`). Verify this in the target
  environment before relying on it.
- The README must say that the Timestral weights have their own license
  (Mistral calls it Modified MIT), separate from this repo's code license, and
  that each user is responsible for complying with it.

## Engineering standards

- Python 3.11. Dependencies in `pyproject.toml` with a lock file.
- The forecasting code is a package under `src/` that knows nothing about the
  user interface. The interface code calls the package.
- Type hints on public functions. `ruff` for linting and formatting.
  `logging`, not `print`.
- `pytest` tests for data preparation, building the array of series, future
  date generation, backtest cutoffs and metrics. Tests use a stand-in model
  that returns fixed numbers, so they run without downloading weights.
- No file written to a path shared between users or runs.

## Order of work

1. The checks below.
2. The forecasting package with its tests. It does not depend on the
   interface framework.
3. The interface, after Oliver decides the framework.

## First build step: check what is not yet verified

Before building features, run these and record the results in
`docs/decisions.md`:

1. Install Timestral and forecast a small sample on CPU.
2. Print `forecast.quantile_levels` and the output shapes.
3. Confirm NaN inside a history is accepted.
4. Time one call for 500 series and for 5,000 series of about 200 steps.

## Not in version one

Fine-tuning, anomaly adjustment, extra predictor columns, on-premises
deployment, and any model other than Timestral. `docs/decisions.md` says why.
