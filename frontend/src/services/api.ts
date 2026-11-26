import type {
  AnalysisConfig,
  AnalysisRunReport,
  CollectionStatus,
  CoverageReport,
  Envelope,
  ImportReport,
  IngestionRun,
  Overview,
  PropertyDetail,
  PropertyInfo,
  PropertySummary,
  Recommendation,
  Review,
  ReviewFilters,
  Topics,
  WeeklyTrends,
} from "@/types/api";
import { check, collectionStatusSchema, importReportSchema, overviewSchema, propertiesSchema, reviewsSchema } from "@/services/schemas";
import type { z } from "zod";

/**
 * All backend access lives in this file. The browser only calls same-origin `/api/v1/*`;
 * Next.js proxies that to the FastAPI backend (BACKEND_URL, server-side). No secrets involved.
 */
const BASE = "/api/v1";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type Params = Record<string, string | number | boolean | undefined | null>;

export async function get<T>(path: string, params: Params = {}, signal?: AbortSignal, schema?: z.ZodType): Promise<T> {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  }
  const qs = q.toString();
  let res: Response;
  try {
    res = await fetch(`${BASE}/${path}${qs ? `?${qs}` : ""}`, { signal, headers: { Accept: "application/json" } });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError("Cannot reach the review service. Check that the backend is running.", 0);
  }
  if (!res.ok) {
    let detail = res.statusText || `Request failed (${res.status})`;
    try {
      const body = (await res.json()) as { detail?: unknown; title?: string };
      if (typeof body.detail === "string") detail = body.detail;
      else if (body.title) detail = body.title;
    } catch {
      /* body was not JSON */
    }
    throw new ApiError(detail, res.status);
  }
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    throw new ApiError("The review service returned an unreadable response.", res.status);
  }
  assertEnvelope(body);
  if (schema) {
    try {
      return check<T>(schema, body, path.split("/").pop() ?? "data");
    } catch (e) {
      throw new ApiError((e as Error).message, 502);
    }
  }
  return body as T;
}

async function readProblem(res: Response): Promise<{ detail: string; body: unknown }> {
  let body: unknown = null;
  let detail = res.statusText || `Request failed (${res.status})`;
  try {
    body = await res.json();
    const b = body as { detail?: unknown; title?: string };
    if (typeof b.detail === "string") detail = b.detail;
    else if (b.title) detail = b.title;
  } catch {
    /* not JSON */
  }
  return { detail, body };
}

export interface ImportRequest {
  file: File;
  dryRun: boolean;
  property?: string;
  dateFormat?: string;
}

/**
 * Upload a CSV/JSON export. A rejected file (422) still carries a full report (row errors,
 * file error), which is returned rather than thrown so the preview can show it.
 */
export async function uploadImport(req: ImportRequest): Promise<ImportReport> {
  const form = new FormData();
  form.set("file", req.file);
  form.set("dry_run", String(req.dryRun));
  if (req.property) form.set("property", req.property);
  if (req.dateFormat) form.set("date_format", req.dateFormat);
  let res: Response;
  try {
    res = await fetch(`${BASE}/ingestion/imports`, { method: "POST", body: form, headers: { Accept: "application/json" } });
  } catch {
    throw new ApiError("Cannot reach the review service. Check that the backend is running.", 0);
  }
  if (res.ok) return check<ImportReport>(importReportSchema, await res.json(), "the import report");
  const { detail, body } = await readProblem(res);
  const report = (body as { report?: unknown } | null)?.report;
  if (res.status === 422 && report) return check<ImportReport>(importReportSchema, report, "the import report");
  throw new ApiError(detail, res.status);
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}/${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError("Cannot reach the review service. Check that the backend is running.", 0);
  }
  if (!res.ok) throw new ApiError((await readProblem(res)).detail, res.status);
  return ((await res.json()) as { data: T }).data;
}

/** Start an AI (LLM) analysis of up to `limit` unanalysed reviews; runs in the background. */
export const startAiAnalysis = (limit: number) =>
  postJson<{ started: boolean; limit: number; model: string | null }>("analysis/runs", { method: "ai", limit });

/** Start collecting the latest reviews (background); new reviews are analysed offline afterwards. */
export const startCollection = (opts: { mode: "headed" | "headless"; max_pages: number }) =>
  postJson<{ started: boolean; mode: string; max_pages: number }>("collection/runs", opts);

export async function runRuleBasedAnalysis(): Promise<AnalysisRunReport> {
  const res = await fetch(`${BASE}/analysis/runs`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ method: "rule_based" }),
  });
  if (!res.ok) throw new ApiError((await readProblem(res)).detail, res.status);
  return ((await res.json()) as { data: AnalysisRunReport }).data;
}

/**
 * Runtime guard on every response: the contract is `{ data, meta }` (or `{ data }` for run history).
 * A malformed payload becomes a readable error instead of a crash deep inside a component.
 */
export function assertEnvelope(body: unknown): asserts body is { data: unknown } {
  if (typeof body !== "object" || body === null || !("data" in body) || (body as { data: unknown }).data === undefined) {
    throw new ApiError("The review service returned an unexpected response format.", 502);
  }
}

export interface Scope {
  include_demo: boolean;
}
export interface DateRange {
  date_from?: string;
  date_to?: string;
}

const A = "analytics";

export const api = {
  coverage: (s: Scope) => get<Envelope<CoverageReport>>(`${A}/coverage`, { ...s }),
  overview: (s: Scope, p: DateRange = {}) => get<Envelope<Overview>>(`${A}/overview`, { ...s, ...p }, undefined, overviewSchema),
  propertyComparison: (s: Scope, p: DateRange = {}) =>
    get<Envelope<PropertySummary[]>>(`${A}/properties/comparison`, { ...s, ...p }),
  property: (s: Scope, id: string | number) => get<Envelope<PropertyDetail>>(`${A}/properties/${id}`, { ...s }),
  weekly: (s: Scope, p: { property_id?: string } = {}) =>
    get<Envelope<WeeklyTrends>>(`${A}/trends/weekly`, { ...s, ...p }),
  topics: (s: Scope, p: DateRange & { property_id?: string } = {}) =>
    get<Envelope<Topics>>(`${A}/topics`, { ...s, ...p }),
  recommendations: (s: Scope, p: { property_id?: string; min_reviews?: number; window_weeks?: number } = {}) =>
    get<Envelope<Recommendation[]>>(`${A}/recommendations`, { ...s, ...p }),
  reviews: (s: Scope, f: ReviewFilters, pageSize = 12) =>
    get<Envelope<Review[]>>(`${A}/reviews`, { ...s, ...f, page: f.page ?? 1, page_size: pageSize }, undefined, reviewsSchema),
  runs: (pageSize = 15) => get<{ data: IngestionRun[]; meta: { total: number } }>("ingestion/runs", { page_size: pageSize }),
  collectionStatus: () =>
    get<{ data: CollectionStatus; meta: { generated_at: string } }>("ingestion/collection-status", {}, undefined, collectionStatusSchema),
  properties: () => get<{ data: PropertyInfo[] }>("properties", {}, undefined, propertiesSchema),
  analysisConfig: () => get<{ data: AnalysisConfig }>("analysis/config"),
};
