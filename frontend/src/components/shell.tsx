"use client";

import { useQuery } from "@tanstack/react-query";
import { BarChart3, Building2, Compass, Database, FlaskConical, Lightbulb, Menu, MessageSquareText, PanelLeft, RefreshCw, X, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, type ReactNode } from "react";
import { cx } from "@/lib/cx";
import { useScope } from "@/hooks/scope";
import { api } from "@/services/api";
import { ThemeSwitcher } from "@/components/theme-switcher";
import { useTour } from "@/components/tour";
import { EmptyState, ErrorBox } from "@/components/ui";

const NAV: { href: string; label: string; icon: LucideIcon }[] = [
  { href: "/", label: "Executive Overview", icon: BarChart3 },
  { href: "/properties", label: "Properties", icon: Building2 },
  { href: "/insights", label: "Review Intelligence", icon: Lightbulb },
  { href: "/reviews", label: "Review Explorer", icon: MessageSquareText },
  { href: "/data", label: "Data & Collection", icon: Database },
];

function Brand() {
  return (
    <Link href="/" className="flex items-center gap-2.5">
      <span className="grid size-9 place-items-center rounded-lg bg-accent font-bold text-on-accent">A</span>
      <span>
        <span className="block text-sm font-semibold leading-tight text-ink">Azzurro Hotels</span>
        <span className="block text-[11px] uppercase tracking-[0.14em] text-ink-faint">Review Intelligence</span>
      </span>
    </Link>
  );
}

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const path = usePathname();
  return (
    <nav aria-label="Main" className="flex flex-col gap-1">
      {NAV.map(({ href, label, icon: Icon }) => {
        const active = href === "/" ? path === "/" : path.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            onClick={onNavigate}
            data-tour={`nav-${href}`}
            aria-current={active ? "page" : undefined}
            className={cx(
              "flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors",
              active
                ? "bg-accent-50 font-medium text-accent-700"
                : "text-ink-soft hover:bg-sand-100 hover:text-ink",
            )}
          >
            <Icon className="size-4.5" aria-hidden />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}

/** Inset-panel top bar with a breadcrumb, as in the shadcn dashboard layout. */
function TopBar() {
  const path = usePathname();
  const current = NAV.find(({ href }) => (href === "/" ? path === "/" : path.startsWith(href)));
  return (
    <div className="hidden h-14 items-center gap-3 border-b border-line px-6 lg:flex">
      <PanelLeft className="size-4 text-ink-faint" aria-hidden />
      <span className="h-4 w-px bg-line" aria-hidden />
      <nav aria-label="Breadcrumb" className="text-sm">
        <ol className="flex items-center gap-2">
          <li className="text-ink-faint">Dashboard</li>
          <li className="text-ink-faint" aria-hidden>/</li>
          <li className="font-medium text-ink" aria-current="page">{current?.label ?? "Overview"}</li>
        </ol>
      </nav>
      <div className="ml-auto flex items-center gap-2" data-tour="topbar-actions">
        <TourButton />
        <Link
          href="/data#update"
          className="inline-flex h-9 items-center gap-2 rounded-lg bg-accent px-3 text-sm font-semibold text-on-accent hover:opacity-90"
        >
          <RefreshCw className="size-4" aria-hidden /> Update data
        </Link>
        <ThemeSwitcher />
      </div>
    </div>
  );
}

function TourButton({ onClick }: { onClick?: () => void }) {
  const { open } = useTour();
  return (
    <button
      type="button"
      onClick={() => {
        onClick?.();
        open();
      }}
      className="inline-flex h-9 items-center gap-2 rounded-lg border border-line px-3 text-sm text-ink-soft hover:bg-sand-100 hover:text-ink"
    >
      <Compass className="size-4" aria-hidden /> Take the tour
    </button>
  );
}

function ScopeSwitch() {
  const { demo, hasDemo, hasReal, setDemo } = useScope();
  if (!(hasDemo && hasReal)) return null;
  return (
    <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-line px-3 py-2 text-xs text-ink-soft hover:bg-sand-100">
      <input type="checkbox" className="size-3.5" style={{ accentColor: "var(--color-accent)" }} checked={demo} onChange={(e) => setDemo(e.target.checked)} />
      Show demonstration data
    </label>
  );
}

/** DEMO / SYNTHETIC banner. Always visible whenever demonstration data is on screen. */
function Banners() {
  const { demo, hasReal, setDemo, ready } = useScope();
  const cov = useQuery({ queryKey: ["banner", demo], queryFn: () => api.coverage({ include_demo: demo }), enabled: ready });
  const mock = cov.data?.meta.analysis.contains_mock_analysis;
  return (
    <div role="status">
      {demo && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-warning bg-warning-100 px-4 py-2 text-sm text-warning-900">
          <span className="inline-flex items-center gap-1.5 rounded bg-warning px-2 py-0.5 font-bold tracking-wide text-black"><FlaskConical className="size-4" aria-hidden /> DEMO / SYNTHETIC DATA</span>
          <span>Fictional reviews generated for demonstration. These are not real Booking.com reviews or results.</span>
          {hasReal && <button className="ml-auto font-medium underline" onClick={() => setDemo(false)}>Show real data</button>}
        </div>
      )}
      {mock && <div className="bg-sand-100 px-4 py-1 text-xs text-ink-soft">Some topic analysis comes from the offline keyword test provider, not AI.</div>}
      {cov.data && (
        <p className="border-b border-line px-4 py-2 text-xs text-ink-faint sm:px-6 lg:px-8" data-testid="data-context">
          <strong className="text-ink">{cov.data.data.total_reviews.toLocaleString("en-AU")}</strong> {demo ? "synthetic demonstration" : "Booking.com"} reviews
          {" · "}topics analysed for <strong className="text-ink">{cov.data.data.analysed.toLocaleString("en-AU")}</strong> of {cov.data.data.eligible.toLocaleString("en-AU")} ({Math.round((cov.data.data.share_analysed ?? 0) * 100)}%)
          {" · "}dates in {cov.data.meta.reporting_timezone} · data as of {new Date(cov.data.meta.as_of).toLocaleString("en-AU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}
        </p>
      )}
      {cov.isError && <p role="alert" className="bg-negative-50 px-4 py-2 text-xs text-negative-700">Data summary unavailable: {cov.error instanceof Error ? cov.error.message : "unknown error"}</p>}
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const path = usePathname();
  // Menu is "open for" a specific path, so navigating (even with the back button) closes it.
  const [openFor, setOpenFor] = useState<string | null>(null);
  const open = openFor === path;
  const setOpen = (fn: boolean | ((o: boolean) => boolean)) => setOpenFor(typeof fn === "function" ? (fn(open) ? path : null) : fn ? path : null);
  const { ready, failed, hasReal, hasDemo } = useScope();

  let body = children;
  if (ready && failed) {
    body = <ErrorBox error={new Error("Cannot reach the review service. Start the backend (reviewhub serve) and reload this page.")} retry={() => window.location.reload()} />;
  } else if (ready && !hasReal && !hasDemo) {
    body = (
      <EmptyState title="No reviews loaded yet">
        Collect or import reviews first (<code>reviewhub collect</code> or <code>reviewhub import</code>), or load fictional demo data with <code>reviewhub seed-demo</code>.
      </EmptyState>
    );
  }

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[17rem_1fr]">
      <aside className="hidden bg-sidebar p-4 lg:flex lg:flex-col lg:gap-6 lg:sticky lg:top-0 lg:h-screen">
        <div className="px-2 pt-1"><Brand /></div>
        <div>
          <p className="mb-1.5 px-3 text-[11px] font-medium uppercase tracking-wider text-ink-faint">Dashboard</p>
          <NavLinks />
        </div>
        <div className="mt-auto space-y-3 px-1"><ScopeSwitch /><p className="px-2 text-[11px] leading-snug text-ink-faint">Public Booking.com reviews. Ratings are on Booking.com&apos;s 1–10 scale.</p></div>
      </aside>

      <header className="sticky top-0 z-30 flex items-center justify-between border-b border-line bg-surface/90 px-4 py-3 backdrop-blur lg:hidden">
        <Brand />
        <button aria-label={open ? "Close menu" : "Open menu"} aria-expanded={open} onClick={() => setOpen((o) => !o)} className="rounded-lg p-2 text-ink hover:bg-sand-100">
          {open ? <X className="size-5" /> : <Menu className="size-5" />}
        </button>
      </header>
      {open && (
        <div className="fixed inset-x-0 top-15 z-20 space-y-4 border-b border-line bg-surface p-4 shadow-lg lg:hidden">
          <NavLinks onNavigate={() => setOpen(false)} />
          <ScopeSwitch />
          <div className="flex flex-wrap items-center gap-2 border-t border-line pt-4">
            <Link href="/data#update" onClick={() => setOpen(false)} className="inline-flex h-9 items-center gap-2 rounded-lg bg-accent px-3 text-sm font-semibold text-on-accent">
              <RefreshCw className="size-4" aria-hidden /> Update data
            </Link>
            <ThemeSwitcher />
            <TourButton onClick={() => setOpen(false)} />
          </div>
        </div>
      )}

      <div className="min-w-0 bg-canvas lg:my-2 lg:mr-2 lg:min-h-[calc(100vh-1rem)] lg:overflow-hidden lg:rounded-xl lg:border lg:border-line">
        <TopBar />
        <Banners />
        <main id="main" className="mx-auto w-full max-w-7xl px-4 py-6 sm:px-6 lg:px-8 lg:py-8">{body}</main>
      </div>
    </div>
  );
}
