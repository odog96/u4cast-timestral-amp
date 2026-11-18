import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api";
import { ErrorNote, Icon, Loading, useCrumbs } from "../components/ui";
import { FREQUENCY_NAMES } from "../format";

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
        <Link to="/datasets/new" className="btn primary large">
          <Icon name="plus" />
          Bring your data
        </Link>
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
      {datasets.data && datasets.data.length > 0 && (
        <h2 style={{ margin: "0 0 12px" }}>Your data</h2>
      )}
      <div className="grid-cards">
        {(datasets.data ?? []).map((dataset) => {
          const here = `/datasets/${encodeURIComponent(dataset.name)}`;
          return (
            <div key={dataset.name} className="card card-pad">
              <div className="spread" style={{ alignItems: "flex-start" }}>
                <h2>
                  <Link to={here}>{dataset.name}</Link>
                </h2>
                <span className="badge">{FREQUENCY_NAMES[dataset.frequency]}</span>
              </div>
              <div className="muted" style={{ margin: "6px 0 16px", fontSize: 13 }}>
                By {dataset.dimension_columns.join(", ") || "date only"}
              </div>
              <div className="spread">
                <Link to={`${here}/forecast`} className="btn primary">
                  <Icon name="plus" />
                  New forecast
                </Link>
                <Link to={here} className="row muted" style={{ gap: 6, fontSize: 13 }}>
                  {dataset.forecasts === 0
                    ? "No forecast yet"
                    : `${dataset.forecasts} forecast${dataset.forecasts === 1 ? "" : "s"}`}
                  <Icon name="arrow" size={14} />
                </Link>
              </div>
            </div>
          );
        })}
      </div>
    </main>
  );
}
