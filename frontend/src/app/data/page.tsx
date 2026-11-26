"use client";

import { CheckCircle2, CircleSlash, Clock, Database } from "lucide-react";
import { ImportPanel } from "@/components/import-panel";
import { UpdatePanel } from "@/components/update-panel";
import { Card, EmptyState, ErrorBox, Loading, PageHeader } from "@/components/ui";
import { useCollectionStatus, useCoverage, useRuns } from "@/hooks/queries";
import { cx } from "@/lib/cx";
import { fmtDate, fmtDateTime, pct } from "@/lib/format";
import type { BlockReason, RunOutcome, StrategyStatus } from "@/types/api";

const KIND: Record<string, string> = { collect: "Collection", import: "File import", analyse: "Topic analysis" };
const ANALYSER: Record<string, string> = { builtin: "rule-based (offline)", openrouter: "AI model", mock: "test provider" };
const runLabel = (r: { kind: string; source: string; trigger: string; strategy: string | null }) =>
  r.kind === "analyse"
    ? `${KIND.analyse} · ${ANALYSER[r.source] ?? r.source}`
    : r.kind === "import" && r.trigger === "sample"
      ? "Sample data load"
      : `${KIND[r.kind] ?? r.kind}${r.strategy ? ` · ${STRATEGY[r.strategy] ?? r.strategy}` : ""}`;
const STRATEGY: Record<string, string> = { browser_headed: "Visible browser", browser_headless: "Headless browser", http_structured: "Page check (HTTP)" };
const BLOCK_REASON: Record<BlockReason, string> = {
  aws_waf_challenge: "AWS WAF challenge page served (not solved)",
  captcha: "CAPTCHA shown (not solved)",
  headless_refused: 'Headless request refused ("Request not supported")',
  access_denied: "Access denied",
  cooldown: "In cooldown after a recent refusal",
  circuit_breaker: "Skipped after another property was refused",
};
const BADGE: Record<string, string> = {
  success: "bg-positive-50 text-positive-700 ring-positive-100",
  ok: "bg-positive-50 text-positive-700 ring-positive-100",
  partial: "bg-warning-100 text-warning-700 ring-warning-100",
  blocked: "bg-negative-50 text-negative-700 ring-negative-100",
  cooling_down: "bg-warning-100 text-warning-700 ring-warning-100",
  failed: "bg-negative-50 text-negative-700 ring-negative-100",
  running: "bg-sand-100 text-ink-soft ring-line",
  never_run: "bg-sand-100 text-ink-faint ring-line",
  abandoned: "bg-sand-100 text-ink-soft ring-line",
};
const STATE_LABEL: Record<StrategyStatus["state"], string> = {
  ok: "Working", blocked: "Blocked", cooling_down: "Cooling down", failed: "Failed", never_run: "Not run",
};

function Badge({ status, children }: { status: string; children?: React.ReactNode }) {
  return <span className={cx("inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium uppercase tracking-wide ring-1", BADGE[status] ?? BADGE.running)}>{children ?? status}</span>;
}

function Outcome({ o }: { o: RunOutcome | null }) {
  if (!o) return <span className="text-sm text-ink-faint">never attempted</span>;
  return (
    <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
      <Badge status={o.status} />
      <span className="text-ink-faint tnum">{fmtDateTime(o.started_at)}</span>
      {o.block_reason && <span className="text-negative-700">{BLOCK_REASON[o.block_reason]}</span>}
    </span>
  );
}

export default function DataPage() {
  const status = useCollectionStatus();
  const runs = useRuns();

  return (
    <>
      <PageHeader title="Data & Collection" subtitle="Where the numbers come from, how fresh they are, and exactly what happened on each collection and analysis run." />

      {/* Actions: get the latest reviews, analyse with AI or offline (target of the top bar's "Update data") */}
      <Card title="Update data" subtitle="Get the newest reviews and analyse them, without the command line" className="mb-4 scroll-mt-20">
        <div id="update" className="scroll-mt-24" />
        <UpdatePanel />
      </Card>

      {/* Per-property holdings vs Booking's published totals */}
      <Card title="Reviews held, by property" subtitle="How many reviews we hold against Booking.com's own published count and score" className="mb-4">
        {status.isPending ? <Loading what="collection status" /> : status.isError ? <ErrorBox error={status.error} retry={() => void status.refetch()} /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-faint">
                  <th className="py-2 pr-4 font-medium">Property</th>
                  <th className="py-2 pr-4 font-medium">Reviews held</th>
                  <th className="py-2 pr-4 font-medium">Date range collected</th>
                  <th className="py-2 pr-4 font-medium">Booking.com published</th>
                  <th className="py-2 pr-4 font-medium">Last collected</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {status.data.data.properties.map((p) => (
                  <tr key={p.property.id}>
                    <td className="py-2.5 pr-4 font-medium text-ink">{p.property.name}</td>
                    <td className="py-2.5 pr-4 tnum">{p.reviews_held.total.toLocaleString("en-AU")}</td>
                    <td className="py-2.5 pr-4 tnum text-ink-soft">{p.collected_range.oldest ? `${fmtDate(p.collected_range.oldest)} – ${fmtDate(p.collected_range.newest!)}` : "—"}</td>
                    <td className="py-2.5 pr-4 text-ink-soft">
                      {p.booking_published.score != null ? <span className="font-medium text-ink tnum">{p.booking_published.score.toFixed(1)}</span> : "—"}
                      {p.booking_published.review_count != null && <span className="text-ink-faint"> · {p.booking_published.review_count.toLocaleString("en-AU")} reviews</span>}
                    </td>
                    <td className="py-2.5 pr-4"><Outcome o={p.last_success ?? p.last_attempt} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-2 text-xs text-ink-faint">Booking.com&apos;s published score is calculated by Booking, not by us, and is stored separately from our own average of the individual reviews we hold.</p>
          </div>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        {/* Collection strategies & honest block reporting */}
        <Card title="Collection methods" subtitle="Each strategy, its last result, and why">
          {status.isPending ? <Loading what="methods" /> : status.isError ? <ErrorBox error={status.error} /> : (
            <ul className="space-y-3">
              {status.data.data.strategies.map((s) => (
                <li key={s.key} className="rounded-xl border border-line p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <StateIcon state={s.state} />
                    <span className="font-medium text-ink">{s.label}</span>
                    <Badge status={s.state}>{STATE_LABEL[s.state]}</Badge>
                  </div>
                  <p className="mt-1.5 text-xs text-ink-soft">{s.detail}</p>
                  {s.block_reason && <p className="mt-1.5 text-xs font-medium text-negative-700">{BLOCK_REASON[s.block_reason]}</p>}
                  {s.cooldown_until && <p className="mt-1 text-xs text-warning-700">No request will be made until {fmtDateTime(s.cooldown_until)} (cooldown).</p>}
                  {s.last_success_at && <p className="mt-1 text-xs text-ink-faint">Last success: {fmtDateTime(s.last_success_at)}</p>}
                </li>
              ))}
            </ul>
          )}
          <p className="mt-3 rounded-lg bg-canvas p-2.5 text-xs text-ink-soft">
            A CAPTCHA, WAF challenge or explicit refusal stops the run, is recorded as <strong>BLOCKED</strong> with its reason, and starts a cooldown. Nothing is solved, spoofed or worked around.
          </p>
        </Card>

        {/* Analysis coverage */}
        <Card title="Topic analysis coverage" subtitle="How many reviews have topic and sentiment analysis, and by which method">
          <QueryCov />
        </Card>
      </div>

      {/* Authorised import workflow */}
      <Card title="Import reviews from an authorised export" className="mt-4" subtitle="Booking.com Extranet / Guest Reviews export, or a channel-manager CSV / JSON">
        <ImportPanel />
      </Card>

      {/* Run history */}
      <Card title="Recent activity" subtitle="Collection, imports and analysis runs" className="mt-4">
        {runs.isPending ? <Loading what="runs" /> : runs.isError ? <ErrorBox error={runs.error} retry={() => void runs.refetch()} /> : runs.data.data.length === 0 ? (
          <EmptyState title="No runs yet">Collect or import reviews to see activity here.</EmptyState>
        ) : (
          <ul className="divide-y divide-line">
            {runs.data.data.map((r) => (
              <li key={r.id} className="py-3 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge status={r.status} />
                  <span className="font-medium text-ink">{runLabel(r)}</span>
                  <span className="text-ink-faint tnum">{fmtDateTime(r.started_at)} · {r.items_ok ?? 0}/{r.items_total ?? 0} ok</span>
                  {r.block_reason && <span className="text-xs text-negative-700">{BLOCK_REASON[r.block_reason]}</span>}
                </div>
                {r.properties.map((p) => (
                  <p key={p.property} className="ml-1 mt-1 text-ink-soft">
                    <span className="text-ink">{p.property}</span>: <strong>{p.status}</strong>{p.pages ? ` · ${p.pages} pages` : ""} · {p.new} new, {p.updated} updated, {p.unchanged} unchanged
                    {p.booking_reported_total ? ` · Booking lists ${p.booking_reported_total.toLocaleString("en-AU")}` : ""}
                    {p.error && <span className="text-negative-700"> — {p.error}</span>}
                  </p>
                ))}
                {r.error && r.properties.length === 0 && <p className="ml-1 mt-1 text-ink-soft">{r.error}</p>}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}

function StateIcon({ state }: { state: StrategyStatus["state"] }) {
  if (state === "ok") return <CheckCircle2 className="size-4 text-positive-700" aria-hidden />;
  if (state === "blocked" || state === "failed") return <CircleSlash className="size-4 text-negative-700" aria-hidden />;
  if (state === "cooling_down") return <Clock className="size-4 text-warning-700" aria-hidden />;
  return <Database className="size-4 text-ink-faint" aria-hidden />;
}

function QueryCov() {
  const cov = useCoverage();
  if (cov.isPending) return <Loading what="coverage" height="h-28" />;
  if (cov.isError) return <ErrorBox error={cov.error} retry={() => void cov.refetch()} />;
  const d = cov.data.data;
  const methods = d.analysis_methods ?? {};
  const bar = (n: number) => `${d.eligible ? (n / d.eligible) * 100 : 0}%`;
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <p className="font-display text-3xl font-semibold text-ink tnum">{pct(d.share_analysed)}</p>
        <p className="text-sm text-ink-soft tnum">{d.analysed.toLocaleString("en-AU")} of {d.eligible.toLocaleString("en-AU")} analysed</p>
      </div>
      <div className="mt-2 flex h-2.5 overflow-hidden rounded-full bg-sand-100" role="img" aria-label={`${d.analysed} of ${d.eligible} reviews analysed`}>
        <div className="bg-positive" style={{ width: bar(methods.ai ?? 0) }} title="AI" />
        <div className="bg-accent" style={{ width: bar(methods.rule_based ?? 0) }} title="Rule-based" />
      </div>
      <dl className="mt-3 space-y-1 text-sm">
        <Row label="By an AI model" value={methods.ai ?? 0} swatch="bg-positive" />
        <Row label="By the offline rule-based analyzer" value={methods.rule_based ?? 0} swatch="bg-accent" />
        {(methods.mock ?? 0) > 0 && <Row label="By the offline test provider" value={methods.mock ?? 0} swatch="bg-sand-100" />}
        {d.pending > 0 && <Row label="Not analysed yet" value={d.pending} swatch="bg-sand-100" />}
        {d.failed > 0 && <Row label="Analysis failed" value={d.failed} swatch="bg-negative" />}
      </dl>
      {d.active_configuration.model && <p className="mt-2 text-xs text-ink-faint">AI model when used: {d.active_configuration.model}</p>}
    </div>
  );
}

function Row({ label, value, swatch }: { label: string; value: number; swatch: string }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="flex items-center gap-2 text-ink-soft"><span className={cx("size-2.5 rounded-sm", swatch)} aria-hidden />{label}</dt>
      <dd className="font-medium text-ink tnum">{value.toLocaleString("en-AU")}</dd>
    </div>
  );
}
