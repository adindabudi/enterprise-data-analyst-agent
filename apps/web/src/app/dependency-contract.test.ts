import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const lockPath = resolve(process.cwd(), "../../package-lock.json");

describe("frontend dependency contract", () => {
  it("pins the approved Fluent and preview stack", () => {
    const lock = JSON.parse(readFileSync(lockPath, "utf8")) as {
      packages: Record<string, { version?: string } | undefined>;
    };

    expect(
      lock.packages["node_modules/@fluentui/react-components"]?.version,
    ).toBe("9.74.4");
    expect(lock.packages["node_modules/@fluentui/react-icons"]?.version).toBe(
      "2.0.333",
    );
    expect(lock.packages["node_modules/dompurify"]?.version).toBe("3.4.12");
    expect(lock.packages["node_modules/mermaid"]?.version).toBe("11.16.0");
    expect(lock.packages["node_modules/@azure/msal-browser"]).toBeUndefined();
    expect(lock.packages["node_modules/@azure/msal-react"]).toBeUndefined();
  });
});
