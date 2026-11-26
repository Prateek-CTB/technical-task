import type { Metadata } from "next";
import { Inter } from "next/font/google";
import { AppShell } from "@/components/shell";
import { DEFAULT_THEME, THEME_INIT_SCRIPT } from "@/hooks/theme";
import { Providers } from "./providers";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });

export const metadata: Metadata = {
  title: { default: "Review Intelligence · Azzurro Hotels", template: "%s · Azzurro Review Intelligence" },
  description: "Guest review performance and operational insights for Azzurro Hotels.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // data-mode / data-accent are set before paint by THEME_INIT_SCRIPT from the saved choice,
    // so they can differ from the server-rendered defaults: suppressHydrationWarning is intended.
    <html lang="en-AU" className={inter.variable} data-mode={DEFAULT_THEME.mode} data-accent={DEFAULT_THEME.accent} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="min-h-screen bg-sidebar">
        <Providers>
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  );
}
