import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api";
import { ErrorNote, Icon, Loading, useCrumbs } from "../components/ui";
import { FREQUENCY_NAMES } from "../format";
import { age, size } from "./Cleanup";

export function Datasets() {
  useCrumbs([]);
  const datasets = useQuery({ queryKey: ["datasets"], queryFn: api.datasets });

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>Forecast your data</h1>
          <p className="sub">
            Bring a table of history, say which number to forecast, and get a forecast for every
            series in it. No code, and no model to train.
          </p>
        </div>
      </div>

      <section className="card card-pad" style={{ marginBottom: 20 }}>
        <ol className="steps">
          <li>
            <span className="step-no">1</span>
            <div>
              <strong>Bring your data</strong>
              <span>A table in the data lake, or a CSV file. The columns are worked out for you.</span>
            </div>
          </li>
          <li>
            <span className="step-no">2</span>
            <div>
              <strong>Say what to forecast</strong>
              <span>Which number, for which products or places, and how far ahead.</span>
            </div>
          </li>
          <li>
            <span className="step-no">3</span>
            <div>
              <strong>Use the forecast</strong>
              <span>Explore it here, run it again when new data arrives, read it from Iceberg.</span>
            </div>
          </li>
        </ol>
      </section>

      {datasets.isLoading && <Loading what="Loading your data" />}
      {datasets.error && <ErrorNote error={datasets.error} />}
      <section className="card">
        <div className="card-head">
          <div>
            <h2>Your datasets</h2>
            <div className="sub">
              Forecast opens a dataset's forecasts. Explore shows its history.
            </div>
          </div>
          <Link to="/datasets/new" className="btn primary">
            <Icon name="plus" />
            Add a dataset
          </Link>
        </div>
        {datasets.data && datasets.data.length === 0 ? (
          <div className="empty">
            <h2>No dataset yet</h2>
            <p>Point the application at an Iceberg table, or upload a CSV file, to start forecasting.</p>
          </div>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Dataset</th>
                  <th>Frequency</th>
                  <th>Dimension list</th>
                  <th className="r">Forecasts</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {(datasets.data ?? []).map((dataset) => {
                  const here = `/datasets/${encodeURIComponent(dataset.name)}`;
                  return (
                    <tr key={dataset.name}>
                      <td>
                        <Link to={`${here}/forecasts`} className="cell-main" style={{ display: "block" }}>
                          {dataset.name}
                        </Link>
                        <span className="cell-sub mono">{dataset.source_table}</span>
                      </td>
                      <td>{FREQUENCY_NAMES[dataset.frequency]}</td>
                      <td>{dataset.dimension_columns.join(", ") || "date only"}</td>
                      <td className="r key-number">{dataset.forecasts}</td>
                      <td className="r nowrap">
                        <Link to={`${here}/forecasts`} className="btn primary small">
                          Forecast
                        </Link>{" "}
                        <Link to={here} className="btn small">
                          Explore
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <CleanupNote />
    </main>
  );
}

/** A quiet line under the datasets: how many forecast tables there are, and where to clean up. */
function CleanupNote() {
  const cleanup = useQuery({ queryKey: ["cleanup"], queryFn: api.cleanup });
  const data = cleanup.data;
  if (!data || data.tables === 0 || !data.oldest) return null;
  return (
    <p className="sub" style={{ marginTop: 16, fontSize: 13 }}>
      You have {data.tables} forecast table{data.tables === 1 ? "" : "s"} that are not in
      production, {size(data.bytes)} in all, the oldest last run {age(data.oldest) === "today" ? "today" : `${age(data.oldest)} ago`}.{" "}
      <Link to="/cleanup">Clean up</Link>
    </p>
  );
}
