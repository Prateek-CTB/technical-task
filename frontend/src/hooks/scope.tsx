"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "@/services/api";

interface ScopeState {
  /** True when synthetic demonstration data is being shown. Real data is the default. */
  demo: boolean;
  ready: boolean;
  /** The coverage request itself failed (backend unreachable). */
  failed: boolean;
  hasReal: boolean;
  hasDemo: boolean;
  setDemo: (v: boolean) => void;
}

const Ctx = createContext<ScopeState>({
  demo: false, ready: false, failed: false, hasReal: false, hasDemo: false, setDemo: () => {},
});
export const useScope = () => useContext(Ctx);

const KEY = "reviewhub.demo";
const NON_REAL = new Set(["demo_synthetic", "booking_experimental"]);

/**
 * Decides which dataset the dashboard shows. Real data is the default; if the database holds only
 * synthetic data, demo mode switches on automatically and every screen carries a DEMO banner.
 */
export function ScopeProvider({ children }: { children: ReactNode }) {
  const [demo, setDemoState] = useState(false);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const [info, setInfo] = useState({ hasReal: false, hasDemo: false });

  useEffect(() => {
    const ctl = new AbortController();
    api
      .coverage({ include_demo: false })
      .then(({ meta }) => {
        const hasDemo = meta.dataset.contains_demo;
        const hasReal = Object.keys(meta.dataset.sources).some((s) => !NON_REAL.has(s));
        let stored: string | null = null;
        try {
          stored = localStorage.getItem(KEY);
        } catch {
          /* storage unavailable */
        }
        setInfo({ hasReal, hasDemo });
        setDemoState(hasDemo && (!hasReal || stored === "1"));
      })
      .catch(() => setFailed(true))
      .finally(() => setReady(true));
    return () => ctl.abort();
  }, []);

  const setDemo = useCallback((v: boolean) => {
    try {
      localStorage.setItem(KEY, v ? "1" : "0");
    } catch {
      /* ignore */
    }
    setDemoState(v);
  }, []);

  const value = useMemo(() => ({ demo, ready, failed, ...info, setDemo }), [demo, ready, failed, info, setDemo]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
