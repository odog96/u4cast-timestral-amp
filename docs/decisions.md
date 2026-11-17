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

23. **Minimum history is 32 time steps, enforced.** Oliver: "if Timestral
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

## Open questions

1. **Who is named as the copyright owner?** Apache 2.0 asks for a short
   notice at the top of source files that names the owner: Oliver, or
   Cloudera. Not set yet. Oliver to check whether Cloudera has a rule for
   work published by an employee. Until then, do not add copyright notices
   to source files.
2. **Which demo dataset, and which cut of it?** Claude compared candidates on
   17 November 2026 and recommends staying with Iowa liquor sales. Oliver has
   not confirmed. See "Demo dataset comparison" below. Proposed cut: the
   yearly datasets from 2022 through the latest month. Not yet known: how many
   stores and items the data has, the size of the CSV files, and whether the
   Cloudera AI environment can reach the download address.
3. **How does the running application read and write Iceberg tables?** Spark
   is decided for the demo data ingest. For the running application, a Spark
   session may be slow to start. To be settled in the target environment.
4. **Can a workbench model endpoint carry the volume?** Its request size and
   time limits are not yet known. Known since 17 November 2026: the request
   for 100 series of 32 steps is 29 KB of JSON text, for 500 series of 200
   steps 892 KB, and the model takes 1.2 seconds and 21 seconds for them on 2
   processor threads.
5. **How is the React front end built during the AMP install?** Depends on
   whether the Cloudera AI runtime has Node. Seen on 17 November 2026: `node`
   and `npm` are not installed in the runtime the first checks ran in
   (JupyterLab, Python 3.12, version 2026.08). Check 6 still has to settle it
   for the runtime the AMP will use.
6. **Keep the 32-step minimum history, or change it?** Decision 23 enforces 32
   until the first build checks show what Timestral does with less. They now
   have: it runs with any length from 1 step, and error does not change at 32.
   What matters is how many seasonal cycles the history holds: with two or
   more, Timestral beat the reference of repeating the last cycle; with fewer,
   it did not. Claude's recommendation: require two seasonal cycles for
   monthly data (24 months) and quarterly data (8 quarters), which would
   accept the two years of monthly history in decision 19's example, and keep
   32 for daily and weekly data. The evidence is synthetic data only, so
   confirm on the demo data's backtest. Until Oliver decides, 32 stays
   enforced.
7. **Is the Hugging Face access token required, or optional?** The weights
   downloaded with no token and no sign-in on 17 November 2026, which differs
   from decision 20. Hugging Face printed a notice that requests without a
   token get lower rate limits. Claude's recommendation: keep the AMP's token
   field but make it optional.
8. **Python 3.11 or 3.12?** `CLAUDE.md` says 3.11. The runtime the first checks
   ran in has Python 3.12.13, and Timestral works on it. Oliver to say
   whether a 3.11 runtime will be used or the standard changes to 3.12.
9. **Which resource profile do the application and the workbench model get?**
   The first checks ran in 2 vCPU and 3,933 MB. The loaded model needs about
   1 GB, and each call adds about 0.5 GB per 1,000 series of 200 steps. Speed
   scales with processor threads: 500 series of 200 steps took 21 seconds on
   2 threads and 6.6 seconds on 8.

## Demo dataset comparison (17 November 2026)

What was read: the Iowa Data Hub and data.gov catalog pages, the Kaggle rules
page for the M5 competition, the fev-bench paper's task table, GIFT-Eval
dataset tables quoted in other papers, the Hugging Face page for
FreshRetailNet-50K and the UCI page for Online Retail II. Not read: the list
of datasets in the TIME benchmark, and the Kaggle rules for Favorita,
Rossmann and Walmart.

- **Iowa liquor sales (recommended).** Orders of spirits by Iowa stores, by
  product and date, from January 2012, updated monthly. License: CC BY 4.0,
  which allows any use, including commercial, if the source is credited.
  About 2.4 to 2.6 million rows per year (2018: 2,355,558; 2022: 2,564,565;
  2024: 2,590,975; 2025: 2,490,620; 2026 through August: 1,604,208). A January
  2023 copy of the full table had 31,339,341 rows and 24 columns. Not in the
  retail tasks of fev-bench or the sales datasets of GIFT-Eval.
  Download: `https://idh-be.iowa.gov/api/v1/datasets/<id>/rows.csv`, with id
  1259 for 2022, 1261 for 2024, 1262 for 2025 and 1263 for 2026. The id for
  2023 was not confirmed.
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

Two things follow if Iowa is confirmed. The README must credit the State of
Iowa, Department of Revenue, as CC BY 4.0 requires. And because Mistral does
not name the real data Timestral was trained on, overlap with any public
dataset cannot be ruled out; say so if the demo reports accuracy.

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

Not yet verified by running anything:

- Whether 100 series fit in one request to a workbench model endpoint.
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
- Filling missing dates with zero.
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

