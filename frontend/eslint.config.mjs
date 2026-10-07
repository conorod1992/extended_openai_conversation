// Deliberately narrow bug-catching rules for the shipped plain-JavaScript frontend.
// Styling, naming and TypeScript migration are intentionally out of scope.
export default [{
  files: ["../custom_components/extended_openai_conversation_responses/frontend/*.js"],
  languageOptions: {ecmaVersion: 2022, sourceType: "module"},
  rules: {
    "no-dupe-args": "error",
    "no-duplicate-case": "error",
    "no-unreachable": "error",
    "no-unsafe-finally": "error",
    "no-constant-binary-expression": "error",
    "no-self-assign": "error",
    "no-sparse-arrays": "error"
  }
}];
