// Clean up: the forecasts that are not in production, with the age and size of their tables.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type Cleanup } from "../api";
import { ErrorNote, Loading, Tile, useCrumbs } from "../components/ui";
import { day } from "../format";

export function size(bytes: number): string {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(1)} MB`;
  if (bytes >= 1e3) return `${Math.round(bytes / 1e3)} KB`;
  return `${bytes} bytes`;
}

export function age(iso: string): string {
  // Count calendar days, so that something last run yesterday is "1 day" old.
  const then = new Date(iso);
  const now = new Date();
  const days = Math.round(
    (Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()) -
      Date.UTC(then.getFullYear(), then.getMonth(), then.getDate())) /
      86_400_000,
  );
  if (days <= 0) return "today";
  if (days === 1) return "1 day";
  return days < 60 ? `${days} days` : `${Math.round(days / 30)} months`;
}

export function CleanupPage() {
  useCrumbs([{ label: "Clean up" }]);
  const client = useQueryClient();
  const cleanup = useQuery({ queryKey: ["cleanup"], queryFn: api.cleanup });
  const remove = useMutation({
    mutationFn: (row: { dataset: string; forecast_name: string }) =>
      api.deleteForecast(row.dataset, row.forecast_name),
    onSuccess: (data: Cleanup) => {
      client.setQueryData(["cleanup"], data);
      client.invalidateQueries({ queryKey: ["dataset"] });
      client.invalidateQueries({ queryKey: ["datasets"] });
    },
  });

  if (cleanup.isLoading) {
    return (
      <main className="page">
        <Loading what="Measuring the forecast tables" />
      </main>
    );
  }
  if (cleanup.error || !cleanup.data) {
    return (
      <main className="page">
        <ErrorNote error={cleanup.error ?? new Error("The forecast tables could not be read.")} />
      </main>
    );
  }
  const data = cleanup.data;
  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>Clean up</h1>
          <p className="sub">
            Every forecast has its own table, and trying things out leaves tables behind. Delete
            the forecasts you no longer need. A forecast in production is not listed here.
          </p>
        </div>
      </div>
      <div className="stack">
        <section className="card">
          <div className="tiles">
            <Tile label="Forecasts" value={data.forecasts.length} note="Not in production" />
            <Tile label="Tables" value={data.tables} note="One per forecast, and one for test runs" />
            <Tile label="Size" value={size(data.bytes)} note="Of their files in the data lake" />
            <Tile
              label="In production"
              value={data.in_production}
              note="Kept out of this list"
            />
          </div>
        </section>
        <section className="card">
          <div className="card-head">
            <div>
              <h2>Forecasts you can delete</h2>
              <div className="sub">
                Oldest first. Deleting drops the forecast's tables and all its runs. It cannot be
                undone.
              </div>
            </div>
          </div>
          {remove.error && (
            <div className="card-pad">
              <ErrorNote error={remove.error} />
            </div>
          )}
          {data.forecasts.length === 0 ? (
            <div className="card-pad">
              <p className="sub">There is nothing to clean up.</p>
            </div>
          ) : (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Forecast</th>
                    <th>Last run</th>
                    <th className="r">Age</th>
                    <th className="r">Runs</th>
                    <th className="r">Tables</th>
                    <th className="r">Rows</th>
                    <th className="r">Size</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {data.forecasts.map((row) => (
                    <tr key={`${row.dataset}|${row.forecast_name}`}>
                      <td>
                        <Link
                          to={`/datasets/${encodeURIComponent(row.dataset)}/forecasts/${encodeURIComponent(row.forecast_name)}`}
                          className="cell-main"
                          style={{ display: "block" }}
                        >
                          {row.forecast_name}
                        </Link>
                        <span className="cell-sub">{row.dataset}</span>
                      </td>
                      <td className="nowrap">{day(row.last_run)}</td>
                      <td className="r nowrap">{age(row.last_run)}</td>
                      <td className="r">
                        {row.runs}
                        {row.test_runs > 0 && <span className="muted"> + {row.test_runs} test</span>}
                      </td>
                      <td className="r" title={row.tables.join("\n")}>
                        {row.tables.length}
                      </td>
                      <td className="r">{row.rows.toLocaleString()}</td>
                      <td className="r nowrap">{size(row.bytes)}</td>
                      <td className="r">
                        <button
                          className="btn small"
                          disabled={remove.isPending}
                          onClick={() => {
                            const tables = row.tables.join("\n");
                            if (
                              window.confirm(
                                `Delete the forecast "${row.forecast_name}" for good?\n\nThis drops:\n${tables}\n\nIt cannot be undone.`,
                              )
                            )
                              remove.mutate(row);
                          }}
                        >
                          {remove.isPending && remove.variables === row ? "Deleting…" : "Delete"}
                        </button>
                      </td>
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
