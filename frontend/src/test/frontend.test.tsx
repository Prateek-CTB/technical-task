import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Highlighted } from "@/components/review-card";
import { Delta, EmptyState, ErrorBox, SentimentBar } from "@/components/ui";
import { isoDaysAgo, mondayIso, pct, plural, signed, topicLabel } from "@/lib/format";
import { ApiError, get } from "@/services/api";



describe("format helpers", () => {
  it("formats percentages, signs and plurals", () => {
    expect(pct(0.4)).toBe("40%");
    expect(pct(null)).toBe("—");
    expect(signed(1.234)).toBe("+1.23");
    expect(signed(-0.5)).toBe("-0.50");
    expect(plural(1, "review")).toBe("1 review");
    expect(plural(2, "review")).toBe("2 reviews");
  });
  it("labels topics", () => {
    expect(topicLabel("room_condition")).toBe("Room condition");
    expect(topicLabel("unknown")).toBe("unknown");
  });
  it("computes the Monday of a week and relative dates", () => {
    expect(mondayIso(new Date(2026, 10, 26))).toBe("2026-11-23"); // Thursday
    expect(mondayIso(new Date(2026, 10, 23))).toBe("2026-11-23"); // Monday
    expect(isoDaysAgo(28, new Date(2026, 10, 26))).toBe("2026-10-29");
  });
});

describe("components", () => {
  afterEach(() => vi.restoreAllMocks());
  it("Delta shows sign and a text alternative to colour", () => {
    render(<Delta value={-0.4} />);
    expect(screen.getByText("-0.40")).toBeInTheDocument();
  });
  it("Delta explains a missing comparison", () => {
    render(<Delta value={null} />);
    expect(screen.getByText(/no comparison yet/i)).toBeInTheDocument();
  });
  it("SentimentBar exposes the shares to screen readers", () => {
    render(<SentimentBar positive={6} neutral={2} negative={2} />);
    expect(screen.getByRole("img")).toHaveAccessibleName("60% positive, 20% neutral, 20% negative");
  });
  it("ErrorBox announces the error and offers retry", () => {
    const retry = vi.fn();
    render(<ErrorBox error={new Error("boom")} retry={retry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("boom");
    screen.getByRole("button", { name: /try again/i }).click();
    expect(retry).toHaveBeenCalled();
  });
  it("EmptyState renders its message", () => {
    render(<EmptyState title="Nothing here">Try later</EmptyState>);
    expect(screen.getByText("Nothing here")).toBeInTheDocument();
  });
  it("Highlighted marks only valid evidence spans", () => {
    const text = "Dirty room but friendly staff";
    const { container } = render(
      <Highlighted
        text={text}
        field="disliked"
        aspects={[
          { topic: "cleanliness", sentiment: "negative", evidence: [{ field: "disliked", start: 0, end: 5 }] },
          { topic: "staff", sentiment: "positive", evidence: [{ field: "disliked", start: 20, end: 999 }] }, // out of range: ignored
        ]}
      />,
    );
    const marks = container.querySelectorAll("mark");
    expect(marks).toHaveLength(1);
    expect(marks[0]).toHaveTextContent("Dirty");
    expect(container).toHaveTextContent(text);
  });
});

describe("api service", () => {
  afterEach(() => vi.restoreAllMocks());
  it("builds the query string and skips empty params", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ data: 1 }), { status: 200 }));
    await get("analytics/reviews", { page: 2, q: "", topic: undefined, include_demo: false });
    expect(spy.mock.calls[0]?.[0]).toBe("/api/v1/analytics/reviews?page=2&include_demo=false");
  });
  it("turns an RFC 7807 error body into a readable ApiError", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ title: "Validation failed" }), { status: 422 }));
    await expect(get("x")).rejects.toMatchObject({ message: "Validation failed", status: 422 });
  });
  it("rejects a payload that is not in the { data } format", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ unexpected: true }), { status: 200 }));
    await expect(get("x")).rejects.toMatchObject({ message: expect.stringMatching(/unexpected response format/i) });
  });
  it("reports an unreachable backend in plain language", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("failed"));
    const err = await get("x").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).message).toMatch(/cannot reach the review service/i);
  });
});
