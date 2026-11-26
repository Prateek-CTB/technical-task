"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { use } from "react";
import { DistributionChart, SentimentTrendChart, TopicBars, VolumeRatingChart } from "@/components/charts";
import { RecommendationList } from "@/components/insights";
import { Card, Delta, Kpi, QueryBoundary, SentimentBar } from "@/components/ui";
import { useProperty, useWeekly } from "@/hooks/queries";
import { fmtNum, plural } from "@/lib/format";

export default function PropertyPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const detail = useProperty(id);
  const weekly = useWeekly(id);
  return (
    <>
      <Link href="/properties" className="mb-3 inline-flex items-center gap-1 text-sm text-accent-700 hover:underline"><ArrowLeft className="size-4" aria-hidden /> All properties</Link>
      <QueryBoundary q={detail} what="property" height="h-48">
        {(d) => {
          const wc = d.week_comparison;
          return (
            <>
              <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
                <div>
                  <h1 className="font-display text-3xl font-semibold tracking-tight text-ink sm:text-4xl">{d.property.name}</h1>
                  <p className="mt-1 text-sm text-ink-soft">{plural(d.reviews, "review")} · times shown in {d.property.timezone}</p>
                </div>
                <Link href={`/reviews?property_id=${id}`} className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-on-accent hover:opacity-90">Read this property&apos;s reviews</Link>
              </div>
              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
                <Kpi accent label="Average rating" value={fmtNum(d.avg_rating.value)} unit={d.avg_rating.value !== null ? "/ 10" : undefined} sub={plural(d.reviews, "review")} status={d.avg_rating.status} />
                <Kpi label="This week vs last week" value={<Delta value={wc.avg_rating_delta} />} sub={`${plural(wc.current_week_to_date.reviews, "review")} now · ${wc.previous_week_same_elapsed.reviews} then`} status={wc.delta_status === "ok" ? undefined : wc.delta_status} />
                <Kpi label="Sentiment mix" value={<span className="text-2xl">{d.rating_bands.positive + d.rating_bands.neutral + d.rating_bands.negative}</span>} sub={<SentimentBar {...d.rating_bands} />} />
                <Kpi label="Booking.com's own score" value={d.booking_score ? d.booking_score.value : "—"} sub={d.booking_score ? "Calculated by Booking.com" : "Not captured yet"} />
              </div>
              <div className="mt-4 grid gap-4 lg:grid-cols-3">
                <Card title="Weekly volume and rating" className="lg:col-span-2"><QueryBoundary q={weekly} height="h-72">{(t) => <VolumeRatingChart weeks={t.weeks} />}</QueryBoundary></Card>
                <Card title="Rating distribution"><DistributionChart dist={d.rating_distribution} /></Card>
                <Card title="Positive and negative by week" className="lg:col-span-2"><QueryBoundary q={weekly} height="h-64">{(t) => <SentimentTrendChart weeks={t.weeks} />}</QueryBoundary></Card>
                <Card title="Topics: praise vs criticism"><TopicBars topics={d.topics} /></Card>
              </div>
              <Card title="Suggested focus areas" subtitle="Topics criticised repeatedly in the last 12 weeks" className="mt-4">
                <RecommendationList recs={d.recommendations} showProperty={false} />
              </Card>
            </>
          );
        }}
      </QueryBoundary>
    </>
  );
}
