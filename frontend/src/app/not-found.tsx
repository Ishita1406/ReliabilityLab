import Link from "next/link";

export default function NotFound() {
  return (
    <div className="flex flex-col gap-2">
      <h1 className="text-2xl font-semibold">Page not found</h1>
      <Link href="/" className="text-blue-700 underline dark:text-blue-400">
        Back to the overview
      </Link>
    </div>
  );
}
