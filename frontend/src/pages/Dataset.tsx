// One dataset: its forecasts, and the history it reads.

import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { CopyButton, ErrorNote, Icon, Loading, RowsTable, Tile, useCrumbs } from "../components/ui";
import { day, FILLS, FREQUENCY_NAMES, period, periods } from "../format";

export function DatasetPage() {
  const { dataset: name = "" } = useParams();
  useCrumbs([{ label: name }]);
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
    [dataset.target_column]: "Value to forecast",
  };
  for (const column of dataset.dimension_columns) roles[column] = "Tells series apart";
  const sql = `select *\nfrom ${dataset.source_table}\norder by ${dataset.date_column} desc\nlimit 100;`;

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>{dataset.name}</h1>
          <p className="sub">
            {FREQUENCY_NAMES[dataset.frequency]} history of{" "}
            <strong style={{ color: "var(--ink)" }}>{dataset.target_column}</strong> in{" "}
            <span className="mono">{dataset.source_table}</span>
          </p>
        </div>
        <Link to={`${here}/forecast`} className="btn primary large">
          <Icon name="plus" />
          New forecast
        </Link>
      </div>

      <div className="stack">
        <section className="card">
          <div className="tiles">
            <Tile
              label="History"
              value={periods(dates.length, dataset.frequency)}
              note={
                dates.length
                  ? `${period(dates[0], dataset.frequency)} to ${period(dates[dates.length - 1], dataset.frequency)}`
                  : "No dates"
              }
            />
            <Tile label="Rows" value={dataset.source.rows.toLocaleString()} note="In the Iceberg table" />
            <Tile
              label="Forecasts"
              value={dataset.forecast_groups.length}
              note={`${dataset.issues} issue${dataset.issues === 1 ? "" : "s"} stored`}
            />
            <Tile
              label="Minimum history"
              value={periods(dataset.minimum_history, dataset.frequency)}
              note="A series with less is left out"
            />
          </div>
        </section>

        <section className="card">
          <div className="card-head">
            <div>
              <h2>Forecasts</h2>
              <div className="sub">Each forecast keeps every issue made of it.</div>
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
                    <tr key={group.forecast_name} className="clickable">
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

        <section className="card">
          <div className="card-head">
            <div>
              <h2>History</h2>
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
          <RowsTable
            columns={dataset.source.columns.map((column) => column.name)}
            rows={dataset.source.preview.slice(0, 8)}
          />
        </section>
      </div>
    </main>
  );
}
