"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

/**
 * Periodic polling instead of real-time infrastructure: while `active`, re-render the route
 * (server components refetch from the API) every `intervalSeconds`. Pauses while the tab is
 * hidden and stops by itself once the page renders with `active` false (job finished).
 */
export function AutoRefresh({
  active,
  intervalSeconds = 2,
  label,
}: {
  active: boolean;
  intervalSeconds?: number;
  label: string;
}) {
  const router = useRouter();
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    if (!active) return;
    const onVisibility = () => setPaused(document.hidden);
    document.addEventListener("visibilitychange", onVisibility);
    const timer = window.setInterval(() => {
      if (!document.hidden) router.refresh();
    }, intervalSeconds * 1000);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [active, intervalSeconds, router]);

  if (!active) return null;
  return (
    <p className="text-xs text-zinc-600 dark:text-zinc-400">
      {paused
        ? `${label}: paused while this tab is hidden`
        : `${label}: refreshing every ${intervalSeconds} s`}
    </p>
  );
}
