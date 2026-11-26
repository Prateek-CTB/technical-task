"use client";

import { Lightbulb, Quote } from "lucide-react";
import type { ReactNode } from "react";
import Link from "next/link";
import { EmptyState, StatusNote, TopicChip } from "@/components/ui";
import { usePropertyComparison } from "@/hooks/queries";
import { fmtNum, pct, plural, topicLabel } from "@/lib/format";
import type { Evidence, Overview, PropertySummary, Recommendation, Topics } from "@/types/api";

/** The headline sentence, e.g. "40% of negative-rated reviews this week mentioned cleanliness". Numbers come from the API. */
export function HeadlineInsight({ topics, period }: { topics: Topics | undefined; period: string }) {
  const ranked = (topics?.topics ?? [])
    .filter((t) => t.share_of_negative_rated_reviews.value !== null)
    .sort((a, b) => (b.share_of_negative_rated_reviews.value ?? 0) - (a.share_of_negative_rated_reviews.value ?? 0));
  const top = ranked[0];
  if (!top) {
    return (
      <div className="rounded-xl bg-sand-100 p-4 text-sm text-ink-soft">
        Not enough negative-rated reviews have been analysed {period} to give a reliable percentage.
      </div>
    );
  }
  const s = top.share_of_negative_rated_reviews;
  return (
    <div className="rounded-xl border border-negative-100 bg-negative-50 p-4 text-negative-700">
      <p className="text-sm leading-relaxed">
        <strong className="font-display text-2xl text-negative-700">{pct(s.value)}</strong> of negative-rated reviews {period} mention{" "}
        <strong>{topicLabel(top.topic).toLowerCase()}</strong> problems
        <span className="opacity-80"> ({s.count} of {s.of} analysed)</span>
      </p>
      <StatusNote status={s.status} />
    </div>
  );
}

/** The API labels evidence with the property slug; resolve it to the property name and id. */
function usePropertyRef(slug: string): { id?: number; name: string } {
  const q = usePropertyComparison();
  const p = q.data?.data.find((x) => x.property.slug === slug)?.property;
  return { id: p?.id, name: p?.name ?? slug };
}

/** Link to the explorer with the filters that reproduce an insight's underlying reviews. */
export function reviewsHref(f: { property_id?: number | string; topic?: string; topic_sentiment?: string; q?: string }): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) if (v !== undefined && v !== "") qs.set(k, String(v));
  return `/reviews?${qs.toString()}`;
}

export function EvidenceQuote({ e }: { e: Evidence }) {
  const { id, name } = usePropertyRef(e.property);
  // Quotes are verbatim, so searching for one finds the review it came from.
  const href = reviewsHref({ property_id: id, q: e.quote.slice(0, 60) });
  return (
    <li className="flex gap-2 text-sm text-ink-soft">
      <Quote className="mt-0.5 size-3.5 shrink-0 text-ink-faint" aria-hidden />
      <span>
        <em>“{e.quote}”</em>
        <span className="ml-1 text-xs text-ink-faint">· {name} · rated {e.rating} · </span>
        <Link href={href} className="text-xs font-medium text-accent-700 hover:underline">open review</Link>
      </span>
    </li>
  );
}

/** Prioritised by number of supporting reviews, then by whether the issue is getting worse. */
export function prioritise(recs: Recommendation[]): Recommendation[] {
  const worse = (r: Recommendation) => (r.trend.direction === "rising" || r.trend.direction === "worsening" || r.trend.last_4_weeks > r.trend.previous_4_weeks ? 1 : 0);
  return [...recs].sort((a, b) => b.supporting_reviews - a.supporting_reviews || worse(b) - worse(a));
}

export function RecommendationList({ recs, limit, showProperty = true }: { recs: Recommendation[]; limit?: number; showProperty?: boolean }) {
  const list = prioritise(recs).slice(0, limit);
  if (!list.length) {
    return <EmptyState title="No recurring problems found" icon={<Lightbulb className="size-8" />}>No topic has been criticised by enough reviews at one property in this period.</EmptyState>;
  }
  return (
    <ol className="space-y-4">
      {list.map((r, i) => (
        <li key={`${r.property.id}-${r.topic}`} className="rounded-xl border border-line p-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="grid size-6 place-items-center rounded-full bg-accent text-xs font-bold text-on-accent" aria-label={`Priority ${i + 1}`}>{i + 1}</span>
            {showProperty && <Link href={`/properties/${r.property.id}`} className="font-semibold text-accent-700 hover:underline">{r.property.name}</Link>}
            <TopicChip topic={r.topic} sentiment="negative" />
            <span className="text-xs text-ink-faint">
              {r.supporting_reviews} reviews · last 4 weeks {r.trend.last_4_weeks} vs {r.trend.previous_4_weeks} before
            </span>
          </div>
          <p className="mt-2 text-sm text-ink">{r.problem}</p>
          {r.suggested_action ? (
            <p className="mt-2 rounded-lg bg-positive-50 p-2.5 text-sm text-ink"><strong>Suggested action:</strong> {r.suggested_action}</p>
          ) : (
            <p className="mt-2 text-xs text-ink-faint">No specific action was justified by these reviews.</p>
          )}
          {r.evidence.length > 0 && <ul className="mt-3 space-y-1.5">{r.evidence.slice(0, 2).map((e) => <EvidenceQuote key={e.review_id} e={e} />)}</ul>}
          <p className="mt-2 text-sm">
            <Link href={reviewsHref({ property_id: r.property.id, topic: r.topic, topic_sentiment: "negative" })} className="font-medium text-accent-700 hover:underline">
              Read the {r.supporting_reviews} underlying reviews →
            </Link>
          </p>
          <p className="mt-1 text-xs text-ink-faint">
            Based on {r.supporting_reviews} of {r.analysed_reviews} analysed reviews at this property. {r.uncertainty.includes("low_sample") && "Small sample. "}
            {r.uncertainty.includes("mock_analysis") && "From the offline test analyser, not AI. "}
            A trend shows association, not cause.
          </p>
        </li>
      ))}
    </ol>
  );
}

export function TopicList({ topics }: { topics: { topic: string; reviews: number }[] }) {
  return <>{topics.map((t) => <span key={t.topic} className="mr-1.5 inline-block"><TopicChip topic={t.topic} /></span>)}</>;
}


/**
 * Plain-language summary assembled from API responses. It states numbers the backend computed and
 * never derives new statistics.
 */
export function ExecutiveSummary({ overview, comparison, recs }: { overview: Overview | undefined; comparison: PropertySummary[] | undefined; recs: Recommendation[] | undefined }) {
  const lines: ReactNode[] = [];
  if (overview) {
    const wc = overview.week_comparison;
    const cur = wc.current_week_to_date;
    if (cur.value === null) {
      lines.push(
        cur.reviews === 0 ? (
          <>
            No reviews have been published yet this week
            {wc.previous_week_full.value !== null ? <>; last full week averaged <strong>{fmtNum(wc.previous_week_full.value)}</strong> from {plural(wc.previous_week_full.reviews, "review")}</> : null}.
          </>
        ) : (
          <>Only {plural(cur.reviews, "review")} so far this week, too few for a reliable average.</>
        ),
      );
    } else {
      const known = wc.avg_rating_delta !== null && wc.delta_status !== "insufficient_data";
      lines.push(
        <>
          This week averages <strong>{fmtNum(cur.value)} / 10</strong> from {plural(cur.reviews, "review")}
          {known && wc.avg_rating_delta !== null ? (
            <>, {wc.avg_rating_delta >= 0 ? "up" : "down"} {Math.abs(wc.avg_rating_delta).toFixed(2)} on the same days last week{wc.delta_status !== "ok" ? " (small sample, indicative only)" : ""}</>
          ) : (
            <>; there is not enough data from last week for a reliable comparison</>
          )}
          .
        </>,
      );
    }
  }
  const rated = (comparison ?? []).filter((p) => p.avg_rating.value !== null && p.avg_rating.status !== "insufficient_data");
  if (rated.length >= 2) {
    const sorted = [...rated].sort((a, b) => (b.avg_rating.value ?? 0) - (a.avg_rating.value ?? 0));
    const hi = sorted[0];
    const lo = sorted[sorted.length - 1];
    if (hi && lo) lines.push(<>Highest rated: <strong>{hi.property.name}</strong> ({fmtNum(hi.avg_rating.value)}). Lowest: <strong>{lo.property.name}</strong> ({fmtNum(lo.avg_rating.value)}).</>);
  }
  if (recs) {
    const top = prioritise(recs)[0];
    lines.push(
      top ? (
        <>
          Most repeated problem: <strong>{topicLabel(top.topic).toLowerCase()}</strong> at <strong>{top.property.name}</strong>, raised by {top.supporting_reviews} reviews.{" "}
          <Link className="font-medium text-accent-700 hover:underline" href={reviewsHref({ property_id: top.property.id, topic: top.topic, topic_sentiment: "negative" })}>Read them →</Link>
        </>
      ) : (
        <>No topic has been criticised by enough reviews at one property to count as a recurring problem yet.</>
      ),
    );
  }
  if (!lines.length) return null;
  return (
    <section aria-label="Executive summary" className="mb-4 rounded-2xl border border-line bg-surface p-5">
      <h2 className="text-xs font-semibold uppercase tracking-[0.14em] text-ink-faint">Summary</h2>
      <ul className="mt-2 space-y-1.5 text-[15px] leading-relaxed text-ink">
        {lines.map((l, i) => <li key={i}>{l}</li>)}
      </ul>
    </section>
  );
}
