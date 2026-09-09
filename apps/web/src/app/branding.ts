export type Branding = {
  productName: string;
  primary: string;
  accent: string;
  surface: string;
  foreground: string;
  logoArtifactId?: string;
};

export const DEFAULT_BRANDING: Branding = {
  productName: "Enterprise Data Analyst",
  primary: "#0B5D52",
  accent: "#D85B3F",
  surface: "#F6F7F3",
  foreground: "#1E2421",
};

const HEX = /^#[0-9A-F]{6}$/i;
const LOGO_ARTIFACT = /^artifact-[A-Za-z0-9_-]{8,}$/;

export function validateBranding(value: Branding): Branding {
  if (
    !value.productName.trim() ||
    ![value.primary, value.accent, value.surface, value.foreground].every(
      (color) => HEX.test(color),
    )
  ) {
    throw new Error("branding colors and productName are required");
  }
  if (
    contrastRatio(value.surface, value.foreground) < 4.5 ||
    contrastRatio(value.primary, "#FFFFFF") < 4.5
  ) {
    throw new Error("branding contrast is insufficient");
  }
  if (
    value.logoArtifactId !== undefined &&
    !LOGO_ARTIFACT.test(value.logoArtifactId)
  ) {
    throw new Error("logoArtifactId is invalid");
  }
  return value;
}

function contrastRatio(left: string, right: string): number {
  const luminance = (color: string): number => {
    const [red, green, blue] = [1, 3, 5].map(
      (offset) => Number.parseInt(color.slice(offset, offset + 2), 16) / 255,
    ) as [number, number, number];
    const linear = (part: number): number =>
      part <= 0.03928 ? part / 12.92 : ((part + 0.055) / 1.055) ** 2.4;
    return (
      0.2126 * linear(red) + 0.7152 * linear(green) + 0.0722 * linear(blue)
    );
  };
  const first = luminance(left);
  const second = luminance(right);
  return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
}
