"use client";

/** Last-resort boundary for unexpected render errors (API failures are handled inline). */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div
      role="alert"
      className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-900 dark:border-red-800 dark:bg-red-950 dark:text-red-200"
    >
      <p className="font-semibold">Something went wrong while rendering this page.</p>
      <p className="mt-1">{error.digest ? `Reference: ${error.digest}` : error.message}</p>
      <button
        type="button"
        onClick={reset}
        className="mt-3 rounded border border-red-400 px-3 py-1"
      >
        Try again
      </button>
    </div>
  );
}
