// Add a dataset: say where the history is, then pick its columns.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type Fill, type Frequency, type Profile } from "../api";
import { ErrorNote, Hint, Icon, Loading, RowsTable, useCrumbs } from "../components/ui";
import { FILLS, FREQUENCY_NAMES, period } from "../format";

export function AddDataset() {
  useCrumbs([{ label: "Add a dataset" }]);
  const [table, setTable] = useState<string | null>(null);

  return (
    <main className="page" style={{ maxWidth: 980 }}>
      <div className="page-head">
        <div>
          <h1>Add a dataset</h1>
          <p className="sub">
            The history stays where it is. The application reads it and never changes it.
          </p>
        </div>
      </div>
      <div className="stack">
        <ChooseSource table={table} onChoose={setTable} />
        {table && <PickColumns key={table} table={table} />}
      </div>
    </main>
  );
}

function ChooseSource({ table, onChoose }: { table: string | null; onChoose: (t: string) => void }) {
  const sources = useQuery({ queryKey: ["sources"], queryFn: api.sources });
  const [other, setOther] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const client = useQueryClient();
  const upload = useMutation({
    mutationFn: api.upload,
    onSuccess: (result) => {
      client.invalidateQueries({ queryKey: ["sources"] });
      onChoose(result.table);
    },
  });

  return (
    <section className="card">
      <div className="form-section">
        <h2>
          <span className="step-no">1</span>Where is the history?
        </h2>
        <p className="sub">Choose an Iceberg table, name another one, or upload a CSV file.</p>
        {sources.isLoading && <Loading what="Looking for tables" />}
        {sources.error && <ErrorNote error={sources.error} />}
        <div className="chips" style={{ marginBottom: 14 }}>
          {(sources.data ?? []).map((source) => (
            <button
              key={source.table}
              className={`chip ${table === source.table ? "on" : ""}`}
              onClick={() => onChoose(source.table)}
              title={source.dataset ? `Already used by the dataset ${source.dataset}` : undefined}
            >
              <Icon name="table" size={13} />
              <span className="mono">{source.table}</span>
            </button>
          ))}
        </div>
        <div className="row wrap" style={{ gap: 16, alignItems: "flex-end" }}>
          <form
            className="field"
            style={{ flex: "1 1 300px" }}
            onSubmit={(event) => {
              event.preventDefault();
              if (other.trim()) onChoose(other.trim());
            }}
          >
            <label htmlFor="other-table">Another table</label>
            <div className="row">
              <input
                id="other-table"
                className="input mono"
                placeholder="database.table"
                value={other}
                onChange={(event) => setOther(event.target.value)}
              />
              <button className="btn" type="submit" disabled={!other.trim()}>
                Use it
              </button>
            </div>
          </form>
          <div className="field">
            <label>A CSV file</label>
            <input
              ref={fileInput}
              type="file"
              accept=".csv,text/csv"
              hidden
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) upload.mutate(file);
                event.target.value = "";
              }}
            />
            <button className="btn" onClick={() => fileInput.current?.click()} disabled={upload.isPending}>
              {upload.isPending ? <span className="spinner" /> : <Icon name="upload" />}
              {upload.isPending ? "Storing the file as a table" : "Upload a CSV"}
            </button>
          </div>
        </div>
        {upload.error && (
          <div style={{ marginTop: 12 }}>
            <ErrorNote error={upload.error} />
          </div>
        )}
        {upload.data && (
          <p className="hint" style={{ marginTop: 10 }}>
            Stored {upload.data.rows.toLocaleString()} rows as the new table{" "}
            <span className="mono">{upload.data.table}</span>.
          </p>
        )}
      </div>
    </section>
  );
}

function defaultName(table: string) {
  const name = table.split(".").pop() ?? table;
  const words = name.replace(/^upload_/, "").replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function PickColumns({ table }: { table: string }) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [dateColumn, setDateColumn] = useState<string | null>(null);
  const profile = useQuery({
    queryKey: ["profile", table, dateColumn],
    queryFn: () => api.profile(table, dateColumn),
    placeholderData: (previous) => previous,
  });
  const [target, setTarget] = useState<string | null>(null);
  const [dimensions, setDimensions] = useState<string[] | null>(null);
  const [frequency, setFrequency] = useState<Frequency | null>(null);
  const [fill, setFill] = useState<Fill>("missing");
  const [name, setName] = useState(defaultName(table));
  const data: Profile | undefined = profile.data;

  // Start from what the server suggests; keep whatever the user has changed.
  useEffect(() => {
    if (!data) return;
    setDateColumn((current) => current ?? data.suggested.date_column);
    setTarget((current) => current ?? data.suggested.target_column);
    setDimensions((current) => current ?? data.suggested.dimension_columns);
  }, [data]);
  useEffect(() => {
    if (data?.frequency) setFrequency(data.frequency);
  }, [data?.frequency]);

  const save = useMutation({
    mutationFn: api.saveDataset,
    onSuccess: (saved) => {
      client.invalidateQueries({ queryKey: ["datasets"] });
      navigate(`/datasets/${encodeURIComponent(saved.name)}/forecast`);
    },
  });

  if (profile.isLoading) {
    return (
      <section className="card">
        <Loading what="Reading the table" />
      </section>
    );
  }
  if (profile.error || !data) {
    return <ErrorNote error={profile.error ?? new Error("The table could not be read.")} />;
  }

  const chosen = dimensions ?? [];
  const taken = new Set([dateColumn, target]);
  const ready = dateColumn && target && frequency && name.trim();
  const toggle = (column: string) =>
    setDimensions(chosen.includes(column) ? chosen.filter((c) => c !== column) : [...chosen, column]);

  return (
    <section className="card">
      <div className="form-section">
        <h2>
          <span className="step-no">2</span>Pick its columns
        </h2>
        <p className="sub">
          <span className="mono">{data.table}</span> has {data.rows.toLocaleString()} rows.
        </p>
        <div className="row wrap" style={{ gap: 16, alignItems: "flex-start" }}>
          <div className="field" style={{ flex: "1 1 220px" }}>
            <label htmlFor="date-column">Date</label>
            <select
              id="date-column"
              className="select"
              value={dateColumn ?? ""}
              onChange={(event) => setDateColumn(event.target.value)}
            >
              {!dateColumn && <option value="">Choose a column</option>}
              {data.columns.map((column) => (
                <option key={column.name} value={column.name}>
                  {column.name} ({column.type})
                </option>
              ))}
            </select>
            {data.first_date && frequency && (
              <span className="hint">
                {data.periods.toLocaleString()} dates, {period(data.first_date, frequency)} to{" "}
                {period(data.last_date, frequency)}
              </span>
            )}
          </div>
          <div className="field" style={{ flex: "1 1 220px" }}>
            <label htmlFor="target-column">Number to forecast</label>
            <select
              id="target-column"
              className="select"
              value={target ?? ""}
              onChange={(event) => setTarget(event.target.value)}
            >
              {!target && <option value="">Choose a column</option>}
              {data.number_columns.map((column) => (
                <option key={column} value={column}>
                  {column}
                </option>
              ))}
            </select>
            <div className="hint">The usual one. Each forecast can choose another number column.</div>
          </div>
          <div className="field" style={{ flex: "1 1 220px" }}>
            <label htmlFor="frequency">Frequency</label>
            <select
              id="frequency"
              className="select"
              value={frequency ?? ""}
              onChange={(event) => setFrequency(event.target.value as Frequency)}
            >
              {!frequency && <option value="">Choose one</option>}
              {(Object.keys(FREQUENCY_NAMES) as Frequency[]).map((value) => (
                <option key={value} value={value}>
                  {FREQUENCY_NAMES[value]}
                </option>
              ))}
            </select>
            <span className="hint">
              {data.frequency
                ? `Detected ${FREQUENCY_NAMES[data.frequency].toLowerCase()}: ${Math.round(
                    data.frequency_share * 100,
                  )}% of the dates are one ${data.frequency.replace("ly", "").replace("dai", "day")} apart. Change it if that is wrong.`
                : "The dates do not show a frequency. Choose the one this data has."}
            </span>
          </div>
        </div>
      </div>

      <div className="form-section">
        <h2>
          Dimension list
          <Hint text="The columns that describe what a row is about, for example store, category and item. Every forecast of this dataset chooses its level from this list." />
        </h2>
        <p className="sub">
          The columns a forecast can be made by, for example a store and an item.
        </p>
        <div className="chips">
          {data.columns
            .filter((column) => !taken.has(column.name))
            .map((column) => (
              <button
                key={column.name}
                className={`chip ${chosen.includes(column.name) ? "on" : ""}`}
                onClick={() => toggle(column.name)}
                aria-pressed={chosen.includes(column.name)}
              >
                {column.name}
              </button>
            ))}
        </div>
      </div>

      <div className="form-section">
        <h2>When a date has no row for a series</h2>
        <p className="sub">
          The application does not guess. Say what a missing row means in this data.
        </p>
        <div className="field" style={{ maxWidth: 460 }}>
          <select
            className="select"
            aria-label="What a date with no row means"
            value={fill}
            onChange={(event) => setFill(event.target.value as Fill)}
          >
            {FILLS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.name}
              </option>
            ))}
          </select>
          <span className="hint">{FILLS.find((option) => option.value === fill)?.meaning}</span>
        </div>
      </div>

      <div className="form-section">
        <h2>First rows</h2>
        <p className="sub">To check that the columns are the ones you mean.</p>
        <div style={{ margin: "0 -22px" }}>
          <RowsTable columns={data.columns.map((column) => column.name)} rows={data.preview.slice(0, 6)} />
        </div>
      </div>

      <div className="form-section">
        <div className="row wrap" style={{ alignItems: "flex-end", gap: 16 }}>
          <div className="field" style={{ flex: "1 1 320px" }}>
            <label htmlFor="dataset-name">Name</label>
            <input
              id="dataset-name"
              className="input"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </div>
          <button
            className="btn primary large"
            disabled={!ready || save.isPending}
            onClick={() =>
              save.mutate({
                name: name.trim(),
                source_table: table,
                date_column: dateColumn!,
                target_column: target!,
                dimension_columns: chosen,
                frequency: frequency!,
                fill,
              })
            }
          >
            {save.isPending && <span className="spinner" />}
            Save and forecast
          </button>
        </div>
        {save.error && (
          <div style={{ marginTop: 12 }}>
            <ErrorNote error={save.error} />
          </div>
        )}
      </div>
    </section>
  );
}
