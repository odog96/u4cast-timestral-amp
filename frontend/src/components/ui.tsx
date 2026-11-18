// Small pieces used on every screen.

import { createContext, useContext, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { Row } from "../api";
import { num, signedPct } from "../format";

// Icons: 16 pixel line drawings that take the color of their text.

const paths: Record<string, React.ReactNode> = {
  plus: <path d="M8 3.5v9M3.5 8h9" />,
  arrow: <path d="M3.5 8h9M8.5 4l4 4-4 4" />,
  back: <path d="M12.5 8h-9M7.5 4l-4 4 4 4" />,
  download: <path d="M8 2.5v7.5M4.8 7.2 8 10.4l3.2-3.2M3 13h10" />,
  search: (
    <>
      <circle cx="7" cy="7" r="4" />
      <path d="m10.2 10.2 3 3" />
    </>
  ),
  check: <path d="m3.5 8.5 3 3 6-7" />,
  copy: (
    <>
      <rect x="5.5" y="5.5" width="8" height="8" rx="1.5" />
      <path d="M10.5 5.5v-2a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2" />
    </>
  ),
  refresh: <path d="M13 8a5 5 0 1 1-1.5-3.6M13 2.8v3h-3" />,
  table: (
    <>
      <rect x="2.5" y="3" width="11" height="10" rx="1.5" />
      <path d="M2.5 6.5h11M6.5 6.5V13" />
    </>
  ),
  upload: <path d="M8 10.5V3M4.8 6.2 8 3l3.2 3.2M3 13h10" />,
  up: <path d="M8 12.5v-9M4 7.5l4-4 4 4" />,
  down: <path d="M8 3.5v9M4 8.5l4 4 4-4" />,
  sun: (
    <>
      <circle cx="8" cy="8" r="2.8" />
      <path d="M8 1.5v1.6M8 12.9v1.6M1.5 8h1.6M12.9 8h1.6M3.4 3.4l1.1 1.1M11.5 11.5l1.1 1.1M3.4 12.6l1.1-1.1M11.5 4.5l1.1-1.1" />
    </>
  ),
  moon: <path d="M13 9.6A5.5 5.5 0 0 1 6.4 3a5.5 5.5 0 1 0 6.6 6.6Z" />,
  x: <path d="m4 4 8 8M12 4l-8 8" />,
  chevron: <path d="m4 6 4 4 4-4" />,
};

export function Icon({ name, size = 16 }: { name: keyof typeof paths; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={{ flex: "none" }}
    >
      {paths[name]}
    </svg>
  );
}

// Breadcrumbs: a page says where it is, the top bar shows it.

export interface Crumb {
  label: string;
  to?: string;
}

const CrumbContext = createContext<(crumbs: Crumb[]) => void>(() => {});
export const CrumbProvider = CrumbContext.Provider;

export function useCrumbs(crumbs: Crumb[]) {
  const set = useContext(CrumbContext);
  const key = JSON.stringify(crumbs);
  useEffect(() => {
    set(crumbs);
    document.title = crumbs.length ? `${crumbs[crumbs.length - 1].label} · u4cast` : "u4cast";
    // The crumbs are compared by their text, not by identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, set]);
}

export function Crumbs({ crumbs }: { crumbs: Crumb[] }) {
  return (
    <nav className="crumbs" aria-label="Where you are">
      {crumbs.map((crumb, i) => (
        <span key={i} className="row" style={{ gap: 8, minWidth: 0 }}>
          <span aria-hidden="true">/</span>
          {crumb.to && i < crumbs.length - 1 ? (
            <Link to={crumb.to}>{crumb.label}</Link>
          ) : (
            <span className="here">{crumb.label}</span>
          )}
        </span>
      ))}
    </nav>
  );
}

export function Loading({ what = "Loading" }: { what?: string }) {
  return (
    <div className="loading" role="status">
      <span className="spinner" />
      {what}…
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  const message = error instanceof Error ? error.message : String(error);
  return (
    <div className="notice error" role="alert">
      {message}
    </div>
  );
}

export function Tile({
  label,
  value,
  note,
}: {
  label: string;
  value: React.ReactNode;
  note?: React.ReactNode;
}) {
  return (
    <div className="tile">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {note && <div className="note">{note}</div>}
    </div>
  );
}

/** A change against an earlier period: an arrow and a signed percent, never color alone. */
export function Delta({ value }: { value: number | null }) {
  if (value === null) return <span className="faint">–</span>;
  const direction = value > 0.0005 ? "up" : value < -0.0005 ? "down" : "";
  return (
    <span className={`delta ${direction}`}>
      {direction && <Icon name={direction} size={12} />}
      {signedPct(value)}
    </span>
  );
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // Clipboard access can be refused inside a frame; select-and-copy still works.
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };
  return (
    <button className="btn small" onClick={copy}>
      <Icon name={copied ? "check" : "copy"} size={13} />
      {copied ? "Copied" : label}
    </button>
  );
}

/** The first rows of a table, as stored. */
export function RowsTable({ columns, rows }: { columns: string[]; rows: Row[] }) {
  const isNumber = (column: string) => rows.some((row) => typeof row[column] === "number");
  return (
    <div className="table-wrap">
      <table className="data compact">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column} className={isNumber(column) ? "r" : ""}>
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {columns.map((column) => {
                const value = row[column];
                return (
                  <td key={column} className={typeof value === "number" ? "r" : "nowrap"}>
                    {value === null || value === undefined ? (
                      <span className="faint">–</span>
                    ) : typeof value === "number" && !Number.isInteger(value) ? (
                      num(value)
                    ) : (
                      String(value)
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Close a popover when the user clicks outside it or presses Escape. */
export function useDismiss(open: boolean, close: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) close();
    };
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && close();
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);
  return ref;
}

/** A value that follows another one only after it has stopped changing for a moment. */
export function useDebounced<T>(value: T, delay = 300): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return settled;
}
