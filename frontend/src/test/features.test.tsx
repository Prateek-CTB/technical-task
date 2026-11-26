import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import DataPage from "@/app/data/page";
import { ImportPanel } from "@/components/import-panel";
import { ReviewCard } from "@/components/review-card";
import { ApiError, get, uploadImport } from "@/services/api";
import { overviewSchema } from "@/services/schemas";
import type { Review } from "@/types/api";

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const META = {
  as_of: "2026-11-26T00:00:00Z",
  reporting_timezone: "Australia/Sydney",
  dataset: { sources: { booking_public: 3 }, contains_demo: false },
  analysis: { providers: { builtin: 3 }, methods: { ai: 0, rule_based: 3, mock: 0 }, contains_mock_analysis: false },
};
const STAT = { value: 6.5, n: 4, status: "ok" };
const REPORT = {
  status: "partial", dry_run: true, run_id: null, file_name: "x.csv", file_sha256: "a", file_format: "csv", source: "csv_import",
  rows_total: 3, rows_valid: 2, rows_rejected: 1, new: 2, updated: 0, unchanged: 0, duplicates_in_file: 0,
  issue_counts: { DQ003: 1 }, issues: [{ line: 4, code: "DQ003", severity: "error", message: "rating must be 1-10", column: "rating", property: null }],
  issues_truncated: false, ignored_columns: [], per_property: {}, file_error: null,
};

describe("API boundary validation (Zod)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("accepts a well-formed overview", async () => {
    const body = { data: { reviews: 4, avg_rating: STAT, rating_bands: { positive: 1, neutral: 1, negative: 2 }, week_comparison: { current_week_to_date: STAT, previous_week_same_elapsed: STAT, avg_rating_delta: 0.2 } }, meta: META };
    expect(overviewSchema.safeParse(body).success).toBe(true);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json(body));
    await expect(get("analytics/overview", {}, undefined, overviewSchema)).resolves.toMatchObject({ data: { reviews: 4 } });
  });

  it("turns a contract mismatch into a readable error naming the field", async () => {
    const broken = { data: { reviews: "four" }, meta: META };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json(broken));
    const err = await get("analytics/overview", {}, undefined, overviewSchema).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).message).toMatch(/unexpected format \(data\.reviews/);
  });
});

describe("import upload", () => {
  afterEach(() => vi.restoreAllMocks());

  it("sends the file and options as multipart form data", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(json(REPORT));
    const file = new File(["a,b"], "x.csv", { type: "text/csv" });
    const rep = await uploadImport({ file, dryRun: true, property: "olympic-paddington" });
    expect(rep.rows_rejected).toBe(1);
    const [url, init] = spy.mock.calls[0]!;
    expect(url).toBe("/api/v1/ingestion/imports");
    const form = (init as RequestInit).body as FormData;
    expect(form.get("dry_run")).toBe("true");
    expect(form.get("property")).toBe("olympic-paddington");
    expect((form.get("file") as File).name).toBe("x.csv");
  });

  it("returns the full report for a rejected file instead of throwing", async () => {
    const rejected = { ...REPORT, status: "failed", file_error: { code: "F001", message: "No review rows found" } };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ title: "File rejected", report: rejected }, 422));
    const rep = await uploadImport({ file: new File([""], "empty.csv"), dryRun: true });
    expect(rep.file_error?.message).toBe("No review rows found");
  });

  it("raises other failures (e.g. the same file already importing)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ title: "This file is already being imported" }, 409));
    await expect(uploadImport({ file: new File(["x"], "x.csv"), dryRun: false })).rejects.toMatchObject({ status: 409 });
  });
});

describe("ImportPanel", () => {
  afterEach(() => vi.restoreAllMocks());

  it("previews first (nothing saved), then imports on request", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/properties")) return json({ data: [{ id: 1, slug: "olympic-paddington", name: "Olympic Hotel Paddington" }] });
      const dry = ((init as RequestInit).body as FormData).get("dry_run") === "true";
      return json({ ...REPORT, dry_run: dry, status: dry ? "partial" : "partial", run_id: dry ? null : 7 });
    });
    const { container } = wrap(<ImportPanel />);
    const input = container.querySelector("input[type=file]") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["a"], "x.csv", { type: "text/csv" })] } });

    fireEvent.click(screen.getByRole("button", { name: /validate/i }));
    expect(await screen.findByText(/Preview of x.csv \(nothing saved yet\)/)).toBeInTheDocument();
    expect(screen.getByText("Row 4")).toBeInTheDocument();
    expect(screen.getByText(/rating must be 1-10/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^import$/i }));
    expect(await screen.findByText("Imported x.csv")).toBeInTheDocument();
    const posts = fetchMock.mock.calls.filter(([u]) => String(u).endsWith("/ingestion/imports"));
    expect(posts.map(([, i]) => ((i as RequestInit).body as FormData).get("dry_run"))).toEqual(["true", "false"]);
  });
});

describe("ReviewCard analysis labels", () => {
  const base: Review = {
    id: 1, property: { id: 1, slug: "p", name: "Potts Point" }, rating: 4, rating_band: "negative",
    published_at: "2026-11-20T00:00:00Z", local_date: "2026-11-20", language: "en", traveller_type: "Couple",
    room_type: "Double Room", nights: 2, source: "booking_public", title: null, liked: "Location", disliked: "Dirty bathroom",
    management_reply: null,
    analysis: {
      overall_sentiment: "mixed", summary_en: "Rule-based: guest praises location; criticises cleanliness.", provider: "builtin",
      method: "rule_based", main_complaint_topic: "cleanliness", status: "ok", flags: ["rule_based"], main_complaint: "Dirty bathroom",
      suggested_action: null, aspects: [{ topic: "cleanliness", sentiment: "negative", evidence: [{ field: "disliked", start: 0, end: 14 }] }],
    },
  };

  it("never calls the rule-based analyzer AI, and shows stay details", () => {
    render(<ReviewCard r={base} />);
    expect(screen.getByText(/offline rule-based analyzer \(keyword rules, not AI\)/)).toBeInTheDocument();
    expect(screen.getByText(/Double Room · 2 nights/)).toBeInTheDocument();
    expect(screen.queryByText(/Flags:/)).toBeNull(); // the internal "rule_based" flag is not shown
  });

  it("labels an LLM analysis as AI", () => {
    render(<ReviewCard r={{ ...base, analysis: { ...base.analysis!, provider: "openrouter", method: "ai", flags: [] } }} />);
    expect(screen.getByText(/Analysed by an AI model/)).toBeInTheDocument();
  });
});

describe("Data & Collection page", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows BLOCKED collection results with their reason, and per-property holdings", async () => {
    const outcome = { run_id: 11, strategy: "http_structured", status: "blocked", started_at: "2026-11-26T01:30:00Z", finished_at: null, pages: 0, received: 0, new: 0, unchanged: 0, rejected: 0, error: "AWS WAF challenge page", block_reason: "aws_waf_challenge", skipped_for_cooldown: false };
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("collection-status"))
        return json({
          data: {
            properties: [{ property: { id: 1, slug: "p", name: "Potts Point", url: null }, reviews_held: { total: 300, by_source: { booking_public: 300 } }, collected_range: { oldest: "2025-06-02", newest: "2026-11-25" }, booking_published: { score: 6.3, review_count: 2384, captured_at: null, note: "" }, share_of_published_collected: 0.126, last_attempt: outcome, last_success: null }],
            strategies: [{ key: "http_structured", label: "Public page check", detail: "One request", state: "cooling_down", last_attempt: outcome, last_real_attempt: outcome, last_success_at: null, cooldown_until: "2026-11-27T01:30:00Z", next_eligible_at: "2026-11-27T01:30:00Z", block_reason: "aws_waf_challenge" }],
            authorised_sources: { file_import: { label: "", runs: 0, last_at: null }, inbox: { label: "", path: "", waiting_files: [] }, feed: { label: "", configured: false } },
            cooldown_hours: 24, policy: "",
          },
          meta: { generated_at: "2026-11-26T02:00:00Z" },
        });
      if (url.includes("ingestion/runs")) return json({ data: [{ id: 11, kind: "collect", source: "booking_public", mode: "page-check", trigger: "cli", strategy: "http_structured", status: "blocked", block_reason: "aws_waf_challenge", started_at: "2026-11-26T01:30:00Z", finished_at: null, items_total: 4, items_ok: 0, items_failed: 4, error: null, properties: [] }], meta: { total: 1 } });
      return json({ data: [], meta: META });
    });
    wrap(<DataPage />);
    expect(await screen.findByRole("cell", { name: "Potts Point" })).toBeInTheDocument();
    expect(screen.getByText("2,384 reviews", { exact: false })).toBeInTheDocument();
    expect(await screen.findByText("Cooling down")).toBeInTheDocument();
    expect(screen.getAllByText("AWS WAF challenge page served (not solved)").length).toBeGreaterThan(0);
    expect(screen.getAllByText("blocked").length).toBeGreaterThan(0);
    await waitFor(() => expect(screen.getByText(/Collection · Page check \(HTTP\)/)).toBeInTheDocument());
  });
});
