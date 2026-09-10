// ESLint 9 flat config.
//
// Without this file `next lint` drops into an interactive setup prompt, which
// means `make check` could never pass unattended — it hung CI rather than
// failing it, which is the more annoying of the two.
//
// eslint-config-next is still published as eslintrc-style, so it is loaded
// through FlatCompat rather than imported directly.
import { dirname } from "path";
import { fileURLToPath } from "url";
import { FlatCompat } from "@eslint/eslintrc";

const compat = new FlatCompat({
  baseDirectory: dirname(fileURLToPath(import.meta.url)),
});

export default [
  {
    ignores: [".next/**", "node_modules/**", "next-env.d.ts"],
  },
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    rules: {
      // The API client mirrors a generated OpenAPI schema whose payloads are
      // genuinely open-ended; a warning keeps them visible without blocking.
      "@typescript-eslint/no-explicit-any": "warn",
      // Prefix an intentionally unused binding with _ rather than deleting it,
      // which matters for destructured event payloads.
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
];
