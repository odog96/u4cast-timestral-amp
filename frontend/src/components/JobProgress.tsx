// Follows a background job until it is done: what it is doing, and how far along.

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { api, type Job } from "../api";
import { ErrorNote } from "./ui";

export function JobProgress({
  job,
  onDone,
  onDismiss,
}: {
  job: Job;
  onDone: (job: Job) => void;
  onDismiss?: () => void;
}) {
  const client = useQueryClient();
  const reported = useRef(false);
  const live = useQuery({
    queryKey: ["job", job.id],
    queryFn: () => api.job(job.id),
    initialData: job,
    refetchInterval: (query) =>
      query.state.data && ["done", "failed"].includes(query.state.data.state) ? false : 700,
  });
  const current = live.data;

  useEffect(() => {
    if (current.state === "done" && !reported.current) {
      reported.current = true;
      // Lists and charts may have changed: read them again.
      client.invalidateQueries({ predicate: (query) => query.queryKey[0] !== "job" });
      onDone(current);
    }
  }, [current, client, onDone]);

  if (current.state === "failed") {
    return (
      <div className="stack" style={{ gap: 12 }}>
        <ErrorNote error={new Error(current.error ?? "The work did not finish.")} />
        {onDismiss && (
          <div>
            <button className="btn" onClick={onDismiss}>
              Back
            </button>
          </div>
        )}
      </div>
    );
  }

  const percent = Math.round(current.progress * 100);
  return (
    <div role="status" aria-live="polite">
      <div className="spread" style={{ marginBottom: 8 }}>
        <span className="row">
          <span className="spinner" />
          <span style={{ fontWeight: 560 }}>{current.stage}</span>
        </span>
        <span className="faint num">
          {percent}% · {Math.round(current.seconds)} s
        </span>
      </div>
      <div className="progress" aria-hidden="true">
        <span style={{ width: `${Math.max(3, percent)}%` }} />
      </div>
    </div>
  );
}
