"use client";

import { usePathname } from "next/navigation";

import { NavLinks } from "./nav-links";

/** Reads the URL, so the layout renders it inside <Suspense> (Cache Components). */
export function SiteNav() {
  return <NavLinks pathname={usePathname()} />;
}
