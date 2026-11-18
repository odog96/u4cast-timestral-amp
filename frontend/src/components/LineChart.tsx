// The one chart of the application: lines over time, an optional range, a
// crosshair with a tooltip, and a table view of the same numbers.

import { extent } from "d3-array";
import { scaleLinear } from "d3-scale";
import { area, line } from "d3-shape";
import { useEffect, useMemo, useRef, useState } from "react";
import type { Frequency, Values } from "../api";
import { monthName, num, period, short, yearOf } from "../format";

export interface ChartSeries {
  id: string;
  label: string;
  /** A CSS color, usually a token such as var(--series-forecast). */
  color: string;
  values: Values;
  /** Mark the last point with a dot and write the label beside it. */
  direct?: boolean;
  /** Draw a thin joining line from the last point of another series to this one's first. */
  joinFrom?: string;
}

export interface ChartBand {
  label: string;
  low: Values;
  high: Values;
}

interface ChartProps {
  dates: string[];
  series: ChartSeries[];
  band?: ChartBand;
  /** The position of the first forecast date. A hairline is drawn just before it. */
  markerIndex?: number;
  markerLabel?: string;
  frequency: Frequency;
  height?: number;
  /** Names for the positions along the bottom, when they are not dates. */
  labels?: string[];
  /** How a value is written in the tooltip and the table. */
  formatValue?: (value: number) => string;
  /** How a value is written on the axis. */
  formatTick?: (value: number) => string;
}

const MARGIN = { top: 22, right: 18, bottom: 26, left: 50 };

function useWidth(): [React.RefObject<HTMLDivElement>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver((entries) => setWidth(entries[0].contentRect.width));
    observer.observe(element);
    setWidth(element.clientWidth);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

/** Where to put labels along the time axis: at year changes, or every few periods. */
function timeTicks(dates: string[], frequency: Frequency, plotWidth: number, labels?: string[]) {
  const ticks: { index: number; label: string }[] = [];
  if (labels) {
    const gap = Math.max(1, Math.ceil((labels.length * 44) / Math.max(plotWidth, 1)));
    return labels.flatMap((label, index) => (index % gap === 0 ? [{ index, label }] : []));
  }
  const years = dates.map(yearOf);
  const yearStarts = years.flatMap((year, i) => (i > 0 && year !== years[i - 1] ? [i] : []));
  if (yearStarts.length >= 2) {
    for (const index of yearStarts) ticks.push({ index, label: String(years[index]) });
  } else {
    const every = Math.max(1, Math.ceil(dates.length / 6));
    for (let index = 0; index < dates.length; index += every) {
      const label =
        frequency === "monthly" || frequency === "quarterly"
          ? period(dates[index], frequency)
          : `${monthName(dates[index])} ${dates[index].slice(8, 10)}`;
      ticks.push({ index, label });
    }
  }
  // Keep labels at least 56 pixels apart.
  const step = plotWidth / Math.max(1, dates.length - 1);
  const kept: typeof ticks = [];
  for (const tick of ticks) {
    const last = kept[kept.length - 1];
    if (!last || (tick.index - last.index) * step >= 56) kept.push(tick);
  }
  return kept;
}

function lastDefined(values: Values): number {
  for (let i = values.length - 1; i >= 0; i--) if (values[i] !== null) return i;
  return -1;
}

function firstDefined(values: Values): number {
  return values.findIndex((value) => value !== null);
}

export function LineChart({
  dates,
  series,
  band,
  markerIndex,
  markerLabel,
  frequency,
  height = 340,
  labels,
  formatValue = num,
  formatTick = short,
}: ChartProps) {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState<number | null>(null);

  const directLabels = series.filter((s) => s.direct);
  const right = MARGIN.right + (directLabels.length ? 8 + Math.max(...directLabels.map((s) => s.label.length)) * 6.4 : 0);
  const plotWidth = Math.max(10, width - MARGIN.left - right);
  const plotHeight = height - MARGIN.top - MARGIN.bottom;

  const { x, y, yTicks } = useMemo(() => {
    const all: number[] = [];
    for (const s of series) for (const v of s.values) if (v !== null) all.push(v);
    if (band) {
      for (const v of band.low) if (v !== null) all.push(v);
      for (const v of band.high) if (v !== null) all.push(v);
    }
    let [low, high] = extent(all) as [number, number];
    if (low === undefined) [low, high] = [0, 1];
    // Start at zero when the data comes close to it; otherwise leave room around the data.
    const bottom = low >= 0 && low < 0.4 * high ? 0 : low - (high - low) * 0.12;
    const top = high + (high - bottom) * 0.06;
    const yScale = scaleLinear()
      .domain([low >= 0 ? Math.max(0, bottom) : bottom, top === bottom ? bottom + 1 : top])
      .range([MARGIN.top + plotHeight, MARGIN.top])
      .nice(5);
    const xScale = scaleLinear()
      .domain([0, Math.max(1, dates.length - 1)])
      .range([MARGIN.left, MARGIN.left + plotWidth]);
    return { x: xScale, y: yScale, yTicks: yScale.ticks(5) };
  }, [series, band, dates.length, plotWidth, plotHeight]);

  const xTicks = useMemo(
    () => timeTicks(dates, frequency, plotWidth, labels),
    [dates, frequency, plotWidth, labels],
  );
  const nameOf = (index: number) => (labels ? labels[index] : period(dates[index], frequency));

  const path = line<number | null>()
    .defined((value) => value !== null)
    .x((_, i) => x(i))
    .y((value) => y(value as number));

  const bandPath =
    band &&
    area<number>()
      .defined((i) => band.low[i] !== null && band.high[i] !== null)
      .x((i) => x(i))
      .y0((i) => y(band.low[i] as number))
      .y1((i) => y(band.high[i] as number))(dates.map((_, i) => i));

  const setFromPointer = (event: React.PointerEvent) => {
    const box = event.currentTarget.getBoundingClientRect();
    const index = Math.round(x.invert(event.clientX - box.left));
    setHover(Math.max(0, Math.min(dates.length - 1, index)));
  };

  const onKey = (event: React.KeyboardEvent) => {
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      const start = hover ?? dates.length - 1;
      const next = start + (event.key === "ArrowLeft" ? -1 : 1);
      setHover(Math.max(0, Math.min(dates.length - 1, next)));
    } else if (event.key === "Escape") {
      setHover(null);
    }
  };

  const hovered =
    hover === null
      ? []
      : series.flatMap((s) => (s.values[hover] !== null ? [{ s, value: s.values[hover] as number }] : []));
  const hoveredBand =
    hover !== null && band && band.low[hover] !== null && band.high[hover] !== null
      ? { low: band.low[hover] as number, high: band.high[hover] as number }
      : null;
  const tipOnLeft = hover !== null && x(hover) > MARGIN.left + plotWidth * 0.55;

  const summary = `${series.map((s) => s.label).join(", ")} from ${nameOf(0)} to ${nameOf(
    dates.length - 1,
  )}. Use the left and right arrow keys to read values.`;

  return (
    <div
      className="chart"
      ref={ref}
      style={{ height }}
      tabIndex={0}
      role="img"
      aria-label={summary}
      onKeyDown={onKey}
      onBlur={() => setHover(null)}
    >
      {width > 0 && (
        <svg height={height} viewBox={`0 0 ${width} ${height}`} onPointerLeave={() => setHover(null)}>
          {yTicks.map((tick) => (
            <g key={tick}>
              <line className="grid" x1={MARGIN.left} x2={MARGIN.left + plotWidth} y1={y(tick)} y2={y(tick)} />
              <text className="tick" x={MARGIN.left - 8} y={y(tick)} dy="0.32em" textAnchor="end">
                {formatTick(tick)}
              </text>
            </g>
          ))}
          <line
            className="axis"
            x1={MARGIN.left}
            x2={MARGIN.left + plotWidth}
            y1={MARGIN.top + plotHeight}
            y2={MARGIN.top + plotHeight}
          />
          {xTicks.map((tick) => (
            <text
              key={tick.index}
              className="tick"
              x={x(tick.index)}
              y={MARGIN.top + plotHeight + 17}
              textAnchor={tick.index === 0 && !labels ? "start" : "middle"}
            >
              {tick.label}
            </text>
          ))}

          {markerIndex !== undefined && markerIndex > 0 && markerIndex < dates.length && (
            <g>
              <line
                className="marker"
                x1={x(markerIndex - 0.5)}
                x2={x(markerIndex - 0.5)}
                y1={MARGIN.top - 8}
                y2={MARGIN.top + plotHeight}
              />
              {markerLabel && (
                <text className="marker-label" x={x(markerIndex - 0.5) + 6} y={MARGIN.top - 10}>
                  {markerLabel}
                </text>
              )}
            </g>
          )}

          {bandPath && <path d={bandPath} fill="var(--band)" />}

          {series.map((s) => {
            const joined = s.joinFrom ? series.find((other) => other.id === s.joinFrom) : undefined;
            const to = firstDefined(s.values);
            const from = joined ? lastDefined(joined.values.slice(0, Math.max(0, to))) : -1;
            return (
              <g key={s.id}>
                {joined && from >= 0 && to >= 0 && (
                  <line
                    x1={x(from)}
                    y1={y(joined.values[from] as number)}
                    x2={x(to)}
                    y2={y(s.values[to] as number)}
                    stroke={s.color}
                    strokeWidth={2}
                    strokeLinecap="round"
                    opacity={0.55}
                  />
                )}
                <path
                  d={path(s.values) ?? undefined}
                  fill="none"
                  stroke={s.color}
                  strokeWidth={2}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
              </g>
            );
          })}

          {directLabels.map((s) => {
            const last = lastDefined(s.values);
            if (last < 0) return null;
            const cy = y(s.values[last] as number);
            return (
              <g key={s.id}>
                <circle cx={x(last)} cy={cy} r={4.5} fill={s.color} stroke="var(--surface)" strokeWidth={2} />
                <text className="end-label" x={x(last) + 10} y={cy} dy="0.32em">
                  {s.label}
                </text>
              </g>
            );
          })}

          {hover !== null && (
            <g>
              <line
                className="crosshair"
                x1={x(hover)}
                x2={x(hover)}
                y1={MARGIN.top}
                y2={MARGIN.top + plotHeight}
              />
              {hovered.map(({ s, value }) => (
                <circle
                  key={s.id}
                  cx={x(hover)}
                  cy={y(value)}
                  r={4.5}
                  fill={s.color}
                  stroke="var(--surface)"
                  strokeWidth={2}
                />
              ))}
            </g>
          )}

          <rect
            x={MARGIN.left}
            y={MARGIN.top}
            width={plotWidth}
            height={plotHeight}
            fill="transparent"
            onPointerMove={setFromPointer}
            onPointerDown={setFromPointer}
          />
        </svg>
      )}

      {hover !== null && width > 0 && (
        <div
          className="chart-tip"
          style={tipOnLeft ? { right: width - x(hover) + 14 } : { left: x(hover) + 14 }}
        >
          <div className="when">{nameOf(hover)}</div>
          {hovered.map(({ s, value }) => (
            <div className="line" key={s.id}>
              <span className="key" style={{ color: s.color }} />
              <span className="value">{formatValue(value)}</span>
              <span className="name">{s.label}</span>
            </div>
          ))}
          {hoveredBand && band && (
            <div className="line">
              <span className="key box" style={{ color: "var(--band)" }} />
              <span className="value">
                {formatValue(hoveredBand.low)} – {formatValue(hoveredBand.high)}
              </span>
              <span className="name">{band.label}</span>
            </div>
          )}
          {hovered.length === 0 && !hoveredBand && <div className="name">No value</div>}
        </div>
      )}
    </div>
  );
}

/** The same numbers as a table: every chart has one. */
export function ChartTable({ dates, series, band, frequency, labels, formatValue = num }: ChartProps) {
  const write = (value: number | null) => (value === null ? "–" : formatValue(value));
  const rows = dates
    .map((date, i) => ({ date, i }))
    .filter(({ i }) => series.some((s) => s.values[i] !== null) || (band && band.low[i] !== null));
  return (
    <div className="table-wrap" style={{ maxHeight: 340, overflowY: "auto" }}>
      <table className="data compact">
        <thead>
          <tr>
            <th style={{ position: "sticky", top: 0 }}>{labels ? "" : "Period"}</th>
            {series.map((s) => (
              <th key={s.id} className="r" style={{ position: "sticky", top: 0 }}>
                {s.label}
              </th>
            ))}
            {band && (
              <>
                <th className="r" style={{ position: "sticky", top: 0 }}>
                  Low
                </th>
                <th className="r" style={{ position: "sticky", top: 0 }}>
                  High
                </th>
              </>
            )}
          </tr>
        </thead>
        <tbody>
          {(labels ? rows : rows.reverse()).map(({ date, i }) => (
            <tr key={date}>
              <td className="nowrap">{labels ? labels[i] : period(date, frequency)}</td>
              {series.map((s) => (
                <td key={s.id} className="r">
                  {write(s.values[i])}
                </td>
              ))}
              {band && (
                <>
                  <td className="r">{write(band.low[i])}</td>
                  <td className="r">{write(band.high[i])}</td>
                </>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface FigureProps extends ChartProps {
  title: string;
  subtitle?: React.ReactNode;
  actions?: React.ReactNode;
  note?: string | null;
}

/** A chart in its card: title, legend, and the switch between chart and table. */
export function ChartFigure({ title, subtitle, actions, note, ...chart }: FigureProps) {
  const [view, setView] = useState<"chart" | "table">("chart");
  const showLegend = chart.series.length + (chart.band ? 1 : 0) >= 2;
  return (
    <figure className="card" style={{ margin: 0 }}>
      <div className="card-head">
        <div style={{ minWidth: 0 }}>
          <h2>{title}</h2>
          {subtitle && <div className="sub">{subtitle}</div>}
        </div>
        <div className="row">
          {actions}
          <div className="segmented" role="tablist" aria-label="Show as">
            <button className={view === "chart" ? "on" : ""} onClick={() => setView("chart")}>
              Chart
            </button>
            <button className={view === "table" ? "on" : ""} onClick={() => setView("table")}>
              Table
            </button>
          </div>
        </div>
      </div>
      {view === "chart" ? (
        <div style={{ padding: "14px 16px 10px" }}>
          {showLegend && (
            <div className="legend" style={{ padding: "0 6px 6px" }}>
              {chart.series.map((s) => (
                <span key={s.id}>
                  <span className="key" style={{ color: s.color }} />
                  {s.label}
                </span>
              ))}
              {chart.band && (
                <span>
                  <span className="key box" style={{ color: "var(--band)" }} />
                  {chart.band.label}
                </span>
              )}
            </div>
          )}
          <LineChart {...chart} />
          {note && (
            <p className="hint" style={{ padding: "4px 6px 6px" }}>
              {note}
            </p>
          )}
        </div>
      ) : (
        <ChartTable {...chart} />
      )}
    </figure>
  );
}

