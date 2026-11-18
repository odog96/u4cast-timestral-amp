// How numbers, dates and periods are written. One place, so every screen agrees.

import type { Fill, Frequency } from "./api";

const whole = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const oneDecimal = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });
const twoDecimals = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });

/** A value in full: 1,284 or 12.5 or 0.46. */
export function num(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "–";
  const size = Math.abs(value);
  if (size >= 100) return whole.format(value);
  if (size >= 10) return oneDecimal.format(value);
  return twoDecimals.format(value);
}

/** A value made short: 12.9K, 4.2M. Used on axes and in stat tiles. */
export function short(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "–";
  return Math.abs(value) >= 10000 ? compact.format(value) : num(value);
}

const headline = new Intl.NumberFormat("en-US", { notation: "compact", maximumSignificantDigits: 3 });

/** A headline figure: 5.06M, 651K, 1,284. Three digits, so two close totals do not read the same. */
export function figure(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "–";
  return Math.abs(value) >= 10000 ? headline.format(value) : num(value);
}

/** A word with its first letter raised: month becomes Month. */
export const capital = (word: string) => word.charAt(0).toUpperCase() + word.slice(1);

/** A ratio as a percent: 0.138 becomes 13.8%. */
export function pct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "–";
  return `${(value * 100).toFixed(digits)}%`;
}

/** A change as a signed percent: +4.2% or -16.2%. */
export function signedPct(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "–";
  const text = `${Math.abs(value * 100).toFixed(1)}%`;
  return value > 0 ? `+${text}` : value < 0 ? `−${text}` : text;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function parts(iso: string): [number, number, number] {
  const [year, month, day] = iso.split("-").map(Number);
  return [year, month, day];
}

/** A period by its frequency: Jun 2026, Q2 2026, or Jun 8, 2026. */
export function period(iso: string | null | undefined, frequency: Frequency): string {
  if (!iso) return "–";
  const [year, month, day] = parts(iso);
  if (frequency === "monthly") return `${MONTHS[month - 1]} ${year}`;
  if (frequency === "quarterly") return `Q${Math.floor((month - 1) / 3) + 1} ${year}`;
  return `${MONTHS[month - 1]} ${day}, ${year}`;
}

/** A day in full: Nov 18, 2026. */
export function day(iso: string | null | undefined): string {
  if (!iso) return "–";
  const [year, month, dayOfMonth] = parts(iso.slice(0, 10));
  return `${MONTHS[month - 1]} ${dayOfMonth}, ${year}`;
}

export const yearOf = (iso: string) => parts(iso)[0];
export const monthOf = (iso: string) => parts(iso)[1];
export const monthName = (iso: string) => MONTHS[parts(iso)[1] - 1];

const UNITS: Record<Frequency, [string, string]> = {
  daily: ["day", "days"],
  weekly: ["week", "weeks"],
  monthly: ["month", "months"],
  quarterly: ["quarter", "quarters"],
};

/** A count of periods: 1 month, 12 months. */
export function periods(count: number, frequency: Frequency): string {
  return `${whole.format(count)} ${UNITS[frequency][count === 1 ? 0 : 1]}`;
}

export const unit = (frequency: Frequency, count = 2) => UNITS[frequency][count === 1 ? 0 : 1];

export const FREQUENCY_NAMES: Record<Frequency, string> = {
  daily: "Daily",
  weekly: "Weekly",
  monthly: "Monthly",
  quarterly: "Quarterly",
};

export const FILLS: { value: Fill; name: string; meaning: string }[] = [
  { value: "missing", name: "Unknown", meaning: "Leave it empty. The model works around it." },
  { value: "zero", name: "Zero", meaning: "Nothing happened, as in sales and orders." },
  { value: "average", name: "The series' average", meaning: "Use the average of the series' known values." },
  {
    value: "interpolate",
    name: "A straight line",
    meaning: "Draw a straight line between the known values around it.",
  },
  { value: "previous", name: "The last known value", meaning: "Repeat the value before it." },
];

/** About how long something takes, in words: 8 seconds, 2 minutes. */
export function duration(seconds: number): string {
  if (seconds < 50) return `${Math.max(5, Math.round(seconds / 5) * 5)} seconds`;
  const minutes = Math.round(seconds / 60);
  return minutes <= 1 ? "a minute" : `${minutes} minutes`;
}

/** The model's short name: Timestral-209M from mistralai/Timestral-209M. */
export const modelName = (name: string) => name.split("/").pop() ?? name;

/** A series' label split into the part to show large and the rest. */
export function splitLabel(label: string): { main: string; rest: string } {
  const pieces = label.split(" · ");
  return { main: pieces[pieces.length - 1], rest: pieces.slice(0, -1).join(" · ") };
}
