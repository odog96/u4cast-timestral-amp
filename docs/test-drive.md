# Test drive

A walk through every part of the application, in about 25 minutes. Each step
says what to do and what you should see. If what you see differs, that step
has found a fault: note the step and the message.

Open the application from the **Applications** page of the Cloudera AI
project: it is called `u4cast`. The top right corner of every screen shows
the model and the database it is using.

Parts 2 to 6 use forecasts that are already stored in the workspace where
this project was built. A fresh install has the two demo datasets and no
forecasts: start at part 1, do part 7, then come back to parts 2 to 6 with
the forecast you made.

## 0. Before you start (1 minute)

In the Cloudera AI project:

- **Applications** shows `u4cast` as Running.
- **Model Deployments** shows `timestral` as Deployed.

If the application shows "Connecting to the data lake", wait: it starts Spark
once, which takes under a minute.

## 1. The data (2 minutes)

1. The first screen lists two datasets: "Iowa liquor sales, monthly" and
   "Iowa liquor sales, weekly".
2. Open the monthly one. Four tiles at the top: 58 months of history from
   January 2022 to October 2026, 399,736 rows, the number of forecasts, and
   the minimum history of 24 months.
3. Under "History" you see the table it reads, `u4cast.iowa_liquor_monthly`,
   each column and what it is used for, and the first rows. **Copy SQL**
   gives a query for the same table.

## 2. A stored forecast (5 minutes)

Open the weekly dataset, then the forecast **SKUs, 4 weeks ahead**.

1. The heading says 300 series, 4 weeks ahead. Four tiles give the forecast
   total, the same weeks a year earlier, the history used and how many weeks
   have actuals since.
2. The chart shows the total: actuals in gray, the forecast in blue, the
   range as a light band. Move the pointer over it to read the values.
   **Table**, top right of the chart, shows the same numbers as a table.
   The history has two stretches at about twice the usual level, December
   2022 and August to October 2025. They are in the source data.
3. In **Find one series** type `fireball` and pick one. The chart changes to
   that one SKU. Clear it to go back to the total.
4. Below the chart, the list of series can be searched and sorted. Click a
   row to chart it.
5. The picker at the top right, "History to ...", switches between the issues
   of this forecast. Pick the older one: the chart now also shows the
   actuals that arrived after it was made, next to what it predicted.

One thing that looks odd and is not a fault: the tile "The same weeks a year
earlier" can show the forecast 30% below last year. Last year's weeks fall
in the unusual stretch of 2025.

## 3. Reissue (1 minute)

1. Click **Reissue**. "With history to" offers the latest data, 26 October
   2026. Click the black **Reissue** button.
2. A progress bar runs for well under a minute, then the new issue opens.
3. The tab **Issues compared** now counts 3, where it counted 2.

What this shows: the same 300 series were forecast again from a later date,
under a new identifier, and the earlier issues are untouched.

## 4. Issues compared (2 minutes)

Open the tab **Issues compared**. For each date that several issues
predicted, you see each issue's prediction side by side with what actually
happened, as a chart and as a table. Up to the 5 newest issues are shown.
Pick one series to see the same for a single SKU.

## 5. Accuracy (3 minutes)

Open the tab **Accuracy**.

1. The top figures: the error as a share of actuals (WAPE), and the same for
   the reference, which repeats the value of a year earlier. For this
   forecast: 19.4% against 37.3%, checked from 8 past dates.
2. "Error by how far ahead" shows the error for week 1 to week 4.
3. "Error by series, worst first" lists all 300 series. Search works here
   too.

Read the figures with care. The screen scores over all dates, and the
unusual stretch of 2025 is among them. Part of the gap to the reference
comes from the reference repeating that stretch a year later. With those
dates left out, this level scores 18.2% against 24.4%. The tables are in
`docs/decisions.md` under "Accuracy by level of detail".

## 6. Stored rows, SQL and CSV (3 minutes)

Open the tab **Stored rows**.

1. It names the two Iceberg tables: the values table,
   `u4cast.forecast_values_iowa_liquor_sales_weekly`, and the forecasts
   table, `u4cast.forecasts`.
2. "Every issue of this forecast, one row each" lists the issues with their
   identifiers. Each issue of this forecast has 1,200 rows: 300 series by 4
   weeks.
3. **Copy SQL** copies a query. Paste it into Hue, or any SQL editor on the
   data lake, and run it. You should get the same rows the screen shows.
   This is the check that the forecasts are in Iceberg and not only in the
   application.
4. **CSV**, top right, downloads the issue you are looking at: 1,200 rows
   and a header.

## 7. A forecast of your own (5 minutes)

Open the monthly dataset and click **New forecast**.

1. **One series per**: leave all four columns on (county, category, vendor,
   item). Turning one off adds its values together.
2. **Only these**: under `county` choose `LINN`. The panel on the right now
   says the forecast covers 569 series.
3. **History to use**: leave "From" alone. Under "To" choose July 2026, so
   that later months are left to reissue. The panel says 55 months of
   history.
4. **How far ahead**: 3 months.
5. **Check accuracy on the past**: leave it on, from 4 past dates, 3 months
   apart. The screen lists the dates: July 2025, October 2025, January 2026
   and April 2026.
6. Give it a name and click **Create forecast**. It takes about half a
   minute and then opens.
7. Reissue it with history to August 2026, then September 2026. Open
   **Issues compared**: three issues predicted October 2026, and the actual
   is next to them.

To see a summed-up forecast, make another with only `category` on under "One
series per" and no filter: 42 series, one per category, statewide.

## 8. A CSV of your own (3 minutes)

The file `docs/sample-sales.csv` in this repo is a made-up example: 6
series, 36 months, 216 rows. Download it to your computer first.

1. On the first screen click **Add a dataset**, then **Upload a CSV**.
2. The screen should say the table has 216 rows, and suggest `month` as the
   date, `units` as the value to forecast, and `region` and `product` as
   what tells series apart. Frequency: "Detected monthly".
3. "When a date has no row for a series": leave it. The file has no gaps.
4. Name it and click **Save the dataset**.
5. Click **New forecast**: 6 series of 36 months. Forecast 3 months ahead
   with an accuracy check from 4 past dates, 3 months apart.

The upload becomes a new Iceberg table, `u4cast.upload_sample_sales`.
Uploading the same file again makes `upload_sample_sales_2`: an upload never
replaces a table.

## 9. Things it should refuse (2 minutes)

- Create a forecast with a name that is already used in the dataset. It
  says a forecast of that name exists, and to open it to reissue it.
- In **New forecast** on the monthly dataset, set "From" to a date in 2025.
  The count of series drops to zero, because a monthly series needs 24
  months of history.
- Start a reissue while a forecast is running. It waits its turn: the
  application runs one job at a time.

## 10. The automated tests (1 minute)

In a session terminal, in the project folder:

```
.venv/bin/pytest
```

Expected: 156 passed, 12 skipped. The 12 need Spark or the model weights and
are skipped unless asked for; the README says how.

## What to expect that is not a fault

- **There is no delete.** A dataset or forecast made while testing stays.
  Nothing is ever overwritten, by design. Test data can be removed with SQL
  on the data lake.
- **The first chart of a dataset takes a few seconds,** later ones are
  immediate. The application keeps what it has read.
- **A total is the total of the kept series,** not of all sales in Iowa. The
  demo tables hold only the series that sold in every period.
- **Accuracy depends on the level of detail.** Summed up to a category or a
  county, repeating last year's value is as good as the model. The model's
  advantage is on single SKUs over a short horizon.

## If something goes wrong

- A red message on a screen is meant to say what to change. If it does not,
  that is a fault worth noting.
- The application's own log is on the **Applications** page: `u4cast`, then
  Logs.
- To restart the application, run `python3 webapp/deploy_application.py` in
  a session. It takes a minute or two and loses nothing: everything is in
  Iceberg.
