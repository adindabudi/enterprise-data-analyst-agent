import { webLightTheme, type Theme } from "@fluentui/react-components";

import { validateBranding, type Branding } from "./branding";

export function compileTheme(branding: Branding): Theme {
  const value = validateBranding(branding);
  return {
    ...webLightTheme,
    fontFamilyBase: '"Roboto", sans-serif',
    colorBrandBackground: value.primary,
    colorBrandForeground1: value.primary,
    colorNeutralBackground1: value.surface,
    colorNeutralForeground1: value.foreground,
  };
}
