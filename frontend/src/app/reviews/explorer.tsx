"use client";

import { ChevronLeft, ChevronRight, Search, X } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ReviewCard } from "@/components/review-card";
import { Card, EmptyState, ErrorBox, Loading, PageHeader } from "@/components/ui";
import { usePropertyComparison, useReviews } from "@/hooks/queries";
import { SENTIMENT_LABEL, TOPIC_LABEL, TOPICS } from "@/lib/format";
import type { ReviewFilters, Sentiment } from "@/types/api";

const FIELD = "mt-1 w-full rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm text-ink disabled:opacity-50";
const KEYS = ["property_id", "date_from", "date_to", "rating_min", "rating_max", "sentiment", "topic", "topic_sentiment", "q", "sort", "page"] as const;

const RATING_OPTIONS = [
  { value: "", label: "Any rating", min: "", max: "" },
  { value: "pos", label: "Positive (8–10)", min: "8", max: "" },
  { value: "neu", label: "Neutral (7)", min: "7", max: "7" },
  { value: "neg", label: "Negative (1–6)", min: "", max: "6" },
];
const SORTS = [
  { value: "newest", label: "Newest first" },
  { value: "oldest", label: "Oldest first" },
  { value: "rating_high", label: "Highest rating" },
  { value: "rating_low", label: "Lowest rating" },
];

export function ReviewExplorer() {
  const router = useRouter();
  const pathname = usePathname();
  const sp = useSearchParams();
  const filters: ReviewFilters = {};
  for (const k of KEYS) {
    const v = sp.get(k);
    if (v) filters[k] = v;
  }

  const update = (patch: Partial<Record<(typeof KEYS)[number], string>>) => {
    const next = new URLSearchParams(sp.toString());
    for (const [k, v] of Object.entries(patch)) {
      if (v) next.set(k, v);
      else next.delete(k);
    }
    if (!("page" in patch)) next.delete("page");
    if (!next.get("topic")) next.delete("topic_sentiment");
    const qs = next.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  };

  // Search is debounced so we do not query on every keystroke.
  const [draft, setDraft] = useState<string | null>(null);
  const search = draft ?? filters.q ?? "";
  useEffect(() => {
    if (draft === null || draft.trim() === (sp.get("q") ?? "")) return;
    const t = setTimeout(() => update({ q: draft.trim() }), 350);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft]);

  const props = usePropertyComparison();
  const q = useReviews(filters);
  const ratingValue = RATING_OPTIONS.find((o) => o.min === (filters.rating_min ?? "") && o.max === (filters.rating_max ?? ""))?.value ?? "";
  const active = KEYS.some((k) => k !== "page" && k !== "sort" && filters[k]);
  const pg = q.data?.meta.pagination;

  return (
    <>
      <PageHeader title="Review Explorer" subtitle="Read what guests actually wrote. Filter by property, date, rating, sentiment or topic." />
      <Card className="mb-4">
        <form role="search" aria-label="Review filters" onSubmit={(e) => e.preventDefault()} className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <label className="text-sm text-ink-soft sm:col-span-2">
            Search reviews
            <span className="relative mt-1 block">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-ink-faint" aria-hidden />
              <input type="search" value={search} onChange={(e) => setDraft(e.target.value)} placeholder="e.g. air conditioning, breakfast, noise" className={`${FIELD} !mt-0 pl-9`} />
            </span>
          </label>
          <label className="text-sm text-ink-soft">
            Property
            <select className={FIELD} value={filters.property_id ?? ""} onChange={(e) => update({ property_id: e.target.value })}>
              <option value="">All properties</option>
              {props.data?.data.map((p) => <option key={p.property.id} value={p.property.id}>{p.property.name}</option>)}
            </select>
          </label>
          <label className="text-sm text-ink-soft">
            Sort by
            <select className={FIELD} value={filters.sort ?? "newest"} onChange={(e) => update({ sort: e.target.value === "newest" ? "" : e.target.value })}>
              {SORTS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
            </select>
          </label>
          <label className="text-sm text-ink-soft">
            From
            <input type="date" className={FIELD} value={filters.date_from ?? ""} max={filters.date_to} onChange={(e) => update({ date_from: e.target.value })} />
          </label>
          <label className="text-sm text-ink-soft">
            To
            <input type="date" className={FIELD} value={filters.date_to ?? ""} min={filters.date_from} onChange={(e) => update({ date_to: e.target.value })} />
          </label>
          <label className="text-sm text-ink-soft">
            Rating
            <select className={FIELD} value={ratingValue} onChange={(e) => { const o = RATING_OPTIONS.find((x) => x.value === e.target.value); update({ rating_min: o?.min ?? "", rating_max: o?.max ?? "" }); }}>
              {RATING_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </label>
          <label className="text-sm text-ink-soft">
            Overall sentiment
            <select className={FIELD} value={filters.sentiment ?? ""} onChange={(e) => update({ sentiment: e.target.value })}>
              <option value="">Any</option>
              {(["positive", "mixed", "negative", "neutral"] as Sentiment[]).map((s) => <option key={s} value={s}>{SENTIMENT_LABEL[s]}</option>)}
            </select>
          </label>
          <label className="text-sm text-ink-soft">
            Topic
            <select className={FIELD} value={filters.topic ?? ""} onChange={(e) => update({ topic: e.target.value })}>
              <option value="">Any topic</option>
              {TOPICS.map((t) => <option key={t} value={t}>{TOPIC_LABEL[t]}</option>)}
            </select>
          </label>
          <label className="text-sm text-ink-soft">
            Topic is
            <select className={FIELD} value={filters.topic_sentiment ?? ""} disabled={!filters.topic} onChange={(e) => update({ topic_sentiment: e.target.value })}>
              <option value="">Praised or criticised</option>
              <option value="positive">Praised</option>
              <option value="negative">Criticised</option>
            </select>
          </label>
          <div className="flex items-end">
            <button type="button" disabled={!active} onClick={() => { setDraft(null); router.replace(pathname, { scroll: false }); }} className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium text-positive-700 hover:bg-positive-50 disabled:text-ink-faint disabled:hover:bg-transparent">
              <X className="size-4" aria-hidden /> Clear filters
            </button>
          </div>
        </form>
      </Card>

      {q.isPending ? (
        <Loading what="reviews" height="h-64" />
      ) : q.isError ? (
        <ErrorBox error={q.error} retry={() => void q.refetch()} />
      ) : (
        <div aria-busy={q.isFetching}>
          <p className="mb-3 text-sm text-ink-soft" aria-live="polite">
            {pg?.total.toLocaleString("en-AU")} review{pg?.total === 1 ? "" : "s"}
            {pg && pg.pages > 1 ? ` · page ${pg.page} of ${pg.pages}` : ""}
            {!q.data.data.some((r) => r.analysis) && q.data.data.length > 0 ? " · none of these are analysed yet, so topic and sentiment filters will not match them" : ""}
          </p>
          {q.data.data.length === 0 ? (
            <Card><EmptyState title="No reviews match these filters">Try widening the dates, or clear a filter. Sentiment and topic filters only match reviews that have been analysed.</EmptyState></Card>
          ) : (
            <div className={`space-y-3 ${q.isFetching ? "opacity-60" : ""}`}>{q.data.data.map((r) => <ReviewCard key={r.id} r={r} />)}</div>
          )}
          {pg && pg.pages > 1 && (
            <nav className="mt-5 flex items-center justify-center gap-3 text-sm" aria-label="Pagination">
              <button className="inline-flex items-center gap-1 rounded-lg border border-line-strong bg-surface px-3 py-2 disabled:opacity-40" disabled={pg.page <= 1} onClick={() => update({ page: String(pg.page - 1) })}>
                <ChevronLeft className="size-4" aria-hidden /> Previous
              </button>
              <span className="tnum text-ink-soft">Page {pg.page} of {pg.pages}</span>
              <button className="inline-flex items-center gap-1 rounded-lg border border-line-strong bg-surface px-3 py-2 disabled:opacity-40" disabled={pg.page >= pg.pages} onClick={() => update({ page: String(pg.page + 1) })}>
                Next <ChevronRight className="size-4" aria-hidden />
              </button>
            </nav>
          )}
        </div>
      )}
    </>
  );
}
