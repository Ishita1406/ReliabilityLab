import { execFileSync } from "node:child_process";
import path from "node:path";

import { E2E_DATABASE_URL } from "../playwright.config";

/** Recreate and seed the e2e database before the tests run. */
export default function globalSetup() {
  const backend = path.resolve(__dirname, "../../backend");
  const output = execFileSync(
    "uv",
    ["run", "python", "scripts/prepare_e2e_db.py", E2E_DATABASE_URL],
    {
      cwd: backend,
      env: { ...process.env, PATH: `${process.env.HOME}/.local/bin:${process.env.PATH}` },
      encoding: "utf-8",
    },
  );
  // Fail fast if seeding did not report the four demo experiments.
  const seeded = JSON.parse(output.trim().split("\n").at(-1) ?? "{}") as Record<string, string>;
  for (const label of ["baseline", "no-retry", "single-source", "pending"]) {
    if (!seeded[label]?.startsWith("exp_")) throw new Error(`seeding did not create '${label}'`);
  }
}
