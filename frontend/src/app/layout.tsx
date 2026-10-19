import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import "./globals.css";

import { NavLinks } from "@/components/nav-links";
import { SiteNav } from "@/components/site-nav";

export const metadata: Metadata = {
  title: { default: "AI Reliability Lab", template: "%s · AI Reliability Lab" },
  description: "Reliability testing platform for AI agents.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="flex min-h-full flex-col">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-10 focus:rounded focus:bg-white focus:px-3 focus:py-2 focus:text-sm focus:shadow dark:focus:bg-zinc-900"
        >
          Skip to content
        </a>
        <header className="border-b border-zinc-200 dark:border-zinc-800">
          <div className="mx-auto flex w-full max-w-7xl flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-6">
            <Link href="/" className="text-base font-semibold tracking-tight">
              AI Reliability Lab
            </Link>
            <Suspense fallback={<NavLinks pathname={null} />}>
              <SiteNav />
            </Suspense>
          </div>
        </header>
        <main id="main" className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6">
          {children}
        </main>
        <footer className="border-t border-zinc-200 px-4 py-3 text-xs text-zinc-500 sm:px-6 dark:border-zinc-800 dark:text-zinc-400">
          Local development tool: no authentication. Anyone who can reach this server can read data
          and submit reviews.
        </footer>
      </body>
    </html>
  );
}
