"use client";

import { CalendarDays, MessageSquare, Star, ThumbsUp } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { DistributionChart, SentimentDonut, SentimentTrendChart, VolumeRatingChart } from "@/components/charts";
import { ExecutiveSummary, HeadlineInsight, RecommendationList } from "@/components/insights";
import { Card, Delta, Kpi, PageHeader, QueryBoundary, SentimentBar, StatusNote, TopicChip } from "@/components/ui";
import { useOverview, usePropertyComparison, useRecommendations, useTopics, useWeekly } from "@/hooks/queries";
import { fmtNum, isoDaysAgo, mondayIso, pct, plural } from "@/lib/format";
import { cx } from "@/lib/cx";

const PERIODS = [
  { id: "4w", label: "4 weeks", days: 28 },
  { id: "12w", label: "12 weeks", days: 84 },
  { id: "all", label: "All time", days: -1 },
] as const;
type PeriodId = (typeof PERIODS)[number]["id"];

export default function OverviewPage() {
  const [period, setPeriod] = useState<PeriodId>("12w");
  const p = PERIODS.find((x) => x.id === period) ?? PERIODS[1];
  const range = useMemo(() => ({ date_from: p.days < 0 ? undefined : isoDaysAgo(p.days) }), [p.days]);
  const weekStart = useMemo(() => mondayIso(), []);

  const overview = useOverview(range);
  const weekly = useWeekly();
  const topics = useTopics(range);
  const weekTopics = useTopics({ date_from: weekStart });
  const recs = useRecommendations({ min_reviews: 3 });
  const comparison = usePropertyComparison();

  return (
    <>
      <PageHeader title="Executive Overview" subtitle="How guests rate the four Azzurro properties, and what to act on first.">
        <div role="group" aria-label="Time period" className="inline-flex rounded-xl border border-line bg-surface p-1">
          {PERIODS.map((x) => (
            <button
              key={x.id}
              aria-pressed={period === x.id}
              onClick={() => setPeriod(x.id)}
              className={cx("rounded-lg px-3.5 py-1.5 text-sm transition-colors", period === x.id ? "bg-accent font-semibold text-on-accent" : "text-ink-soft hover:bg-sand-100")}
            >
              {x.label}
            </button>
          ))}
        </div>
      </PageHeader>

      <ExecutiveSummary overview={overview.data?.data} comparison={comparison.data?.data} recs={recs.data?.data} />

      <QueryBoundary q={overview} what="overview" height="h-36">
        {(d) => {
          const wc = d.week_comparison;
          const cur = wc.current_week_to_date;
          const prev = wc.previous_week_same_elapsed;
          const b = d.rating_bands;
          const total = b.positive + b.neutral + b.negative;
          return (
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              <Kpi
                accent
                icon={<Star className="size-4" />}
                label="Average rating this week so far"
                value={cur.value !== null ? fmtNum(cur.value) : "—"}
                unit={cur.value !== null ? "/ 10" : undefined}
                sub={
                  cur.reviews === 0
                    ? `No reviews published yet this week. Last full week: ${wc.previous_week_full.value !== null ? `${fmtNum(wc.previous_week_full.value)} (${plural(wc.previous_week_full.reviews, "review")})` : "not enough reviews"}`
                    : plural(cur.reviews, "review") + " this week"
                }
              />
              <Kpi
                icon={<CalendarDays className="size-4" />}
                label="Versus last week, same days"
                value={<Delta value={wc.avg_rating_delta} />}
                sub={`Last week: ${prev.value !== null ? fmtNum(prev.value) : "not enough reviews"} (${plural(prev.reviews, "review")})`}
                status={wc.delta_status === "ok" ? undefined : wc.delta_status}
              />
              <Kpi
                icon={<MessageSquare className="size-4" />}
                label={`Reviews, last ${p.label.toLowerCase()}`}
                value={d.reviews.toLocaleString("en-AU")}
                sub={
                  <span>
                    Avg {fmtNum(d.avg_rating.value)} / 10
                    {d.avg_rating.ci95 ? ` (likely ${fmtNum(d.avg_rating.ci95[0])}–${fmtNum(d.avg_rating.ci95[1])})` : ""}
                    <br />
                    Last 28 days: {d.volume.last_28_days} · before: {d.volume.previous_28_days}
                  </span>
                }
                status={d.avg_rating.status}
              />
              <Kpi
                icon={<ThumbsUp className="size-4" />}
                label="Positive vs negative reviews"
                value={total ? pct(b.positive / total) : "—"}
                unit={total ? "positive" : undefined}
                sub={total ? `${pct(b.negative / total)} negative · ${pct(b.neutral / total)} neutral (positive = rated 8–10, negative = 1–6)` : undefined}
              />
            </div>
          );
        }}
      </QueryBoundary>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card title="Weekly volume and average rating" subtitle="All properties, by week" className="lg:col-span-2">
          <QueryBoundary q={weekly} what="trend" height="h-72">{(d) => <VolumeRatingChart weeks={d.weeks} />}</QueryBoundary>
        </Card>
        <Card title="Sentiment mix" subtitle={`By guest rating, last ${p.label.toLowerCase()}`}>
          <QueryBoundary q={overview} height="h-48">
            {(d) => (
              <>
                <SentimentDonut bands={d.rating_bands} />
                <SentimentBar {...d.rating_bands} />
              </>
            )}
          </QueryBoundary>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card title="Positive and negative reviews by week" className="lg:col-span-2">
          <QueryBoundary q={weekly} height="h-64">{(d) => <SentimentTrendChart weeks={d.weeks} />}</QueryBoundary>
        </Card>
        <Card title="Rating distribution" subtitle="Booking.com 1–10 scale">
          <QueryBoundary q={overview} height="h-48">{(d) => <DistributionChart dist={d.rating_distribution} />}</QueryBoundary>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card title="What guests talk about" className="lg:col-span-1">
          <div className="space-y-4">
            <QueryBoundary q={weekTopics} height="h-16">
              {(wt) => {
                // Prefer "this week" only when the week has enough analysed negative reviews to
                // report a share; otherwise fall back to the selected window, which has real
                // numbers (weekly negative samples are usually too small to report).
                const weekHasShare = wt.topics.some((t) => t.share_of_negative_rated_reviews.value !== null);
                return <HeadlineInsight topics={weekHasShare ? wt : topics.data?.data} period={weekHasShare ? "this week" : `in the last ${p.label.toLowerCase()}`} />;
              }}
            </QueryBoundary>
            <QueryBoundary q={topics} what="topics" height="h-32">
              {(d) => (
                <>
                  <div>
                    <p className="mb-1.5 text-sm font-medium text-ink">Most praised</p>
                    <ul className="space-y-1.5">{d.most_praised.slice(0, 3).map((t) => (
                      <li key={t.topic} className="flex items-center justify-between text-sm"><TopicChip topic={t.topic} sentiment="positive" /><span className="text-ink-soft tnum">{plural(t.reviews, "review")}</span></li>
                    ))}</ul>
                  </div>
                  <div>
                    <p className="mb-1.5 text-sm font-medium text-ink">Most criticised</p>
                    <ul className="space-y-1.5">{d.most_criticised.slice(0, 3).map((t) => (
                      <li key={t.topic} className="flex items-center justify-between text-sm"><TopicChip topic={t.topic} sentiment="negative" /><span className="text-ink-soft tnum">{plural(t.reviews, "review")}</span></li>
                    ))}</ul>
                  </div>
                  <StatusNote extra={`Based on ${d.analysed_reviews} of ${d.reviews_in_period} reviews analysed`} />
                </>
              )}
            </QueryBoundary>
          </div>
        </Card>

        <Card title="Properties at a glance" subtitle="Average rating and sentiment" className="lg:col-span-2" action={<Link href="/properties" className="text-sm font-medium text-accent-700 hover:underline">Compare →</Link>}>
          <QueryBoundary q={comparison} what="properties" height="h-48">
            {(list) => (
              <ul className="divide-y divide-line">
                {list.map((x) => (
                  <li key={x.property.id} className="grid items-center gap-3 py-3 sm:grid-cols-[1fr_5rem_1.2fr]">
                    <Link href={`/properties/${x.property.id}`} className="font-medium text-ink hover:underline">{x.property.name}</Link>
                    <p className="font-display text-2xl font-semibold tnum text-ink">{x.avg_rating.value !== null ? fmtNum(x.avg_rating.value) : "—"}</p>
                    <SentimentBar {...x.rating_bands} />
                  </li>
                ))}
              </ul>
            )}
          </QueryBoundary>
        </Card>
      </div>

      <Card title="Issues worth acting on" subtitle="Topics criticised repeatedly at one property in the last 12 weeks" className="mt-4"
        action={<Link href="/insights" className="text-sm font-medium text-accent-700 hover:underline">All insights →</Link>}>
        <QueryBoundary q={recs} what="recommendations" height="h-32">{(d) => <RecommendationList recs={d} limit={3} />}</QueryBoundary>
      </Card>
    </>
  );
}
