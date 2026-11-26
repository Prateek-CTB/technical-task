"use client";

import { createContext, useContext, useMemo, useSyncExternalStore, type ReactNode } from "react";

/**
 * Theme = mode (dark | light) x accent preset. Applied as data attributes on <html>; all colours
 * live in globals.css. The choice is remembered per browser (localStorage) and applied before the
 * first paint by THEME_INIT_SCRIPT, so there is no flash of the wrong theme.
 */
export type Mode = "dark" | "light";
export type Accent = "lime" | "blue" | "violet" | "amber" | "teal" | "neutral";

export const ACCENTS: { id: Accent; label: string; swatch: string }[] = [
  { id: "lime", label: "Light Green", swatch: "#b4f03c" },
  { id: "blue", label: "Blue", swatch: "#3b82f6" },
  { id: "violet", label: "Violet", swatch: "#8b5cf6" },
  { id: "amber", label: "Amber", swatch: "#f59e0b" },
  { id: "teal", label: "Teal", swatch: "#14b8a6" },
  { id: "neutral", label: "Neutral", swatch: "#a1a1aa" },
];
const MODES: Mode[] = ["dark", "light"];
export const DEFAULT_THEME: { mode: Mode; accent: Accent } = { mode: "dark", accent: "lime" };
export const THEME_KEY = "reviewhub.theme";

/** Runs in <head> before React: applies the saved theme so the first paint is already correct. */
export const THEME_INIT_SCRIPT = `(function(){var d=document.documentElement,t={};try{t=JSON.parse(localStorage.getItem(${JSON.stringify(
  THEME_KEY,
)})||"{}")}catch(e){}d.setAttribute("data-mode",${JSON.stringify(MODES)}.indexOf(t.mode)>=0?t.mode:"${DEFAULT_THEME.mode}");d.setAttribute("data-accent",${JSON.stringify(
  ACCENTS.map((a) => a.id),
)}.indexOf(t.accent)>=0?t.accent:"${DEFAULT_THEME.accent}");})();`;

interface ThemeState {
  mode: Mode;
  accent: Accent;
  setMode: (m: Mode) => void;
  setAccent: (a: Accent) => void;
}
const Ctx = createContext<ThemeState>({ ...DEFAULT_THEME, setMode: () => {}, setAccent: () => {} });
export const useTheme = () => useContext(Ctx);

/** The data attributes on <html> are the single source of truth; React subscribes to them. */
function subscribe(onChange: () => void): () => void {
  const obs = new MutationObserver(onChange);
  obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-mode", "data-accent"] });
  return () => obs.disconnect();
}
function snapshot(): string {
  const d = document.documentElement;
  return `${d.getAttribute("data-mode") ?? ""}|${d.getAttribute("data-accent") ?? ""}`;
}
const serverSnapshot = () => `${DEFAULT_THEME.mode}|${DEFAULT_THEME.accent}`;

function parse(snap: string): { mode: Mode; accent: Accent } {
  const [m, a] = snap.split("|");
  return {
    mode: MODES.includes(m as Mode) ? (m as Mode) : DEFAULT_THEME.mode,
    accent: ACCENTS.some((x) => x.id === a) ? (a as Accent) : DEFAULT_THEME.accent,
  };
}

function apply(next: { mode: Mode; accent: Accent }): void {
  const d = document.documentElement;
  d.setAttribute("data-mode", next.mode);
  d.setAttribute("data-accent", next.accent);
  try {
    localStorage.setItem(THEME_KEY, JSON.stringify(next));
  } catch {
    /* storage unavailable: the choice lasts for this page only */
  }
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const snap = useSyncExternalStore(subscribe, snapshot, serverSnapshot);
  const value = useMemo(() => {
    const theme = parse(snap);
    return {
      ...theme,
      setMode: (mode: Mode) => apply({ ...theme, mode }),
      setAccent: (accent: Accent) => apply({ ...theme, accent }),
    };
  }, [snap]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
