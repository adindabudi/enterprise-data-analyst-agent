import { describe, expect, it } from "vitest";

import { DEFAULT_BRANDING, validateBranding } from "./branding";
import { compileTheme } from "./theme";

describe("branding", () => {
  it("compiles the analyst desk into sparse Fluent overrides", () => {
    const theme = compileTheme(DEFAULT_BRANDING);

    expect(theme.fontFamilyBase).toContain("Roboto");
    expect(theme.colorBrandBackground).toBe("#0B5D52");
    expect(theme.colorNeutralBackground1).toBe("#F6F7F3");
    expect(theme.colorNeutralForeground1).toBe("#1E2421");
  });

  it("rejects low contrast and scriptable assets", () => {
    expect(() =>
      validateBranding({
        ...DEFAULT_BRANDING,
        primary: "#FFFFFF",
        surface: "#FFFFFF",
      }),
    ).toThrow("contrast");
    expect(() =>
      validateBranding({
        ...DEFAULT_BRANDING,
        logoArtifactId: "javascript:alert(1)",
      }),
    ).toThrow("logoArtifactId");
  });
});
