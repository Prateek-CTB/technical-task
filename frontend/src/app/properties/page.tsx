"use client";

import { useQueries } from "@tanstack/react-query";
import Link from "next/link";
import { PropertyRatingChart } from "@/components/charts";
import { Card, Delta, PageHeader, QueryBoundary, SentimentBar, StatusNote, TopicChip } from "@/components/ui";
import { usePropertyComparison } from "@/hooks/queries";
import { useScope } from "@/hooks/scope";
import { fmtNum, plural } from "@/lib/format";
import { api } from "@/services/api";
import type { PropertySummary } from "@/types/api";

function PropertyCard({ p, delta }: { p: PropertySummary; delta: number | null | undefined }) {
  return (
    <Card as="article">
      <div className="flex items-start justify-between gap-3">
        <div>
          <Link href={`/properties/${p.property.id}`} className="font-display text-xl font-semibold text-ink hover:underline">{p.property.name}</Link>
          <p className="mt-0.5 text-sm text-ink-faint">{plural(p.reviews, "review")}</p>
        </div>
        <div className="text-right">
          <p className="font-display text-4xl font-semibold tnum text-ink">
            {p.avg_rating.value !== null ? fmtNum(p.avg_rating.value) : "—"}
            <span className="ml-1 text-base font-normal text-ink-faint">/ 10</span>
          </p>
          <StatusNote status={p.avg_rating.status === "ok" ? undefined : p.avg_rating.status} />
        </div>
      </div>
      <div className="mt-4"><SentimentBar {...p.rating_bands} /></div>
      <div className="mt-4 flex items-center gap-2 text-sm text-ink-soft">
        Week on week: {delta === undefined ? <span className="text-ink-faint">loading…</span> : <Delta value={delta} />}
      </div>
      <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
        <div>
          <p className="text-ink-faint">Guests praise</p>
          <div className="mt-1 flex flex-wrap gap-1">{p.most_positive_aspects.length ? p.most_positive_aspects.map((t) => <TopicChip key={t.topic} topic={t.topic} sentiment="positive" />) : <span className="text-ink-faint">—</span>}</div>
        </div>
        <div>
          <p className="text-ink-faint">Guests criticise</p>
          <div className="mt-1 flex flex-wrap gap-1">{p.most_negative_aspects.length ? p.most_negative_aspects.map((t) => <TopicChip key={t.topic} topic={t.topic} sentiment="negative" />) : <span className="text-ink-faint">—</span>}</div>
        </div>
      </div>
      <p className="mt-4 text-xs text-ink-faint">
        Last 28 days: {p.volume_trend.last_28_days} reviews (previous 28: {p.volume_trend.previous_28_days}) · {p.coverage.analysed} of {p.coverage.eligible} analysed
        {p.booking_score && <> · Booking.com score <strong>{p.booking_score.value}</strong></>}
      </p>
    </Card>
  );
}

export default function PropertiesPage() {
  const q = usePropertyComparison();
  const { demo } = useScope();
  const ids = q.data?.data.map((p) => p.property.id) ?? [];
  // Week-on-week change per property comes from the property endpoint (computed server-side).
  const details = useQueries({
    queries: ids.map((id) => ({ queryKey: ["property", { include_demo: demo }, String(id)], queryFn: () => api.property({ include_demo: demo }, id), staleTime: 30_000 })),
  });
  const deltaOf = (id: number) => {
    const i = ids.indexOf(id);
    const d = details[i];
    if (!d || d.isPending) return undefined;
    return d.data?.data.week_comparison.avg_rating_delta ?? null;
  };

  return (
    <>
      <PageHeader title="Property Performance" subtitle="Ratings, review volume and sentiment for each hotel, side by side." />
      <QueryBoundary q={q} what="properties" height="h-64">
        {(list) => (
          <>
            <div className="grid gap-4 md:grid-cols-2">{list.map((p) => <PropertyCard key={p.property.id} p={p} delta={deltaOf(p.property.id)} />)}</div>
            <div className="mt-4 grid gap-4 lg:grid-cols-5">
              <Card title="Average rating by property" subtitle="Mean of individual guest ratings, 1–10" className="lg:col-span-2">
                <PropertyRatingChart list={list} />
              </Card>
              <Card title="Side by side" className="lg:col-span-3">
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[34rem] text-sm">
                    <caption className="sr-only">Comparison of the four properties</caption>
                    <thead className="text-left text-ink-faint">
                      <tr>
                        <th scope="col" className="py-2 font-medium">Property</th>
                        <th scope="col" className="font-medium">Avg rating</th>
                        <th scope="col" className="font-medium">Reviews</th>
                        <th scope="col" className="font-medium">Negative</th>
                        <th scope="col" className="font-medium">Volume (28 d)</th>
                        <th scope="col" className="font-medium">Vs last week</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-line tnum">
                      {list.map((p) => {
                        const dl = deltaOf(p.property.id);
                        return (
                          <tr key={p.property.id}>
                            <th scope="row" className="py-2.5 text-left font-medium"><Link href={`/properties/${p.property.id}`} className="hover:underline">{p.property.name}</Link></th>
                            <td>{p.avg_rating.value !== null ? fmtNum(p.avg_rating.value) : <span className="text-warning-700">not enough data</span>}</td>
                            <td>{p.reviews}</td>
                            <td>{p.rating_bands.negative}</td>
                            <td>{p.volume_trend.last_28_days} <span className="text-ink-faint">({p.volume_trend.direction.replace("_", " ")})</span></td>
                            <td>{dl === undefined ? "…" : <Delta value={dl} />}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                <p className="mt-3 text-xs text-ink-faint">Our average is the mean of the guest ratings we hold. Booking.com&apos;s own score is calculated by Booking.com and shown separately when captured.</p>
              </Card>
            </div>
          </>
        )}
      </QueryBoundary>
    </>
  );
}
