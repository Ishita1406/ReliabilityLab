import Link from "next/link";

export const NAV_ITEMS = [
  { href: "/", label: "Overview" },
  { href: "/benchmarks", label: "Benchmarks" },
  { href: "/experiments", label: "Experiments" },
  { href: "/compare", label: "Compare" },
  { href: "/review", label: "Review" },
] as const;

export function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return (
    pathname === href ||
    pathname.startsWith(`${href}/`) ||
    (href === "/experiments" && pathname.startsWith("/runs/"))
  );
}

/** Main navigation; `pathname` null (static shell, before the URL is known) marks nothing current. */
export function NavLinks({ pathname }: { pathname: string | null }) {
  return (
    <nav aria-label="Main">
      <ul className="flex flex-wrap gap-1">
        {NAV_ITEMS.map((item) => {
          const active = pathname !== null && isActive(pathname, item.href);
          return (
            <li key={item.href}>
              <Link
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={`block rounded px-3 py-1.5 text-sm font-medium focus-visible:outline-2 focus-visible:outline-blue-600 ${
                  active
                    ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900"
                    : "text-zinc-700 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800"
                }`}
              >
                {item.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
