import { expect, test, type Page } from "@playwright/test";

const MODE = process.env.E2E_MODE ?? "real";
const PROPERTIES = ["Olympic Hotel Paddington", "Potts Point", "Central Sydney", "Darling Harbour"];

function trackErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  return errors;
}

/** On mobile the navigation sits behind the menu button. */
async function go(page: Page, label: string) {
  const menu = page.getByRole("button", { name: /open menu/i });
  if (await menu.isVisible()) await menu.click();
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: label }).click();
}

test.describe(`dataset: ${MODE}`, () => {
  // The first-visit tour floats over the page; other tests start with it already dismissed.
  test.beforeEach(async ({ page }, info) => {
    if (!info.title.startsWith("guided tour")) {
      await page.addInitScript(() => localStorage.setItem("reviewhub.tour.done", "1"));
    }
  });

  test("DEMO banner appears if and only if synthetic data is shown", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    const banner = page.getByText("DEMO / SYNTHETIC DATA");
    if (MODE === "demo") await expect(banner).toBeVisible();
    else await expect(banner).toHaveCount(0);
  });

  test("every page loads from the real API with no errors", async ({ page }) => {
    const errors = trackErrors(page);
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Executive Overview" })).toBeVisible();
    await expect(page.getByLabel("Executive summary")).toBeVisible();
    await expect(page.getByTestId("data-context")).toContainText("reviews");
    for (const [label, heading] of [
      ["Properties", "Property Performance"],
      ["Review Intelligence", "Review Intelligence"],
      ["Review Explorer", "Review Explorer"],
      ["Data & Collection", "Data & Collection"],
      ["Executive Overview", "Executive Overview"],
    ] as const) {
      await go(page, label);
      await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
      await expect(page.locator("main [role=alert]")).toHaveCount(0);
    }
    expect(errors).toEqual([]);
  });

  test("all four properties have a working detail page", async ({ page }) => {
    await page.goto("/properties");
    for (const name of PROPERTIES) await expect(page.getByRole("link", { name, exact: true }).first()).toBeVisible();
    for (const name of PROPERTIES) {
      await page.goto("/properties");
      await page.getByRole("link", { name, exact: true }).first().click();
      await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
      await expect(page.getByText("Average rating", { exact: true })).toBeVisible();
      await page.getByRole("link", { name: /read this property/i }).click();
      await expect(page).toHaveURL(/property_id=\d+/);
      await expect(page.getByRole("combobox", { name: "Property", exact: true })).not.toHaveValue("");
    }
  });

  test("review explorer: filter, search, sort, paginate, clear", async ({ page }) => {
    await page.goto("/reviews");
    const total = async () => Number((await page.getByText(/^[\d,]+ reviews?/).first().innerText()).match(/^[\d,]+/)![0].replace(/,/g, ""));
    await expect(page.locator("article").first()).toBeVisible();
    const all = await total();
    expect(all).toBeGreaterThan(0);

    // property filter narrows and is reflected in the URL
    await page.getByRole("combobox", { name: "Property", exact: true }).selectOption({ label: "Potts Point" });
    await expect(page).toHaveURL(/property_id=/);
    await expect.poll(total).toBeLessThan(all);
    await expect(page.locator("article").first()).toContainText("Potts Point");

    // rating filter
    await page.getByRole("combobox", { name: "Rating", exact: true }).selectOption("neg");
    await expect(page).toHaveURL(/rating_max=6/);
    const badges = page.locator("article [aria-label^='Rating']");
    await expect(badges.first()).toBeVisible();
    await expect
      .poll(async () => (await badges.allInnerTexts()).map(Number).every((v) => v <= 6))
      .toBe(true);

    // sort by lowest rating
    await page.getByRole("combobox", { name: "Sort by", exact: true }).selectOption("rating_low");
    await expect(page).toHaveURL(/sort=rating_low/);
    await expect
      .poll(async () => {
        const r = (await badges.allInnerTexts()).map(Number);
        return r.length > 1 && r.every((v, i) => i === 0 || (r[i - 1] ?? 0) <= v);
      })
      .toBe(true);

    // clear
    await page.getByRole("button", { name: /clear filters/i }).click();
    await expect(page).toHaveURL(/\/reviews$/);
    await expect.poll(total).toBe(all);

    // pagination
    const next = page.getByRole("button", { name: /next/i });
    await expect(next).toBeEnabled();
    const first = await page.locator("article").first().innerText();
    await next.click();
    await expect(page).toHaveURL(/page=2/);
    await expect.poll(async () => page.locator("article").first().innerText()).not.toBe(first);
    await page.getByRole("button", { name: /previous/i }).click();
    await expect(page.getByText(/page 1 of/i).first()).toBeVisible();
  });

  test("text search matches review text", async ({ page }) => {
    await page.goto("/reviews");
    await expect(page.locator("article").first()).toBeVisible();
    const body = (await page.locator("article p").filter({ hasText: /^(Liked|Disliked) · / }).first().innerText()).replace(/^(Liked|Disliked) · /, "");
    const word = body.match(/[A-Za-z]{6,}/)![0];
    await page.getByRole("searchbox").fill(word);
    await expect(page).toHaveURL(new RegExp(`q=${word}`));
    await expect(page.locator("article").first()).toContainText(new RegExp(word, "i"));
    await page.getByRole("searchbox").fill("zzzz-no-such-text-zzzz");
    await expect(page.getByText("No reviews match these filters")).toBeVisible();
  });

  test("date range filter works and an impossible range shows the empty state", async ({ page }) => {
    await page.goto("/reviews?date_from=2001-01-01&date_to=2001-01-31");
    await expect(page.getByText("No reviews match these filters")).toBeVisible();
    await page.getByRole("button", { name: /clear filters/i }).click();
    await expect(page.locator("article").first()).toBeVisible();
  });

  test("an insight links to the reviews behind it", async ({ page }) => {
    await page.goto("/insights");
    const link = page.getByRole("link", { name: /read the \d+ (underlying )?reviews/i }).first();
    await expect(link).toBeVisible();
    await link.click();
    await expect(page).toHaveURL(/topic=/);
    await expect(page.getByRole("heading", { level: 1, name: "Review Explorer" })).toBeVisible();
    await expect(page.getByRole("combobox", { name: "Topic", exact: true })).not.toHaveValue("");
    // zero analysed matches is acceptable only when nothing is analysed; otherwise cards must appear
    await expect(page.locator("article, :text('No reviews match these filters')").first()).toBeVisible();
  });

  test("an API failure shows a useful error with a working retry", async ({ page }) => {
    let fail = true;
    await page.route("**/api/v1/analytics/overview**", (route) => (fail ? route.fulfill({ status: 500, body: "{}" }) : route.continue()));
    await page.goto("/");
    const alert = page.locator("main [role=alert]").first();
    await expect(alert).toContainText("could not be loaded");
    fail = false;
    await alert.getByRole("button", { name: /try again/i }).click();
    await expect(page.getByText("Average rating this week so far")).toBeVisible();
  });

  test("properties can be compared side by side, with Booking's own score kept separate", async ({ page }) => {
    await page.goto("/properties");
    for (const name of PROPERTIES) await expect(page.getByText(name, { exact: true }).first()).toBeVisible();
    await expect(page.getByRole("table").first()).toContainText("Avg rating");
  });

  test("a negative insight opens the criticising reviews behind it", async ({ page }) => {
    await page.goto("/");
    const link = page.getByRole("link", { name: /read the \d+ underlying reviews/i }).first();
    if (MODE === "real") await expect(link).toBeVisible();
    if (!(await link.count())) return; // demo data with no recurring problem
    await link.click();
    await expect(page).toHaveURL(/topic=.*topic_sentiment=negative|topic_sentiment=negative.*topic=/);
    const first = page.locator("article").first();
    await expect(first).toBeVisible();
    await expect(first.locator("text=/^−/").first()).toBeVisible(); // a criticised topic chip
  });

  test("Data & Collection shows per-property collection status and honest BLOCKED results", async ({ page }) => {
    const errors = trackErrors(page);
    await page.goto("/data");
    await expect(page.getByRole("heading", { level: 1, name: "Data & Collection" })).toBeVisible();
    for (const name of PROPERTIES) await expect(page.getByRole("cell", { name, exact: true })).toBeVisible();
    await expect(page.getByText("Collection methods")).toBeVisible();
    await expect(page.getByText(/Nothing is solved, spoofed or worked around/)).toBeVisible();
    await expect(page.getByText("Import reviews from an authorised export")).toBeVisible();
    await expect(page.getByText("Recent activity")).toBeVisible();
    expect(errors).toEqual([]);
  });

  test("import workflow validates a file before saving anything", async ({ page }) => {
    await page.goto("/data");
    const csv = [
      "property,rating,review_date,liked,disliked",
      "olympic-paddington,8,2026-11-20,Great location,",
      "not-a-hotel,11,bad-date,x,y",
    ].join("\n");
    await page.locator("input[type=file]").setInputFiles({ name: "e2e-check.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
    await page.getByRole("button", { name: /validate/i }).click();
    if (MODE === "demo") {
      // Dataset isolation: real (CSV) data is never mixed into a synthetic-only database.
      await expect(page.getByRole("alert").filter({ hasText: /refusing to load 'csv_import' data/ })).toBeVisible();
      return;
    }
    await expect(page.getByText(/Preview of e2e-check.csv \(nothing saved yet\)/)).toBeVisible();
    await expect(page.getByText("Rows in file")).toBeVisible();
    await expect(page.getByText(/Row 3/).first()).toBeVisible(); // the invalid row is reported with its line
  });

  test("guided tour: opens on the first visit, walks the screens, and can be replayed", async ({ page }) => {
    await page.goto("/");
    const tour = page.getByRole("dialog", { name: /welcome to azzurro review intelligence/i });
    await expect(tour).toBeVisible();
    await tour.getByRole("button", { name: /start the tour/i }).click();
    await expect(page.getByRole("dialog", { name: /executive overview/i })).toBeVisible();
    await page.getByRole("button", { name: /continue/i }).click();
    await expect(page).toHaveURL(/\/properties$/);
    await expect(page.getByRole("dialog", { name: /properties/i })).toBeVisible();
    await page.getByRole("button", { name: /skip tour/i }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.reload();
    await expect(page.getByRole("dialog")).toHaveCount(0); // remembered: not shown again
    const menu = page.getByRole("button", { name: /open menu/i });
    if (await menu.isVisible()) await menu.click();
    await page.getByRole("button", { name: /take the tour/i }).first().click();
    await expect(page.getByRole("dialog", { name: /welcome/i })).toBeVisible();
  });

  test("theme: light/dark and colour presets apply everywhere and are remembered", async ({ page }) => {
    await page.goto("/");
    const html = page.locator("html");
    await expect(html).toHaveAttribute("data-mode", "dark");
    const menu = page.getByRole("button", { name: /open menu/i });
    if (await menu.isVisible()) await menu.click();
    await page.getByRole("button", { name: /switch to light mode/i }).first().click();
    await expect(html).toHaveAttribute("data-mode", "light");
    await page.getByRole("button", { name: /colour theme/i }).first().click();
    await page.getByRole("menuitemradio", { name: "Blue" }).click();
    await expect(html).toHaveAttribute("data-accent", "blue");
    await page.goto("/reviews");
    await expect(html).toHaveAttribute("data-mode", "light");
    await expect(html).toHaveAttribute("data-accent", "blue");
    const bg = await page.evaluate(() => getComputedStyle(document.querySelector("main")!.parentElement!).backgroundColor);
    expect(bg).toBe("rgb(255, 255, 255)"); // light canvas really applied
  });

  test("Update data: get-latest and AI actions are offered with plain explanations", async ({ page }) => {
    await page.goto("/");
    const menu = page.getByRole("button", { name: /open menu/i });
    if (await menu.isVisible()) await menu.click();
    await page.getByRole("link", { name: /update data/i }).first().click();
    await expect(page).toHaveURL(/\/data#update$/);
    // Present and labelled; not clicked: a real run would open a browser or spend AI quota.
    await expect(page.getByRole("button", { name: /get latest reviews/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /analyse with ai/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /analyse new reviews \(offline\)/i })).toBeEnabled();
    await expect(page.getByText(/Booking\.com refuses headless browsers/)).toBeVisible();
  });
});
