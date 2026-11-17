# Decisions, open questions and deferred items

This is the shared record between Oliver's planning conversations and Claude
Code sessions. "Proposed" means Claude suggested it and Oliver has not yet
confirmed it.

Last updated from a planning conversation: 17 November 2026.

## Goal

Modernize u4cast into an open-source Cloudera AI application, packaged as an
AMP, that forecasts many time series at once with Mistral's Timestral model.
First demo: 27 November 2026. The priority is speed to a working deployment.

The user Oliver has in mind is a demand forecasting team that does not write
code. The application's value is that it handles what is hard about hundreds
of series: the volume of forecasts, forecasts reissued over time, which
prediction belongs to which date and which series, flexible time windows, and
different frequencies. The full goal statement is in `CLAUDE.md` under "Goal".

## Decisions made by Oliver (17 November 2026)

1. **Timestral is the only model**, size `mistralai/Timestral-209M`. Reason:
   showcase Cloudera AI with Mistral.
2. **Zero-shot only, no fine-tuning in version one.** Zero-shot means
   forecasting with no training on the user's data. Reason: Timestral's public
   code is inference only. A second edition with fine-tuning through Mistral
   Forge, Mistral's model customization service, is possible later.
3. **No ARIMA anywhere, including the backtest.** This reverses an earlier
   decision the same day to keep ARIMA as a backtest comparison. Reason:
   fitting ARIMA per series is a different and much larger piece of work than a
   Timestral call, and it would add code only to make a comparison.
4. **Drop Django, the login, Postgres and the PHP front end.** Rebuild as one
   Cloudera AI application. Reason: Cloudera AI already handles sign-in, the
   old storage design does not fit thousands of series, and one application
   needs no separate front end.
5. **Input: Iceberg tables, plus CSV.**
6. **Output: Iceberg tables.** CSV download is a side option.
7. **Backtesting against actuals is required.**
8. **All series in a dataset must have the same history length.** Reason: it
   removes the question of how to send unequal-length series in one model call.
9. **Speed is acceptable for now.** The estimate was about one minute for 500
   series if run one at a time on the largest model. It is an estimate from
   Mistral's published figure of 113 ms per forecast on 8 CPU threads, not a
   measurement.
10. **Public cloud first.** On-premises later.
11. **The design should stay general.** Retail is the flagship; IoT and network
    traffic are possible later uses. Work within what Timestral offers.
12. **Licensing of the Timestral weights is each user's responsibility.** The
    README must say so, and must say this project is a reference example that
    Cloudera does not run in production. Oliver will check separately what
    permissions cover building this prototype; Cloudera has a partnership with
    Mistral.
13. **Start in a new repo.** Reason: the old backend repo has a committed
    `.env` file with a database address, a storage bucket name and a Django
    secret key, and this project will be public.
14. **The interface must be top quality, and that is part of the goal.**
    Oliver's reference is the quality of his spending app, with different
    colors.
15. **The goal includes making hundreds of series easy to manage** for a team
    that does not write code.
16. **Interface: a FastAPI web server with a React front end.** Oliver's
    condition: "if you can make it work". This replaces Claude's first
    proposal, Streamlit, whose look can only be customized within limits.
17. **Serve Timestral as a model in Cloudera AI Workbench for now**, deployed
    through Cloudera AI's API. Design the code so the model can be upgraded and
    so it can later be served from Cloudera AI Inference Service.
18. **Demo data: retail with many series, ingested with Spark.** Iowa liquor
    sales is the working choice; Oliver said it does not have to be Iowa. If
    it is Iowa, cap what is pulled into Spark at 1 to 5 GB.
19. **Forecasts must be traceable when they are reissued.** Oliver's example:
    100 SKUs with two years of history, forecast monthly for the next 12
    months, reissued every month, so forecasts overlap. Each series has a
    forecast value for every date in the forecast range, and a forecast
    identifier says which issue a value came from. Oliver left the design to
    Claude. It is in `CLAUDE.md` under "How forecasts are stored".
20. **The Cloudera AI environment can reach Hugging Face.** The download needs
    Oliver's Hugging Face sign-in, so the AMP takes an access token.
21. **Daily is the finest frequency.**
22. **Accepted from Claude's proposals:** leaving out and listing a series that
    fails the equal-length rule; a forecast range from the 0.1 and 0.9
    quantiles; WAPE as an error measure that works when actuals are zero; a
    backtest comparison that repeats the value from one season earlier;
    detecting the frequency and asking the user to confirm; a history window
    the user can choose; keeping every reissue of a forecast; a view of
    several forecasts' predictions for the same date next to what happened.

23. **Minimum history is 32 time steps, enforced.** (Replaced by decision 28.)
    Oliver: "if Timestral
    requires 32 steps, then that is what we enforce - full stop." What is
    known: Timestral's README states no minimum, and a secondary source
    (marktechpost.com) says it reads history in blocks of 32 steps. So 32 is
    not a confirmed requirement of the model. It is enforced until the first
    build checks show what Timestral does with shorter histories. Consequence:
    monthly data needs 32 months, so the two years of monthly history in
    decision 19's example would be rejected. This withdraws a change Claude
    had made to a default of 24 steps.
24. **Minimum bar for volume: 100 series in one forecast.** Request size
    limits of the workbench model endpoint are worked out as the design goes
    on, starting with a test of 100 series.
25. **A forecast is made for a slice of the data, not the whole table.** The
    table in Spark can be gigabytes; what goes to the model is small, for
    example 100 series of 32 steps. The pipeline needs a way to select the
    slice. Oliver left the way to Claude.

26. **The repo's code is licensed under Apache 2.0.** The `LICENSE` file is
    the standard Apache 2.0 text, unchanged. It lets anyone use, change and
    share the code. It does not cover the Timestral weights, which have
    Mistral's own license (decision 12).

27. **Demo data is Iowa liquor sales, 2022 through the latest month.** Oliver
    confirmed after Claude's comparison of candidates. This settles decision
    18. The README must credit the State of Iowa, Department of Revenue.

Decisions 28 to 31 were made by Oliver on 17 November 2026 in a Claude Code
session, after reading the results of first build checks 1 to 5.

28. **Minimum history depends on the frequency, and the user can override
    it.** This replaces decision 23. Oliver: "min of 2 cycles and a min of 32
    observations, but let them override (they know their business better than
    anyone so an override should let them use 32". The message was cut off
    there, so the table below is Claude's reading of it. Oliver confirmed it in
    decision 32.

    | Frequency | Default minimum | With the user's override |
    | --- | --- | --- |
    | Monthly | 24 steps (two years) | no change |
    | Quarterly | 8 steps (two years) | no change |
    | Weekly | 104 steps (two years) | can go down to 32 |
    | Daily | 32 steps | no change |

    Reason: check 5 showed that 32 steps is not a requirement of Timestral,
    and that error depends on whether the history holds two seasonal cycles.
    Two years of monthly history, as in decision 19's example, is now
    accepted. The override exists because the user knows whether their weekly
    data has a yearly pattern worth waiting two years for.
29. **The Hugging Face access token is optional.** This changes decision 20.
    Reason: the weights downloaded with no sign-in. The AMP keeps the field
    because Hugging Face gives lower rate limits to requests without a token.
30. **Python 3.12.** `CLAUDE.md` said 3.11. Reason: the Cloudera AI runtime in
    use has 3.12, and Timestral works on it.
31. **Resource profile: 4 vCPU and 8 GB of memory, for the application and for
    the workbench model.** Oliver asked whether that is plenty and asked
    Claude to recommend. Claude's answer: yes. The loaded model needs about 1
    GB and stays under 3 GB with batches of 500 series, and 500 series of 200
    steps took 11.5 seconds on 4 threads. To be looked at again if the
    application runs Spark in the same container.

Decisions 32 to 34 were made by Oliver on 17 November 2026 in the same Claude
Code session, after the forecasting package was built.

32. **The table in decision 28 is confirmed.** Weekly data needs 104 steps by
    default, and the user's override can lower that to 32. The override
    applies to weekly data only.
33. **The demo ingest writes weekly totals per store and item.** The
    application does not change the frequency of a dataset in version one:
    the rows of a dataset must already be daily, weekly, monthly or
    quarterly. Reason: Iowa liquor sales is one row per order line, and the
    forecasting package works on a regular frequency.
34. **The default history window stays all of the history, and the interface
    shows how many series each choice of start date keeps.** Reason: with
    series that start at different dates, the default leaves out every
    series that starts later than the earliest one. The user moves the start
    date; the rule that all series cover the whole window does not change.
    (Decision 35 has since replaced that rule. What the interface shows is
    now how many series have the minimum history inside the window.)

Decision 35 was made by Oliver on 17 November 2026 in the same Claude Code
session, after seeing how many series the demo data lost.

35. **The user chooses how a date with no row is filled, and series in one
    forecast can have different lengths.** This replaces decision 8 and the
    rule that such a date is always missing. Oliver: "yes lets ask user how
    to fill in - 0, average, or some other interpolation technique."
    Reason: on the Iowa data the old rules kept 29% of stores and 0.2% of
    store-by-item series, because in order data a week with no row means
    nothing was ordered. The choices and their exact meaning are Claude's
    and are listed under "Proposed by Claude".

## Proposed by Claude, not yet confirmed by Oliver

- How a slice is selected: the user filters on dimension values and picks a
  history window; the filter runs in the query, so only the slice is loaded;
  the filter is saved with the forecast, so a reissue uses the same slice.
- A second way to run the model, loaded in the same process as the
  application, as the fallback if the workbench model endpoint cannot carry
  the volume.
- `ts_id` built from the dimension values, so a series keeps the same
  identifier in every forecast.
- Backtests stored in the same tables as forecasts, marked by a `kind` column.
- One forecast values table per dataset, because each dataset has different
  dimension columns.
- Send series to the model in batches of 500 as the starting size. The first
  build checks showed batch size does not change speed or results. The final
  size depends on the endpoint limits found in check 6.
- Keep Timestral's two default settings: forecasts are floored at zero for a
  series whose history has no negative value, and each forecast is averaged
  with a mirrored second pass. See "First build checks 1 to 5", check 2.
- Leave out and list a series that has no values at all in the chosen history
  window. Timestral returns a flat zero forecast for it with no error.

The following were chosen while building the forecasting package on 17
November 2026. Each is a small, separate piece of code and easy to change.

- **Form of `ts_id`:** `column=value` for each dimension column, joined by
  `|`, for example `store=2633|category=VODKA`. A dataset with no dimension
  columns has one series called `total`.
- **What "the same history length" means in practice:** the history window
  runs from the first to the last date that has data in the slice. A series
  is kept only if it has a value on both the first and the last date of the
  window. Dates in between with no data become NaN.
- **The date grid follows the dataset's own pattern:** its usual weekday for
  weekly data, and for monthly and quarterly data either the last day of the
  month or its usual day of the month. A row whose date is off that grid is
  dropped and counted, and the count is reported with the forecast.
- **Rows of the same series and date are summed.** This matches summing up to
  the chosen dimension columns.
- **Season lengths for the reference comparison:** 7 steps for daily data, 52
  for weekly, 12 for monthly, 4 for quarterly.
- **Backtest details:** every cutoff uses all history from the start of the
  window to the cutoff. A cutoff is used only if a full horizon of actuals
  follows it. A date with no actual is not scored.
- **MAPE and WAPE are stored as ratios,** for example 0.138 for 13.8%. The
  interface shows them as percents.
- **If the model does not offer the 0.1 or 0.9 quantile,** the nearest level
  it offers is used.
- **`forecast_id` is a random identifier** (a UUID). Creation time is in UTC.
- **In the forecasts and datasets tables, the list of dimension columns and
  the slice filter are stored as JSON text.** The storage layer may change
  this when the tables are created.
- **Setting names:** `U4CAST_MODEL_NAME` and `U4CAST_BATCH_SIZE`.
- **Tooling:** `uv` for the lock file (`uv.lock`). torch and Timestral are an
  optional extra called `model`, so the tests install and run without them.
  torch comes from the CPU-only index.

The following come from first build check 6 on 17 November 2026.

- **The application reads and writes Iceberg tables through one Spark
  session,** taken from the project's Spark data connection, started when
  the application starts and kept open. Reason: starting a session takes 20
  seconds and the first query another 38, but a query on a warm session takes
  1 to 4 seconds. The name of the data connection is a setting.
- **The AMP installs Node through pip** (`nodejs-wheel`) and builds the React
  front end in an install step.
- **The workbench model keeps authentication on.** The application sends its
  Cloudera AI API key with each request. Reason: an endpoint with no check
  on who is calling is one of the things not to repeat from the old code.
- **The model image uses the runtime's own numpy and pandas** and adds only
  torch, Timestral and this project. Reason: replacing packages the runtime
  itself runs on is a risk with no benefit.
- **A request to the endpoint holds at most 2,000 series-blocks,** meaning
  series multiplied by the blocks of 32 steps in history and horizon, and at
  most 500 series. That is about 5 seconds of model time on 4 vCPU, a sixth
  of the 30-second limit. This replaces a fixed batch of 500 series for the
  endpoint. Reason: a request that reaches the limit blocks the replica.
- **One replica of the model.** Requests are sent one after the other. When
  the model says it is busy, the caller waits and tries again, up to twice.

The following were chosen while building the storage layer and the ingest on
17 November 2026.

- **One database holds everything the application writes.** Its name is the
  setting `U4CAST_DATABASE`, `u4cast` by default. In it: `forecasts`,
  `datasets`, and one `forecast_values_<dataset>` table per dataset.
- **A forecast values table has all the dimension columns of its dataset, as
  text.** A forecast summed up to fewer columns leaves the others empty.
- **A forecast's values are written before its row in the forecasts table,**
  so a forecast that is listed always has its values.
- **An uploaded CSV becomes a new table.** The application refuses a table
  name that exists, so it never changes history that is already there.
- **Spark is capped at 8 workers, 2 GB for the driver and 2 GB per worker.**

The following are the details of decision 35, chosen while building it on 17
November 2026.

- **Five choices for a date with no row,** made once per dataset when it is
  added:
  - missing: it stays missing and Timestral works around it;
  - zero: nothing happened, as in sales and orders;
  - average: the average of the series' known values in the history window;
  - interpolate: a straight line between the known values before and after
    it, and the last known value repeated after the last one;
  - previous: the last known value repeated.
- **The fill applies from a series' first row to the end of the history
  window.** Before its first row a series is always missing, because it did
  not exist yet. So a store that opened in 2024 is not given zeros for 2022.
- **A series is kept if it has the minimum history from its first row to the
  end of the window.** It no longer needs a value on the first or the last
  date. Consequence: a series whose rows stopped long ago is still forecast.
  With zero that gives a forecast near zero, which is right for a store that
  closed. With the other choices the forecast rests on old values.
- **In a backtest the fill uses only what was known at the cutoff.** An
  average or a straight line computed over the whole history would hand the
  model values from after the cutoff.
- **In a backtest a date with no row is scored only when it means zero.**
  Then zero is what actually happened. With the other choices the filled
  value is a guess, not an actual, and the date is not scored.
- **In a backtest a series that starts late is forecast only from the
  cutoffs by which it has the minimum history.**
- **The choice is stored with the dataset and again with every forecast,** in
  a column `fill` of the datasets and forecasts tables, so each forecast
  says how its history was prepared. A reissue must use the same choice.
- **Iowa liquor sales is set to zero.**
- **A table made by an earlier version gets new columns added,** not
  recreated. That is how the existing tables got the `fill` column.

## Open questions

1. **Who is named as the copyright owner?** Apache 2.0 asks for a short
   notice at the top of source files that names the owner: Oliver, or
   Cloudera. Not set yet. Oliver to check whether Cloudera has a rule for
   work published by an employee. Until then, do not add copyright notices
   to source files.
2. **Unknowns about the Iowa data.** Answered by the ingest: 2,705 stores,
   9,440 items, 2.83 GB. See "Storage layer and demo data: results".
3. **How does the running application read and write Iceberg tables?**
   Answered by check 6, as a proposal: with one Spark session that the
   application starts once and keeps. See "Proposed by Claude".
4. **Can a workbench model endpoint carry the volume?** Answered by check 6:
   yes. One replica forecast 5,000 series of 200 steps in 105 seconds. The
   limits are 30 seconds and 5 MB for a request, and one request at a time.
   More volume means more replicas and requests sent side by side, which is
   not built.
5. **How is the React front end built during the AMP install?** Answered by
   check 6, as a proposal: the runtime has no Node, so an install step
   installs it through pip and then builds. See "Proposed by Claude".
6. **Do the minimum history figures of decision 28 hold on real data?**
   Answered: yes, they can stand. See "Storage layer and demo data: results".
7. **What does a date with no row mean, and must a series cover the whole
   window?** Answered by decision 35: the user chooses, and series can have
   different lengths.
8. **Add pyarrow to speed up loading slices?** Moving rows from Spark to
   pandas takes most of the 4 to 14 seconds a slice needs. pyarrow is the
   standard way to speed that up and is not in the runtime. Claude's
   recommendation: add it as a dependency and measure.

## Demo dataset comparison (17 November 2026)

What was read: the Iowa Data Hub and data.gov catalog pages, the Kaggle rules
page for the M5 competition, the fev-bench paper's task table, GIFT-Eval
dataset tables quoted in other papers, the Hugging Face page for
FreshRetailNet-50K and the UCI page for Online Retail II. Not read: the list
of datasets in the TIME benchmark, and the Kaggle rules for Favorita,
Rossmann and Walmart.

- **Iowa liquor sales (chosen, decision 27).** Orders of spirits by Iowa stores, by
  product and date, from January 2012, updated monthly. License: CC BY 4.0,
  which allows any use, including commercial, if the source is credited.
  About 2.4 to 2.6 million rows per year (2018: 2,355,558; 2022: 2,564,565;
  2024: 2,590,975; 2025: 2,490,620; 2026 through August: 1,604,208). A January
  2023 copy of the full table had 31,339,341 rows and 24 columns. Not in the
  retail tasks of fev-bench or the sales datasets of GIFT-Eval.
  Download: `https://idh-be.iowa.gov/api/v1/datasets/<id>/rows.csv`, with id
  1259 for 2022, 1260 for 2023, 1261 for 2024, 1262 for 2025 and 1263 for
  2026. All five were confirmed on 17 November 2026. The address returns a
  zip archive of CSV parts, not a CSV.
  Size estimate, not a measurement: an older description gives 800 MB for 3
  million rows, so about 0.7 GB per year and about 3 GB for 2022 through 2026.
- **M5 (Walmart).** 30,490 item-by-store daily series over 1,941 days. Ruled
  out: the Kaggle rules allow non-commercial use only, and it is one of the
  fev-bench tasks Timestral reports scores on.
- **Favorita, Rossmann, Walmart store sales, Rohlik.** All are fev-bench
  tasks, so a demo on them repeats the benchmark. Their license terms were
  not checked.
- **FreshRetailNet-50K.** 50,000 store-by-product series from a Chinese fresh
  grocery retailer, CC BY 4.0. Only 90 days of history and the sales values
  are normalized, so it cannot show yearly patterns or monthly reissues.
- **UCI Online Retail II.** 1,067,371 transaction rows from a UK gift
  retailer, December 2009 to December 2011, 43.5 MB. The UCI page says CC BY
  4.0; an R package that bundles it says non-commercial use only. Too small
  for the Spark ingest to mean anything.

Because Mistral does not name the real data Timestral was trained on,
overlap with any public dataset cannot be ruled out. Say so if the demo
reports accuracy.

## Deferred items

- **Fine-tuning.** Deferred because Timestral's public code does not include
  it. Comes back as a possible second edition using Mistral Forge.
- **Serving from Cloudera AI Inference Service.** Deferred because a workbench
  model is enough for the demo. Comes back when the model is upgraded or the
  volume outgrows the workbench endpoint.
- **Anomaly adjustment** (replace out-of-range values in a window after a date
  the user gives, then forecast from the corrected history). Deferred because
  it was already switched off in the 2022 code and Oliver did not ask for it.
  Comes back if a demo needs a known shock, such as 2020, taken out of history.
- **Extra predictor columns** such as price or holidays. Timestral accepts
  them. Deferred to keep version one small. Comes back when a dataset with
  such columns is chosen.
- **On-premises deployment.** Deferred because public cloud is first. Comes
  back after the 27 November demo.
- **Frequencies finer than daily.** Deferred by decision 21. Comes back with
  an IoT or network traffic use.
- **Other models as comparisons.** Deferred with decision 3. Comes back only if
  Oliver asks for a model comparison.
- **Unequal history lengths in one dataset.** Deferred by decision 8. Comes
  back if real datasets fail the equal-length rule too often.

## What is known about Timestral, and from where

From Mistral's launch post (https://mistral.ai/news/timestral/):

- Four sizes: 5M, 28M, 123M and 209M parameters.
- No GPU needed. One forecast took 17 ms on the 5M model and 113 ms on the
  209M model, on 8 CPU threads, for three weeks of 15-minute history.
- Fine-tuning is offered through Mistral Forge.
- Supports missing values and extra predictor columns.

From the README (https://github.com/mistralai/timestral):

- Inference only. Needs PyTorch, NumPy, safetensors and huggingface_hub.
- `forecaster.forecast(context, prediction_length=96)`. `context` can have
  shape (series, variates, time), (series, time) or (time,).
- NaN marks a missing value.
- History is capped at 32,768 steps; the most recent steps are kept. No
  minimum is stated.
- Horizons over 1,024 steps are produced block by block.
- Output: 21 quantile levels from 0.01 to 0.99. `forecast.median`,
  `forecast.quantile(0.9)`, `forecast.quantile_levels`.

From secondary write-ups, not confirmed against the license file itself:

- The weights license withdraws permission from a company whose global revenue
  passed $20 million in the preceding month, unless it has a commercial license.
  Sources: dev.to (gabrielanhaia) and byteiota.com.
- History is read in blocks of 32 time steps. Source: marktechpost.com.

Verified by running it on 17 November 2026 (see "First build checks 1 to 5"):
NaN inside a history, speed for hundreds and thousands of series, the exact
quantile levels, and what happens with a history shorter than 32 steps.

Verified on 17 November 2026 by check 6: 100 series fit in one request to a
workbench model endpoint.

Not yet verified by running anything:

- The time of one call of 5,000 series. It was timed only as batches.
- The terms of the weights license. The download brings no license file.

## First build checks 1 to 5: results (17 November 2026)

Run by Claude Code on CPU, in a Cloudera AI session of this project. Check 6
(workbench model endpoint, Node, Spark and Iceberg) has not been run.

**Where it ran.** Session profile: 2 vCPU and 3,933 MB of memory. Runtime
2026.08, Python 3.12.13, JupyterLab, Nvidia GPU edition; no GPU was used.
Processor: Intel Xeon Platinum 8175M. The session can see 16 processor threads
and has no hard processor cap, so torch used 8 threads by default and got
them. Timings on 8 threads therefore depend on the host being idle. Timings on
2 threads are what the profile guarantees.

**Versions.** timestral 1.0.0 (commit `a534bdb`), torch 2.15.0 CPU build,
numpy 2.5.5, huggingface_hub 2.4.0, safetensors 0.8.0. Weights:
`mistralai/Timestral-209M`, snapshot `9874d25`, one file of 837 MB,
209,312,256 parameters.

**Test data.** Synthetic series made for these checks: a level, a slow trend,
one repeating cycle and 15% random noise, never negative. No real data was
used, so the error figures in check 5 show how error changes with history
length, not how accurate Timestral is on retail data.

### Check 1: install and forecast a small sample. Passed.

- The install command in `CLAUDE.md` works. torch was installed first from
  the CPU-only index, `https://download.pytorch.org/whl/cpu`. Installing into
  a virtual environment on the project disk took 7 minutes and 1.1 GB.
- pip in this runtime installs to the user's folder by default. Inside a
  virtual environment that fails until `PIP_USER=0` is set.
- The weights downloaded with no Hugging Face token. The first load took 13
  seconds including the download; later loads take 2 to 4 seconds.
- The download cache is `~/.cache/huggingface`, which is inside the project
  folder `/home/cdsw`. It has to be kept out of git.
- The download brings two files, `config.json` and `model.safetensors`, and
  no license file. The timestral Python package declares Apache-2.0 for its
  code; that says nothing about the weights.
- 5 series of 48 steps, horizon 12: the forecast follows the cycle in the
  history, and low is at or below the median, which is at or below high, at
  every date.
- The same call twice gives identical numbers. A series forecast alone and
  inside a batch gives the same numbers (difference of 1 part in 10 million).
  So splitting series into batches does not change any forecast.

### Check 2: quantile levels and output shapes

- `forecast.quantile_levels` has 21 levels: 0.01, then 0.05 to 0.95 in steps
  of 0.05, then 0.99. The levels 0.1, 0.5 and 0.9 are among them.
- `forecast.quantile(level)` raises `ValueError` for a level not in the list.
  It does not interpolate between levels.
- Output always has four dimensions: (series, variates, horizon, 21).

  | Input shape | `forecast.quantiles` | `forecast.median` |
  | --- | --- | --- |
  | (5, 48) | (5, 1, 12, 21) | (5, 1, 12) |
  | (48,) | (1, 1, 12, 21) | (1, 1, 12) |
  | (2, 3, 48) | (2, 3, 12, 21) | (2, 3, 12) |

- Output is a torch tensor of 32-bit floats, whatever number type goes in.
- **Independent series must go in as (series, time).** The three-dimensional
  form treats the variates of one series as related and forecasts them
  together. Passing 5 unrelated series that way changed their medians by up
  to 5.7%.
- The model's configuration confirms what was secondary before: history is
  read in blocks of 32 steps, the history cap is 32,768 steps, and one pass
  produces up to 1,024 steps of horizon.
- Two settings are on by default. `clip_nonnegative` floors the forecast at
  zero for a series whose history has no negative value. On 200 series with
  67% zeros, no forecast value was below zero; with the setting off, 83% of
  the low values and 20% of the medians were below zero.
  `symmetric_averaging` runs the model a second time on the mirrored history
  and averages the two. Turning it off halves the time (3.4 against 6.6
  seconds for 500 series of 200 steps). Its effect on accuracy was not
  measured.

### Check 3: NaN inside a history. Passed.

- NaN anywhere in a history runs and gives finite forecasts. The other series
  in the same call are not affected at all.
- How far the median forecast moved from the forecast on the complete
  history, for one series of 64 steps:

  | Missing values | Median moved by |
  | --- | --- |
  | 1 value | 0.5% |
  | 6 in a row | 2.4% |
  | 25% of values, scattered | 1.7% |
  | The first 10 | 2.0% |
  | The last 3 | 2.0% |
  | The last 32 | 2.9% |
  | All but one | 31% |

- NaN is much better than zero. On 200 series of 96 steps with values removed
  at random, marking them NaN moved the median by 1.0%, 2.6% and 5.8% for 5%,
  20% and 50% removed. Filling them with zero moved it by 2.6%, 10.3% and
  70.8%.
- An infinite value is treated as missing, the same as NaN.
- **A series that is all NaN does not fail.** It returns zero for every date
  and every quantile, with no error or warning. The application has to catch
  such a series itself.
- A history of zero steps raises an error.

### Check 4: timing

Wall-clock seconds for one `forecast` call with default settings, median of 2
to 5 runs, model already loaded.

| Series × steps | Horizon | Threads | Seconds |
| --- | --- | --- | --- |
| 1 × 32 | 12 | 8 | 0.06 |
| 100 × 32 | 12 | 8 | 0.45 |
| 100 × 32 | 12 | 2 | 1.17 |
| 100 × 32 | 52 | 8 | 0.63 |
| 100 × 200 | 12 | 8 | 1.21 |
| 500 × 200 | 12 | 1 | 39.1 |
| 500 × 200 | 12 | 2 | 20.9 |
| 500 × 200 | 12 | 4 | 11.5 |
| 500 × 200 | 12 | 8 | 6.6 |
| 500 × 200 | 12 | 16 | 7.8 |
| 500 × 200 | 52 | 8 | 8.0 |
| 500 × 200 | 365 | 8 | 17.0 |
| 500 × 730 | 30 | 8 | 24.9 |
| 1,000 × 200 | 12 | 8 | 13.4 |
| 2,000 × 200 | 12 | 8 | 36.1 |
| 5,000 × 200, as 50 calls of 100 | 12 | 8 | 63.9 |
| 5,000 × 200, as 10 calls of 500 | 12 | 8 | 66.7 |
| 5,000 × 200, as 5 calls of 1,000 | 12 | 8 | 69.1 |

- **5,000 series in one call was not run.** It does not fit in this session's
  memory. The container held 1.5 GB with the model loaded (about 1 GB for the
  model, the rest other programs in the session), and peaked at 1.85, 2.09
  and 2.56 GB during calls of 500, 1,000 and 2,000 series. That is about 0.5
  GB more per 1,000 series, which puts 5,000 series at about 4.0 GB against a
  limit of 3.93 GB.
- Batch size does not change speed between 100 and 1,000 series per call:
  about 13 milliseconds per series of 200 steps on 8 threads. At 2,000 per
  call it was slower, 18 milliseconds per series.
- On 2 threads it is about 42 milliseconds per series of 200 steps. By that
  rate 5,000 series would take about 3.5 minutes. This is a projection, not a
  measurement.
- Time grows with history length and with horizon: 730 steps of history took
  about 4 times as long as 200, and a horizon of 365 about 2.5 times as long
  as a horizon of 12.
- This replaces the estimate in decision 9 (about one minute for 500 series
  run one at a time): 500 series of 200 steps in one call took 6.6 seconds on
  8 threads and 20.9 seconds on 2.
- Size of a request written as JSON text, values rounded to 4 decimals: 29 KB
  for 100 × 32, 892 KB for 500 × 200, 8.9 MB for 5,000 × 200.

### Check 5: history shorter than 32 steps

- **It runs with any history from 1 step up**, with no error and no warning.
  A history that is not a multiple of 32 steps is padded at the start, and
  the padding is marked as missing.
- Backtest error by history length. Each block is 300 synthetic series, one
  cutoff, and the same actuals in every row. WAPE is the sum of absolute
  errors divided by the sum of actuals. The noise in the data puts the lowest
  reachable WAPE near 0.12.

  | History (steps) | Monthly-like: cycle 12, horizon 12 | Daily-like: cycle 7, horizon 28 | Weekly-like: cycle 52, horizon 13 |
  | --- | --- | --- | --- |
  | 4 | 0.337 | 0.387 | |
  | 8 | 0.278 | 0.245 | |
  | 12 | 0.240 | | |
  | 14 | | 0.186 | |
  | 16 | 0.187 | | 0.254 |
  | 21 | | 0.147 | |
  | 24 | 0.144 | | |
  | 31 | 0.138 | 0.141 | 0.256 |
  | 32 | 0.138 | 0.141 | 0.248 |
  | 33 | 0.137 | 0.140 | |
  | 52 | | | 0.183 |
  | 64 | 0.128 | 0.134 | 0.158 |
  | 104 | | | 0.133 |
  | 128 | 0.125 | 0.132 | |
  | 256 | 0.122 | 0.127 | |
  | 400 | | | 0.123 |
  | Reference: repeat the last cycle | 0.164 | 0.172 | 0.178 |

- **Nothing changes at 32 steps.** Histories of 31, 32 and 33 steps give the
  same error.
- Error follows the number of cycles in the history. With about two cycles or
  more, Timestral beat the reference (monthly-like with 24 steps: 0.144
  against 0.164). With one cycle it was worse than the reference
  (monthly-like with 12 steps: 0.240). With less than one cycle the reference
  cannot be computed at all.
- So 32 steps is too strict for monthly data and too loose for weekly data
  with a yearly cycle, where 32 weeks gave 0.248, nearly twice the error of 104
  weeks.
- Share of actuals that fell between low and high, expected to be 0.80: from
  0.76 to 0.96 for histories of 2 steps or more. With 1 step the range
  collapses and the share was 0.09.
- 32 is therefore not a requirement of the model. Decision 23 stays in force
  until Oliver decides; see open question 6.

### Notes for later Claude Code sessions in this environment

- `free` reports the host's 61 GB. The session's real limit is in
  `/sys/fs/cgroup/memory.max` and in the variable `CDSW_MEMORY_MB`.
- `/tmp` is held in memory, counts against that limit, and is emptied when
  the session restarts. Keep work files on the project disk.
- The first two runs of these checks ended the session. The likely cause is
  memory: the virtual environment was in `/tmp` and the script included the
  single call of 5,000 series. No log survived to confirm it. The runs
  recorded here kept the container under 2.6 GB.
- The throwaway script and its raw output are in `/home/cdsw/.timestral-checks`,
  which is not in git. Every number from them is in this section.

## First build check 6: results (17 November 2026)

Run by Claude Code in the same Cloudera AI session as checks 1 to 5, with
Oliver's go-ahead, because it creates things in his workspace.

### Spark and Iceberg. Works.

- The project has a Spark data connection, named `applied-ai-aw-dl` in this
  workspace. A session comes from Cloudera AI's own library:
  `cml.data_v1.get_connection(name).get_spark_session()`. It is Spark 3.5.1
  and arrives already set up for Iceberg; no extra settings were needed.
- An Iceberg table is created with `create table ... using iceberg`, appended
  to with `dataframe.writeTo(table).append()`, and read with
  `spark.table(table)` or SQL. The table's `snapshots` listing showed each
  append as its own entry, so nothing was overwritten.
- Timings, in a 2 vCPU, 3.9 GB session:

  | Step | Seconds |
  | --- | --- |
  | Start the Spark session | 20 |
  | First write of 1,200 rows, which also starts the Spark workers | 38 |
  | Later write of 1,200 rows | 1.2 |
  | Write of 60,000 rows (5,000 series by 12 dates) | 4.1 |
  | Read one forecast of 1,200 rows into pandas | 1.6 |
  | Filter and sum in the query, result into pandas | 1.5 |

- Spark takes its memory from the container before Python does: it set
  itself 3,051 MB of the session's 3,933 MB. The application has to cap
  Spark's memory so that the model and the web server fit beside it.
- A date column comes back to pandas as Python date objects, not as pandas
  dates. The storage layer has to convert it.
- The test used a scratch database, `u4cast_check`, and dropped it afterwards.
- The project's own Python environment (`.venv`) can import Spark and
  Cloudera AI's libraries, so the application can run from it.
- numpy and pandas are now pinned below 2 and below 3 in `pyproject.toml`.
  Reason: the runtime's Spark 3.5.1 was released for numpy 1 and pandas 2,
  and the lock file had picked numpy 2.5 and pandas 3.1.

### Building the front end. Works, with Node installed through pip.

- The runtime has no Node. `pip install nodejs-wheel` installs Node 24.19 and
  npm 11.17 into a Python environment, with no administrator rights needed.
  It took 90 seconds and 205 MB on the project disk.
- With that, a new React project (made with Vite, a common React build tool)
  installed its packages from the npm registry in 9 seconds and built in
  about 1 second. So the AMP can build the front end in an install step.

### Workbench model endpoint. The minimum bar is met.

One forecast of 100 series of 32 steps, horizon 12, went through the endpoint
by way of the forecasting package in 0.75 seconds. The answer was complete,
finite, and low was at or below the median, which was at or below high.

How it was deployed:

- `serving/deploy_workbench_model.py` created the model `timestral`, built
  it and deployed it through Cloudera AI's API. The build took 3 minutes and
  the deployment 45 seconds. One replica, 4 vCPU and 8 GB, on the runtime
  Python 3.12, Standard, PBJ Workbench, 2026.08.3.
- `cdsw-build.sh` installs torch and Timestral into the image and downloads
  the weights there, so a replica starts without downloading anything.
- Authentication is on. A call without the API key is refused with status
  403.
- The model used 4 processor threads, matching its 4 vCPU.
- The deploy step saves the model's access key in the project's environment
  variables as `U4CAST_MODEL_ACCESS_KEY`. The application reads it there.

Two things went wrong on the way and are worth knowing:

- The first deployment answered every call with status 400 and no reason.
  Cause: Cloudera AI requires its decorator `cml.models_v1.cml_model` on the
  served function. It is in `serving/workbench_model.py` now.
- One of three builds sat in its upload stage ("pushing") for over 20
  minutes. Starting a new build of the same code finished in 15 seconds.

Time for one request, model on 4 threads:

| Series × steps | Horizon | Seconds in total | Of which the model |
| --- | --- | --- | --- |
| 1 × 32 | 12 | 0.13 | 0.08 |
| 100 × 32 | 12 | 0.71 | 0.66 |
| 100 × 200 | 12 | 2.2 | 2.1 |
| 250 × 200 | 12 | 5.5 | 5.0 |
| 500 × 200 | 12 | 12.0 | 11.1 |
| 125 × 730 | 30 | 8.8 | 7.9 |
| 100 × 200 | 365 | 5.1 | 4.7 |
| 2,000 × 32 | 64 | 15.0 | 13.3 |

- Model time follows the work: about 2.5 milliseconds per series for each
  block of 32 steps, counting the blocks of history and of horizon together.
  The measurements ranged from 2.2 to 3.3.

The limits, measured:

- **Time: a request is cut off at 30 seconds,** with status 500 and the text
  "context deadline exceeded".
- **A request that is cut off keeps the replica busy.** The replica goes on
  working on it and answers nothing else until it is done. Other requests
  wait 30 seconds and fail with "context deadline exceeded --- model busy".
  In the test the replica was unavailable for about 3 minutes after two
  oversized requests. So a request must never be sized near the limit.
- **Request size: 4 MB was accepted, 6 MB was refused** with status 413.
  That fits the 5 MB in Cloudera's documentation. Sending is slow: about
  0.85 seconds for each MB. The request for 250 series of 200 steps is
  0.5 MB.
- **Answer size:** an answer of 3.8 MB came back without trouble.
- **One request at a time.** Two requests sent together were answered one
  after the other: 5.6 and 10.7 seconds.

Volume, through the forecasting package with its default batching:

| Series × steps | Horizon | Requests | Seconds |
| --- | --- | --- | --- |
| 500 × 200 | 12 | 2 of 250 series | 10.6 |
| 5,000 × 200 | 12 | 20 of 250 series | 105 |
| 1,000 × 104 | 13 | 3 of 400 series | 12.8 |

No request in these runs took longer than 5.3 seconds. This answers open
question 4: one replica carries 5,000 series in under two minutes.

### What Cloudera's documentation says about workbench models

Read on 17 November 2026 from docs.cloudera.com (Cloudera AI, public cloud).

- A request is cut off after 30 seconds by default. Only an administrator can
  change that, with a cluster command.
- "Requests to models should not be more than 5 MB in size." It is written as
  guidance; no enforced limit is described.
- A replica (one running copy of the model) answers one request at a time. A
  request waits up to 30 seconds for a free replica, then gets the error
  `model.busy` with status 429.
- Cloudera's guideline: work that takes over 15 seconds belongs in a batch
  job, not a model.
- The model image is built from a copy of the project files that leaves out
  what `.gitignore` lists. Packages installed in a session are not in it.
  The build script `cdsw-build.sh` has to install them.
- A model gets the project's environment variables.
- A caller authenticates with `Authorization: Bearer` and a user API key,
  unless authentication is switched off for the model.

### The Iowa data, seen while waiting for the model build

- All five download addresses answer from this environment. The dataset id
  for 2023 is 1260.
- An address returns a zip archive, not a CSV. Inside are CSV parts stored
  without compression, so the archive is about as large as the data.
- 23 columns. The ones the demo needs: `ordered_on` (the date, as
  `2025-01-01`), `store_no`, `store_name`, `store_city`, `county_name`,
  `category_code`, `category_name`, `vendor_name`, `item_no`, `im_desc` (the
  item's name), `sales_bottles`, `sales_dollars`, `sales_liters`.
- Rows average 216 bytes in the first part of the 2025 file. By that figure a
  year is about 0.55 GB and 2022 through 2026 about 2.6 GB, inside the cap of
  1 to 5 GB. This is an estimate from one sample, not a measurement.

## Storage layer and demo data: results (17 November 2026)

### The demo data is ingested

`ingest/ingest_iowa.py` downloaded Iowa liquor sales for 2022 through 2026,
summed it with Spark and wrote the Iceberg table `u4cast.iowa_liquor_weekly`.
This answers open question 2.

- Download: 24 CSV files, 2.83 GB, in about one minute.
- The table: 12,122,021 rows, one per week, store and item. 252 weeks, from
  the week of 3 January 2022 to the week of 26 October 2026. 2,705 stores,
  9,440 items, 49 categories, 99 counties, 1,417,157 pairs of store and item.
- Columns: `week`, `county`, `city`, `store`, `category`, `vendor`, `item`,
  and three measures, `bottles`, `dollars` and `liters`.
- A week starts on Monday. The first and the last week were dropped because
  the data covers them only in part. A store or item carries the name, place,
  category and vendor of its most recent order, and its number in brackets,
  so a renamed store stays one series and two stores never share a label.
- The ingest took 5.5 minutes in a 2 vCPU session.
- The dataset is saved in the datasets table as "Iowa liquor sales", with
  `bottles` as the target.
- The downloaded files stay in `data/iowa` on the project disk, which git
  ignores, so a second run does not download them again.

### Spark needed two settings

- **A cap on workers.** The first ingest failed. Cloudera AI's default lets
  one job ask for up to 249 Spark workers; this job asked for 89, the
  cluster added machines, and workers then disappeared after two to five
  minutes until Spark gave up. With a cap of 8 workers the ingest finished.
  Two workers were still lost in that run, and Spark recovered. The cause of
  the losses was not found.
- **A cap on memory.** Cloudera AI's default gives Spark's driver 3,051 MB of
  a 3,933 MB container. The project sets 2 GB.
- Both are applied in `u4cast.storage.spark_session` and can be changed with
  environment variables.

### The storage layer works against real Spark and Iceberg

- `u4cast.storage.Store` creates the tables, saves datasets, loads a slice
  with the filter, the summing and the history window done in the query, and
  appends forecasts. Eight tests run it against a scratch database in this
  workspace and all pass. They are skipped unless `U4CAST_TEST_SPARK=1`.
- Loading a slice takes 4 to 14 seconds: 12 seconds for 352,164 rows (all
  stores) and 14 for 651,559 rows (all items). Most of that is moving rows
  from Spark to pandas.
- Memory in the 3.9 GB session peaked at 2.9 GB while Spark, a slice of
  780,000 rows and Claude Code were all in it.

### Open question 7: the equal-length rule leaves out most fine-grained series

All 252 weeks of history, each row a different way of summing the data up:

| Summed up to | Series | Kept | Share kept | Share of bottles kept |
| --- | --- | --- | --- | --- |
| Category | 50 | 44 | 88% | 99.7% |
| County | 100 | 98 | 98% | 99.9% |
| Vendor | 357 | 83 | 23% | 98.6% |
| County by category | 4,744 | 2,458 | 52% | 97.9% |
| Store | 2,705 | 785 | 29% | 67.9% |
| Item | 9,440 | 1,331 | 14% | 78.3% |
| Store by category, Polk county | 12,690 | 1,411 | 11% | 64.1% |
| Store by item, city of Ames | 33,894 | 75 | 0.2% | 2.7% |

- Every series left out was left out for the same reason: it has no row in
  the first week or in the last week of the window.
- Moving the start date helps little. At store level, starting in January
  2026 instead of January 2022 keeps 1,171 stores instead of 785.
- The cause is what a missing row means. In order data, a week with no row
  is a week in which nothing was ordered. The current rules read it as
  unknown, and then require a known value at both ends of the window.
- Coarse levels work today: county by category gives 2,458 series, which is
  enough to demonstrate volume. Store by item, the level of decision 19's
  example of 100 SKUs, does not work with the current rules.
- This is now open question 7, with a recommendation.

### Open question 6: the minimum history figures on real data

Backtest on the Iowa data with the real model through the endpoint. Horizon
13 weeks, four cutoffs 13 weeks apart, the last on 27 July 2026. Each row
gave the model only that many weeks of history. The figures are WAPE.

| Weeks of history | Category, 44 series | County by category, 1,000 series | Store, 785 series |
| --- | --- | --- | --- |
| 32 | 0.140 | 0.308 | 0.338 |
| 52 | 0.136 | 0.299 | 0.332 |
| 78 | 0.138 | 0.297 | 0.331 |
| 104 | 0.129 | 0.290 | 0.321 |
| 156 | 0.116 | 0.283 | 0.310 |
| Reference: repeat the value of a year earlier | 0.321 | 0.518 | 0.544 |

- More history helps, gradually. There is no sharp step at two years, unlike
  on the synthetic data of check 5. With 32 weeks the error is 5% to 9%
  higher than with 104 weeks.
- Timestral beat the reference by a wide margin at every length.
- Decision 28 can stand: 104 weeks by default, and the override to 32 costs
  little accuracy on this data.
- A caution for the demo: Mistral does not name the data Timestral was
  trained on, so overlap with this public dataset cannot be ruled out.

### Decision 35 on the demo data

With Iowa set to "no row means zero" and history counted from each series'
first row, all 252 weeks:

| Summed up to | Series | Kept before | Kept now | Share of bottles kept now |
| --- | --- | --- | --- | --- |
| Category | 50 | 44 | 49 | 100% |
| County | 100 | 98 | 100 | 100% |
| Vendor | 357 | 83 | 319 | 100% |
| County by category | 4,744 | 2,458 | 4,708 | 100% |
| Store | 2,705 | 785 | 2,345 | 98.1% |
| Item | 9,440 | 1,331 | 7,307 | 98.2% |
| Store by category, Polk county | 12,690 | 1,411 | 10,749 | 98.1% |
| Store by item, city of Ames | 33,894 | 75 | 23,676 | 94.3% |

- The series still left out have under 104 weeks since their first row.
- Fine-grained series are mostly zeros: 77% of the values of the kept
  store-by-item series are zero, and 36% at store level.
- **A forecast at store-by-item level works.** 23,676 series of 252 weeks,
  horizon 13, went through the one-replica endpoint in 566 seconds and gave
  307,788 rows. 70% of the forecast values are exactly zero.
- Backtest with zero fill, real model, 1,000 series sampled per level,
  horizon 13 weeks, four cutoffs 13 weeks apart:

  | Level | WAPE | Reference WAPE | MAE in bottles | Reference MAE |
  | --- | --- | --- | --- | --- |
  | County by category | 0.288 | 0.522 | 37.0 | 67.1 |
  | Store | 0.409 | 0.754 | 103.2 | 190.3 |
  | Store by item, Ames | 0.753 | 1.487 | 0.46 | 0.91 |

- Timestral's error is about half the reference's at every level. At store
  by item the WAPE is high because most weeks are zero and the quantities
  are small: the average error is under half a bottle a week.
- The store figure is higher than in the earlier table (0.31 to 0.34)
  because that table held only the 785 stores with orders in every first and
  last week. This one includes stores that order rarely.
- Memory in the 3.9 GB session reached 3.3 GB during this run.

## What the old code did (reference only)

Three private repos were read on 17 November 2026:

- `odog96/u4cast`: the 2021 Flask prototype. ARIMA only in the web app.
- `Rev-Analytics/u4cast-base`: the 2022 Django backend with ARIMA, Prophet and
  exponential smoothing.
- `Rev-Analytics/u4cast_web`: the 2022 PHP and jQuery front end.

Ideas worth carrying over:

- **The user flow.** Add a dataset, pick dimension, target and date columns
  and a frequency, create a named forecast, explore a chart as a total or
  drilled down to one combination of dimension values, download.
- **One series per combination of dimension values**, identified by `ts_id`
  (stored in a column called `srl_num` in the old code). See
  `api/algorithms/prep.py` in `Rev-Analytics/u4cast-base`.
- **The backtest loop.** Forecast from a cutoff, compare with actuals, move
  the cutoff forward, repeat. See `api/algorithms/pr_algo.py` lines 211-338 in
  `Rev-Analytics/u4cast-base`. It was never reachable from the old app.

Things not to repeat:

- Storing every cell of an upload as its own database row.
- Filling missing dates with zero without asking. Since decision 35 it is a
  choice the user makes for each dataset.
- Numbering series in the order they appear, which changes when data changes.
- API endpoints with no check on who is calling.
- Secrets, compiled files and generated outputs committed to the repo.

## Changes made during implementation

Claude Code adds dated lines here.

- 17 November 2026. Ran first build checks 1 to 5. Results are in "First
  build checks 1 to 5: results". Check 6 has not been run. No code was
  written and no decision was changed.
- 17 November 2026. The speed estimate in decision 9 is replaced by
  measurements (check 4). Why: the estimate assumed one series per call;
  many series per call is much faster.
- 17 November 2026. Check 5 answered what decision 23 was waiting for: 32
  steps is not a requirement of Timestral. The minimum is unchanged. Added
  open question 6 for Oliver to decide.
- 17 November 2026. Added open questions 7 (the Hugging Face token was not
  needed for the download), 8 (the runtime has Python 3.12, not 3.11) and 9
  (which resource profile to use). Why: each differs from, or is missing
  from, what `CLAUDE.md` assumes.
- 17 November 2026. Added three proposals to "Proposed by Claude": batches of
  500 series, keeping Timestral's two default settings, and leaving out a
  series with no values in its history window. Why: checks 2, 3 and 4 showed
  the model's behavior that each one responds to.
- 17 November 2026. Recorded that independent series must be passed to
  Timestral as a two-dimensional array (check 2). Why: the three-dimensional
  form forecasts them jointly and changes the numbers.
- 17 November 2026. One call of 5,000 series (part of check 4) was not run,
  because it does not fit in the session's 3,933 MB. It was timed as batches
  instead.
- 17 November 2026. Oliver answered the open questions that were numbered 6
  to 9. They are now decisions 28 to 31 and were removed from "Open
  questions". `CLAUDE.md` was changed to match: the minimum history rule, the
  optional Hugging Face token, Python 3.12, and the resource profile.
- 17 November 2026. Decision 23 (32 steps for every frequency) is replaced by
  decision 28. Why: check 5 showed 32 is not a requirement of the model.
- 17 November 2026. Added a new open question 6. Why: Oliver's message on the
  minimum history was cut off, and two points of decision 28 are Claude's
  reading of it.
- 17 November 2026. Built the forecasting package `u4cast` under `src/` with
  its tests: frequency detection and the date grid, data preparation,
  the model interface, issuing and reissuing forecasts, backtests, error
  measures and the layout of the stored tables. 99 tests run without the
  weights. One more test runs the real model on 100 series of 32 steps and is
  skipped unless `U4CAST_TEST_WEIGHTS=1` is set; it passed.
- 17 November 2026. The model interface has two implementations so far:
  Timestral loaded in the same process, and a stand-in for tests that repeats
  each series' last value. The implementation that calls a workbench model
  endpoint is not built. Why: it depends on the endpoint's request format
  and limits, which check 6 has to find.
- 17 November 2026. Recorded the choices made while building the package in
  "Proposed by Claude", and added open questions 7 (the default history
  window can leave out most series) and 8 (the package does not change the
  frequency of the data, so the ingest has to). Why: both came up in the
  code and neither is settled by `CLAUDE.md`.
- 17 November 2026. The lock file resolved pandas to 3.1, where text columns
  and some arrays behave differently from pandas 2. The package is written
  for both. To be checked again when Spark hands data to pandas.
- 17 November 2026. Added `.gitignore`. Why: in a Cloudera AI session the
  project folder is also the home folder, so the weights cache, tool
  settings and sign-in files sit next to the code.
- 17 November 2026. Oliver confirmed Claude's reading of decision 28 and
  accepted two recommendations. They are decisions 32 to 34. The open
  questions that were numbered 6, 7 and 8 are replaced by two checks to run
  on the demo data. `CLAUDE.md` was changed to match.
- 17 November 2026. Oliver gave the go-ahead to run first build check 6,
  which deploys a model in his Cloudera AI workspace, and to merge the work
  so far into `main` and push it to GitHub.
- 17 November 2026. Merged a commit the planning conversation had pushed to
  GitHub before this session started: Iowa liquor sales is confirmed as the
  demo data. That commit's decision 27 keeps its number. The decisions this
  session had recorded as 27 to 33 are now 28 to 34, here and in
  `CLAUDE.md`. Commit messages written before the merge use the old numbers.
- 17 November 2026. Ran first build check 6. Results are in "First build
  check 6: results". All six checks are now done. The minimum bar is met:
  100 series forecast through a workbench model endpoint.
- 17 November 2026. Built what check 6 needed, as project code: the endpoint
  caller and the served handler (`src/u4cast/endpoint.py`), the choice of
  where the model runs (`src/u4cast/serving.py`), the served function and the
  deploy step (`serving/`), and the image build script (`cdsw-build.sh`).
  With this the model interface has both implementations version one asks
  for. 116 tests run without the weights.
- 17 November 2026. Batches for the endpoint are sized by work, not by a
  fixed number of series. Why: the endpoint cuts a request off at 30 seconds
  and a request that is cut off blocks the replica.
- 17 November 2026. Pinned numpy below 2 and pandas below 3. Why: the
  runtime's Spark 3.5.1 was released for those versions. This replaces the
  earlier note that the lock file had resolved pandas to 3.1.
- 17 November 2026. Check 6 left two things in Oliver's workspace: the
  running workbench model `timestral` (one replica, 4 vCPU and 8 GB) and the
  project environment variable `U4CAST_MODEL_ACCESS_KEY`. Its scratch
  database was dropped.
- 17 November 2026. Built the storage layer (`src/u4cast/storage.py`) and the
  demo data ingest (`ingest/`), and ran the ingest. Results are in "Storage
  layer and demo data: results". 119 tests run without Spark or the weights;
  8 more run against real Spark and passed.
- 17 November 2026. Answered open questions 2 and 6 from the demo data.
  Open question 7 was measured and is now a question for Oliver, with a
  recommendation that would change decision 8. Added open question 8 on
  pyarrow.
- 17 November 2026. The ingest left two things in Oliver's data lake: the
  database `u4cast` and in it the tables `iowa_liquor_weekly`, `datasets`
  and `forecasts`.
- 17 November 2026. Built decision 35: the user's choice of how a date with
  no row is filled, and series of different lengths in one forecast. Changed
  `CLAUDE.md` to match. Why: the old rules lost most fine-grained series of
  the demo data. 132 tests run without Spark or the weights, 10 against real
  Spark; all pass.
- 17 November 2026. Open question 7 is answered by decision 35. The tables
  `datasets` and `forecasts` in the database `u4cast` got a `fill` column,
  and the Iowa dataset is set to zero.
