"use client";

import { useId, useMemo, useState } from "react";

import type { BenchmarkCase, BenchmarkSuite, Scenario } from "@/lib/api/types";
import { humanize } from "@/lib/format";

import { Badge, EmptyState } from "./ui";

export type CaseSort = "id" | "title" | "difficulty" | "scenarios";

const DIFFICULTY_ORDER = { easy: 0, medium: 1, hard: 2 } as const;

export interface CaseFilters {
  query: string;
  tag: string;
  difficulty: string;
  outcome: string;
  sort: CaseSort;
}

export const NO_FILTERS: CaseFilters = {
  query: "",
  tag: "",
  difficulty: "",
  outcome: "",
  sort: "id",
};

export function filterCases(
  suite: BenchmarkSuite,
  filters: CaseFilters,
): { case: BenchmarkCase; scenarios: Scenario[] }[] {
  const query = filters.query.trim().toLowerCase();
  const rows = suite.cases
    .filter((c) => {
      if (filters.tag && !c.tags.includes(filters.tag)) return false;
      if (filters.difficulty && c.difficulty !== filters.difficulty) return false;
      if (filters.outcome && c.expectation.expected_behavior.outcome !== filters.outcome)
        return false;
      if (!query) return true;
      return [c.id, c.title, c.task_description, c.inputs.prompt]
        .join("\n")
        .toLowerCase()
        .includes(query);
    })
    .map((c) => ({ case: c, scenarios: suite.scenarios.filter((s) => s.case_id === c.id) }));
  const compare: Record<CaseSort, (a: (typeof rows)[0], b: (typeof rows)[0]) => number> = {
    id: (a, b) => a.case.id.localeCompare(b.case.id),
    title: (a, b) => a.case.title.localeCompare(b.case.title),
    difficulty: (a, b) =>
      DIFFICULTY_ORDER[a.case.difficulty] - DIFFICULTY_ORDER[b.case.difficulty] ||
      a.case.id.localeCompare(b.case.id),
    scenarios: (a, b) =>
      b.scenarios.length - a.scenarios.length || a.case.id.localeCompare(b.case.id),
  };
  return rows.sort(compare[filters.sort]);
}

const field =
  "rounded border border-zinc-300 bg-white px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-950";

function CaseDetails({
  benchmarkCase,
  scenarios,
}: {
  benchmarkCase: BenchmarkCase;
  scenarios: Scenario[];
}) {
  const expected = benchmarkCase.expectation.expected_behavior;
  return (
    <div className="mt-3 flex flex-col gap-4 text-sm">
      <div>
        <h4 className="font-semibold">Prompt</h4>
        <p className="mt-1 whitespace-pre-wrap">{benchmarkCase.inputs.prompt}</p>
      </div>
      <div>
        <h4 className="font-semibold">Expected outcome</h4>
        <p className="mt-1">
          <Badge>{humanize(expected.outcome)}</Badge> {expected.description}
        </p>
        {expected.reference_answer ? (
          <p className="mt-1">Reference answer: {expected.reference_answer}</p>
        ) : null}
        <p className="mt-1 text-zinc-600 dark:text-zinc-400">
          Pass threshold {benchmarkCase.expectation.scoring.pass_threshold}
        </p>
      </div>
      <div>
        <h4 className="font-semibold">
          Criteria ({benchmarkCase.expectation.evaluation_criteria.length})
        </h4>
        <ul className="mt-1 flex flex-col gap-1">
          {benchmarkCase.expectation.evaluation_criteria.map((criterion) => (
            <li key={criterion.id}>
              <code className="text-xs">{criterion.id}</code>{" "}
              <Badge>{humanize(criterion.type)}</Badge>{" "}
              {criterion.required ? <Badge tone="warn">required</Badge> : null}{" "}
              {criterion.description}{" "}
              <span className="text-zinc-500">(weight {criterion.weight})</span>
            </li>
          ))}
        </ul>
      </div>
      <div>
        <h4 className="font-semibold">Scenarios ({scenarios.length})</h4>
        <ul className="mt-1 flex flex-col gap-1">
          {scenarios.map((scenario) => (
            <li key={scenario.id}>
              <code className="text-xs">{scenario.id}</code>{" "}
              <Badge>{humanize(scenario.kind)}</Badge> {scenario.description}
              {scenario.faults.length ? (
                <span className="text-zinc-500">
                  {" "}
                  · faults: {scenario.faults.map((f) => humanize(f.type)).join(", ")}
                </span>
              ) : null}
              {scenario.expectation_override ? (
                <span className="text-zinc-500">
                  {" "}
                  · expects {humanize(scenario.expectation_override.expected_behavior.outcome)}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export function CaseExplorer({ suite }: { suite: BenchmarkSuite }) {
  const [filters, setFilters] = useState<CaseFilters>(NO_FILTERS);
  const id = useId();
  const tags = useMemo(() => [...new Set(suite.cases.flatMap((c) => c.tags))].sort(), [suite]);
  const outcomes = useMemo(
    () => [...new Set(suite.cases.map((c) => c.expectation.expected_behavior.outcome))].sort(),
    [suite],
  );
  const rows = useMemo(() => filterCases(suite, filters), [suite, filters]);
  const update = (patch: Partial<CaseFilters>) =>
    setFilters((current) => ({ ...current, ...patch }));

  return (
    <div>
      <form
        role="search"
        aria-label="Filter cases"
        className="mb-4 flex flex-wrap items-end gap-3"
        onSubmit={(event) => event.preventDefault()}
      >
        <label className="flex flex-col gap-1 text-sm">
          Search
          <input
            type="search"
            className={field}
            value={filters.query}
            onChange={(e) => update({ query: e.target.value })}
            placeholder="id, title, prompt…"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Tag
          <select
            className={field}
            value={filters.tag}
            onChange={(e) => update({ tag: e.target.value })}
          >
            <option value="">All tags</option>
            {tags.map((tag) => (
              <option key={tag} value={tag}>
                {tag}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Difficulty
          <select
            className={field}
            value={filters.difficulty}
            onChange={(e) => update({ difficulty: e.target.value })}
          >
            <option value="">All</option>
            <option value="easy">easy</option>
            <option value="medium">medium</option>
            <option value="hard">hard</option>
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Expected outcome
          <select
            className={field}
            value={filters.outcome}
            onChange={(e) => update({ outcome: e.target.value })}
          >
            <option value="">All</option>
            {outcomes.map((outcome) => (
              <option key={outcome} value={outcome}>
                {humanize(outcome)}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Sort by
          <select
            className={field}
            value={filters.sort}
            onChange={(e) => update({ sort: e.target.value as CaseSort })}
          >
            <option value="id">ID</option>
            <option value="title">Title</option>
            <option value="difficulty">Difficulty</option>
            <option value="scenarios">Most scenarios</option>
          </select>
        </label>
        <button
          type="button"
          className="rounded border border-zinc-300 px-3 py-1.5 text-sm hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
          onClick={() => setFilters(NO_FILTERS)}
        >
          Reset
        </button>
      </form>
      <p id={`${id}-count`} role="status" className="mb-2 text-sm text-zinc-600 dark:text-zinc-400">
        Showing {rows.length} of {suite.cases.length} cases
      </p>
      {rows.length === 0 ? (
        <EmptyState title="No cases match these filters" />
      ) : (
        <ul
          aria-label="Benchmark cases"
          className="flex flex-col gap-3"
          aria-describedby={`${id}-count`}
        >
          {rows.map(({ case: benchmarkCase, scenarios }) => (
            <li
              key={benchmarkCase.id}
              className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800"
            >
              <details>
                <summary className="cursor-pointer list-none focus-visible:outline-2 focus-visible:outline-blue-600">
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold">{benchmarkCase.title}</span>
                    <code className="text-xs text-zinc-500">{benchmarkCase.id}</code>
                    <Badge>{benchmarkCase.difficulty}</Badge>
                    <Badge>
                      expects {humanize(benchmarkCase.expectation.expected_behavior.outcome)}
                    </Badge>
                    <span className="text-xs text-zinc-500">{scenarios.length} scenarios</span>
                    {benchmarkCase.tags.map((tag) => (
                      <span key={tag} className="text-xs text-zinc-500">
                        #{tag}
                      </span>
                    ))}
                  </span>
                  <span className="mt-1 block text-sm text-zinc-600 dark:text-zinc-400">
                    {benchmarkCase.task_description}
                  </span>
                </summary>
                <CaseDetails benchmarkCase={benchmarkCase} scenarios={scenarios} />
              </details>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
