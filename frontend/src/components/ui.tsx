"use client";

import type { UseQueryResult } from "@tanstack/react-query";
import { AlertTriangle, ArrowDownRight, ArrowRight, ArrowUpRight, Inbox, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";
import { cx } from "@/lib/cx";
import { fmtNum, SENTIMENT_LABEL, STATUS_TEXT, topicLabel } from "@/lib/format";
import type { Envelope, Meta, SampleStatus, Sentiment } from "@/types/api";

export function PageHeader({ title, subtitle, children }: { title: string; subtitle?: string; children?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-ink">{title}</h1>
        {subtitle && <p className="mt-1 max-w-2xl text-sm text-ink-soft">{subtitle}</p>}
      </div>
      {children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
    </div>
  );
}

export function Card({
  title,
  subtitle,
  action,
  children,
  className,
  as: Tag = "section",
}: {
  title?: string;
  subtitle?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  as?: "section" | "article" | "div";
}) {
  return (
    <Tag className={cx("card-surface min-w-0 p-5", className)}>
      {(title || action) && (
        <header className="mb-4 flex items-start justify-between gap-3">
          <div>
            {title && <h2 className="text-base font-semibold text-ink">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-sm text-ink-faint">{subtitle}</p>}
          </div>
          {action}
        </header>
      )}
      {children}
    </Tag>
  );
}

export function StatusNote({ status, extra }: { status?: SampleStatus; extra?: string }) {
  const text = status ? STATUS_TEXT[status] : "";
  if (!text && !extra) return null;
  return <p className="mt-1 text-xs text-warning-700">{[text, extra].filter(Boolean).join(" · ")}</p>;
}

/** Change indicator. Colour is never the only signal: an arrow and a sign are always shown. */
export function Delta({ value, digits = 2, unit = "", invert = false }: { value: number | null; digits?: number; unit?: string; invert?: boolean }) {
  if (value === null) return <span className="text-sm text-ink-faint">no comparison yet</span>;
  const good = invert ? value < 0 : value > 0;
  const bad = invert ? value > 0 : value < 0;
  const Icon = value > 0 ? ArrowUpRight : value < 0 ? ArrowDownRight : ArrowRight;
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-sm font-medium tnum",
        good && "bg-positive-50 text-positive-700",
        bad && "bg-negative-50 text-negative-700",
        !good && !bad && "bg-sand-100 text-ink-soft",
      )}
    >
      <Icon className="size-3.5" aria-hidden />
      {value > 0 ? "+" : ""}
      {value.toFixed(digits)}
      {unit}
    </span>
  );
}

export function Kpi({
  label, value, unit, sub, status, icon, accent,
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  sub?: ReactNode;
  status?: SampleStatus;
  icon?: ReactNode;
  accent?: boolean;
}) {
  return (
    <div className="card-surface flex flex-col p-5">
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm text-ink-faint">{label}</p>
        {icon && <span className="inline-flex items-center rounded-full border border-line px-2 py-1 text-ink-soft" aria-hidden>{icon}</span>}
      </div>
      <p className={cx("mt-1.5 text-3xl font-semibold tracking-tight tnum", accent ? "text-accent" : "text-ink")}>
        {value}
        {unit && <span className="ml-1 text-base font-normal text-ink-faint">{unit}</span>}
      </p>
      {(sub || (status && status !== "ok")) && (
        <div className="mt-4 border-t border-line pt-3 text-sm text-ink-soft">
          {sub}
          <StatusNote status={status === "ok" ? undefined : status} />
        </div>
      )}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx("animate-pulse rounded-lg bg-sand-100", className)} />;
}

export function Loading({ what = "data", height = "h-40" }: { what?: string; height?: string }) {
  return (
    <div role="status" aria-label={`Loading ${what}`} className="space-y-3">
      <Skeleton className="h-5 w-1/3" />
      <Skeleton className={height} />
    </div>
  );
}

export function ErrorBox({ error, retry }: { error: unknown; retry?: () => void }) {
  return (
    <div role="alert" className="rounded-xl border border-negative-100 bg-negative-50 p-4 text-sm text-negative-700">
      <p className="flex items-center gap-2 font-semibold">
        <AlertTriangle className="size-4" aria-hidden /> This section could not be loaded
      </p>
      <p className="mt-1">{error instanceof Error ? error.message : "Unknown error"}</p>
      {retry && (
        <button onClick={retry} className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-negative/40 bg-surface px-3 py-1.5 font-medium text-negative-700 hover:bg-negative-100">
          <RefreshCw className="size-3.5" aria-hidden /> Try again
        </button>
      )}
    </div>
  );
}

export function EmptyState({ title, children, icon }: { title: string; children?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex flex-col items-center py-10 text-center">
      <span className="mb-3 text-ink-faint" aria-hidden>{icon ?? <Inbox className="size-8" />}</span>
      <p className="font-medium text-ink">{title}</p>
      {children && <p className="mt-1 max-w-md text-sm text-ink-soft">{children}</p>}
    </div>
  );
}

/** Renders loading / error / empty / data for a query; hands `meta` to the render function. */
export function QueryBoundary<T>({
  q, children, what, height, isEmpty, empty,
}: {
  q: UseQueryResult<Envelope<T, Meta>>;
  children: (data: T, meta: Meta) => ReactNode;
  what?: string;
  height?: string;
  isEmpty?: (d: T) => boolean;
  empty?: ReactNode;
}) {
  if (q.isPending) return <Loading what={what} height={height} />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => void q.refetch()} />;
  if (isEmpty?.(q.data.data)) return <>{empty ?? <EmptyState title="Nothing to show yet" />}</>;
  return <>{children(q.data.data, q.data.meta)}</>;
}

const SENTIMENT_STYLE: Record<Sentiment, string> = {
  positive: "bg-positive-50 text-positive-700 ring-positive-100",
  negative: "bg-negative-50 text-negative-700 ring-negative-100",
  neutral: "bg-sand-100 text-ink-soft ring-line",
  mixed: "bg-accent-50 text-accent-800 ring-accent-100",
};
const MARK: Record<Sentiment, string> = { positive: "+", negative: "−", mixed: "±", neutral: "·" };

export function SentimentBadge({ sentiment }: { sentiment: Sentiment }) {
  return (
    <span className={cx("inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1", SENTIMENT_STYLE[sentiment])}>
      {SENTIMENT_LABEL[sentiment]}
    </span>
  );
}

export function TopicChip({ topic, sentiment }: { topic: string; sentiment?: Sentiment }) {
  return (
    <span className={cx("inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1", SENTIMENT_STYLE[sentiment ?? "neutral"])}>
      {sentiment && <span aria-hidden>{MARK[sentiment]}</span>}
      {topicLabel(topic)}
      {sentiment && <span className="sr-only"> ({sentiment})</span>}
    </span>
  );
}

export function RatingBadge({ rating }: { rating: number }) {
  const tone =
    rating >= 8
      ? "bg-positive-50 text-positive-700 ring-positive-100"
      : rating <= 6
        ? "bg-negative-50 text-negative-700 ring-negative-100"
        : "bg-sand-100 text-ink-soft ring-line";
  return (
    <span className={cx("inline-flex min-w-10 justify-center rounded-lg px-2 py-1.5 text-sm font-semibold ring-1 tnum", tone)} aria-label={`Rating ${fmtNum(rating)} out of 10`}>
      {fmtNum(rating)}
    </span>
  );
}

/** Stacked horizontal bar showing positive / neutral / negative shares. */
export function SentimentBar({ positive, neutral, negative }: { positive: number; neutral: number; negative: number }) {
  const total = positive + neutral + negative;
  if (!total) return <p className="text-sm text-ink-faint">No reviews</p>;
  const w = (n: number) => `${(n / total) * 100}%`;
  return (
    <div>
      <div className="flex h-2.5 overflow-hidden rounded-full bg-sand-100" role="img"
        aria-label={`${Math.round((positive / total) * 100)}% positive, ${Math.round((neutral / total) * 100)}% neutral, ${Math.round((negative / total) * 100)}% negative`}>
        <div style={{ width: w(positive) }} className="bg-positive" />
        <div style={{ width: w(neutral) }} className="bg-[#d4d4d8]" />
        <div style={{ width: w(negative) }} className="bg-negative" />
      </div>
      <p className="mt-1.5 text-xs text-ink-soft tnum">
        {Math.round((positive / total) * 100)}% positive · {Math.round((neutral / total) * 100)}% neutral · {Math.round((negative / total) * 100)}% negative
      </p>
    </div>
  );
}
