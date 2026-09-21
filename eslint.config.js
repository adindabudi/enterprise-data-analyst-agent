import eslint from "@eslint/js";
import tseslint from "typescript-eslint";

const typeCheckedConfigs = tseslint.configs.strictTypeChecked.map((config) => ({
  ...config,
  files: ["**/*.{ts,tsx}"],
}));

export default tseslint.config(
  {
    ignores: [
      ".venv/**",
      "**/dist/**",
      "**/node_modules/**",
      "**/src/generated/**",
      "apps/api/static/**",
      "services/sandbox/vendor/**",
    ],
  },
  eslint.configs.recommended,
  ...typeCheckedConfigs,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      "@typescript-eslint/consistent-type-imports": "error",
      "@typescript-eslint/no-floating-promises": "error",
    },
  },
);
