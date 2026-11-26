"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Bot, Download, Loader2, Sparkles } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useAnalysisConfig, useCoverage, useRuns } from "@/hooks/queries";
import { cx } from "@/lib/cx";
import { fmtDateTime } from "@/lib/format";
import { runRuleBasedAnalysis, startAiAnalysis, startCollection } from "@/services/api";
import type { IngestionRun } from "@/types/api";

const SELECT = "rounded-lg border border-line-strong bg-surface px-2.5 py-1.5 text-sm text-ink";
const BUTTON =
  "inline-flex items-center justify-center gap-2 rounded-lg bg-accent px-3.5 py-2 text-sm font-semibold text-on-accent hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50";
const SECONDARY =
  "inline-flex items-center justify-center gap-2 rounded-lg border border-line-strong px-3.5 py-2 text-sm font-medium text-ink hover:bg-sand-100 disabled:cursor-not-allowed disabled:opacity-50";

function Action({ icon, title, children, footer }: { icon: ReactNode; title: string; children: ReactNode; footer: ReactNode }) {
  return (
    <div className="flex flex-col rounded-xl border border-line bg-canvas/40 p-4">
      <div className="flex items-center gap-2.5">
        <span className="grid size-8 place-items-center rounded-lg bg-accent-50 text-accent-700" aria-hidden>{icon}</span>
        <h3 className="font-semibold text-ink">{title}</h3>
      </div>
      <div className="mt-2 flex-1 space-y-2 text-sm text-ink-soft">{children}</div>
      <div className="mt-4 space-y-2">{footer}</div>
    </div>
  );
}

function latest(runs: IngestionRun[] | undefined, kind: string, source?: string) {
  return runs?.find((r) => r.kind === kind && (!source || r.source === source));
}

/**
 * "Update data": get the latest reviews, analyse with AI, analyse offline. Long jobs run on the
 * server in the background; this panel follows them through the run history and refreshes every
 * screen when they finish.
 */
export function UpdatePanel() {
  const qc = useQueryClient();
  const runs = useRuns();
  const cfg = useAnalysisConfig();
  const cov = useCoverage();
  const [pages, setPages] = useState(5);
  const [mode, setMode] = useState<"headed" | "headless">("headed");
  const [aiLimit, setAiLimit] = useState(20);

  const running = runs.data?.data.filter((r) => r.status === "running") ?? [];
  const busyCollect = running.some((r) => r.kind === "collect");
  const busyAnalyse = running.some((r) => r.kind === "analyse");

  // When a background job finishes, refresh every screen's data.
  const wasBusy = useRef(false);
  useEffect(() => {
    const busy = running.length > 0;
    if (wasBusy.current && !busy) void qc.invalidateQueries();
    wasBusy.current = busy;
  }, [running.length, qc]);

  const afterStart = () => void qc.invalidateQueries({ queryKey: ["runs"] });
  const collect = useMutation({ mutationFn: () => startCollection({ mode, max_pages: pages }), onSuccess: afterStart });
  const ai = useMutation({ mutationFn: () => startAiAnalysis(aiLimit), onSuccess: afterStart });
  const offline = useMutation({ mutationFn: runRuleBasedAnalysis, onSuccess: () => void qc.invalidateQueries() });

  const aiCfg = cfg.data?.data.ai;
  const methods = cov.data?.data.analysis_methods ?? {};
  const eligible = cov.data?.data.eligible ?? 0;
  const withoutAi = Math.max(0, eligible - (methods.ai ?? 0));
  const lastCollect = latest(runs.data?.data, "collect");
  const lastAi = latest(runs.data?.data, "analyse", "openrouter");

  return (
    <div className="space-y-4">
      {running.length > 0 && (
        <p role="status" className="flex items-center gap-2 rounded-lg border border-accent/40 bg-accent-50 px-3 py-2 text-sm text-ink">
          <Loader2 className="size-4 animate-spin text-accent-700" aria-hidden />
          {running.map((r) => (r.kind === "collect" ? "Collecting the latest reviews" : "Analysing reviews")).join(" · ")}…
          <span className="text-ink-faint">this page updates by itself when it finishes.</span>
        </p>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <Action
          icon={<Download className="size-4" />}
          title="Get latest reviews"
          footer={
            <>
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <label className="text-ink-soft">
                  Pages per hotel{" "}
                  <select className={SELECT} value={pages} onChange={(e) => setPages(Number(e.target.value))} aria-label="Pages per hotel">
                    {[1, 3, 5, 10, 30].map((n) => <option key={n} value={n}>{n} ({n * 10} reviews)</option>)}
                  </select>
                </label>
                <select className={SELECT} value={mode} onChange={(e) => setMode(e.target.value as "headed" | "headless")} aria-label="Browser mode">
                  <option value="headed">Visible browser</option>
                  <option value="headless">Headless (servers)</option>
                </select>
              </div>
              <button type="button" className={cx(BUTTON, "w-full")} disabled={busyCollect || collect.isPending} onClick={() => collect.mutate()}>
                {busyCollect ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Download className="size-4" aria-hidden />}
                {busyCollect ? "Collecting…" : "Get latest reviews"}
              </button>
              {collect.isError && <p role="alert" className="text-xs text-negative-700">{(collect.error as Error).message}</p>}
              {collect.isSuccess && !busyCollect && <p className="text-xs text-positive-700">Started. Results appear in Recent activity below.</p>}
            </>
          }
        >
          <p>Reads the newest reviews from the four Booking.com pages, adds only the ones we don&apos;t have yet, then analyses them.</p>
          <p className="text-xs text-ink-faint">
            <strong className="text-ink-soft">Visible browser</strong> opens a window on the computer running the server. Booking.com refuses headless browsers; if it refuses, the run is recorded as <strong className="text-ink-soft">BLOCKED</strong> and not retried.
          </p>
          <p className="text-xs text-ink-faint">Last run: {lastCollect ? `${lastCollect.status} · ${fmtDateTime(lastCollect.started_at)}` : "never"}</p>
        </Action>

        <Action
          icon={<Bot className="size-4" />}
          title="Analyse with AI"
          footer={
            <>
              <label className="flex items-center gap-2 text-sm text-ink-soft">
                Reviews to analyse
                <select className={SELECT} value={aiLimit} onChange={(e) => setAiLimit(Number(e.target.value))} aria-label="Reviews to analyse with AI">
                  {[5, 10, 20, 30, 50].filter((n) => n <= (aiCfg?.max_per_run ?? 50)).map((n) => <option key={n} value={n}>{n}</option>)}
                </select>
              </label>
              <button type="button" className={cx(BUTTON, "w-full")} disabled={!aiCfg?.can_run || busyAnalyse || ai.isPending} onClick={() => ai.mutate()}>
                {busyAnalyse ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Bot className="size-4" aria-hidden />}
                {busyAnalyse ? "Analysing…" : "Analyse with AI"}
              </button>
              {aiCfg && !aiCfg.can_run && <p className="text-xs text-warning-700">{aiCfg.reason}</p>}
              {ai.isError && <p role="alert" className="text-xs text-negative-700">{(ai.error as Error).message}</p>}
              {ai.isSuccess && !busyAnalyse && <p className="text-xs text-positive-700">Started. Results appear in Recent activity below.</p>}
            </>
          }
        >
          <p>An AI model reads reviews that so far have only the rule-based analysis, and its result replaces it. Every topic still needs a quote from the review.</p>
          <p className="text-xs text-ink-faint">
            {withoutAi.toLocaleString("en-AU")} reviews without AI analysis{aiCfg?.model ? ` · model ${aiCfg.model}` : ""}. The free tier allows about 50 a day; a run stops safely at the limit and the rest wait.
          </p>
          <p className="text-xs text-ink-faint">Last AI run: {lastAi ? `${lastAi.status} · ${fmtDateTime(lastAi.started_at)}` : "never"}</p>
        </Action>

        <Action
          icon={<Sparkles className="size-4" />}
          title="Analyse offline"
          footer={
            <>
              <button type="button" className={cx(SECONDARY, "w-full")} disabled={offline.isPending || busyAnalyse} onClick={() => offline.mutate()}>
                {offline.isPending ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Sparkles className="size-4" aria-hidden />}
                {offline.isPending ? "Analysing…" : "Analyse new reviews (offline)"}
              </button>
              {offline.isSuccess && <p className="text-xs text-positive-700">Analysed {offline.data.succeeded.toLocaleString("en-AU")} review{offline.data.succeeded === 1 ? "" : "s"} ({offline.data.cached.toLocaleString("en-AU")} already done).</p>}
              {offline.isError && <p role="alert" className="text-xs text-negative-700">{(offline.error as Error).message}</p>}
            </>
          }
        >
          <p>The built-in rule-based analyzer: no internet or key needed, finishes in seconds. Use it after importing a file.</p>
          <p className="text-xs text-ink-faint">Never replaces an AI analysis.</p>
        </Action>
      </div>
    </div>
  );
}
