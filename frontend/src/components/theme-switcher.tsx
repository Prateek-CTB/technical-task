"use client";

import { Check, ChevronDown, Moon, Palette, Sun } from "lucide-react";
import { useEffect, useId, useRef, useState, type KeyboardEvent, type MouseEvent as ReactMouseEvent } from "react";
import { cx } from "@/lib/cx";
import { ACCENTS, useTheme, type Accent } from "@/hooks/theme";

type ViewTransitionDoc = Document & { startViewTransition?: (update: () => void) => { ready: Promise<void> } };

/**
 * Light/dark toggle with a circular reveal: the new theme expands in a circle from the button
 * (View Transitions API). Without that API, or with reduced motion requested, it switches at once.
 */
function useCircularModeSwitch() {
  const { mode, setMode } = useTheme();
  const next = mode === "dark" ? "light" : "dark";
  const toggle = (e: ReactMouseEvent<HTMLButtonElement>) => {
    const doc = document as ViewTransitionDoc;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!doc.startViewTransition || reduce) {
      setMode(next);
      return;
    }
    const r = e.currentTarget.getBoundingClientRect();
    const x = r.left + r.width / 2;
    const y = r.top + r.height / 2;
    const radius = Math.hypot(Math.max(x, window.innerWidth - x), Math.max(y, window.innerHeight - y));
    const transition = doc.startViewTransition(() => setMode(next));
    void transition.ready.then(() =>
      document.documentElement.animate(
        { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`] },
        { duration: 550, easing: "cubic-bezier(0.4, 0, 0.2, 1)", pseudoElement: "::view-transition-new(root)" },
      ),
    );
  };
  return { mode, next, toggle };
}

/** Light/dark toggle + colour preset menu (top bar on desktop, menu on mobile). */
export function ThemeSwitcher() {
  const { mode, next, toggle } = useCircularModeSwitch();
  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={toggle}
        aria-label={`Switch to ${next} mode`}
        title={`Switch to ${next} mode`}
        className="grid size-9 place-items-center rounded-full border border-line text-ink-soft transition-colors hover:bg-sand-100 hover:text-ink"
      >
        {mode === "dark" ? <Sun className="size-4" aria-hidden /> : <Moon className="size-4" aria-hidden />}
      </button>
      <AccentMenu />
    </div>
  );
}

/** Accessible menu button (role=menu + menuitemradio), styled like the rest of the app. */
function AccentMenu() {
  const { accent, setAccent } = useTheme();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const menuId = useId();
  const current = ACCENTS.find((a) => a.id === accent) ?? ACCENTS[0]!;

  // Close on a click outside (subscription to an external event: no state set in the effect body).
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  // Move focus to the selected option when the menu opens.
  useEffect(() => {
    if (open) itemRefs.current[ACCENTS.findIndex((a) => a.id === accent)]?.focus();
  }, [open, accent]);

  const choose = (id: Accent) => {
    setAccent(id);
    setOpen(false);
    buttonRef.current?.focus();
  };
  const onMenuKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const items = itemRefs.current.filter(Boolean) as HTMLButtonElement[];
    const i = items.indexOf(document.activeElement as HTMLButtonElement);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const n = items.length;
      items[(i + (e.key === "ArrowDown" ? 1 : -1) + n) % n]?.focus();
    } else if (e.key === "Home" || e.key === "End") {
      e.preventDefault();
      items[e.key === "Home" ? 0 : items.length - 1]?.focus();
    } else if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
      buttonRef.current?.focus();
    } else if (e.key === "Tab") {
      setOpen(false);
    }
  };

  return (
    <div ref={rootRef} className="relative">
      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={menuId}
        aria-label={`Colour theme: ${current.label}`}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" && !open) {
            e.preventDefault();
            setOpen(true);
          }
        }}
        className={cx(
          "flex h-9 items-center gap-2 rounded-lg border px-3 text-sm text-ink transition-colors hover:bg-sand-100",
          open ? "border-line-strong bg-sand-100" : "border-line",
        )}
      >
        <Palette className="size-4 text-ink-faint" aria-hidden />
        <span className="size-3 rounded-full ring-1 ring-black/10" style={{ background: current.swatch }} aria-hidden />
        <span>{current.label}</span>
        <ChevronDown className={cx("size-4 text-ink-faint transition-transform", open && "rotate-180")} aria-hidden />
      </button>

      {open && (
        <div
          id={menuId}
          role="menu"
          aria-label="Colour theme"
          onKeyDown={onMenuKey}
          className="absolute right-0 z-40 mt-2 w-52 rounded-xl border border-line bg-surface p-1.5 shadow-xl"
        >
          <p className="px-2.5 pb-1.5 pt-1 text-[11px] font-medium uppercase tracking-wider text-ink-faint">Colour theme</p>
          {ACCENTS.map((a, i) => {
            const selected = a.id === accent;
            return (
              <button
                key={a.id}
                ref={(el) => {
                  itemRefs.current[i] = el;
                }}
                type="button"
                role="menuitemradio"
                aria-checked={selected}
                tabIndex={selected ? 0 : -1}
                onClick={() => choose(a.id)}
                className={cx(
                  "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm outline-none transition-colors",
                  "hover:bg-sand-100 focus-visible:bg-sand-100",
                  selected ? "font-medium text-ink" : "text-ink-soft",
                )}
              >
                <span className="size-3.5 rounded-full ring-1 ring-black/10" style={{ background: a.swatch }} aria-hidden />
                <span className="flex-1">{a.label}</span>
                {selected && <Check className="size-4 text-accent-700" aria-hidden />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
