// Select a slice of a dataset and create a forecast for it.

import { useMutation, useQuery } from "@tanstack/react-query";
import { useCallback, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, type DatasetOverview, type Frequency, type Job, type SliceRequest } from "../api";
import { JobProgress } from "../components/JobProgress";
import { MultiSelect } from "../components/pickers";
import { ErrorNote, Hint, Icon, Loading, useCrumbs, useDebounced } from "../components/ui";
import { capital, duration, modelName, period, periods, unit } from "../format";

const HORIZONS: Record<Frequency, number[]> = {
  daily: [7, 14, 28, 90],
  weekly: [4, 13, 26, 52],
  monthly: [3, 6, 12, 24],
  quarterly: [2, 4, 8],
};
const DEFAULT_HORIZON: Record<Frequency, number> = { daily: 28, weekly: 13, monthly: 12, quarterly: 4 };
const DEFAULT_STEP: Record<Frequency, number> = { daily: 28, weekly: 13, monthly: 3, quarterly: 1 };

const REASONS: Record<string, string> = {
  too_short: "too little history",
  no_values: "no values in these dates",
};

export function NewForecast() {
  const { dataset: name = "" } = useParams();
  const here = `/datasets/${encodeURIComponent(name)}`;
  useCrumbs([{ label: name, to: `${here}/forecasts` }, { label: "New forecast" }]);
  const overview = useQuery({ queryKey: ["dataset", name], queryFn: () => api.dataset(name) });

  if (overview.isLoading) {
    return (
      <main className="page">
        <Loading what="Reading the dataset" />
      </main>
    );
  }
  if (overview.error || !overview.data) {
    return (
      <main className="page">
        <ErrorNote error={overview.error ?? new Error("The dataset could not be read.")} />
      </main>
    );
  }
  return <Builder dataset={overview.data} />;
}

function Builder({ dataset }: { dataset: DatasetOverview }) {
  const navigate = useNavigate();
  const frequency = dataset.frequency;
  const dates = dataset.dates;
  const [target, setTarget] = useState<string>(dataset.target_column);
  const models = useQuery({ queryKey: ["models"], queryFn: api.models });
  const [model, setModel] = useState<string | null>(null);
  const chosenModel = model ?? models.data?.find((choice) => choice.default)?.key ?? null;
  const [dimensions, setDimensions] = useState<string[]>(dataset.dimension_columns);
  const [filters, setFilters] = useState<Record<string, (string | number)[]>>({});
  const [start, setStart] = useState<string | null>(null);
  const [end, setEnd] = useState<string | null>(null);
  const [horizon, setHorizon] = useState(DEFAULT_HORIZON[frequency]);
  const [check, setCheck] = useState(true);
  const [cutoffs, setCutoffs] = useState(4);
  const [step, setStep] = useState(DEFAULT_STEP[frequency]);
  const [shortHistory, setShortHistory] = useState(false);
  const [forecastName, setForecastName] = useState("");
  const [job, setJob] = useState<Job | null>(null);

  const request: SliceRequest = useMemo(
    () => ({
      // Keep the dataset's own column order, whatever order they were clicked in.
      dimension_columns: dataset.dimension_columns.filter((column) => dimensions.includes(column)),
      slice_filter: filters,
      history_start: start,
      history_end: end,
      minimum_history: shortHistory ? dataset.minimum_history_floor : null,
      target_column: target,
      horizon: Math.max(1, horizon || 1),
      cutoffs: check ? Math.max(0, cutoffs || 0) : 0,
      cutoff_step: Math.max(1, step || 1),
    }),
    [dataset, target, dimensions, filters, start, end, shortHistory, horizon, check, cutoffs, step],
  );
  const settled = useDebounced(request, 350);
  const summary = useQuery({
    queryKey: ["slice", dataset.name, settled],
    queryFn: () => api.slice(dataset.name, settled),
    placeholderData: (previous) => previous,
    retry: false,
  });
  const create = useMutation({
    mutationFn: () => api.createForecast(dataset.name, { ...request, forecast_name: chosenName, model: chosenModel }),
    onSuccess: setJob,
  });
  const onDone = useCallback(
    (done: Job) => {
      if (done.result) {
        navigate(
          `/datasets/${encodeURIComponent(done.result.dataset)}/forecasts/${encodeURIComponent(
            done.result.forecast_name,
          )}`,
        );
      }
    },
    [navigate],
  );

  // A name is offered, so a forecast can be made without typing one.
  const taken = new Set(dataset.forecast_groups.map((group) => group.forecast_name));
  const base = `${capital(target)}, ${periods(Math.max(1, horizon || 1), frequency)} ahead`;
  let suggested = base;
  for (let n = 2; taken.has(suggested); n += 1) suggested = `${base} (${n})`;
  const chosenName = forecastName.trim() || suggested;

  const data = summary.data;
  const stale = summary.isFetching || settled !== request;
  const toggleDimension = (column: string) =>
    setDimensions(dimensions.includes(column) ? dimensions.filter((c) => c !== column) : [...dimensions, column]);
  const canShorten = dataset.minimum_history > dataset.minimum_history_floor;
  const total = data ? data.seconds + (check ? data.backtest_seconds : 0) : 0;

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>New forecast</h1>
          <p className="sub">
            Choose what to forecast in {dataset.name}, for which series, and how far ahead. Every
            choice has a default: the button on the right works as it is.
          </p>
        </div>
      </div>

      <div className="grid-2">
        <section className="card">
          <div className="form-section">
            <h2>
              <span className="step-no">1</span>What to forecast
            </h2>
            <p className="sub">The number in {dataset.name} to forecast.</p>
            <div className="chips">
              {dataset.number_columns.map((column) => (
                <button
                  key={column}
                  className={`chip ${target === column ? "on" : ""}`}
                  onClick={() => setTarget(column)}
                  aria-pressed={target === column}
                >
                  {column}
                </button>
              ))}
            </div>
          </div>

          <div className="form-section">
            <h2>
              <span className="step-no">2</span>Dimension list
              <Hint
                text="The dimension list sets the level you forecast at. Each combination of the columns in it gets its own forecast. Take a column out and its values are added together: with county and item you forecast every item in every county; with item only, every item across all counties."
              />
            </h2>
            <p className="sub">
              The level to forecast at. Each combination of these columns gets its own forecast;
              take a column out to add its values together.
            </p>
            <div className="chips">
              {dataset.dimension_columns.map((column) => (
                <button
                  key={column}
                  className={`chip ${dimensions.includes(column) ? "on" : ""}`}
                  onClick={() => toggleDimension(column)}
                  aria-pressed={dimensions.includes(column)}
                >
                  {column}
                </button>
              ))}
            </div>
            {dimensions.length === 0 && (
              <p className="hint" style={{ marginTop: 10 }}>
                With no column chosen, everything is added into one series.
              </p>
            )}
          </div>

          <div className="form-section">
            <h2>
              <span className="step-no">3</span>Only these
            </h2>
            <p className="sub">Narrow the data by any column. The filter runs in the data lake.</p>
            <div className="stack" style={{ gap: 12 }}>
              {dataset.dimension_columns.map((column) => (
                <div key={column} className="row" style={{ alignItems: "flex-start", gap: 14 }}>
                  <span className="label" style={{ width: 96, paddingTop: 9, flex: "none" }}>
                    {column}
                  </span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <MultiSelect
                      dataset={dataset.name}
                      column={column}
                      chosen={filters[column] ?? []}
                      onChange={(values) => setFilters({ ...filters, [column]: values })}
                    />
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="form-section">
            <h2>
              <span className="step-no">4</span>History to use
            </h2>
            <p className="sub">
              The forecast starts after the last date. Choose an earlier one to forecast as of the
              past.
            </p>
            <div className="row wrap" style={{ gap: 16 }}>
              <PeriodSelect
                id="history-start"
                label="From"
                dates={dates}
                frequency={frequency}
                value={start}
                onChange={setStart}
                first="The first date"
              />
              <PeriodSelect
                id="history-end"
                label="To"
                dates={dates}
                frequency={frequency}
                value={end}
                onChange={setEnd}
                first="The latest date"
                newestFirst
              />
            </div>
            {data && data.by_start.length > 1 && (
              <div style={{ marginTop: 14 }}>
                <div className="hint" style={{ marginBottom: 6 }}>
                  Series kept by where the history starts. A series needs{" "}
                  {periods(data.minimum_history, frequency)}.
                </div>
                <div className="chips">
                  {data.by_start.map((option) => (
                    <button
                      key={option.start}
                      className={`chip ${(start ?? data.by_start[0].start) === option.start ? "on" : ""}`}
                      onClick={() => setStart(option.start === data.by_start[0].start ? null : option.start)}
                    >
                      From {period(option.start, frequency)} · {option.series.toLocaleString()} series
                    </button>
                  ))}
                </div>
              </div>
            )}
            {canShorten && (
              <label className="row" style={{ marginTop: 14, cursor: "pointer" }}>
                <input
                  type="checkbox"
                  checked={shortHistory}
                  onChange={(event) => setShortHistory(event.target.checked)}
                />
                <span>
                  Also keep series with as little as {periods(dataset.minimum_history_floor, frequency)}{" "}
                  of history
                </span>
              </label>
            )}
          </div>

          <div className="form-section">
            <h2>
              <span className="step-no">5</span>How far ahead
            </h2>
            <p className="sub">The number of {unit(frequency)} to forecast after the end of history.</p>
            <div className="row wrap">
              <input
                className="input narrow num"
                type="number"
                min={1}
                max={1024}
                aria-label={`${unit(frequency)} ahead`}
                value={horizon || ""}
                onChange={(event) => setHorizon(Number(event.target.value))}
              />
              <span className="muted">{unit(frequency, horizon)}</span>
              <div className="chips" style={{ marginLeft: 8 }}>
                {HORIZONS[frequency].map((value) => (
                  <button
                    key={value}
                    className={`chip ${horizon === value ? "on" : ""}`}
                    onClick={() => setHorizon(value)}
                  >
                    {value}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="form-section">
            <h2>
              <span className="step-no">6</span>Check accuracy on the past
            </h2>
            <p className="sub">
              The same forecast is made from earlier dates and compared with what then happened.
            </p>
            <label className="row" style={{ cursor: "pointer" }}>
              <input type="checkbox" checked={check} onChange={(event) => setCheck(event.target.checked)} />
              <span>Check accuracy with this forecast</span>
            </label>
            {check && (
              <div className="row wrap" style={{ marginTop: 12 }}>
                <span className="muted">From</span>
                <input
                  className="input narrow num"
                  type="number"
                  min={1}
                  max={12}
                  aria-label="Number of past dates"
                  value={cutoffs || ""}
                  onChange={(event) => setCutoffs(Number(event.target.value))}
                />
                <span className="muted">past dates,</span>
                <input
                  className="input narrow num"
                  type="number"
                  min={1}
                  aria-label={`${unit(frequency)} between them`}
                  value={step || ""}
                  onChange={(event) => setStep(Number(event.target.value))}
                />
                <span className="muted">{unit(frequency, step)} apart</span>
              </div>
            )}
            {check && data && (
              <p className="hint" style={{ marginTop: 10 }}>
                {data.backtest_cutoffs.length
                  ? `Forecasting from ${data.backtest_cutoffs.map((d) => period(d, frequency)).join(", ")}.`
                  : `The history is too short to hold back ${periods(request.horizon, frequency)}. No check will run.`}
              </p>
            )}
          </div>
        </section>

        <aside className="card sticky">
          <div className="card-pad">
            <div className="label">This forecast covers</div>
            {summary.error ? (
              <div style={{ marginTop: 10 }}>
                <ErrorNote error={summary.error} />
              </div>
            ) : !data ? (
              <Loading what="Reading the slice from the data lake" />
            ) : (
              <div style={{ opacity: stale ? 0.55 : 1, transition: "opacity 0.15s" }}>
                <div className="hero" style={{ margin: "6px 0 2px" }}>
                  {data.series.toLocaleString()}
                </div>
                <div className="muted" style={{ marginBottom: 14 }}>
                  series, each with {periods(data.steps, frequency)} of history
                </div>
                <dl className="kv">
                  <dt>History</dt>
                  <dd>
                    {period(data.history_start, frequency)} to {period(data.history_end, frequency)}
                  </dd>
                  <dt>Forecast</dt>
                  <dd>
                    {periods(request.horizon, frequency)}, {(data.series * request.horizon).toLocaleString()} rows
                  </dd>
                  <dt>Takes about</dt>
                  <dd>{duration(total)}</dd>
                  {data.left_out > 0 && (
                    <>
                      <dt>Left out</dt>
                      <dd>
                        {data.left_out.toLocaleString()} series:{" "}
                        {Object.entries(data.left_out_by_reason)
                          .map(([reason, count]) => `${count.toLocaleString()} with ${REASONS[reason] ?? reason}`)
                          .join(", ")}
                      </dd>
                    </>
                  )}
                </dl>
                {data.left_out > 0 && (
                  <details style={{ marginTop: 10 }}>
                    <summary className="hint" style={{ cursor: "pointer" }}>
                      Show the first {Math.min(data.left_out, data.left_out_sample.length)} left out
                    </summary>
                    <ul style={{ margin: "8px 0 0", paddingLeft: 18, fontSize: 12.5 }} className="muted">
                      {data.left_out_sample.map((series) => (
                        <li key={series.label}>
                          {series.label}: {periods(series.steps, frequency)}
                          {series.first_date ? ` from ${period(series.first_date, frequency)}` : ""}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
                {data.values_filled + data.values_missing > 0 && (
                  <p className="hint" style={{ marginTop: 10 }}>
                    {data.values_filled > 0 &&
                      `${data.values_filled.toLocaleString()} dates with no row were filled as this dataset says. `}
                    {data.values_missing > 0 &&
                      `${data.values_missing.toLocaleString()} dates with no row stay empty.`}
                  </p>
                )}
              </div>
            )}
          </div>
          <div className="card-pad" style={{ borderTop: "1px solid var(--line)" }}>
            {job ? (
              <JobProgress job={job} onDone={onDone} onDismiss={() => setJob(null)} />
            ) : (
              <div className="stack" style={{ gap: 12 }}>
                <div className="field">
                  <label htmlFor="forecast-model">Forecasting model</label>
                  <select
                    id="forecast-model"
                    className="select"
                    value={chosenModel ?? ""}
                    onChange={(event) => setModel(event.target.value)}
                  >
                    {(models.data ?? []).map((choice) => (
                      <option key={choice.key} value={choice.key}>
                        {modelName(choice.model_name)}
                        {choice.key !== modelName(choice.model_name) ? ` (${choice.key})` : ""}
                      </option>
                    ))}
                  </select>
                  <div className="hint">
                    {(models.data?.length ?? 0) > 1
                      ? "The models deployed in this workbench for forecasting."
                      : "The one model deployed in this workbench. Others appear here when deployed."}
                  </div>
                </div>
                <div className="field">
                  <label htmlFor="forecast-name">Name</label>
                  <input
                    id="forecast-name"
                    className="input"
                    placeholder={suggested}
                    value={forecastName}
                    onChange={(event) => setForecastName(event.target.value)}
                  />
                </div>
                <button
                  className="btn primary large block"
                  disabled={!data || data.series === 0 || stale || create.isPending}
                  onClick={() => create.mutate()}
                >
                  Create forecast
                  <Icon name="arrow" />
                </button>
                {data && data.series === 0 && (
                  <div className="notice warning">
                    No series has enough history in these dates. Start earlier or change the filter.
                  </div>
                )}
                {create.error && <ErrorNote error={create.error} />}
              </div>
            )}
          </div>
        </aside>
      </div>
    </main>
  );
}

function PeriodSelect({
  id,
  label,
  dates,
  frequency,
  value,
  onChange,
  first,
  newestFirst,
}: {
  id: string;
  label: string;
  dates: string[];
  frequency: Frequency;
  value: string | null;
  onChange: (value: string | null) => void;
  first: string;
  newestFirst?: boolean;
}) {
  // A list of periods reads better than a calendar; a very long history gets a date box.
  if (dates.length > 420) {
    return (
      <div className="field" style={{ flex: "1 1 200px" }}>
        <label htmlFor={id}>{label}</label>
        <input
          id={id}
          className="input"
          type="date"
          min={dates[0]}
          max={dates[dates.length - 1]}
          value={value ?? ""}
          onChange={(event) => onChange(event.target.value || null)}
        />
      </div>
    );
  }
  const options = newestFirst ? [...dates].reverse() : dates;
  return (
    <div className="field" style={{ flex: "1 1 200px" }}>
      <label htmlFor={id}>{label}</label>
      <select
        id={id}
        className="select"
        value={value ?? ""}
        onChange={(event) => onChange(event.target.value || null)}
      >
        <option value="">
          {first} ({period(options[0], frequency)})
        </option>
        {options.slice(1).map((date) => (
          <option key={date} value={date}>
            {period(date, frequency)}
          </option>
        ))}
      </select>
    </div>
  );
}
