// One dataset, two screens: Explore shows its history, Forecasts lists what was forecast from it.

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type DatasetOverview } from "../api";
import { ChartFigure } from "../components/LineChart";
import { CopyButton, ErrorNote, Icon, Loading, RowsTable, Tile, useCrumbs } from "../components/ui";
import { day, FILLS, FREQUENCY_NAMES, period, periods } from "../format";

export function DatasetPage() {
  const { dataset: name = "" } = useParams();
  useCrumbs([{ label: name }, { label: "Explore" }]);
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
  const dataset = overview.data;
  const dates = dataset.dates;
  const here = `/datasets/${encodeURIComponent(name)}`;
  const roles: Record<string, string> = {
    [dataset.date_column]: "Date",
    [dataset.target_column]: "Number to forecast",
  };
  for (const column of dataset.dimension_columns) roles[column] = "Dimension";
  const sql = `select *\nfrom ${dataset.source_table}\norder by ${dataset.date_column} desc\nlimit 100;`;

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>{dataset.name}</h1>
          <p className="sub">
            {FREQUENCY_NAMES[dataset.frequency]} history, {periods(dates.length, dataset.frequency)}
            {dates.length > 0 &&
              `, ${period(dates[0], dataset.frequency)} to ${period(dates[dates.length - 1], dataset.frequency)}`}
            . Dimension list: {dataset.dimension_columns.join(", ") || "none"}.
          </p>
        </div>
        <Link to={`${here}/forecasts`} className="btn primary large">
          Forecasts
          <Icon name="arrow" />
        </Link>
      </div>

      <div className="stack">
        <History dataset={dataset} />


        <section className="card">
          <div className="tiles">
            <Tile label="Rows" value={dataset.source.rows.toLocaleString()} note="In the Iceberg table" />
            <Tile
              label="Columns"
              value={dataset.source.columns.length}
              note={`${dataset.dimension_columns.length} in the dimension list, ${dataset.number_columns.length} numbers`}
            />
            <Tile
              label="History"
              value={periods(dates.length, dataset.frequency)}
              note={
                dates.length
                  ? `${period(dates[0], dataset.frequency)} to ${period(dates[dates.length - 1], dataset.frequency)}`
                  : "No dates"
              }
            />
            <Tile label="Forecasts" value={dataset.forecast_groups.length} note={`${dataset.issues} issues stored`} />
          </div>
        </section>

        <section className="card">
          <div className="card-head">
            <div>
              <h2>Schema</h2>
              <div className="sub">
                Read from <span className="mono">{dataset.source_table}</span>. The application never
                writes to this table.
              </div>
            </div>
            <CopyButton text={sql} label="Copy SQL" />
          </div>
          <div className="card-pad" style={{ borderBottom: "1px solid var(--line)" }}>
            <dl className="kv">
              {dataset.source.columns.map((column) => (
                <div key={column.name} style={{ display: "contents" }}>
                  <dt className="mono">{column.name}</dt>
                  <dd>
                    <span className="faint mono">{column.type}</span>
                    {roles[column.name] && (
                      <span className="badge" style={{ marginLeft: 10 }}>
                        {roles[column.name]}
                      </span>
                    )}
                  </dd>
                </div>
              ))}
              <dt>A date with no row</dt>
              <dd>{FILLS.find((option) => option.value === dataset.fill)?.name}</dd>
              <dt>Forecasts are stored in</dt>
              <dd className="mono">{dataset.values_table}</dd>
            </dl>
          </div>
        </section>

        <section className="card">
          <div className="card-head">
            <div>
              <h2>First 10 rows</h2>
              <div className="sub">As they are in the table.</div>
            </div>
          </div>
          <RowsTable
            columns={dataset.source.columns.map((column) => column.name)}
            rows={dataset.source.preview.slice(0, 10)}
          />
        </section>
      </div>
    </main>
  );
}

/** The whole dataset added together over time, for whichever number is chosen. */
function History({ dataset }: { dataset: DatasetOverview }) {
  const [target, setTarget] = useState(dataset.target_column);
  const history = useQuery({
    queryKey: ["history", dataset.name, target],
    queryFn: () => api.history(dataset.name, target),
    placeholderData: (previous) => previous,
  });
  const chips = dataset.number_columns.length > 1 && (
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
  );
  if (history.error) return <ErrorNote error={history.error} />;
  if (!history.data) {
    return (
      <section className="card card-pad">
        <Loading what="Reading the history" />
      </section>
    );
  }
  return (
    <ChartFigure
      title="History"
      subtitle={`${history.data.target} by ${UNIT[dataset.frequency]}, everything in this data added together`}
      actions={chips}
      dates={history.data.dates}
      series={[
        { id: "actual", label: "Actual", color: "var(--series-actual)", values: history.data.values },
      ]}
      frequency={dataset.frequency}
    />
  );
}

const UNIT = { daily: "day", weekly: "week", monthly: "month", quarterly: "quarter" } as const;

/** The forecasts of one dataset: every one made so far, and the way to make a new one. */
export function DatasetForecasts() {
  const { dataset: name = "" } = useParams();
  useCrumbs([{ label: name }, { label: "Forecasts" }]);
  const navigate = useNavigate();
  const client = useQueryClient();
  const overview = useQuery({ queryKey: ["dataset", name], queryFn: () => api.dataset(name) });
  if (overview.isLoading) {
    return (
      <main className="page">
        <Loading what="Reading the forecasts" />
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
  const dataset = overview.data;
  const here = `/datasets/${encodeURIComponent(name)}`;
  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>Forecasts of {dataset.name}</h1>
          <p className="sub">
            Every forecast made from this dataset. Open one to explore it, or make a new one.
          </p>
        </div>
        <div className="row">
          <Link to={here} className="btn">
            Explore the data
          </Link>
          <Link to={`${here}/forecast`} className="btn primary large">
            <Icon name="plus" />
            New forecast
          </Link>
        </div>
      </div>
      <div className="stack">
        <section className="card">
          <div className="card-head">
            <div>
              <h2>Forecasts</h2>
              <div className="sub">Each forecast keeps every issue made of it. Open one to explore it.</div>
            </div>
          </div>
          {dataset.forecast_groups.length === 0 ? (
            <div className="empty">
              <h2>No forecast yet</h2>
              <p>Choose which series to forecast and how far ahead. The result is stored in Iceberg.</p>
              <Link to={`${here}/forecast`} className="btn primary">
                <Icon name="plus" />
                New forecast
              </Link>
            </div>
          ) : (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Forecast</th>
                    <th>Of</th>
                    <th>History to</th>
                    <th className="r">Series</th>
                    <th className="r">Ahead</th>
                    <th className="r">Issues</th>
                    <th>Accuracy</th>
                    <th>Last issued</th>
                  </tr>
                </thead>
                <tbody>
                  {dataset.forecast_groups.map((group) => (
                    <tr
                      key={group.forecast_name}
                      className="clickable"
                      onClick={() => navigate(`${here}/forecasts/${encodeURIComponent(group.forecast_name)}`)}
                      onMouseEnter={() =>
                        client.prefetchQuery({
                          queryKey: ["chart", group.latest.forecast_id, null, {}],
                          queryFn: () => api.chart(group.latest.forecast_id, null),
                          staleTime: 60_000,
                        })
                      }
                    >
                      <td>
                        <Link
                          to={`${here}/forecasts/${encodeURIComponent(group.forecast_name)}`}
                          className="cell-main"
                          style={{ display: "block" }}
                        >
                          {group.forecast_name}
                        </Link>
                        <span className="cell-sub">By {group.latest.dimension_columns.join(", ") || "total"}</span>
                      </td>
                      <td>{group.latest.target_column}</td>
                      <td className="nowrap">{period(group.latest.history_end_date, dataset.frequency)}</td>
                      <td className="r">{group.latest.series?.toLocaleString() ?? "–"}</td>
                      <td className="r nowrap">{periods(group.latest.horizon, dataset.frequency)}</td>
                      <td className="r">{group.issues}</td>
                      <td>
                        {group.backtests > 0 ? (
                          <span className="badge accent">Checked on {group.backtests} dates</span>
                        ) : (
                          <span className="faint">Not checked</span>
                        )}
                      </td>
                      <td className="nowrap muted">{day(group.latest.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
