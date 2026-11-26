"use client";

import { Bar, BarChart, CartesianGrid, Cell, ComposedChart, Legend, Line, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useTheme } from "@/hooks/theme";
import { CHART, shortDate, topicLabel } from "@/lib/format";
import type { Bands, PropertySummary, TopicInsight, TopicSentimentRow, WeekPoint } from "@/types/api";

/**
 * Recharts needs literal colours, so the palette is read from the live CSS variables of the
 * current theme (globals.css) and recomputed whenever the mode or accent changes. CHART holds
 * the server-side / first-render fallback (dark mode, lime accent).
 */
/** Mix a #rrggbb colour toward another; used to darken the accent for lines on white. */
function mixHex(hex: string, toward: string, amount: number): string {
  const parse = (h: string) => {
    const m = /^#?([0-9a-f]{6})$/i.exec(h.trim());
    return m ? [0, 2, 4].map((i) => parseInt(m[1]!.slice(i, i + 2), 16)) : null;
  };
  const a = parse(hex);
  const b = parse(toward);
  if (!a || !b) return hex;
  return `#${a.map((v, i) => Math.round(v + (b[i]! - v) * amount).toString(16).padStart(2, "0")).join("")}`;
}

function usePalette() {
  // Subscribing to the theme re-renders the charts when the mode or accent changes; the palette
  // itself is read from the CSS variables that change with it (a dozen cheap lookups).
  const { mode } = useTheme();
  const c = { ...CHART };
  if (typeof window !== "undefined") {
    const cs = getComputedStyle(document.documentElement);
    const v = (name: string, fallback: string) => cs.getPropertyValue(name).trim() || fallback;
    c.positive = v("--color-positive", c.positive);
    c.negative = v("--color-negative", c.negative);
    c.neutral = v("--chart-neutral", c.neutral);
    c.accent = v("--color-accent", c.accent);
    // Light accents (lime, amber, teal) are too faint as a thin line on white: darken them there.
    c.rating = mode === "light" ? mixHex(c.accent, "#000000", 0.35) : c.accent;
    c.volume = v("--chart-volume", c.volume);
    c.grid = v("--color-line", c.grid);
    c.axis = v("--color-ink-faint", c.axis);
    c.cursor = v("--color-sand-100", c.cursor);
    c.tooltipBg = v("--color-surface", c.tooltipBg);
    c.ink = v("--color-ink", c.ink);
  }
  const tooltipStyle = {
    borderRadius: 12,
    border: `1px solid ${c.grid}`,
    backgroundColor: c.tooltipBg,
    color: c.ink,
    boxShadow: mode === "dark" ? "0 10px 25px rgb(0 0 0 / 0.35)" : "0 4px 12px rgb(0 0 0 / 0.08)",
    fontSize: 13,
  };
  return { c, axis: { fontSize: 12, fill: c.axis }, tooltipStyle, tooltipText: { color: c.ink } };
}

function Figure({ label, height, children }: { label: string; height: number; children: React.ReactNode }) {
  return (
    <div role="img" aria-label={label} style={{ height }} className="w-full">
      <ResponsiveContainer width="100%" height="100%">
        {children as React.ReactElement}
      </ResponsiveContainer>
    </div>
  );
}

/** Weekly review volume (bars) and average rating (line). Weeks with too few reviews have no rating point. */
export function VolumeRatingChart({ weeks }: { weeks: WeekPoint[] }) {
  const { c, axis, tooltipStyle, tooltipText } = usePalette();
  const data = weeks.map((w) => ({ week: shortDate(w.week_start), reviews: w.reviews, rating: w.avg_rating.value }));
  return (
    <Figure label="Weekly review volume and average rating" height={300}>
      <ComposedChart data={data} margin={{ top: 8, right: 4, bottom: 0, left: -16 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={c.grid} vertical={false} />
        <XAxis dataKey="week" tick={axis} interval="preserveStartEnd" minTickGap={28} tickLine={false} />
        <YAxis yAxisId="v" tick={axis} allowDecimals={false} tickLine={false} axisLine={false} />
        <YAxis yAxisId="r" orientation="right" domain={[1, 10]} tick={axis} tickLine={false} axisLine={false} />
        <Tooltip contentStyle={tooltipStyle} labelStyle={tooltipText} itemStyle={tooltipText} cursor={{ fill: c.cursor }} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Bar yAxisId="v" dataKey="reviews" name="Reviews" fill={c.volume} fillOpacity={0.85} radius={[4, 4, 0, 0]} />
        <Line yAxisId="r" dataKey="rating" name="Average rating (weeks with 5+ reviews)" stroke={c.rating} strokeWidth={2.5} dot={{ r: 3, fill: c.tooltipBg }} connectNulls={false} />
      </ComposedChart>
    </Figure>
  );
}

/** Positive / neutral / negative reviews per week, by guest rating (8+, 7, 6 or below). */
export function SentimentTrendChart({ weeks }: { weeks: WeekPoint[] }) {
  const { c, axis, tooltipStyle, tooltipText } = usePalette();
  const data = weeks.map((w) => ({ week: shortDate(w.week_start), ...w.rating_bands }));
  return (
    <Figure label="Positive and negative reviews per week" height={280}>
      <BarChart data={data} margin={{ top: 8, right: 4, bottom: 0, left: -16 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={c.grid} vertical={false} />
        <XAxis dataKey="week" tick={axis} interval="preserveStartEnd" minTickGap={28} tickLine={false} />
        <YAxis tick={axis} allowDecimals={false} tickLine={false} axisLine={false} />
        <Tooltip contentStyle={tooltipStyle} labelStyle={tooltipText} itemStyle={tooltipText} cursor={{ fill: c.cursor }} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Bar dataKey="positive" name="Positive (8–10)" stackId="a" fill={c.positive} />
        <Bar dataKey="neutral" name="Neutral (7)" stackId="a" fill={c.neutral} />
        <Bar dataKey="negative" name="Negative (1–6)" stackId="a" fill={c.negative} radius={[4, 4, 0, 0]} />
      </BarChart>
    </Figure>
  );
}

export function SentimentDonut({ bands }: { bands: Bands }) {
  const { c, axis, tooltipStyle, tooltipText } = usePalette();
  const data = [
    { name: "Positive (8–10)", value: bands.positive, fill: c.positive },
    { name: "Neutral (7)", value: bands.neutral, fill: c.neutral },
    { name: "Negative (1–6)", value: bands.negative, fill: c.negative },
  ];
  return (
    <Figure label="Share of positive, neutral and negative reviews" height={200}>
      <PieChart>
        <Pie data={data} dataKey="value" nameKey="name" innerRadius={58} outerRadius={84} paddingAngle={2} stroke="none">
          {data.map((d) => <Cell key={d.name} fill={d.fill} />)}
        </Pie>
        <Tooltip contentStyle={tooltipStyle} labelStyle={tooltipText} itemStyle={tooltipText} cursor={{ fill: c.cursor }} />
      </PieChart>
    </Figure>
  );
}

export function DistributionChart({ dist }: { dist: Record<string, number> }) {
  const { c, axis, tooltipStyle, tooltipText } = usePalette();
  const data = Object.entries(dist).map(([rating, count]) => ({ rating, count, fill: Number(rating) >= 8 ? c.positive : Number(rating) <= 6 ? c.negative : c.neutral }));
  return (
    <Figure label="Distribution of guest ratings" height={200}>
      <BarChart data={data} margin={{ top: 8, right: 4, bottom: 0, left: -24 }}>
        <XAxis dataKey="rating" tick={axis} tickLine={false} />
        <YAxis tick={axis} allowDecimals={false} tickLine={false} axisLine={false} />
        <Tooltip contentStyle={tooltipStyle} labelStyle={tooltipText} itemStyle={tooltipText} cursor={{ fill: c.cursor }} />
        <Bar dataKey="count" name="Reviews" radius={[4, 4, 0, 0]}>
          {data.map((d) => <Cell key={d.rating} fill={d.fill} />)}
        </Bar>
      </BarChart>
    </Figure>
  );
}

/** Praise vs criticism per topic (number of reviews). */
export function TopicBars({ topics }: { topics: (TopicSentimentRow | TopicInsight)[] }) {
  const { c, axis, tooltipStyle, tooltipText } = usePalette();
  const data = topics.map((t) => ({ topic: topicLabel(t.topic), Praise: t.positive, Criticism: t.negative + t.mixed }));
  return (
    <Figure label="Praise and criticism by topic" height={340}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 12, bottom: 0, left: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={c.grid} horizontal={false} />
        <XAxis type="number" tick={axis} allowDecimals={false} tickLine={false} axisLine={false} />
        <YAxis type="category" dataKey="topic" tick={axis} width={112} tickLine={false} axisLine={false} />
        <Tooltip contentStyle={tooltipStyle} labelStyle={tooltipText} itemStyle={tooltipText} cursor={{ fill: c.cursor }} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Bar dataKey="Praise" fill={c.positive} radius={[0, 4, 4, 0]} />
        <Bar dataKey="Criticism" fill={c.negative} radius={[0, 4, 4, 0]} />
      </BarChart>
    </Figure>
  );
}

/** Average rating per property, for side-by-side comparison. Properties without enough reviews are omitted. */
export function PropertyRatingChart({ list }: { list: PropertySummary[] }) {
  const { c, axis, tooltipStyle, tooltipText } = usePalette();
  const data = list
    .filter((p) => p.avg_rating.value !== null)
    .map((p) => ({ name: p.property.name, rating: p.avg_rating.value, reviews: p.reviews }));
  return (
    <Figure label="Average rating by property" height={Math.max(180, data.length * 64)}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 24, bottom: 0, left: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={c.grid} horizontal={false} />
        <XAxis type="number" domain={[0, 10]} tick={axis} tickLine={false} axisLine={false} />
        <YAxis type="category" dataKey="name" tick={axis} width={140} tickLine={false} axisLine={false} />
        <Tooltip contentStyle={tooltipStyle} labelStyle={tooltipText} itemStyle={tooltipText} cursor={{ fill: c.cursor }} />
        <Bar dataKey="rating" name="Average rating" fill={c.rating} radius={[0, 4, 4, 0]} barSize={22} label={{ position: "right", fontSize: 12, fill: c.axis, formatter: (v: unknown) => (typeof v === "number" ? v.toFixed(1) : "") }} />
      </BarChart>
    </Figure>
  );
}

