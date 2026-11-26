import type { ReactNode } from "react";
import { Card, RatingBadge, SentimentBadge, TopicChip } from "@/components/ui";
import { fmtDate } from "@/lib/format";
import type { AnalysisMethod, Aspect, Review } from "@/types/api";

const METHOD_NOTE: Record<AnalysisMethod, string> = {
  ai: "Analysed by an AI model",
  rule_based: "Analysed by the offline rule-based analyzer (keyword rules, not AI)",
  mock: "Analysed by the offline test provider (not AI)",
};

/** Wraps the evidence spans the AI quoted (verified server-side against the text) in <mark>. */
export function Highlighted({ text, field, aspects }: { text: string; field: string; aspects: Aspect[] }) {
  const spans = aspects
    .flatMap((a) => a.evidence.filter((e) => e.field === field).map((e) => ({ ...e, sentiment: a.sentiment })))
    .sort((a, b) => a.start - b.start);
  const out: ReactNode[] = [];
  let pos = 0;
  for (const s of spans) {
    if (s.start < pos || s.end > text.length) continue;
    out.push(text.slice(pos, s.start));
    const tone = s.sentiment === "positive" ? "bg-positive-100" : s.sentiment === "negative" ? "bg-negative-100" : "bg-sand-100";
    out.push(<mark key={s.start} className={`${tone} rounded px-0.5 text-inherit`}>{text.slice(s.start, s.end)}</mark>);
    pos = s.end;
  }
  out.push(text.slice(pos));
  return <span className="whitespace-pre-line">{out}</span>;
}

export function ReviewCard({ r }: { r: Review }) {
  const a = r.analysis;
  const aspects = a?.aspects ?? [];
  return (
    <Card as="article" className="p-4 sm:p-5">
      <header className="flex flex-wrap items-center gap-3">
        <RatingBadge rating={r.rating} />
        <div className="min-w-0">
          <p className="font-medium text-ink">{r.property.name}</p>
          <p className="text-xs text-ink-faint">
            <time dateTime={r.local_date}>{fmtDate(r.published_at)}</time>
            {r.traveller_type ? ` · ${r.traveller_type}` : ""}
            {r.room_type ? ` · ${r.room_type}` : ""}
            {r.nights ? ` · ${r.nights} night${r.nights === 1 ? "" : "s"}` : ""}
            {r.language && r.language !== "und" ? ` · ${r.language}` : ""}
          </p>
        </div>
        {a && <div className="ml-auto"><SentimentBadge sentiment={a.overall_sentiment} /></div>}
      </header>
      {r.title && <h3 className="mt-3 font-semibold text-ink"><Highlighted text={r.title} field="title" aspects={aspects} /></h3>}
      {r.liked && <p className="mt-2 text-sm leading-relaxed"><span className="font-medium text-positive-700">Liked · </span><Highlighted text={r.liked} field="liked" aspects={aspects} /></p>}
      {r.disliked && <p className="mt-1.5 text-sm leading-relaxed"><span className="font-medium text-negative-700">Disliked · </span><Highlighted text={r.disliked} field="disliked" aspects={aspects} /></p>}
      {!r.title && !r.liked && !r.disliked && <p className="mt-3 text-sm text-ink-faint">The guest left a rating without written feedback.</p>}
      {a ? (
        <div className="mt-3 space-y-2 border-t border-line pt-3 text-sm">
          <div className="flex flex-wrap gap-1.5">{aspects.map((x) => <TopicChip key={x.topic} topic={x.topic} sentiment={x.sentiment} />)}</div>
          {a.summary_en && <p className="text-ink-soft">{a.summary_en}</p>}
          {a.suggested_action && <p className="text-ink-soft"><strong className="text-ink">Suggested action:</strong> {a.suggested_action}</p>}
          <p className="text-xs text-ink-faint">
            {METHOD_NOTE[a.method] ?? METHOD_NOTE.ai}; highlighted words are the evidence.
            {a.flags.filter((f) => f !== "rule_based").length ? ` Flags: ${a.flags.filter((f) => f !== "rule_based").join(", ")}.` : ""}
          </p>
        </div>
      ) : (
        <p className="mt-3 border-t border-line pt-3 text-xs text-ink-faint">Not analysed yet, so no topic tags are shown.</p>
      )}
      {r.management_reply && <p className="mt-3 rounded-lg bg-sand-100 p-2.5 text-sm"><strong>Hotel reply:</strong> {r.management_reply}</p>}
    </Card>
  );
}
