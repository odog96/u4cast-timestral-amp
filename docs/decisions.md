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
18. **Demo data: Iowa liquor sales**, ingested with Spark.
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

## Changed after Oliver accepted it

- **Minimum history.** Oliver accepted Claude's proposal of 32 time steps.
  Oliver's own example in decision 19, two years of monthly history, has 24
  time steps and would fail that rule. Claude changed the default to 24 and
  made it a setting. This is proposed until Oliver confirms. The first build
  checks include a backtest on 24 steps of history.

## Proposed by Claude, not yet confirmed by Oliver

- Minimum history default of 24 time steps (see above).
- A second way to run the model, loaded in the same process as the
  application, as the fallback if the workbench model endpoint cannot carry
  the volume.
- `ts_id` built from the dimension values, so a series keeps the same
  identifier in every forecast.
- Backtests stored in the same tables as forecasts, marked by a `kind` column.
- One forecast values table per dataset, because each dataset has different
  dimension columns.

## Open questions

1. **Which license does this repo's code use?** An open-source repo needs a
   license file that says what other people may do with the code. Without one,
   nobody else may legally reuse it. Claude suggests Apache 2.0. Oliver to
   confirm, and to check whether Cloudera has a rule for this.
2. **Which cut of the Iowa liquor data?** The old backend repo
   `Rev-Analytics/u4cast-base` has a weekly extract at
   `test_folder/iowa_liquors.csv`: 112 stores, 214 weeks, 21,192 rows
   (measured). The State of Iowa publishes the full data at item level, which
   could give thousands of series; that is from Claude's own knowledge and not
   checked. Also to check: the data's license terms, since a public AMP would
   download or bundle it.
3. **How does the running application read and write Iceberg tables?** Spark
   is decided for the demo data ingest. For the running application, a Spark
   session may be slow to start. To be settled in the target environment.
4. **Can a workbench model endpoint carry the volume?** Its request size and
   time limits are not yet known.
5. **How is the React front end built during the AMP install?** Depends on
   whether the Cloudera AI runtime has Node.

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

Not yet verified by running anything:

- Whether NaN inside a history works as expected.
- Speed for hundreds or thousands of series in one call.
- The exact quantile levels.
- Accuracy on a history as short as 24 steps.

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

Claude Code adds dated lines here. None yet.
