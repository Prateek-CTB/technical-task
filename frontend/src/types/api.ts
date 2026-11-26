/** Response contracts of the FastAPI analytics API (read-only; all numbers are computed server-side). */

export type SampleStatus = "ok" | "low_sample" | "insufficient_data" | "indicative";
export type Sentiment = "positive" | "negative" | "neutral" | "mixed";
export type AnalysisMethod = "ai" | "rule_based" | "mock";
export type TopicKey =
  | "cleanliness"
  | "check_in"
  | "staff"
  | "noise"
  | "facilities"
  | "location"
  | "room_condition"
  | "value";

export interface Envelope<T, M = Meta> {
  data: T;
  meta: M;
}

export interface Meta {
  generated_at: string;
  as_of: string;
  scope: { include_demo: boolean; reviews_in_scope: number; excluded_by_scope: number };
  dataset: {
    sources: Record<string, number>;
    contains_demo: boolean;
    contains_experimental: boolean;
    contains_non_production_data: boolean;
  };
  analysis: {
    providers: Record<string, number>;
    /** How the current analyses were produced: an LLM ("ai"), the offline rule-based analyzer, or the test mock. */
    methods: Record<AnalysisMethod, number>;
    contains_mock_analysis: boolean;
    contains_rule_based_analysis: boolean;
  };
  notice: string | null;
  reporting_timezone: string;
  pagination?: { page: number; page_size: number; total: number; pages: number };
}

export interface Stat {
  value: number | null;
  n: number;
  status: SampleStatus;
  ci95: [number, number] | null;
}
export interface WeekStat extends Stat {
  reviews: number;
}
export interface Bands {
  positive: number;
  neutral: number;
  negative: number;
}
export interface Share {
  value: number | null;
  count: number;
  of: number;
  status: SampleStatus;
}
export interface PropertyRef {
  id: number;
  slug: string;
  name: string;
}
export interface TopicCount {
  topic: TopicKey;
  label: string;
  reviews: number;
}
export interface Coverage {
  eligible: number;
  analysed: number;
  pending: number;
  no_text: number;
  share: number;
}
export interface AiSentiment {
  counts: Record<Sentiment, number>;
  analysed: number;
  of: number;
}

export interface WeekComparison {
  basis: string;
  timezones: string[];
  current_week_to_date: WeekStat;
  previous_week_same_elapsed: WeekStat;
  previous_week_full: WeekStat;
  avg_rating_delta: number | null;
  delta_status: SampleStatus;
  note: string;
}

export interface Overview {
  period: { from: string | null; to: string | null };
  reviews: number;
  avg_rating: Stat;
  avg_rating_note: string;
  rating_distribution: Record<string, number>;
  rating_bands: Bands;
  ai_sentiment: AiSentiment;
  week_comparison: WeekComparison;
  volume: { last_28_days: number; previous_28_days: number };
  coverage: Coverage;
  top_positive_topics: TopicCount[];
  top_negative_topics: TopicCount[];
}

export interface PropertySummary {
  property: PropertyRef;
  reviews: number;
  avg_rating: Stat;
  rating_bands: Bands;
  ai_sentiment: AiSentiment;
  volume_trend: { last_28_days: number; previous_28_days: number; direction: string };
  most_positive_aspects: TopicCount[];
  most_negative_aspects: TopicCount[];
  coverage: Coverage;
  booking_score: { value: number } | null;
}

export interface TopicSentimentRow {
  topic: TopicKey;
  label: string;
  positive: number;
  negative: number;
  neutral: number;
  mixed: number;
}

export interface PropertyDetail extends Omit<PropertySummary, "property"> {
  property: PropertyRef & { timezone: string };
  rating_distribution: Record<string, number>;
  week_comparison: WeekComparison;
  topics: TopicSentimentRow[];
  recommendations: Recommendation[];
}

export interface WeekPoint {
  week_start: string;
  reviews: number;
  avg_rating: Stat;
  rating_bands: Bands;
  ai_sentiment: Record<Sentiment, number>;
  partial: boolean;
}
export interface WeeklyTrends {
  timezone: string;
  from: string;
  to: string;
  weeks: WeekPoint[];
  note: string;
}

export interface Evidence {
  review_id: number;
  property: string;
  published: string;
  rating: number;
  sentiment: Sentiment;
  quote: string;
  field: string;
}
export interface TopicInsight extends TopicSentimentRow {
  reviews_mentioning: number;
  share_of_analysed_reviews: Share;
  share_of_negative_rated_reviews: Share;
  most_common_complaints: { complaint: string; count: number }[];
  praise_evidence: Evidence[];
  complaint_evidence: Evidence[];
  by_property: Record<string, Record<Sentiment, number>>;
}
export interface Topics {
  analysed_reviews: number;
  reviews_in_period: number;
  coverage: Coverage;
  most_praised: TopicCount[];
  most_criticised: TopicCount[];
  topics: TopicInsight[];
  note: string;
}

export interface Recommendation {
  property: PropertyRef;
  topic: TopicKey;
  label: string;
  problem: string;
  supporting_reviews: number;
  analysed_reviews: number;
  share: Share;
  trend: { direction: string; last_4_weeks: number; previous_4_weeks: number; note: string };
  evidence: Evidence[];
  review_ids: number[];
  suggested_action: string | null;
  action_basis: string;
  confidence: { model_reported_mean: number; calibrated: boolean };
  uncertainty: string[];
}

export interface Aspect {
  topic: TopicKey;
  sentiment: Sentiment;
  evidence: { field: string; start: number; end: number; quote?: string }[];
}
export interface ReviewAnalysis {
  overall_sentiment: Sentiment;
  summary_en: string | null;
  provider: string;
  method: AnalysisMethod;
  main_complaint_topic: TopicKey | null;
  status: string;
  flags: string[];
  main_complaint: string | null;
  suggested_action: string | null;
  aspects: Aspect[];
}
export interface Review {
  id: number;
  property: PropertyRef;
  rating: number;
  rating_band: string;
  published_at: string;
  local_date: string;
  language: string | null;
  traveller_type: string | null;
  room_type: string | null;
  nights: number | null;
  source: string;
  title: string | null;
  liked: string | null;
  disliked: string | null;
  management_reply: string | null;
  analysis: ReviewAnalysis | null;
}

export interface CoverageReport extends Coverage {
  total_reviews: number;
  share_analysed: number | null;
  failed: number;
  active_configuration: { provider: string; model: string | null; prompt_version: string; taxonomy_version: string };
  analysis_providers: Record<string, number>;
  analysis_methods: Partial<Record<AnalysisMethod, number>>;
  by_property: Record<string, Coverage>;
  note: string;
}

export interface RunProperty {
  property: string;
  status: string;
  pages?: number;
  received?: number;
  rejected?: number;
  block_reason?: BlockReason | null;
  new: number;
  updated: number;
  unchanged: number;
  booking_reported_total?: number | null;
  error?: string | null;
}
export interface IngestionRun {
  id: number;
  kind: string;
  source: string;
  mode: string;
  trigger: string;
  strategy: StrategyKey | null;
  status: string;
  block_reason: BlockReason | null;
  started_at: string;
  finished_at: string | null;
  items_total: number | null;
  items_ok: number | null;
  items_failed: number | null;
  error: string | null;
  properties: RunProperty[];
}

export interface ReviewFilters {
  property_id?: string;
  date_from?: string;
  date_to?: string;
  rating_min?: string;
  rating_max?: string;
  sentiment?: string;
  topic?: string;
  topic_sentiment?: string;
  q?: string;
  sort?: string;
  page?: string;
}

/* ------------------------------------------------------------------ collection & import */
export type StrategyKey = "browser_headed" | "browser_headless" | "http_structured";
export type BlockReason =
  | "aws_waf_challenge"
  | "captcha"
  | "headless_refused"
  | "access_denied"
  | "cooldown"
  | "circuit_breaker";

export interface RunOutcome {
  run_id: number;
  strategy: StrategyKey;
  status: string;
  started_at: string;
  finished_at: string | null;
  pages: number | null;
  received: number | null;
  new: number | null;
  unchanged: number | null;
  rejected: number | null;
  error: string | null;
  block_reason: BlockReason | null;
  skipped_for_cooldown: boolean;
}

export interface StrategyStatus {
  key: StrategyKey;
  label: string;
  detail: string;
  state: "never_run" | "cooling_down" | "blocked" | "ok" | "failed";
  last_attempt: RunOutcome | null;
  last_real_attempt: RunOutcome | null;
  last_success_at: string | null;
  cooldown_until: string | null;
  next_eligible_at: string;
  block_reason: BlockReason | null;
}

export interface PropertyCollection {
  property: { id: number; slug: string; name: string; url: string | null };
  reviews_held: { total: number; by_source: Record<string, number> };
  collected_range: { oldest: string | null; newest: string | null };
  booking_published: { score: number | null; review_count: number | null; captured_at: string | null; note: string };
  share_of_published_collected: number | null;
  last_attempt: RunOutcome | null;
  last_success: RunOutcome | null;
}

export interface CollectionStatus {
  properties: PropertyCollection[];
  strategies: StrategyStatus[];
  authorised_sources: {
    file_import: { label: string; runs: number; last_at: string | null };
    inbox: { label: string; path: string; waiting_files: string[] };
    feed: { label: string; configured: boolean };
  };
  cooldown_hours: number;
  policy: string;
}

export interface PropertyInfo {
  id: number;
  slug: string;
  name: string;
  booking_url: string | null;
  timezone: string;
  active: boolean;
}

export interface ImportIssue {
  line: number;
  code: string;
  severity: string;
  message: string;
  column: string | null;
  property: string | null;
}

export interface ImportReport {
  status: "success" | "partial" | "failed";
  dry_run: boolean;
  run_id: number | null;
  file_name: string;
  file_sha256: string;
  file_format: string | null;
  source: string | null;
  rows_total: number;
  rows_valid: number;
  rows_rejected: number;
  new: number;
  updated: number;
  unchanged: number;
  duplicates_in_file: number;
  issue_counts: Record<string, number>;
  issues: ImportIssue[];
  issues_truncated: boolean;
  ignored_columns: { name: string; reason: string }[];
  per_property: Record<string, Record<string, number>>;
  file_error: { code: string; message: string } | null;
}

export interface AnalysisRunReport {
  run_id: number | null;
  status: string;
  eligible: number;
  pending_at_start: number;
  cached: number;
  succeeded: number;
  failed: number;
  remaining: number;
}

export interface AnalysisConfig {
  rule_based: { can_run: boolean };
  ai: {
    provider: string;
    model: string | null;
    configured: boolean;
    real_text_allowed: boolean;
    can_run: boolean;
    reason: string | null;
    max_per_run: number;
  };
}
