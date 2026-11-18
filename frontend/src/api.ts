// Calls to the web server, and the shapes of its answers.

export type Frequency = "daily" | "weekly" | "monthly" | "quarterly";
export type Fill = "missing" | "zero" | "average" | "interpolate" | "previous";

export interface Status {
  state: "starting" | "ready" | "failed";
  error: string | null;
  model: string;
  database: string | null;
  jobs: Job[];
}

export interface Job {
  id: string;
  label: string;
  state: "queued" | "running" | "done" | "failed";
  stage: string;
  progress: number;
  error: string | null;
  result: {
    dataset: string;
    forecast_name: string;
    forecast_id?: string;
    series?: number;
    rows?: number;
    backtests: number;
  } | null;
  seconds: number;
}

export interface Dataset {
  name: string;
  source_table: string;
  date_column: string;
  target_column: string;
  dimension_columns: string[];
  frequency: Frequency;
  fill: Fill;
  forecasts: number;
  issues: number;
}

export interface Column {
  name: string;
  type: string;
}

export type Row = Record<string, string | number | boolean | null>;

export interface Source {
  table: string;
  columns: Column[];
  rows: number;
  preview: Row[];
}

export interface Profile extends Source {
  date_columns: string[];
  number_columns: string[];
  suggested: { date_column: string | null; target_column: string | null; dimension_columns: string[] };
  frequency: Frequency | null;
  frequency_share: number;
  first_date: string | null;
  last_date: string | null;
  periods: number;
}

export interface ForecastRecord {
  forecast_id: string;
  forecast_name: string;
  dataset: string;
  target_column: string;
  dimension_columns: string[];
  slice_filter: Record<string, (string | number)[]>;
  frequency: Frequency;
  history_start_date: string;
  history_end_date: string;
  horizon: number;
  model_name: string;
  kind: "forecast" | "backtest";
  created_at: string;
  fill: Fill;
  series: number | null;
}

export interface ForecastGroup {
  forecast_name: string;
  issues: number;
  backtests: number;
  latest: ForecastRecord;
}

export interface DatasetOverview extends Dataset {
  source: Source;
  number_columns: string[];
  dates: string[];
  minimum_history: number;
  minimum_history_floor: number;
  forecast_groups: ForecastGroup[];
  values_table: string;
}

export interface SliceRequest {
  dimension_columns: string[];
  slice_filter: Record<string, (string | number)[]>;
  history_start: string | null;
  history_end: string | null;
  minimum_history: number | null;
  target_column: string | null;
  horizon: number;
  cutoffs: number;
  cutoff_step: number;
}

export interface SliceSummary {
  series: number;
  steps: number;
  history_start: string;
  history_end: string;
  minimum_history: number;
  left_out: number;
  left_out_by_reason: Record<string, number>;
  left_out_sample: { label: string; reason: string; first_date: string | null; steps: number }[];
  rows_off_grid: number;
  values_filled: number;
  values_missing: number;
  by_start: { start: string; steps: number; series: number }[];
  sample: string[];
  seconds: number;
  backtest_cutoffs: string[];
  backtest_seconds: number;
  default_horizon: number;
}

export interface ForecastOverview {
  dataset: Dataset;
  forecast_name: string;
  issues: ForecastRecord[];
  backtests: ForecastRecord[];
  reissue_dates: string[];
  latest_data_date: string | null;
  values_table: string;
  forecasts_table: string;
}

export type Values = (number | null)[];

export interface ChartData {
  forecast_id: string;
  ts_id: string | null;
  label: string;
  series: number;
  target: string;
  dates: string[];
  actual: Values;
  future_dates: string[];
  forecast: Values;
  low: Values;
  high: Values;
  actual_after: Values;
  note: string | null;
}

export interface SeriesRow {
  ts_id: string;
  label: string;
  dimensions: Record<string, string | null>;
  forecast_sum: number | null;
  previous_sum: number | null;
  change: number | null;
}

export interface SeriesPage {
  total: number;
  dimension_columns: string[];
  rows: SeriesRow[];
}

export interface Comparison {
  label: string;
  series: number;
  target: string;
  issues: ForecastRecord[];
  dates: string[];
  actual: Values;
  forecasts: Values[];
  context_dates: string[];
  context_actual: Values;
}

export interface Accuracy {
  available: boolean;
  cutoffs: string[];
  horizon: number;
  target: string;
  season: number;
  total: Record<string, number | null>;
  by_step: Record<string, number | null>[];
  series: number;
  by_series: {
    ts_id: string;
    label: string;
    n: number;
    mae: number | null;
    rmse: number | null;
    mape: number | null;
    wape: number | null;
    reference_mae: number | null;
    reference_wape: number | null;
  }[];
}

export interface StoredRows {
  table: string;
  forecasts_table: string;
  rows: number;
  columns: string[];
  preview: Row[];
  sql: string;
  record_sql: string;
}

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    let detail = `The server answered ${response.status}.`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // The answer was not JSON; keep the status as the message.
    }
    throw new ApiError(detail, response.status);
  }
  return response.json() as Promise<T>;
}

const get = <T>(path: string) => request<T>(path);
const post = <T>(path: string, body: unknown) =>
  request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

const e = encodeURIComponent;
const query = (values: Record<string, string | number | null | undefined>) => {
  const parts = Object.entries(values)
    .filter(([, value]) => value !== null && value !== undefined && value !== "")
    .map(([key, value]) => `${key}=${e(String(value))}`);
  return parts.length ? `?${parts.join("&")}` : "";
};

export const api = {
  status: () => get<Status>("/api/status"),
  datasets: () => get<Dataset[]>("/api/datasets"),
  dataset: (name: string) => get<DatasetOverview>(`/api/datasets/${e(name)}`),
  saveDataset: (body: Omit<Dataset, "forecasts" | "issues">) => post<Dataset>("/api/datasets", body),
  history: (name: string, target: string) =>
    get<{ target: string; dates: string[]; values: (number | null)[] }>(
      `/api/datasets/${e(name)}/history?target=${e(target)}`,
    ),
  dimensionValues: (name: string, column: string) =>
    get<(string | number)[]>(`/api/datasets/${e(name)}/dimensions/${e(column)}`),
  slice: (name: string, body: SliceRequest) =>
    post<SliceSummary>(`/api/datasets/${e(name)}/slice`, body),
  sources: () => get<{ table: string; dataset: string | null }[]>("/api/sources"),
  profile: (table: string, dateColumn?: string | null) =>
    get<Profile>(`/api/sources/profile${query({ table, date_column: dateColumn })}`),
  upload: (file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<{ table: string; rows: number }>("/api/uploads", { method: "POST", body });
  },
  createForecast: (name: string, body: SliceRequest & { forecast_name: string }) =>
    post<Job>(`/api/datasets/${e(name)}/forecasts`, body),
  forecast: (dataset: string, name: string) =>
    get<ForecastOverview>(`/api/datasets/${e(dataset)}/forecasts/${e(name)}`),
  reissue: (dataset: string, name: string, asOf: string | null) =>
    post<Job>(`/api/datasets/${e(dataset)}/forecasts/${e(name)}/reissue`, { as_of: asOf }),
  checkAccuracy: (dataset: string, name: string, cutoffs: number, cutoffStep: number) =>
    post<Job>(`/api/datasets/${e(dataset)}/forecasts/${e(name)}/accuracy`, {
      cutoffs,
      cutoff_step: cutoffStep,
    }),
  accuracy: (dataset: string, name: string, q: string, limit: number) =>
    get<Accuracy>(`/api/datasets/${e(dataset)}/forecasts/${e(name)}/accuracy${query({ q, limit })}`),
  compare: (dataset: string, name: string, tsId: string | null, kind: "forecast" | "backtest") =>
    get<Comparison>(
      `/api/datasets/${e(dataset)}/forecasts/${e(name)}/compare${query({ ts_id: tsId, kind })}`,
    ),
  chart: (forecastId: string, tsId: string | null) =>
    get<ChartData>(`/api/forecasts/${forecastId}/chart${query({ ts_id: tsId })}`),
  series: (forecastId: string, q: string, sort: string, limit: number, offset = 0) =>
    get<SeriesPage>(`/api/forecasts/${forecastId}/series${query({ q, sort, limit, offset })}`),
  rows: (forecastId: string, limit = 25) =>
    get<StoredRows>(`/api/forecasts/${forecastId}/rows${query({ limit })}`),
  downloadUrl: (forecastId: string) => `/api/forecasts/${forecastId}/download.csv`,
  job: (id: string) => get<Job>(`/api/jobs/${id}`),
};
