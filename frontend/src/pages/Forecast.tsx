// Explore a forecast: its chart, its runs side by side, its accuracy, and its stored rows.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import {
  api,
  type ChartData,
  type ForecastOverview,
  type ForecastRecord,
  type Frequency,
  type Job,
  type Values,
} from "../api";
import { JobProgress } from "../components/JobProgress";
import { ChartFigure, type ChartSeries } from "../components/LineChart";
import { SeriesPicker } from "../components/pickers";
import {
  CopyButton,
  Delta,
  ErrorNote,
  Icon,
  Loading,
  RowsTable,
  Tile,
  useCrumbs,
  useDebounced,
} from "../components/ui";
import { capital, day, figure, modelName, num, pct, period, periods, splitLabel, unit } from "../format";

type Tab = "forecast" | "compare" | "accuracy" | "stored" | "production";
import { ProductionTab } from "./Production";

type Selected = { ts_id: string; label: string } | null;

const SEASON: Record<Frequency, number> = { daily: 7, weekly: 52, monthly: 12, quarterly: 4 };
const EARLIER: Record<Frequency, string> = {
  daily: "a week earlier",
  weekly: "a year earlier",
  monthly: "a year earlier",
  quarterly: "a year earlier",
};
const RAMP = ["var(--ramp-1)", "var(--ramp-2)", "var(--ramp-3)", "var(--ramp-4)", "var(--ramp-5)"];

const sum = (values: Values) => values.reduce<number>((total, value) => total + (value ?? 0), 0);
const blanks = (count: number): Values => Array.from({ length: count }, () => null);

export function ForecastPage() {
  const { dataset = "", forecast: name = "" } = useParams();
  const datasetPath = `/datasets/${encodeURIComponent(dataset)}`;
  useCrumbs([{ label: dataset, to: `${datasetPath}/forecasts` }, { label: name }]);
  const overview = useQuery({
    queryKey: ["forecast", dataset, name],
    queryFn: () => api.forecast(dataset, name),
  });

  if (overview.isLoading) {
    return (
      <main className="page">
        <Loading what="Reading the forecast" />
      </main>
    );
  }
  if (overview.error || !overview.data) {
    return (
      <main className="page">
        <ErrorNote error={overview.error ?? new Error("The forecast could not be read.")} />
      </main>
    );
  }
  return <Explore overview={overview.data} />;
}

function Explore({ overview }: { overview: ForecastOverview }) {
  const [params, setParams] = useSearchParams();
  const client = useQueryClient();
  const frequency = overview.dataset.frequency;
  const issue = overview.issues.find((i) => i.forecast_id === params.get("issue")) ?? overview.issues[0];
  const tab = (params.get("tab") as Tab) || "forecast";
  const [selected, setSelected] = useState<Selected>(null);
  const [reissuing, setReissuing] = useState(false);

  const update = (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) {
      if (value === null) next.delete(key);
      else next.set(key, value);
    }
    setParams(next, { replace: true });
  };

  return (
    <main className="page">
      <div className="page-head">
        <div style={{ minWidth: 0 }}>
          <h1>{overview.forecast_name}</h1>
          <p className="sub">
            {issue.series?.toLocaleString() ?? "–"} series of {overview.dataset.name},{" "}
            {periods(issue.horizon, frequency)} ahead, by {modelName(issue.model_name)}
          </p>
        </div>
        <div className="actions">
          <select
            className="select"
            style={{ width: "auto" }}
            aria-label="Which run to show"
            value={issue.forecast_id}
            onChange={(event) => update({ issue: event.target.value })}
          >
            {overview.issues.map((record, i) => (
              <option key={record.forecast_id} value={record.forecast_id}>
                History to {period(record.history_end_date, frequency)}
                {i === 0 ? " (newest)" : ""}
              </option>
            ))}
          </select>
          <button className="btn" onClick={() => setReissuing(!reissuing)}>
            <Icon name="refresh" />
            Run again
          </button>
          <a className="btn" href={api.downloadUrl(issue.forecast_id)}>
            <Icon name="download" />
            CSV
          </a>
        </div>
      </div>

      {reissuing && (
        <Reissue
          overview={overview}
          onClose={() => setReissuing(false)}
          onDone={async (forecastId) => {
            // Read the list of runs again before showing the new one, so the page
            // never shows the new run's address with the old run's numbers.
            await client.invalidateQueries({ queryKey: ["forecast", overview.dataset.name] });
            setReissuing(false);
            update({ issue: forecastId });
          }}
        />
      )}

      <div className="tabs" role="tablist">
        {(
          [
            ["forecast", "Forecast", null],
            ["compare", "Runs compared", overview.issues.length],
            ["accuracy", "Accuracy", null],
            ["stored", "Stored rows", null],
            ["production", "Production", null],
          ] as [Tab, string, number | null][]
        ).map(([id, label, count]) => (
          <button
            key={id}
            role="tab"
            aria-selected={tab === id}
            className={tab === id ? "on" : ""}
            onClick={() => update({ tab: id === "forecast" ? null : id })}
          >
            {label}
            {count !== null && <span className="count">{count}</span>}
          </button>
        ))}
      </div>

      {tab === "forecast" && (
        <ForecastTab issue={issue} frequency={frequency} selected={selected} onSelect={setSelected} />
      )}
      {tab === "compare" && (
        <CompareTab overview={overview} issue={issue} selected={selected} onSelect={setSelected} />
      )}
      {tab === "accuracy" && (
        <AccuracyTab overview={overview} issue={issue} selected={selected} onSelect={setSelected} />
      )}
      {tab === "stored" && <StoredTab issue={issue} frequency={frequency} />}
      {tab === "production" && <ProductionTab dataset={overview.dataset.name} name={overview.forecast_name} frequency={frequency} />}
    </main>
  );
}

function Reissue({
  overview,
  onClose,
  onDone,
}: {
  overview: ForecastOverview;
  onClose: () => void;
  onDone: (forecastId: string) => void;
}) {
  const frequency = overview.dataset.frequency;
  const dates = [...overview.reissue_dates].reverse();
  const latestOpen = dates[0] === overview.latest_data_date;
  const [asOf, setAsOf] = useState<string>(dates[0] ?? "");
  const [job, setJob] = useState<Job | null>(null);
  const start = useMutation({
    mutationFn: () => api.reissue(overview.dataset.name, overview.forecast_name, asOf || null),
    onSuccess: setJob,
  });
  const done = useCallback(
    (finished: Job) => finished.result?.forecast_id && onDone(finished.result.forecast_id),
    [onDone],
  );

  return (
    <section className="card card-pad" style={{ marginBottom: 20 }}>
      <div className="spread" style={{ alignItems: "flex-start" }}>
        <div>
          <h2>Run this forecast again</h2>
          <p className="muted" style={{ marginTop: 4, maxWidth: "68ch" }}>
            The same series are forecast again from a later end of history. The new run gets its
            own identifier and is stored next to the earlier ones. Nothing is overwritten.
          </p>
        </div>
        <button className="btn ghost small" onClick={onClose} aria-label="Close">
          <Icon name="x" />
        </button>
      </div>
      <div style={{ marginTop: 16 }}>
        {job ? (
          <JobProgress job={job} onDone={done} onDismiss={() => setJob(null)} />
        ) : dates.length === 0 ? (
          <div className="notice">
            Every date of the history has a run already. There is no newer data to run it with.
          </div>
        ) : (
          <div className="row wrap" style={{ alignItems: "flex-end", gap: 12 }}>
            <div className="field">
              <label htmlFor="as-of">With history to</label>
              <select
                id="as-of"
                className="select"
                style={{ minWidth: 220 }}
                value={asOf}
                onChange={(event) => setAsOf(event.target.value)}
              >
                {dates.map((date, i) => (
                  <option key={date} value={date}>
                    {period(date, frequency)}
                    {i === 0 && latestOpen ? " (the latest data)" : ""}
                  </option>
                ))}
              </select>
            </div>
            <button className="btn primary" onClick={() => start.mutate()} disabled={start.isPending}>
              Run again
              <Icon name="arrow" />
            </button>
          </div>
        )}
        {start.error && (
          <div style={{ marginTop: 12 }}>
            <ErrorNote error={start.error} />
          </div>
        )}
      </div>
    </section>
  );
}

// The forecast: history, forecast and range, for the total or for one series.

function forecastSeries(chart: ChartData): { dates: string[]; series: ChartSeries[]; low: Values; high: Values } {
  const history = chart.dates.length;
  return {
    dates: [...chart.dates, ...chart.future_dates],
    series: [
      {
        id: "actual",
        label: "Actual",
        color: "var(--series-actual)",
        values: [...chart.actual, ...chart.actual_after],
      },
      {
        id: "forecast",
        label: "Forecast",
        color: "var(--series-forecast)",
        values: [...blanks(history), ...chart.forecast],
        direct: true,
        joinFrom: "actual",
      },
    ],
    low: [...blanks(history), ...chart.low],
    high: [...blanks(history), ...chart.high],
  };
}

function ForecastTab({
  issue,
  frequency,
  selected,
  onSelect,
}: {
  issue: ForecastRecord;
  frequency: Frequency;
  selected: Selected;
  onSelect: (series: Selected) => void;
}) {
  // Drill down: choose a value in any dimension and the chart adds up the series that have it.
  const [drill, setDrill] = useState<Record<string, string>>({});
  const chart = useQuery({
    queryKey: ["chart", issue.forecast_id, selected?.ts_id ?? null, drill],
    queryFn: () => api.chart(issue.forecast_id, selected?.ts_id ?? null, drill),
    placeholderData: (previous) => previous,
  });
  const options = useQuery({
    queryKey: ["drill", issue.forecast_id, drill],
    queryFn: () => api.drill(issue.forecast_id, drill),
    placeholderData: (previous) => previous,
  });
  const choose = (column: string, value: string) => {
    const next = { ...drill };
    if (value) next[column] = value;
    else delete next[column];
    setDrill(next);
    if (selected) onSelect(null);
  };
  const data = chart.data;
  const season = SEASON[frequency];
  const built = data && forecastSeries(data);
  const ahead = data ? sum(data.forecast) : null;
  const earlierValues =
    data && issue.horizon <= season && data.actual.length >= season
      ? data.actual.slice(data.actual.length - season, data.actual.length - season + issue.horizon)
      : null;
  const earlier = earlierValues ? sum(earlierValues) : null;
  const happened = data ? data.actual_after.filter((value) => value !== null).length : 0;

  return (
    <div className="stack">
      <section className="card">
        <div className="tiles">
          <Tile
            label={`Forecast for the next ${periods(issue.horizon, frequency)}`}
            value={figure(ahead)}
            note={`${issue.target_column}, ${period(data?.future_dates[0], frequency)} to ${period(
              data?.future_dates[data.future_dates.length - 1],
              frequency,
            )}`}
          />
          <Tile
            label={`The same ${unit(frequency)} ${EARLIER[frequency]}`}
            value={figure(earlier)}
            note={
              earlier && ahead !== null ? (
                <>
                  Forecast is <Delta value={ahead / earlier - 1} /> against it
                </>
              ) : (
                "Not enough history to compare"
              )
            }
          />
          <Tile
            label="History used"
            value={period(issue.history_end_date, frequency)}
            note={`From ${period(issue.history_start_date, frequency)}, issued ${day(issue.created_at)}`}
          />
          <Tile
            label="Since then"
            value={happened ? periods(happened, frequency) : "–"}
            note={happened ? "Have actuals to compare with" : "No actuals yet after this run"}
          />
        </div>
      </section>

      {options.data && options.data.dimensions.length > 0 && (
        <section className="card card-pad">
          <div className="stack" style={{ gap: 12 }}>
            <div
              style={{
                display: "grid",
                gap: 14,
                gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))",
              }}
            >
              {options.data.dimensions.map((column) => (
                <div className="field" key={column} style={{ minWidth: 0 }}>
                  <label htmlFor={`drill-${column}`}>{capital(column)}</label>
                  <select
                    id={`drill-${column}`}
                    className="select"
                    value={selected ? "" : drill[column] ?? ""}
                    onChange={(event) => choose(column, event.target.value)}
                  >
                    <option value="">All</option>
                    {(options.data.options[column] ?? []).map((value) => (
                      <option key={value} value={value}>
                        {value}
                      </option>
                    ))}
                  </select>
                </div>
              ))}
            </div>
            <div className="spread muted" style={{ fontSize: 13 }}>
              <span>
                Drill down by any of these.{" "}
                {selected
                  ? "Showing one series"
                  : options.data.series === 1
                    ? "Showing one series"
                    : `${options.data.series.toLocaleString()} series added together`}
              </span>
              {(Object.keys(drill).length > 0 || selected) && (
                <button
                  className="btn small"
                  onClick={() => {
                    setDrill({});
                    if (selected) onSelect(null);
                  }}
                >
                  Show the total
                </button>
              )}
            </div>
          </div>
        </section>
      )}

      {chart.error && <ErrorNote error={chart.error} />}
      {!built || !data ? (
        <section className="card">
          <Loading what="Reading history from the data lake. The first look at a forecast takes a few seconds" />
        </section>
      ) : (
        <div style={{ opacity: chart.isPlaceholderData ? 0.6 : 1, transition: "opacity 0.15s" }}>
          <ChartFigure
            title={selected ? splitLabel(data.label).main : data.label}
            subtitle={
              selected
                ? splitLabel(data.label).rest || `${data.target} by ${unit(frequency, 1)}`
                : data.series === 1
                  ? `${data.target} by ${unit(frequency, 1)}`
                  : `${data.target} by ${unit(frequency, 1)}, ${data.series.toLocaleString()} series added together`
            }
            actions={<SeriesPicker forecastId={issue.forecast_id} selected={selected} onSelect={onSelect} />}
            dates={built.dates}
            series={built.series}
            band={{ label: "Range, 10% to 90%", low: built.low, high: built.high }}
            markerIndex={data.dates.length}
            frequency={frequency}
            note={data.note}
          />
        </div>
      )}

      <SeriesTable issue={issue} frequency={frequency} selected={selected} onSelect={onSelect} />
    </div>
  );
}

function SeriesTable({
  issue,
  frequency,
  selected,
  onSelect,
}: {
  issue: ForecastRecord;
  frequency: Frequency;
  selected: Selected;
  onSelect: (series: Selected) => void;
}) {
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState("forecast");
  const [limit, setLimit] = useState(15);
  const settled = useDebounced(search, 250);
  const page = useQuery({
    queryKey: ["series", issue.forecast_id, settled, sort, limit],
    queryFn: () => api.series(issue.forecast_id, settled, sort, limit),
    placeholderData: (previous) => previous,
  });
  const rows = page.data?.rows ?? [];

  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2>Series</h2>
          <div className="sub">
            {page.data
              ? `${page.data.total.toLocaleString()} ${settled ? "match" : "in this forecast"}. Click one to chart it.`
              : "Loading"}
          </div>
        </div>
        <div className="row">
          <input
            className="input"
            style={{ width: 240 }}
            placeholder="Search series"
            aria-label="Search series"
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setLimit(15);
            }}
          />
          <select
            className="select"
            style={{ width: "auto" }}
            aria-label="Order"
            value={sort}
            onChange={(event) => setSort(event.target.value)}
          >
            <option value="forecast">Largest forecast</option>
            <option value="growth">Growing most</option>
            <option value="decline">Falling most</option>
            <option value="name">By name</option>
          </select>
        </div>
      </div>
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th>Series</th>
              <th className="r">Next {periods(issue.horizon, frequency)}</th>
              <th className="r">{EARLIER[frequency].replace(/^a /, "A ")}</th>
              <th className="r">Change</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const { main, rest } = splitLabel(row.label);
              return (
                <tr
                  key={row.ts_id}
                  className={`clickable ${selected?.ts_id === row.ts_id ? "selected" : ""}`}
                  onClick={() => {
                    onSelect(selected?.ts_id === row.ts_id ? null : { ts_id: row.ts_id, label: row.label });
                    window.scrollTo({ top: 0, behavior: "smooth" });
                  }}
                >
                  <td>
                    <div className="cell-main">{main}</div>
                    {rest && <div className="cell-sub">{rest}</div>}
                  </td>
                  <td className="r">{num(row.forecast_sum)}</td>
                  <td className="r">{num(row.previous_sum)}</td>
                  <td className="r">
                    <Delta value={row.change} />
                  </td>
                </tr>
              );
            })}
            {page.data && rows.length === 0 && (
              <tr>
                <td colSpan={4} className="faint">
                  No series matches.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {page.data && page.data.total > rows.length && (
        <div className="card-pad" style={{ borderTop: "1px solid var(--line)", padding: "12px 22px" }}>
          <button className="btn small" onClick={() => setLimit(limit + 25)}>
            Show more
          </button>
          <span className="hint" style={{ marginLeft: 12 }}>
            Showing {rows.length} of {page.data.total.toLocaleString()}
          </span>
        </div>
      )}
    </section>
  );
}

// Several runs of one forecast, next to what happened.

function Comparison({
  overview,
  kind,
  selected,
  onSelect,
  pickerFor,
}: {
  overview: ForecastOverview;
  kind: "forecast" | "backtest";
  selected: Selected;
  onSelect: (series: Selected) => void;
  pickerFor: string;
}) {
  const frequency = overview.dataset.frequency;
  const compared = useQuery({
    queryKey: ["compare", overview.dataset.name, overview.forecast_name, kind, selected?.ts_id ?? null],
    queryFn: () => api.compare(overview.dataset.name, overview.forecast_name, selected?.ts_id ?? null, kind),
    placeholderData: (previous) => previous,
  });
  if (compared.error) return <ErrorNote error={compared.error} />;
  if (!compared.data) {
    return (
      <section className="card">
        <Loading what="Lining up the runs" />
      </section>
    );
  }
  const data = compared.data;
  const lead = data.context_dates.length;
  const dates = [...data.context_dates, ...data.dates];
  const offset = RAMP.length - data.issues.length;
  const series: ChartSeries[] = [
    {
      id: "actual",
      label: "Actual",
      color: "var(--series-actual)",
      values: [...data.context_actual, ...data.actual],
    },
    ...data.issues.map((record, i) => ({
      id: record.forecast_id,
      label: `From ${period(record.history_end_date, frequency)}`,
      color: RAMP[Math.max(0, offset + i)],
      values: [...blanks(lead), ...data.forecasts[i]],
    })),
  ];
  const { main, rest } = splitLabel(data.label);

  return (
    <div className="stack" style={{ opacity: compared.isPlaceholderData ? 0.6 : 1 }}>
      <ChartFigure
        title={selected ? main : data.label}
        subtitle={
          kind === "forecast"
            ? `${selected && rest ? `${rest}. ` : ""}Each line is one run, named by where its history ended. The 5 newest runs are shown.`
            : `${selected && rest ? `${rest}. ` : ""}Each line is the forecast made from one past date.`
        }
        actions={<SeriesPicker forecastId={pickerFor} selected={selected} onSelect={onSelect} />}
        dates={dates}
        series={series}
        frequency={frequency}
      />
      <section className="card">
        <div className="card-head">
          <div>
            <h2>The same dates, side by side</h2>
            <div className="sub">
              What each {kind === "forecast" ? "run" : "test run"} said for a date, and what
              then happened.
            </div>
          </div>
        </div>
        <div className="table-wrap" style={{ maxHeight: 460, overflowY: "auto" }}>
          <table className="data compact">
            <thead>
              <tr>
                <th style={{ position: "sticky", top: 0 }}>{capital(unit(frequency, 1))}</th>
                {data.issues.map((record, i) => (
                  <th key={record.forecast_id} className="r" style={{ position: "sticky", top: 0 }}>
                    <span className="key" style={{ color: RAMP[Math.max(0, offset + i)], marginRight: 6 }} />
                    From {period(record.history_end_date, frequency)}
                  </th>
                ))}
                <th className="r" style={{ position: "sticky", top: 0 }}>
                  Actual
                </th>
              </tr>
            </thead>
            <tbody>
              {data.dates.map((date, row) => (
                <tr key={date}>
                  <td className="nowrap">{period(date, frequency)}</td>
                  {data.forecasts.map((values, i) => (
                    <td key={i} className="r">
                      {values[row] === null ? <span className="faint">·</span> : num(values[row])}
                    </td>
                  ))}
                  <td className="r" style={{ fontWeight: 620 }}>
                    {data.actual[row] === null ? <span className="faint">not yet</span> : num(data.actual[row])}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function CompareTab({
  overview,
  issue,
  selected,
  onSelect,
}: {
  overview: ForecastOverview;
  issue: ForecastRecord;
  selected: Selected;
  onSelect: (series: Selected) => void;
}) {
  if (overview.issues.length < 2) {
    return (
      <section className="card empty">
        <h2>One run so far</h2>
        <p>
          When this forecast is run again, its runs overlap: several of them predict the same dates.
          They are shown here side by side, next to what actually happened.
        </p>
      </section>
    );
  }
  return (
    <Comparison
      overview={overview}
      kind="forecast"
      selected={selected}
      onSelect={onSelect}
      pickerFor={issue.forecast_id}
    />
  );
}

// Accuracy: the forecast made from past dates, scored against what happened.

function AccuracyTab({
  overview,
  issue,
  selected,
  onSelect,
}: {
  overview: ForecastOverview;
  issue: ForecastRecord;
  selected: Selected;
  onSelect: (series: Selected) => void;
}) {
  const frequency = overview.dataset.frequency;
  const [search, setSearch] = useState("");
  const [limit, setLimit] = useState(15);
  const settled = useDebounced(search, 250);
  const accuracy = useQuery({
    queryKey: ["accuracy", overview.dataset.name, overview.forecast_name, settled, limit],
    queryFn: () => api.accuracy(overview.dataset.name, overview.forecast_name, settled, limit),
    placeholderData: (previous) => previous,
  });
  const [job, setJob] = useState<Job | null>(null);
  const [cutoffs, setCutoffs] = useState(4);
  const check = useMutation({
    mutationFn: () =>
      api.checkAccuracy(overview.dataset.name, overview.forecast_name, cutoffs, issue.horizon >= 6 ? 3 : 1),
    onSuccess: setJob,
  });
  const done = useCallback(() => setJob(null), []);

  if (accuracy.error) return <ErrorNote error={accuracy.error} />;
  if (!accuracy.data) {
    return (
      <section className="card">
        <Loading what="Scoring the past forecasts" />
      </section>
    );
  }
  const data = accuracy.data;

  if (!data.available) {
    return (
      <section className="card empty">
        <h2>Accuracy has not been checked</h2>
        <p>
          The forecast is made again from earlier dates, as if the later history were not known, and
          compared with what then happened.
        </p>
        {job ? (
          <div style={{ maxWidth: 460, margin: "0 auto", textAlign: "left" }}>
            <JobProgress job={job} onDone={done} onDismiss={() => setJob(null)} />
          </div>
        ) : (
          <div className="row" style={{ justifyContent: "center" }}>
            <span>From</span>
            <input
              className="input narrow num"
              type="number"
              min={1}
              max={12}
              aria-label="Number of past dates"
              value={cutoffs || ""}
              onChange={(event) => setCutoffs(Number(event.target.value))}
            />
            <span>past dates</span>
            <button className="btn primary" onClick={() => check.mutate()} disabled={check.isPending}>
              Check accuracy
            </button>
          </div>
        )}
        {check.error && (
          <div style={{ maxWidth: 460, margin: "14px auto 0" }}>
            <ErrorNote error={check.error} />
          </div>
        )}
      </section>
    );
  }

  const total = data.total;
  const steps = data.by_step.map((row) => `${row.step}`);
  const better =
    total.wape !== null && total.reference_wape !== null && total.reference_wape > 0
      ? total.wape / total.reference_wape - 1
      : null;
  const worst = Math.max(...data.by_series.map((row) => row.wape ?? 0), 0.0001);

  return (
    <div className="stack">
      <section className="card">
        <div className="tiles">
          <Tile
            label="Error, as a share of actuals"
            value={pct(total.wape)}
            note={
              <>
                WAPE. Repeating {EARLIER[frequency].replace("a ", "the ").replace(" earlier", " before")}{" "}
                gives {pct(total.reference_wape)}
              </>
            }
          />
          <Tile
            label="Against that reference"
            value={
              better === null ? (
                "–"
              ) : (
                <span className={`delta ${better < 0 ? "up" : "down"}`} style={{ fontWeight: 640 }}>
                  {better < 0 ? "Better" : "Worse"}
                </span>
              )
            }
            note={
              better === null
                ? "No reference for this history"
                : `Error is ${pct(Math.abs(better), 0)} ${better < 0 ? "lower" : "higher"} than the reference's`
            }
          />
          <Tile
            label="Average error"
            value={num(total.mae)}
            note={`MAE, in ${data.target}. RMSE ${num(total.rmse)}`}
          />
          <Tile
            label="Checked from"
            value={`${data.cutoffs.length} dates`}
            note={data.cutoffs.map((date) => period(date, frequency)).join(", ")}
          />
        </div>
      </section>

      <ChartFigure
        title="Error by how far ahead"
        subtitle={`WAPE for each ${unit(frequency, 1)} of the horizon. Lower is better.`}
        dates={steps}
        labels={steps.map((step) => `${step}`)}
        series={[
          {
            id: "model",
            label: "Forecast",
            color: "var(--series-forecast)",
            values: data.by_step.map((row) => row.wape),
            direct: true,
          },
          {
            id: "reference",
            label: `Repeating ${EARLIER[frequency].replace("a ", "the ").replace(" earlier", " before")}`,
            color: "var(--series-reference)",
            values: data.by_step.map((row) => row.reference_wape),
          },
        ]}
        frequency={frequency}
        height={260}
        formatValue={(value) => pct(value)}
        formatTick={(value) => pct(value, 0)}
      />

      <section className="card">
        <div className="card-head">
          <div>
            <h2>Error by series, worst first</h2>
            <div className="sub">
              {data.series.toLocaleString()} series{settled ? " match" : ""}. Click one to see its past
              forecasts.
            </div>
          </div>
          <input
            className="input"
            style={{ width: 240 }}
            placeholder="Search series"
            aria-label="Search series"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Series</th>
                <th style={{ width: 150 }}>Error</th>
                <th className="r">WAPE</th>
                <th className="r">Reference</th>
                <th className="r">MAE</th>
                <th className="r">Dates scored</th>
              </tr>
            </thead>
            <tbody>
              {data.by_series.map((row) => {
                const { main, rest } = splitLabel(row.label);
                return (
                  <tr
                    key={row.ts_id}
                    className={`clickable ${selected?.ts_id === row.ts_id ? "selected" : ""}`}
                    onClick={() =>
                      onSelect(selected?.ts_id === row.ts_id ? null : { ts_id: row.ts_id, label: row.label })
                    }
                  >
                    <td>
                      <div className="cell-main">{main}</div>
                      {rest && <div className="cell-sub">{rest}</div>}
                    </td>
                    <td>
                      <div className="meter" aria-hidden="true">
                        <span style={{ width: `${Math.min(100, ((row.wape ?? 0) / worst) * 100)}%` }} />
                      </div>
                    </td>
                    <td className="r">{pct(row.wape)}</td>
                    <td className="r">{pct(row.reference_wape)}</td>
                    <td className="r">{num(row.mae)}</td>
                    <td className="r">{row.n}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {data.series > data.by_series.length && (
          <div className="card-pad" style={{ borderTop: "1px solid var(--line)", padding: "12px 22px" }}>
            <button className="btn small" onClick={() => setLimit(limit + 25)}>
              Show more
            </button>
            <span className="hint" style={{ marginLeft: 12 }}>
              Showing {data.by_series.length} of {data.series.toLocaleString()}
            </span>
          </div>
        )}
      </section>

      <Comparison
        overview={overview}
        kind="backtest"
        selected={selected}
        onSelect={onSelect}
        pickerFor={issue.forecast_id}
      />
    </div>
  );
}

// Where the forecast is stored.

function StoredTab({ issue, frequency }: { issue: ForecastRecord; frequency: Frequency }) {
  const stored = useQuery({
    queryKey: ["rows", issue.forecast_id],
    queryFn: () => api.rows(issue.forecast_id, 25),
  });
  if (stored.error) return <ErrorNote error={stored.error} />;
  if (!stored.data) {
    return (
      <section className="card">
        <Loading what="Reading the stored rows" />
      </section>
    );
  }
  const data = stored.data;
  const filter = Object.entries(issue.slice_filter);
  return (
    <div className="stack">
      <section className="card">
        <div className="card-head">
          <div>
            <h2>Stored in Iceberg</h2>
            <div className="sub">
              This run is {data.rows.toLocaleString()} rows appended to this forecast's table in the data lake.
              Earlier runs are still there.
            </div>
          </div>
          <a className="btn" href={api.downloadUrl(issue.forecast_id)}>
            <Icon name="download" />
            Download CSV
          </a>
        </div>
        <div className="card-pad">
          <dl className="kv">
            <dt>Values table</dt>
            <dd className="mono">{data.table}</dd>
            <dt>Forecasts table</dt>
            <dd className="mono">{data.forecasts_table}</dd>
            <dt>forecast_id</dt>
            <dd className="mono">{issue.forecast_id}</dd>
            <dt>History used</dt>
            <dd>
              {period(issue.history_start_date, frequency)} to {period(issue.history_end_date, frequency)}
            </dd>
            <dt>Dimension list</dt>
            <dd>{issue.dimension_columns.join(", ") || "everything added together"}</dd>
            <dt>Filter</dt>
            <dd>
              {filter.length === 0
                ? "None: the whole dataset"
                : filter.map(([column, values]) => `${column} is ${values.join(" or ")}`).join("; ")}
            </dd>
            <dt>Model</dt>
            <dd>{issue.model_name}</dd>
          </dl>
        </div>
      </section>

      <section className="card">
        <div className="card-head">
          <div>
            <h2>See it in SQL</h2>
            <div className="sub">Paste this into Hue or any SQL editor connected to the data lake.</div>
          </div>
          <CopyButton text={data.sql} label="Copy SQL" />
        </div>
        <div className="card-pad stack" style={{ gap: 12 }}>
          <pre className="code">{data.sql}</pre>
          <div className="spread">
            <span className="hint">Every run of this forecast, one row each:</span>
            <CopyButton text={data.record_sql} label="Copy SQL" />
          </div>
          <pre className="code">{data.record_sql}</pre>
        </div>
      </section>

      <section className="card">
        <div className="card-head">
          <div>
            <h2>First rows</h2>
            <div className="sub">
              As stored. <span className="mono">step</span> counts {unit(frequency)} after the end of
              history.
            </div>
          </div>
          <Link to="?tab=forecast" className="btn ghost small">
            Back to the chart
          </Link>
        </div>
        <RowsTable columns={data.columns} rows={data.preview} />
      </section>
    </div>
  );
}
