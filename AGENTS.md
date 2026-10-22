# Agent Guidelines

## Working efficiently

- Read the repository instructions and check the current Git status before
  making changes. Inspect only files relevant to the task, and group independent
  reads or commands when practical.
- Use tools already identified by the task or repository. Do not search for or
  install command-line tools such as `gh` or `glab` unless the task requires
  them.
- If an additional tool not already available or identified by the task or
  repository seems necessary, explain why and ask the user before installing,
  enabling, or using it. Prefer documented repository commands and scripts.
- Keep changes within the requested scope and preserve unrelated work. Resolve
  questions from the repository when possible; ask only when an unresolved
  decision materially affects the result.
- Do not discard or overwrite existing work. Do not stage unrelated changes.

## Finishing work

- Before finishing, format changed files with the formatter configured for
  their file type. Use Ruff for Python files in this repository. Avoid
  reformatting unrelated files.
- Run the relevant checks once after formatting. Do not run the full test suite
  for documentation-only changes.
- When tests are requested or needed to verify a behavior change, prefer the
  focused tests for the affected behavior; run the full suite when the scope
  warrants it.
- End with a concise summary of the changes and checks, and mention any
  material limitation.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/) for every
commit message. Always write commit messages in English:

```text
<type>[optional scope][!]: <short description>
```

Common types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`,
`build`, `ci`, `chore`, and `revert`. Write the description in imperative mood
and lowercase. Add a scope when it clarifies which part of the project changed,
for example `fix(serial): handle disconnects`. For a breaking change, add `!`
after the type or scope and explain `BREAKING CHANGE:` in the commit body.

Separate commits by responsibility: each commit must contain changes for one
responsibility. Split changes into separate commits when they cover distinct
responsibilities.

Do not create commits or push changes unless the user asks you to.
