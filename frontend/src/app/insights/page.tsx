"use client";

import Link from "next/link";
import { useState } from "react";
import { TopicBars } from "@/components/charts";
import { EvidenceQuote, RecommendationList, reviewsHref } from "@/components/insights";
import { Card, PageHeader, QueryBoundary, StatusNote } from "@/components/ui";
import { usePropertyComparison, useRecommendations, useTopics } from "@/hooks/queries";
import { pct, topicLabel } from "@/lib/format";
import { cx } from "@/lib/cx";
import type { TopicInsight } from "@/types/api";

function TopicCard({ t, property }: { t: TopicInsight; property: string }) {
  const mentions = t.positive + t.negative + t.neutral + t.mixed;
  const evidence = [...t.complaint_evidence.slice(0, 2), ...t.praise_evidence.slice(0, 1)];
  return (
    <Card as="article" title={topicLabel(t.topic)} subtitle={`${mentions} review${mentions === 1 ? "" : "s"} mention this`}>
      <div className="flex flex-wrap gap-1.5">
        {t.positive > 0 && <span className="rounded-full bg-positive-50 px-2.5 py-0.5 text-xs font-medium text-positive-700 ring-1 ring-positive-100">+ {t.positive} praise</span>}
        {t.negative + t.mixed > 0 && <span className="rounded-full bg-negative-50 px-2.5 py-0.5 text-xs font-medium text-negative-700 ring-1 ring-negative-100">− {t.negative + t.mixed} criticism</span>}
        {t.neutral > 0 && <span className="rounded-full bg-sand-100 px-2.5 py-0.5 text-xs font-medium text-ink-soft ring-1 ring-line">{t.neutral} neutral</span>}
      </div>
      {t.share_of_negative_rated_reviews.value !== null && (
        <p className="mt-3 text-sm text-ink-soft">
          <strong className="text-ink">{pct(t.share_of_negative_rated_reviews.value)}</strong> of negative-rated reviews mention it ({t.share_of_negative_rated_reviews.count} of {t.share_of_negative_rated_reviews.of})
          <StatusNote status={t.share_of_negative_rated_reviews.status} />
        </p>
      )}
      {t.most_common_complaints.length > 0 && (
        <ul className="mt-3 list-disc space-y-0.5 pl-5 text-sm text-ink-soft">
          {t.most_common_complaints.slice(0, 3).map((c) => <li key={c.complaint}>{c.complaint} <span className="text-ink-faint">×{c.count}</span></li>)}
        </ul>
      )}
      {mentions > 0 && (
        <p className="mt-3 text-sm"><Link href={reviewsHref({ property_id: property, topic: t.topic })} className="font-medium text-accent-700 hover:underline">Read the {mentions} reviews →</Link></p>
      )}
      {evidence.length > 0 && <ul className="mt-3 space-y-1.5 border-t border-line pt-3">{evidence.map((e) => <EvidenceQuote key={`${e.review_id}-${e.quote}`} e={e} />)}</ul>}
    </Card>
  );
}

export default function InsightsPage() {
  const [property, setProperty] = useState("");
  const props = usePropertyComparison();
  const topics = useTopics({ property_id: property || undefined });
  const recs = useRecommendations({ property_id: property || undefined, min_reviews: 3, window_weeks: 12 });

  return (
    <>
      <PageHeader title="Review Intelligence" subtitle="What guests praise and criticise, grouped into eight operational topics, with the guests' own words as evidence.">
        <label className="flex items-center gap-2 text-sm text-ink-soft">
          Property
          <select value={property} onChange={(e) => setProperty(e.target.value)} className="rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm text-ink">
            <option value="">All properties</option>
            {props.data?.data.map((p) => <option key={p.property.id} value={p.property.id}>{p.property.name}</option>)}
          </select>
        </label>
      </PageHeader>

      <div className="grid gap-4 lg:grid-cols-5">
        <Card title="Praise and criticism by topic" subtitle="Number of reviews mentioning each topic" className="lg:col-span-2">
          <QueryBoundary q={topics} what="topics" height="h-80">
            {(d) => (
              <>
                <TopicBars topics={d.topics} />
                <StatusNote extra={`${d.analysed_reviews} of ${d.reviews_in_period} reviews analysed. A review can mention several topics.`} />
              </>
            )}
          </QueryBoundary>
        </Card>
        <Card title="Prioritised recommendations" subtitle="Ranked by how many reviews raise the problem, then by whether it is getting worse" className="lg:col-span-3">
          <QueryBoundary q={recs} what="recommendations" height="h-48">{(d) => <RecommendationList recs={d} showProperty={!property} />}</QueryBoundary>
        </Card>
      </div>

      <h2 className="mb-3 mt-8 font-display text-2xl font-semibold text-ink">Topic by topic</h2>
      <QueryBoundary q={topics} what="topics" height="h-48">
        {(d) => (
          <>
            <div className={cx("grid gap-4 md:grid-cols-2 xl:grid-cols-3")}>{d.topics.map((t) => <TopicCard key={t.topic} t={t} property={property} />)}</div>
            <p className="mt-3 text-xs text-ink-faint">{d.note}</p>
            <p className="mt-1 text-xs text-ink-faint">How this works: an AI model reads each review and tags which of the eight topics it mentions and whether each is praised or criticised. Every tag must be backed by a quote from the review. It can misread sarcasm or short reviews, so treat small counts as indicative.</p>
          </>
        )}
      </QueryBoundary>
    </>
  );
}
