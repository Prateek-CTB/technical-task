"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { ScopeProvider } from "@/hooks/scope";
import { ThemeProvider } from "@/hooks/theme";
import { TourProvider } from "@/components/tour";
import { ApiError } from "@/services/api";

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            refetchOnWindowFocus: false,
            // Retry transient failures only (network / 5xx); a 4xx will not fix itself.
            retry: (count, err) => count < 2 && !(err instanceof ApiError && err.status >= 400 && err.status < 500),
          },
        },
      }),
  );
  return (
    <ThemeProvider>
      <QueryClientProvider client={client}>
        <ScopeProvider>
          <TourProvider>{children}</TourProvider>
        </ScopeProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
