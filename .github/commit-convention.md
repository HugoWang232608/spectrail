# Commit Convention

Commit messages must follow:

`type(scope): subject`

## Allowed types

- `feat`: add a new capability or user-visible behavior
- `fix`: correct behavior that does not match existing expectations
- `refactor`: restructure implementation without changing behavior
- `test`: add or update tests, fixtures, mocks, or test utilities
- `docs`: update documentation only
- `perf`: improve runtime performance
- `build`: update dependencies, packaging, or build configuration
- `ci`: update continuous integration or delivery workflows
- `style`: change formatting without changing behavior
- `chore`: perform repository maintenance not covered above
- `revert`: revert a previous commit

## Scope

Use the primary affected module as scope.

Recommended scopes:

`extractor`, `parser`, `locator`, `evidence`, `validator`,
`pipeline`, `review`, `reqir`, `schema`, `model`, `llm`,
`prompt`, `recorded`, `live`, `transport`, `markdown`, `docx`,
`pdf`, `export`, `api`, `cli`, `ui`, `frontend`, `backend`,
`tests`, `fixtures`, `samples`, `docs`, `deps`, `actions`,
`release`, `config`, `repo`.

## Subject

- Use English.
- Use an action-oriented description.
- Keep the subject under 72 characters where practical.
- Do not end with a period.
- Avoid vague verbs such as `enhance`, `improve`, `update`, or
  `optimize` unless the specific effect is stated.
- Describe only the changes included in the commit.
- Do not include author names.
- Do not claim tests passed unless the commit is specifically about
  test or CI behavior.

## Breaking changes

Use `!` after the type or scope:

`feat(reqir)!: replace source block references with locators`

Add a footer:

`BREAKING CHANGE: describe the incompatible behavior here.`