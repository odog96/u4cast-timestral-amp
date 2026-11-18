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
          <h1>Datasets</h1>
          <p className="sub">
            A dataset is a table of history in the data lake, with its date, the value to forecast
            and the columns that tell its series apart.
          </p>
        </div>
        <Link to="/datasets/new" className="btn primary">
          <Icon name="plus" />
          Add a dataset
        </Link>
      </div>

      {datasets.isLoading && <Loading what="Loading datasets" />}
      {datasets.error && <ErrorNote error={datasets.error} />}
      {datasets.data && datasets.data.length === 0 && (
        <div className="card empty">
          <h2>No dataset yet</h2>
          <p>Point the application at an Iceberg table, or upload a CSV file, to start forecasting.</p>
          <Link to="/datasets/new" className="btn primary">
            <Icon name="plus" />
            Add a dataset
          </Link>
        </div>
      )}
      <div className="grid-cards">
        {(datasets.data ?? []).map((dataset) => (
          <Link
            key={dataset.name}
            to={`/datasets/${encodeURIComponent(dataset.name)}`}
            className="card link card-pad"
          >
            <div className="spread" style={{ alignItems: "flex-start" }}>
              <h2>{dataset.name}</h2>
              <span className="badge">{FREQUENCY_NAMES[dataset.frequency]}</span>
            </div>
            <div className="mono faint" style={{ marginTop: 4 }}>
              {dataset.source_table}
            </div>
            <div className="chips" style={{ margin: "14px 0 16px" }}>
              {dataset.dimension_columns.map((column) => (
                <span key={column} className="chip static">
                  {column}
                </span>
              ))}
            </div>
            <div className="spread muted" style={{ fontSize: 13 }}>
              <span>
                Forecasting <strong style={{ color: "var(--ink)" }}>{dataset.target_column}</strong>
              </span>
              <span className="row" style={{ gap: 6 }}>
                {dataset.forecasts === 0
                  ? "No forecast yet"
                  : `${dataset.forecasts} forecast${dataset.forecasts === 1 ? "" : "s"}`}
                <Icon name="arrow" size={14} />
              </span>
            </div>
          </Link>
        ))}
      </div>
    </main>
  );
}
