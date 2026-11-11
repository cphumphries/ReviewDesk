// Static analysis of the pages' JavaScript: likely mistakes, and the ways a page could come to run text as code or
// markup (CONTRIBUTING.md says how to run it). ReviewDesk itself needs none of this installed; every push is checked
// the same way on GitHub (.github/workflows/analysis.yml).
import js from "@eslint/js";
import globals from "globals";
import noUnsanitized from "eslint-plugin-no-unsanitized";

export default [
  { ignores: ["dist/**", "node_modules/**"] },
  js.configs.recommended,
  noUnsanitized.configs.recommended,          // innerHTML, insertAdjacentHTML, document.write and their kin
  {
    files: ["app/reviewdesk/static/**/*.js"],
    languageOptions: { ecmaVersion: 2022, sourceType: "module", globals: { ...globals.browser } },
    rules: {
      "no-eval": "error",
      "no-implied-eval": "error",
      "no-new-func": "error",
      "no-script-url": "error",
      // a handler often takes arguments it does not use, and a caught error is often only a sign that something failed
      "no-unused-vars": ["error", { args: "none", caughtErrors: "none" }],
    },
  },
];
