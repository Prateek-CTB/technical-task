import { z } from "zod";

/**
 * Runtime validation at the API boundary. These schemas check the fields the screens depend on
 * (loose objects: extra fields are allowed so the backend can add data without breaking the UI).
 * A mismatch becomes one readable error instead of `undefined` deep inside a chart.
 */

const sampleStatus = z.enum(["ok", "low_sample", "insufficient_data", "indicative"]);
const sentiment = z.enum(["positive", "negative", "neutral", "mixed"]);
const stat = z.looseObject({ value: z.number().nullable(), n: z.number(), status: sampleStatus });

export const metaSchema = z.looseObject({
  as_of: z.string(),
  reporting_timezone: z.string(),
  dataset: z.looseObject({ sources: z.record(z.string(), z.number()), contains_demo: z.boolean() }),
  analysis: z.looseObject({
    providers: z.record(z.string(), z.number()),
    methods: z.looseObject({ ai: z.number(), rule_based: z.number(), mock: z.number() }),
    contains_mock_analysis: z.boolean(),
  }),
});

const envelope = <T extends z.ZodType>(data: T) => z.looseObject({ data, meta: metaSchema });

export const overviewSchema = envelope(
  z.looseObject({
    reviews: z.number(),
    avg_rating: stat,
    rating_bands: z.looseObject({ positive: z.number(), neutral: z.number(), negative: z.number() }),
    week_comparison: z.looseObject({
      current_week_to_date: stat,
      previous_week_same_elapsed: stat,
      avg_rating_delta: z.number().nullable(),
    }),
  }),
);

export const reviewsSchema = envelope(
  z.array(
    z.looseObject({
      id: z.number(),
      rating: z.number(),
      property: z.looseObject({ id: z.number(), name: z.string() }),
      published_at: z.string(),
      analysis: z
        .looseObject({
          overall_sentiment: sentiment,
          method: z.enum(["ai", "rule_based", "mock"]),
          aspects: z.array(z.looseObject({ topic: z.string(), sentiment })),
        })
        .nullable(),
    }),
  ),
).and(z.looseObject({ meta: z.looseObject({ pagination: z.looseObject({ page: z.number(), pages: z.number(), total: z.number() }) }) }));

const runOutcome = z.looseObject({ run_id: z.number(), status: z.string(), strategy: z.string() }).nullable();

export const collectionStatusSchema = z.looseObject({
  data: z.looseObject({
    properties: z.array(
      z.looseObject({
        property: z.looseObject({ id: z.number(), name: z.string() }),
        reviews_held: z.looseObject({ total: z.number() }),
        booking_published: z.looseObject({ score: z.number().nullable(), review_count: z.number().nullable() }),
        last_attempt: runOutcome,
        last_success: runOutcome,
      }),
    ),
    strategies: z.array(
      z.looseObject({
        key: z.string(),
        state: z.enum(["never_run", "cooling_down", "blocked", "ok", "failed"]),
        next_eligible_at: z.string(),
      }),
    ),
  }),
});

export const importReportSchema = z.looseObject({
  status: z.enum(["success", "partial", "failed"]),
  dry_run: z.boolean(),
  rows_total: z.number(),
  rows_valid: z.number(),
  rows_rejected: z.number(),
  new: z.number(),
  updated: z.number(),
  unchanged: z.number(),
  issues: z.array(z.looseObject({ line: z.number(), code: z.string(), message: z.string() })),
});

export const propertiesSchema = z.looseObject({
  data: z.array(z.looseObject({ id: z.number(), slug: z.string(), name: z.string() })),
});

/** Throws a readable message naming the first field that does not match. */
export function check<T>(schema: z.ZodType, body: unknown, what: string): T {
  const r = schema.safeParse(body);
  if (!r.success) {
    const first = r.error.issues[0];
    const where = first?.path.length ? first.path.join(".") : "response";
    throw new Error(`The review service sent ${what} in an unexpected format (${where}: ${first?.message ?? "invalid"}).`);
  }
  return body as T;
}
