# Decisions, open questions and deferred items

This is the shared record between Oliver's planning conversations and Claude
Code sessions. Every entry says who decided it and when. "Proposed" means Claude
suggested it and Oliver has not yet confirmed it.

Last updated from a planning conversation: 17 November 2026.

## Goal

Modernize u4cast into an open-source Cloudera AI application, packaged as an
AMP, that forecasts many time series at once with Mistral's Timestral model.
First demo: 27 November 2026. The priority is speed to a working deployment.

The user Oliver has in mind is a demand forecasting team that does not write
code. The application's value is that it handles what is hard about hundreds
of series: the volume of forecasts, rolling forecasts, which prediction
belongs to which date and which series, flexible time windows, and different
frequencies. The full goal statement is in `CLAUDE.md` under "Goal".

## Decisions made by Oliver (17 November 2026)

1. **Timestral is the only model.** Reason: showcase Cloudera AI with Mistral.
2. **Zero-shot only, no fine-tuning.** Zero-shot means forecasting with no
   training on the user's data. Reason: Timestral's public code is inference
   only, and fine-tuning would need Mistral Forge, Mistral's model
   customization service. Oliver: not a must-have.
3. **No ARIMA anywhere, including the backtest.** This reverses an earlier
   decision the same day to keep ARIMA as a backtest comparison. Reason:
   fitting ARIMA per series is a different and much larger piece of work than a
   Timestral call, and it would add code only to make a comparison.
4. **Drop Django, the login, Postgres and the PHP front end.** Rebuild as one
   Cloudera AI application. Reason: Cloudera AI already handles sign-in, the
   old storage design does not fit thousands of series, and one application
   needs no separate front end.
5. **Input: CSV, plus Iceberg tables.** Keep CSV; add reading from the data lake.
6. **Output: an Iceberg table.** CSV download is a side option.
7. **Backtesting against actuals is required.**
8. **All series in a dataset must have the same history length.** Reason: it
   removes the question of how to send unequal-length series in one model call.
9. **Speed is acceptable for now.** The estimate was about one minute for 500
   series if run one at a time on the largest model. It is an estimate from
   Mistral's published figure of 113 ms per forecast on 8 CPU threads, not a
   measurement.
10. **Public cloud first.** On-premises later.
11. **The design should stay general.** Retail is the flagship; IoT and network
    traffic are possible later uses. Work within what Timestral offers,
    including setting our own minimum history.
12. **Licensing of the Timestral weights is each user's responsibility.**
    Oliver will check separately what permissions cover building this
    prototype; Cloudera has a partnership with Mistral.
13. **Start in a new repo.** Reason: the old backend repo has a committed
    `.env` file with a database address, a storage bucket name and a Django
    secret key, and this project will be public.
14. **The interface must be top quality, and that is part of the goal.**
    Oliver's reference is the quality of his spending app, with different
    colors.
15. **The goal includes making hundreds of series easy to manage** for a team
    that does not write code: volume, rolling forecasts, which prediction
    belongs to which date and series, flexible time windows, different
    frequencies.
16. **The demo dataset should be retail with many series.** Choosing it is an
    open question.

## Proposed by Claude, not yet confirmed by Oliver

- Default model size `mistralai/Timestral-209M`.
- Minimum history of 32 time steps per series.
- A series that fails the equal-length rule is left out and listed for the user.
- Forecast range from the 0.1 and 0.9 quantiles.
- WAPE as an extra error measure, because MAPE fails when an actual is zero.
- A reference comparison in the backtest: repeat the value from one season
  earlier. Reason: an error number alone does not show whether it is good.
- Build CSV input before Iceberg input.
- Detect the frequency from the dates and ask the user to confirm it.
- A history window the user can choose (first and last date of history used).
- Three saved objects: dataset, forecast (a named set of choices), and run
  (one execution of a forecast). A new run never replaces an earlier one.
- Two forecast table columns for rolling forecasts: `history_end_date` and
  `step`.
- A view that puts several runs' predictions for the same date side by side
  with what happened.

## Open questions

1. **Which interface framework?** This blocks interface work. Claude first
   proposed Streamlit, a Python library that builds a web page from one script.
   Decision 14 puts that in question: Streamlit is the fastest to build, but
   its look and layout can only be customized within limits. The alternative
   is a Python web server (FastAPI) that serves a React front end, which gives
   full control of the design and costs more build time and one more step in
   the AMP install. Claude's recommendation after decision 14: FastAPI with a
   React front end. Oliver has not decided.
2. How does the application read and write Iceberg tables on Cloudera AI: a
   Spark data connection, Impala, or another route? To be settled in the target
   environment.
3. Where is the list of saved datasets, forecasts and runs kept: a small
   Iceberg table, or files in the project's storage?
4. Which dataset is used for the 27 November demo? It should be retail with
   many series (decision 16). Three things to check for any candidate: whether
   its license allows an open-source AMP to bundle or download it, whether it
   has enough series, and whether Timestral was trained or benchmarked on it,
   because a backtest on data the model has seen overstates accuracy.
   Candidates, from Claude's own knowledge and not checked:
   - Iowa liquor sales, published as open data by the State of Iowa. The old
     backend repo `Rev-Analytics/u4cast-base` has a weekly extract at
     `test_folder/iowa_liquors.csv`: 112 stores, 214 weeks, 21,192 rows
     (measured). The full data is at item level, so it could give thousands
     of series.
   - M5, the Walmart item-by-store daily sales data from a Kaggle competition.
   - Favorita grocery sales, from a Kaggle competition.
5. Which license does this repo's code use?
6. Can the Cloudera AI environment reach Hugging Face to download the weights?
7. Does the demo need frequencies finer than daily, such as hourly?

## Deferred items

- **Fine-tuning.** Deferred because Timestral's public code does not include
  it. Comes back if Mistral publishes training code or Forge access is arranged.
- **Anomaly adjustment** (replace out-of-range values in a window after a date
  the user gives, then forecast from the corrected history). Deferred because
  it was already switched off in the 2022 code and Oliver did not ask for it.
  Comes back if a demo needs a known shock, such as 2020, taken out of history.
- **Extra predictor columns** such as price or holidays. Timestral accepts
  them. Deferred to keep version one small. Comes back when a dataset with
  such columns is chosen.
- **On-premises deployment.** Deferred because public cloud is first. Comes
  back after the 27 November demo.
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

Not yet verified by running anything:

- Whether NaN inside a history works as expected.
- Speed for hundreds or thousands of series in one call.
- The exact quantile levels.

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
- **A minimum-history rule.** The old rule was two seasonal cycles: 24 monthly
  points, 104 weekly, 730 daily, 8 quarterly.

Things not to repeat:

- Storing every cell of an upload as its own database row.
- Filling missing dates with zero.
- API endpoints with no check on who is calling.
- Secrets, compiled files and generated outputs committed to the repo.

## Changes made during implementation

Claude Code adds dated lines here. None yet.
