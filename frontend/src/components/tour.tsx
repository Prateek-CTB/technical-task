"use client";

import { ArrowLeft, ArrowRight, Compass, X } from "lucide-react";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, useSyncExternalStore, type ReactNode } from "react";

/**
 * First-time guided tour. Opens by itself on a browser's first visit (remembered in
 * localStorage), and can be replayed from the "Take the tour" button. Each step navigates to the
 * screen it describes and highlights the control it talks about. It is a non-blocking panel, so
 * the screen stays visible and usable behind it; Esc closes it.
 */
export const TOUR_KEY = "reviewhub.tour.done";

interface Step {
  title: string;
  body: ReactNode;
  href?: string;
  /** value of a data-tour attribute to highlight while this step is shown */
  target?: string;
}

export const STEPS: Step[] = [
  {
    title: "Welcome to Azzurro Review Intelligence",
    body: (
      <>
        This dashboard turns Booking.com guest reviews for the four Azzurro hotels into ratings, trends and the problems
        guests mention most. Every number is calculated from the real reviews behind it. This short tour shows where
        everything is.
      </>
    ),
  },
  {
    title: "Executive Overview: your morning check",
    href: "/",
    target: "nav-/",
    body: (
      <>
        This week&apos;s average rating against the same days last week, rating and sentiment trends, and a ranked list of
        <strong> issues worth acting on</strong>, each with guest quotes and a suggested action. The 4 weeks / 12 weeks / All
        time switch changes the period.
      </>
    ),
  },
  {
    title: "Properties: the four hotels side by side",
    href: "/properties",
    target: "nav-/properties",
    body: (
      <>
        Ratings, sentiment and the most praised and criticised topics per hotel. Booking.com&apos;s own score is shown
        separately because Booking calculates it differently. Click a hotel for its own page.
      </>
    ),
  },
  {
    title: "Review Intelligence: what guests talk about",
    href: "/insights",
    target: "nav-/insights",
    body: (
      <>
        The eight operational topics: cleanliness, check-in, staff, noise, facilities, location, room condition and value
        for money. See <strong>what % of negative reviews mention each</strong>, the guests&apos; own words, and click
        through to the reviews behind any insight.
      </>
    ),
  },
  {
    title: "Review Explorer: read every review",
    href: "/reviews",
    target: "nav-/reviews",
    body: (
      <>
        Search for a word, or filter by hotel, dates, rating, sentiment or topic. Highlighted words show why a topic was
        tagged: green for praise, red for criticism. Each review says whether an AI model or the offline analyzer read it.
      </>
    ),
  },
  {
    title: "Data & Collection: where the numbers come from",
    href: "/data",
    target: "nav-/data",
    body: (
      <>
        <strong>Get latest reviews</strong>, <strong>Analyse with AI</strong> or import a file at the top. Below: how many
        reviews we hold for each hotel, every collection run, and its result. If Booking.com refuses a request it shows as
        <strong> BLOCKED</strong> with the reason; it is never worked around.
      </>
    ),
  },
  {
    title: "Shortcuts and colours",
    target: "topbar-actions",
    body: (
      <>
        <strong>Update data</strong> jumps straight to those actions. The sun/moon button switches between light and dark,
        and the colour picker changes the theme. Replay this tour any time with <strong>Take the tour</strong>.
      </>
    ),
  },
];

interface TourState {
  open: () => void;
  /** data-tour value currently highlighted (for components that want to react) */
  target: string | null;
}
const Ctx = createContext<TourState>({ open: () => {}, target: null });
export const useTour = () => useContext(Ctx);

const noSubscribe = () => () => {};
const readSeen = () => {
  try {
    return localStorage.getItem(TOUR_KEY) === "1";
  } catch {
    return true; // storage unavailable: never auto-open
  }
};

export function TourProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const path = usePathname();
  // Server and hydration render as "seen", so the panel only appears after hydration (no mismatch).
  const seen = useSyncExternalStore(noSubscribe, readSeen, () => true);
  const [manual, setManual] = useState<number | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const step = manual ?? (!seen && !dismissed ? 0 : null);
  const current = step === null ? null : STEPS[step];

  // Keep the highlighted control in sync with the step (DOM side effect only, no React state).
  useEffect(() => {
    const target = current?.target;
    if (!target) return;
    const els = document.querySelectorAll(`[data-tour="${target}"]`);
    els.forEach((el) => el.classList.add("tour-highlight"));
    return () => els.forEach((el) => el.classList.remove("tour-highlight"));
  }, [current, path]);

  const go = useCallback(
    (i: number) => {
      setManual(i);
      const href = STEPS[i]?.href;
      if (href && href !== path) router.push(href);
    },
    [path, router],
  );
  const close = useCallback(() => {
    try {
      localStorage.setItem(TOUR_KEY, "1");
    } catch {
      /* ignore */
    }
    setManual(null);
    setDismissed(true);
  }, []);

  const value = useMemo(() => ({ open: () => go(0), target: current?.target ?? null }), [go, current]);

  return (
    <Ctx.Provider value={value}>
      {children}
      {step !== null && current && (
        <TourPanel
          key={step}
          step={step}
          total={STEPS.length}
          title={current.title}
          onBack={() => go(step - 1)}
          onNext={() => (step === STEPS.length - 1 ? close() : go(step + 1))}
          onClose={close}
        >
          {current.body}
        </TourPanel>
      )}
    </Ctx.Provider>
  );
}

function TourPanel({
  step, total, title, children, onBack, onNext, onClose,
}: {
  step: number;
  total: number;
  title: string;
  children: ReactNode;
  onBack: () => void;
  onNext: () => void;
  onClose: () => void;
}) {
  const last = step === total - 1;
  return (
    <div
      role="dialog"
      aria-modal="false"
      aria-labelledby="tour-title"
      onKeyDown={(e) => e.key === "Escape" && onClose()}
      className="card-surface fixed inset-x-3 bottom-3 z-50 p-5 sm:inset-x-auto sm:right-6 sm:bottom-6 sm:w-[400px]"
    >
      <div className="flex items-start justify-between gap-3">
        <p className="inline-flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-accent-700">
          <Compass className="size-3.5" aria-hidden /> Guided tour · step {step + 1} of {total}
        </p>
        <button type="button" onClick={onClose} aria-label="Close the tour" className="-m-1 rounded-md p-1 text-ink-faint hover:bg-sand-100 hover:text-ink">
          <X className="size-4" aria-hidden />
        </button>
      </div>
      <h2 id="tour-title" className="mt-2 text-lg font-semibold tracking-tight text-ink">{title}</h2>
      <p className="mt-2 text-sm leading-relaxed text-ink-soft">{children}</p>
      <div className="mt-4 flex items-center gap-1.5" aria-hidden>
        {Array.from({ length: total }, (_, i) => (
          <span key={i} className={`h-1.5 rounded-full transition-all ${i === step ? "w-5 bg-accent" : "w-1.5 bg-line-strong"}`} />
        ))}
      </div>
      <div className="mt-4 flex items-center justify-between gap-2">
        <button type="button" onClick={onClose} className="text-sm text-ink-faint hover:text-ink">
          Skip tour
        </button>
        <div className="flex gap-2">
          {step > 0 && (
            <button type="button" onClick={onBack} className="inline-flex items-center gap-1.5 rounded-lg border border-line-strong px-3 py-1.5 text-sm font-medium text-ink hover:bg-sand-100">
              <ArrowLeft className="size-4" aria-hidden /> Back
            </button>
          )}
          {/* autoFocus: each step remounts (key), so keyboard users land on the main action */}
          <button type="button" autoFocus onClick={onNext} className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3.5 py-1.5 text-sm font-semibold text-on-accent hover:opacity-90">
            {last ? "Finish" : step === 0 ? "Start the tour" : "Continue"} {!last && <ArrowRight className="size-4" aria-hidden />}
          </button>
        </div>
      </div>
    </div>
  );
}
