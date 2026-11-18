import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, Route, Routes } from "react-router-dom";
import { api } from "./api";
import { Crumb, CrumbProvider, Crumbs, Icon } from "./components/ui";
import { modelName } from "./format";
import { AddDataset } from "./pages/AddDataset";
import { DatasetPage } from "./pages/Dataset";
import { Datasets } from "./pages/Datasets";
import { ForecastPage } from "./pages/Forecast";
import { NewForecast } from "./pages/NewForecast";

type Theme = "light" | "dark" | null;

function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      return (localStorage.getItem("u4cast-theme") as Theme) || null;
    } catch {
      return null;
    }
  });
  useEffect(() => {
    if (theme) document.documentElement.dataset.theme = theme;
    else delete document.documentElement.dataset.theme;
    try {
      if (theme) localStorage.setItem("u4cast-theme", theme);
    } catch {
      // The choice simply does not outlast this visit.
    }
  }, [theme]);
  const toggle = () => {
    const systemDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const now = theme ?? (systemDark ? "dark" : "light");
    setTheme(now === "dark" ? "light" : "dark");
  };
  return [theme, toggle];
}

function Mark() {
  return (
    <span className="brand-mark" aria-hidden="true">
      <svg width="18" height="18" viewBox="0 0 18 18" fill="none" strokeLinecap="round" strokeLinejoin="round">
        <path d="M2.5 12.5 6.5 8.5l3 2.2 2.6-4.4" stroke="var(--surface)" strokeWidth="1.8" />
        <path d="m12.1 6.3 3.2-1.7" stroke="var(--accent)" strokeWidth="1.8" />
        <circle cx="15.3" cy="4.6" r="1.5" fill="var(--accent)" />
      </svg>
    </span>
  );
}

export function App() {
  const [crumbs, setCrumbs] = useState<Crumb[]>([]);
  const [theme, toggleTheme] = useTheme();
  const status = useQuery({
    queryKey: ["status"],
    queryFn: api.status,
    refetchInterval: (query) => (query.state.data?.state === "ready" ? false : 1500),
    staleTime: 0,
  });
  const state = status.data?.state;
  const dark =
    theme === "dark" || (theme === null && window.matchMedia("(prefers-color-scheme: dark)").matches);

  return (
    <CrumbProvider value={setCrumbs}>
      <header className="topbar">
        <div className="topbar-inner">
          <Link to="/" className="brand">
            <Mark />
            u4cast
          </Link>
          <Crumbs crumbs={crumbs} />
          <div className="topbar-right">
            {status.data && state === "ready" && (
              <span title={`Forecasts are made by ${status.data.model}`}>
                {modelName(status.data.model)} · {status.data.database}
              </span>
            )}
            <button
              className="theme-switch"
              onClick={toggleTheme}
              aria-label={dark ? "Switch to light colors" : "Switch to dark colors"}
              title={dark ? "Light colors" : "Dark colors"}
            >
              <Icon name={dark ? "sun" : "moon"} />
            </button>
          </div>
        </div>
      </header>

      {state === "ready" ? (
        <Routes>
          <Route path="/" element={<Datasets />} />
          <Route path="/datasets/new" element={<AddDataset />} />
          <Route path="/datasets/:dataset" element={<DatasetPage />} />
          <Route path="/datasets/:dataset/forecast" element={<NewForecast />} />
          <Route path="/datasets/:dataset/forecasts/:forecast" element={<ForecastPage />} />
          <Route
            path="*"
            element={
              <main className="page">
                <div className="card empty">
                  <h2>There is nothing at this address</h2>
                  <p>The page may have been renamed.</p>
                  <Link to="/" className="btn">
                    Go to datasets
                  </Link>
                </div>
              </main>
            }
          />
        </Routes>
      ) : (
        <main className="page center-screen">
          {state === "failed" || status.error ? (
            <div className="card card-pad" style={{ maxWidth: 560 }}>
              <h2>The data lake could not be reached</h2>
              <p className="muted" style={{ margin: "8px 0 14px" }}>
                The application reads and writes Iceberg tables through Spark, and that connection
                did not start.
              </p>
              <pre className="code">{status.data?.error ?? String(status.error)}</pre>
            </div>
          ) : (
            <div style={{ textAlign: "center" }}>
              <div className="row" style={{ justifyContent: "center", gap: 12 }}>
                <span className="spinner" />
                <h2>Connecting to the data lake</h2>
              </div>
              <p className="muted" style={{ marginTop: 8 }}>
                This takes about ten seconds after the application starts.
              </p>
            </div>
          )}
        </main>
      )}
    </CrumbProvider>
  );
}
