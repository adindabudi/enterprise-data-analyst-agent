import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App } from "./App";

describe("App", () => {
  it("renders the product identity with a nonblank bootstrap state", () => {
    render(<App />);
    expect(
      screen.getByRole("heading", { name: "Enterprise Data Analyst" }),
    ).toBeVisible();
    expect(screen.getByText("Private workspace")).toBeVisible();
    expect(
      screen.getByRole("progressbar", { name: "Preparing your session" }),
    ).toBeVisible();
  });
});
