// Searchable pickers: many values of one dimension, or one series of a forecast.

import { useQuery } from "@tanstack/react-query";
import { useCallback, useMemo, useState } from "react";
import { api } from "../api";
import { splitLabel } from "../format";
import { Icon, useDebounced, useDismiss } from "./ui";

type Value = string | number;

/** Choose any number of values of one dimension column. Values load when it is first opened. */
export function MultiSelect({
  dataset,
  column,
  chosen,
  onChange,
}: {
  dataset: string;
  column: string;
  chosen: Value[];
  onChange: (values: Value[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const close = useCallback(() => setOpen(false), []);
  const ref = useDismiss(open, close);
  const values = useQuery({
    queryKey: ["dimension", dataset, column],
    queryFn: () => api.dimensionValues(dataset, column),
    enabled: open,
    staleTime: Infinity,
  });
  const matches = useMemo(() => {
    const words = search.toLowerCase().split(/\s+/).filter(Boolean);
    const all = values.data ?? [];
    const found = words.length
      ? all.filter((value) => words.every((word) => String(value).toLowerCase().includes(word)))
      : all;
    return found.slice(0, 200);
  }, [values.data, search]);
  const toggle = (value: Value) =>
    onChange(chosen.includes(value) ? chosen.filter((v) => v !== value) : [...chosen, value]);

  return (
    <div className="pop-anchor" ref={ref}>
      <button className="picker-button" onClick={() => setOpen(!open)} aria-expanded={open}>
        {chosen.length === 0 ? (
          <span className="placeholder">All values</span>
        ) : (
          <span className="chips">
            {chosen.slice(0, 4).map((value) => (
              <span key={value} className="chip static">
                {String(value)}
              </span>
            ))}
            {chosen.length > 4 && <span className="chip static">+{chosen.length - 4} more</span>}
          </span>
        )}
        <Icon name="chevron" size={14} />
      </button>
      {open && (
        <div className="pop" role="listbox" aria-multiselectable="true">
          <div className="search">
            <input
              className="input"
              autoFocus
              placeholder={`Search ${column}`}
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>
          <div className="options">
            {values.isLoading && <div className="option faint">Loading values…</div>}
            {values.error && <div className="option faint">The values could not be loaded.</div>}
            {matches.map((value) => (
              <button
                key={value}
                className="option"
                role="option"
                aria-selected={chosen.includes(value)}
                onClick={() => toggle(value)}
              >
                <span className="check">{chosen.includes(value) ? "✓" : ""}</span>
                <span className="grow">{String(value)}</span>
              </button>
            ))}
            {values.data && matches.length === 0 && <div className="option faint">No value matches.</div>}
          </div>
          <div className="foot">
            <span>
              {chosen.length ? `${chosen.length} chosen` : "Nothing chosen means all"}
              {values.data && ` · ${values.data.length.toLocaleString()} values`}
            </span>
            {chosen.length > 0 && (
              <button className="btn small ghost" onClick={() => onChange([])}>
                Clear
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/** Find one series of a forecast by typing part of its name. */
export function SeriesPicker({
  forecastId,
  selected,
  onSelect,
}: {
  forecastId: string;
  selected: { ts_id: string; label: string } | null;
  onSelect: (series: { ts_id: string; label: string } | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const settled = useDebounced(search, 200);
  const close = useCallback(() => setOpen(false), []);
  const ref = useDismiss(open, close);
  const found = useQuery({
    queryKey: ["series-search", forecastId, settled],
    queryFn: () => api.series(forecastId, settled, "forecast", 30),
    enabled: open,
    placeholderData: (previous) => previous,
  });
  const choose = (series: { ts_id: string; label: string } | null) => {
    onSelect(series);
    setOpen(false);
    setSearch("");
  };

  return (
    <div className="pop-anchor" ref={ref} style={{ minWidth: 300 }}>
      <button className="picker-button" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="row" style={{ minWidth: 0 }}>
          <Icon name="search" size={14} />
          {selected ? (
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {selected.label}
            </span>
          ) : (
            <span className="placeholder">Find one series</span>
          )}
        </span>
        <Icon name="chevron" size={14} />
      </button>
      {open && (
        <div className="pop" role="listbox">
          <div className="search">
            <input
              className="input"
              autoFocus
              placeholder="Type part of a name"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>
          <div className="options">
            <button className="option" role="option" aria-selected={!selected} onClick={() => choose(null)}>
              <span className="check">{selected ? "" : "✓"}</span>
              <span className="grow">Total of all series</span>
            </button>
            {(found.data?.rows ?? []).map((row) => {
              const { main, rest } = splitLabel(row.label);
              return (
                <button
                  key={row.ts_id}
                  className="option"
                  role="option"
                  aria-selected={selected?.ts_id === row.ts_id}
                  onClick={() => choose({ ts_id: row.ts_id, label: row.label })}
                >
                  <span className="check">{selected?.ts_id === row.ts_id ? "✓" : ""}</span>
                  <span className="grow">
                    {main}
                    {rest && <span className="faint"> · {rest}</span>}
                  </span>
                </button>
              );
            })}
            {found.data && found.data.rows.length === 0 && (
              <div className="option faint">No series matches.</div>
            )}
          </div>
          <div className="foot">
            <span>
              {found.data
                ? `${found.data.total.toLocaleString()} series${settled ? " match" : ""}, largest first`
                : "Searching…"}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
