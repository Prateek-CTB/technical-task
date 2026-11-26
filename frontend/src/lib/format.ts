import type { SampleStatus, Sentiment, TopicKey } from "@/types/api";

export const TOPIC_LABEL: Record<TopicKey, string> = {
  cleanliness: "Cleanliness",
  check_in: "Check-in",
  staff: "Staff",
  noise: "Noise",
  facilities: "Facilities",
  location: "Location",
  room_condition: "Room condition",
  value: "Value for money",
};
export const TOPICS = Object.keys(TOPIC_LABEL) as TopicKey[];
export const topicLabel = (t: string) => TOPIC_LABEL[t as TopicKey] ?? t;

export const SENTIMENT_LABEL: Record<Sentiment, string> = {
  positive: "Positive",
  negative: "Negative",
  neutral: "Neutral",
  mixed: "Mixed",
};

/** Chart colours (recharts needs literal values): dark dashboard palette, lime and blue series. Every chart also has a legend
 * and labelled series, so the red/green pair is never the only way to tell series apart. */
export const CHART = {
  positive: "#22c55e",
  neutral: "#475569",
  negative: "#f43f5e",
  mixed: "#a855f7",
  rating: "#b4f03c",
  volume: "#3b82f6",
  accent: "#b4f03c",
  grid: "#1f2738",
  axis: "#94a3b8",
  cursor: "#161d2e",
  tooltipBg: "#0d1324",
  ink: "#f8fafc",
};

export const fmtNum = (v: number | null | undefined, d = 1) => (v === null || v === undefined ? "—" : v.toFixed(d));
export const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${Math.round(v * 100)}%`);
export const signed = (v: number | null | undefined, d = 2) =>
  v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(d)}`;
export const plural = (n: number, one: string, many = `${one}s`) =>
  `${n.toLocaleString("en-AU")} ${n === 1 ? one : many}`;

const UTC = { timeZone: "UTC" } as const;
export const fmtDate = (iso: string) =>
  new Date(iso).toLocaleDateString("en-AU", { day: "numeric", month: "short", year: "numeric", ...UTC });
export const shortDate = (iso: string) =>
  new Date(iso).toLocaleDateString("en-AU", { day: "numeric", month: "short", ...UTC });
export const fmtDateTime = (iso: string) =>
  new Date(iso).toLocaleString("en-AU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

export const STATUS_TEXT: Record<SampleStatus, string> = {
  insufficient_data: "Not enough reviews yet",
  low_sample: "Small sample: treat with caution",
  ok: "",
  indicative: "Indicative only",
};

const toIso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
export const isoDaysAgo = (days: number, from = new Date()) => {
  const d = new Date(from);
  d.setDate(d.getDate() - days);
  return toIso(d);
};
export const mondayIso = (from = new Date()) => {
  const d = new Date(from);
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
  return toIso(d);
};
