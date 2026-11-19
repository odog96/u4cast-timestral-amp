// A forecast in production: its schedule, what each run did, and how it has done against actuals.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type Frequency, type Production } from "../api";
import { ErrorNote, Hint, Icon, Loading, Tile } from "../components/ui";
import { duration, pct, period, periods } from "../format";

interface Preset {
  label: string;
  cron: string;
}

// Schedules a planner would ask for, by how often the data arrives. Times are UTC.
const PRESETS: Record<Frequency, Preset[]> = {
  daily: [{ label: "Every day at 06:00", cron: "0 6 * * *" }],
  weekly: [
    { label: "Every Monday at 06:00", cron: "0 6 * * 1" },
    { label: "Every day at 06:00", cron: "0 6 * * *" },
  ],
  monthly: [
    { label: "The 1st of every month at 06:00", cron: "0 6 1 * *" },
    { label: "The 5th of every month at 06:00", cron: "0 6 5 * *" },
    { label: "Every Monday at 06:00", cron: "0 6 * * 1" },
  ],
  quarterly: [{ label: "The 1st of January, April, July and October at 06:00", cron: "0 6 1 1,4,7,10 *" }],
};

const OUTCOMES: Record<string, string> = {
  issued: "Issued",
  no_new_data: "No new data",
  failed: "Failed",
};

function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function ProductionTab({
  dataset,
  name,
  frequency,
}: {
  dataset: string;
  name: string;
  frequency: Frequency;
}) {
  const client = useQueryClient();
  const key = ["production", dataset, name];
  const production = useQuery({
    queryKey: key,
    queryFn: () => api.production(dataset, name),
    refetchInterval: 30_000,
  });
  const done = (data: Production) => {
    client.setQueryData(key, data);
    client.invalidateQueries({ queryKey: ["dataset", dataset] });
  };
  const promote = useMutation({ mutationFn: (cron: string) => api.promote(dataset, name, cron), onSuccess: done });
  const setState = useMutation({
    mutationFn: (state: string) => api.productionState(dataset, name, state),
    onSuccess: done,
  });
  const presets = PRESETS[frequency];
  const [choice, setChoice] = useState(presets[0].cron);
  const [custom, setCustom] = useState("");

  if (production.isLoading) {
    return (
      <section className="card">
        <Loading what="Reading the schedule and the runs" />
      </section>
    );
  }
  if (production.error || !production.data) {
    return <ErrorNote error={production.error ?? new Error("Production could not be read.")} />;
  }
  const data = production.data;
  const schedule = data.schedule;
  const cron = choice === "custom" ? custom.trim() : choice;
  const label = schedule ? presets.find((p) => p.cron === schedule.cron)?.label : null;
  const track = data.track;

  return (
    <div className="stack">
      <section className="card">
        <div className="card-head">
          <div>
            <h2>
              {schedule ? "In production" : "Put this forecast in production"}
              <Hint text="In production, a Cloudera AI job issues this forecast again on a schedule, from the history as it stands that day. Every run is a new issue in the same forecast table. If no new history has arrived, the run makes nothing and says so." />
            </h2>
            <div className="sub">
              {schedule
                ? `${label ?? `On the schedule ${schedule.cron}`} (UTC), since ${when(schedule.since)}. ${
                    schedule.state === "paused" ? "Paused: it does not run until it is started again." : ""
                  }`
                : "A job then issues it again on a schedule, with the latest data, without anyone clicking."}
            </div>
          </div>
          {schedule && (
            <div className="row">
              <span className={`badge ${schedule.state === "active" ? "accent" : ""}`}>
                {schedule.state === "active" ? "Active" : "Paused"}
              </span>
              <button
                className="btn"
                disabled={setState.isPending}
                onClick={() => setState.mutate(schedule.state === "active" ? "paused" : "active")}
              >
                {schedule.state === "active" ? "Pause" : "Start again"}
              </button>
              <button
                className="btn"
                disabled={setState.isPending}
                onClick={() => {
                  if (window.confirm("Take this forecast out of production? Its issues and its record stay."))
                    setState.mutate("retired");
                }}
              >
                Take out of production
              </button>
            </div>
          )}
        </div>
        {!schedule && data.available && (
          <div className="card-pad">
            <div className="row wrap" style={{ gap: 14, alignItems: "flex-end" }}>
              <div className="field" style={{ flex: "1 1 320px" }}>
                <label htmlFor="schedule">How often</label>
                <select
                  id="schedule"
                  className="select"
                  value={choice}
                  onChange={(event) => setChoice(event.target.value)}
                >
                  {presets.map((preset) => (
                    <option key={preset.cron} value={preset.cron}>
                      {preset.label}
                    </option>
                  ))}
                  <option value="custom">Another schedule, in cron form</option>
                </select>
              </div>
              {choice === "custom" && (
                <div className="field" style={{ flex: "0 1 220px" }}>
                  <label htmlFor="cron">Minute, hour, day, month, weekday</label>
                  <input
                    id="cron"
                    className="input mono"
                    placeholder="0 6 1 * *"
                    value={custom}
                    onChange={(event) => setCustom(event.target.value)}
                  />
                </div>
              )}
              <button
                className="btn primary"
                disabled={!cron || promote.isPending}
                onClick={() => promote.mutate(cron)}
              >
                {promote.isPending && <span className="spinner" />}
                Put in production
                <Icon name="arrow" />
              </button>
            </div>
            <p className="hint" style={{ marginTop: 10 }}>
              Times are UTC. Choose a time after your data usually arrives. The job keeps this
              forecast's series, horizon and model.
            </p>
          </div>
        )}
        {!data.available && (
          <div className="card-pad">
            <p className="sub">Scheduling is not available where this application is running.</p>
          </div>
        )}
        {(promote.error || setState.error) && (
          <div className="card-pad">
            <ErrorNote error={promote.error ?? setState.error} />
          </div>
        )}
        {schedule && (
          <div className="card-pad" style={{ borderTop: "1px solid var(--line)" }}>
            <span className="sub">
              Cloudera AI job: <span className="mono">{schedule.job_name}</span>.{" "}
              {schedule.jobs_address && (
                <a href={schedule.jobs_address} target="_blank" rel="noreferrer">
                  Open the Jobs page
                </a>
              )}
            </span>
          </div>
        )}
      </section>

      <section className="card">
        <div className="tiles">
          <Tile
            label="Error against actuals"
            value={pct(track.total.wape)}
            note={
              track.scored_issues
                ? `Over ${track.scored_issues} issue${track.scored_issues === 1 ? "" : "s"} that have actuals`
                : "No issue has actuals yet"
            }
          />
          <Tile
            label="Repeating last year"
            value={pct(track.total.reference_wape)}
            note="The same dates, the simplest alternative"
          />
          <Tile label="Issues" value={track.issues.length} note="Every run that found new data" />
          <Tile
            label="Runs"
            value={data.runs.length}
            note={data.runs.length ? `Last: ${OUTCOMES[data.runs[0].outcome] ?? data.runs[0].outcome}` : "None yet"}
          />
        </div>
      </section>

      <section className="card">
        <div className="card-head">
          <div>
            <h2>Track record</h2>
            <div className="sub">
              Each issue, scored on the dates it predicted that now have an actual. This is real use,
              not a test on the past.
            </div>
          </div>
        </div>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>History to</th>
                <th>Issued</th>
                <th className="r">Dates with actuals</th>
                <th className="r">Error</th>
                <th className="r">Repeating last year</th>
              </tr>
            </thead>
            <tbody>
              {[...track.issues].reverse().map((issue) => (
                <tr key={issue.forecast_id}>
                  <td className="nowrap">{period(issue.history_end_date, frequency)}</td>
                  <td className="nowrap muted">{when(issue.created_at)}</td>
                  <td className="r">
                    {issue.dates_scored} of {issue.horizon}
                  </td>
                  <td className="r">{issue.dates_scored ? pct(issue.wape) : "–"}</td>
                  <td className="r muted">{issue.dates_scored ? pct(issue.reference_wape) : "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {track.by_step.length > 0 && (
          <div className="card-pad" style={{ borderTop: "1px solid var(--line)" }}>
            <div className="sub" style={{ marginBottom: 8 }}>
              By how far ahead:
            </div>
            <div className="chips">
              {track.by_step.map((row) => (
                <span key={row.step} className="chip static">
                  {periods(row.step, frequency)}: {pct(row.wape)}
                </span>
              ))}
            </div>
          </div>
        )}
      </section>

      <section className="card">
        <div className="card-head">
          <div>
            <h2>Runs</h2>
            <div className="sub">What the job did each time it ran, newest first.</div>
          </div>
        </div>
        {data.runs.length === 0 ? (
          <div className="card-pad">
            <p className="sub">The job has not run yet.</p>
          </div>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Ran</th>
                  <th>Outcome</th>
                  <th>What happened</th>
                  <th className="r">Series</th>
                  <th className="r">Took</th>
                </tr>
              </thead>
              <tbody>
                {data.runs.map((run) => (
                  <tr key={run.ran_at}>
                    <td className="nowrap">{when(run.ran_at)}</td>
                    <td>
                      <span className={`badge ${run.outcome === "issued" ? "accent" : ""}`}>
                        {OUTCOMES[run.outcome] ?? run.outcome}
                      </span>
                    </td>
                    <td>{run.detail}</td>
                    <td className="r">{run.series?.toLocaleString() ?? "–"}</td>
                    <td className="r nowrap">{run.seconds !== null ? duration(run.seconds) : "–"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
