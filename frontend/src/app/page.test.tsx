import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import HomePage from "./page";

describe("HomePage", () => {
  it("renders the product heading inside the main landmark", () => {
    render(<HomePage />);

    const main = screen.getByRole("main");
    const heading = screen.getByRole("heading", { level: 1, name: "AI Reliability Lab" });

    expect(main).toContainElement(heading);
  });
});
