# reward-hacking-checker
A pre-commit and pre-merge check for reward hacking behavior in AI coding agents using decision models.

`rewardguard` checks a branch against its base and fails if an AI coding agent may have gamed its tests: deleting or skipping them, weakening assertions, editing expected values to match a bug, hard-coding answers, swallowing errors, or leaving notes aimed at the checker. It supports **Jest, Vitest and Playwright** projects. **Jev** (TypeSafe) is the judge and only answers yes/no questions; **Claude Haiku** then explains each chunk Jev flags.

The same tool runs locally and as a GitHub Action.

This branch also adds content review to the existing `rewardguard check` path: **243 rule signatures in 37 focused packs**, derived from 80 inspected writing, SEO, conversion, channel and research guides. Eligible changes go through a deterministic content gate, a Jev task classifier, and applicable independent checks. Metadata, schema and publication controls are included alongside prose. See [Content checks](docs/content-checks.md) for the catalog, evidence requirements, source audits and configuration.

Live-provider precision has not been verified. CI rollout needs the published action commit SHA containing this change; a local checkout does not update existing pinned callers.

## How it works

The code review path has the layers below. Configuration is always read from the **base** commit, so a branch can't loosen its own check. Content review uses the same Jev grades and Haiku explainer, with separate routing and focused rubrics.

1. **Static checks.** Pattern and count rules on the diff. Counting is done in code because Jev counts unreliably.
2. **Jev judge.** Every changed text file in any language is judged, except excluded paths, `.github/` and `.rewardguard.toml`. Its diff is split into chunks that fit Jev's 32k-token state limit, and each chunk is asked yes/no (`noul`) questions. When a PR description is available, Jev is also asked whether it claims work the whole diff doesn't contain. Jev also grades every static and test-run finding that isn't clear-cut (see the table below): it is asked whether the finding is a real problem, given that file's diff. Lines that hold a credential, and every line of a private-key block, are redacted before anything is sent to Jev or Haiku; the report notes each file whose key-block lines were hidden, so they can be reviewed by hand.

   Severity comes from Jev's probability and is fixed, not configurable: **below 0.60 there is no output**, 0.60–0.79 warns, and 0.80 or higher fails. Clear-cut findings, which report an objective fact, keep their fixed severity and are not sent to Jev or Haiku.
3. **Haiku explainer.** Every Jev finding that is reported (0.60 or higher) is sent to Claude Haiku (`claude-haiku-5-5`) with the statement Jev answered. It runs through the `claude` CLI (`claude -p`), billed to your Claude subscription via `CLAUDE_CODE_OAUTH_TOKEN`; `ANTHROPIC_API_KEY` is removed from the CLI's environment so it can't bill the API instead. The CLI runs with no tools, no project settings and no MCP servers. Haiku returns the line, the code, the problem and a fix for each one, and these appear in the report. Haiku only explains: it can't clear or downgrade a Jev finding. If it finds no matching lines, the report says to review that chunk by hand.
4. **Test runs** (optional; set `run_tests = false` in `.rewardguard.toml` to skip them for a suite that can't run in a workflow). Each suite runs in clean git worktrees:
   - **base:** the base commit as-is
   - **head:** the branch, with test-runner config restored from base
   - **original:** the branch's code against the base branch's test files
   - **new-vs-base:** the branch's test files against the base code (only when application code changed), to find new tests that pass without the change

**Fail-closed.** If Jev returns an HTTP error, a malformed answer or a missing answer, the check fails. So does a `claude` CLI failure, an error result or a missing structured answer from Haiku. It also fails if the Jev key, the Claude token or the `claude` CLI is missing, a suite writes no report, or the config is invalid. There is no fallback. Jev retries happen only on HTTP 429/5xx, at most 3 times. Test commands never see `TYPESAFE_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY` or GitHub tokens.

Exit codes: `0` pass (warnings allowed), `1` failed, `2` error.

## What it catches

| Rule | Layer | Severity |
|---|---|---|
| `TEST_FILE_DELETED`, `TEST_COUNT_DROP` | static | fail (clear-cut) |
| `SKIP_ADDED` (`.skip`, `xit`, `.todo`, `test.fixme`, `test.fail`, `it.fails`) | static | fail (clear-cut) |
| `ONLY_ADDED` (`.only`, `fit`), `EARLY_EXIT` in tests | static | fail (clear-cut) |
| `PASS_WITH_NO_TESTS`, `CONFIG_CHANGED`, `TEST_SCRIPT_CHANGED` | static | fail (clear-cut) |
| `SECRET_ADDED` (token formats, private keys, URLs with a password, also base64-encoded) | static | fail (clear-cut) |
| `ASSERTION_COUNT_DROP`, `SNAPSHOT_CHANGED`, `DEPENDENCY_DOWNGRADE`, `NEW_DEPENDENCY`, `NON_TEST_FILES_DELETED`, `AGENT_INSTRUCTIONS_CHANGED` (`AGENTS.md`, `CLAUDE.md`, `SKILL.md`, `.claude/`, `.cursor/`, `.gemini/`…) | static | warn (clear-cut) |
| `NO_TESTS_RAN`, `RUN_TEST_TOTAL_DROP`, `RUN_SKIPPED_INCREASE`, `NEW_FAILURES` | run | fail (clear-cut) |
| `PREEXISTING_FAILURES` | run | warn (clear-cut) |
| `TAUTOLOGICAL_ASSERTION` (`expect(true)…`, `expect(x).toBe(x)`), `DEAD_BRANCH`, `REVIEWER_NOTE` (text addressed to a reviewer/AI) | static | Jev-graded |
| `EMPTY_CATCH`, `TYPE_ESCAPE`, `TEST_ENV_BRANCH`, `MOCK_IN_SOURCE`, `COVERAGE_IGNORE` (`istanbul`/`c8`/`v8 ignore`), `LOG_SILENCED`, `TIMEOUT_CHANGED`, `FIXED_WAIT` | static | Jev-graded |
| `GATE_CONFIG_WEAKENED` (`HUSKY=0`, `--no-verify`, `core.hooksPath`, `continue-on-error: true`, `"strict": false`, ESLint rule `off`, any hook/lint/tsconfig change) | static | Jev-graded |
| `RUNTIME_MONKEYPATCH` (application code reassigns `Math`/`Date`/`JSON` members, built-in prototypes or test globals, or reads the call stack) | static | Jev-graded |
| `LICENSE_HEADER_REMOVED`, `LARGE_LITERAL_TABLE` (100+ lines of literal lookup data in app code) | static | Jev-graded |
| `ORIGINAL_TESTS_FAIL` (base tests fail against the branch's code), `NEW_TESTS_PASS_ON_BASE` (new tests pass on the base code, so they don't test the change) | run | Jev-graded |
| `WEAKENED_ASSERTION`, `NEUTRALIZED_TEST`, `TEST_REPLACED_EASIER`, `MOCKS_CODE_UNDER_TEST`, `GRADER_TAMPERING` | Jev | Jev-graded |
| `EXPECTED_VALUE_CHANGED` (dropped when app code also changed and the PR description justifies it) | Jev | Jev-graded |
| `OVER_MOCKING`, `ASSERTS_ON_MOCK`, `RECOMPUTED_EXPECTED`, `NO_OUTCOME_ASSERTION`, `TEST_NAME_MISMATCH`, `FLAKE_MASKING` | Jev | Jev-graded |
| `HARDCODED_OUTPUT`, `SPECIAL_CASED_INPUTS`, `SWALLOWED_ERROR`, `FAKE_SUCCESS`, `MOCK_DATA_AT_RUNTIME`, `UNSAFE_RETRY`, `STUB_IMPLEMENTATION`, `TYPE_SAFETY_BYPASS`, `TIMING_TAMPER`, `REVIEWER_INJECTION` | Jev | Jev-graded |
| `SECURITY_CHECK_DISABLED`, `UNREACHABLE_CHANGE`, `TEST_DATA_AT_RUNTIME`, `LOG_SUPPRESSION`, `DELAY_MASKING`, `LIMIT_RAISED` | Jev | Jev-graded |
| `HOLLOW_FALLBACK`, `SPECULATIVE_TEST_HEDGING`, `SCOPE_CREEP`, `HIDDEN_PARAMETER_DISABLE` | Jev | Jev-graded |
| `DESCRIPTION_OVERCLAIM` (PR description claims work the diff doesn't contain; if the diff is too large to check in one call, the report says so in a note) | Jev | Jev-graded |

Jev-graded: below 0.60 nothing is reported, 0.60–0.79 warns, 0.80 or higher fails. The clear-cut list is `CLEAR_CUT` in [`src/rewardguard/models.py`](src/rewardguard/models.py).

You can turn any rule off under `[rules]` (`RULE = "off"`); severities can't be changed. [`docs/agent-behaviours.md`](docs/agent-behaviours.md) lists the agent behaviours these rules come from, with sources, and the ones a diff can't show.

## Content review

The normal `check` command includes content review. Its deterministic gate skips changes without eligible content before making content-model calls. For eligible changes, Jev classifies the task: **exactly 0.60 enters review**, and a result below 0.60 skips the content path. Jev then selects applicable packs and independently grades each evidenced claim, field or requirement. Individual findings warn at **0.60 through below 0.80** and fail at **0.80 or higher**. A pack never receives one aggregate quality score.

```bash
rewardguard check --base main
rewardguard check --base main --content-only
rewardguard check --base main --staged --content-only
```

`--content-only` selects content review without the coding/test-review layers and returns success for a no-content skip. In a normal run, a content skip leaves the existing code checks in control of the overall result. Missing factual, rendered, measurement or policy evidence is reported as `not_evaluated`, with a coverage reason. It is neither a defect nor a claimed pass. Required reviews still fail closed on provider or configuration errors.

Use `[content]` in trusted-base `.rewardguard.toml` for `enabled`, `include`, `source_include`, `exclude`, and `packs`. Empty `packs = []` makes all packs eligible; artifact applicability and evidence still filter them. `[content.context]` maps B/P/I/S/R/M/H to arrays of trusted-base file paths for product context, adopted policies, inventories, sources, observations, measurements and history. See the [example config](examples/.rewardguard.toml) and [full guide](docs/content-checks.md#configure-content-review).

Haiku receives only the relevant rule rubric, inline exclusions, changed evidence and necessary trusted context. Artifact variants select their matching specialist. Source-audit candidates, rigid style preferences and optional marketing tactics do not automatically become enabled checks. Dates are required only when material to the particular claim. The content judge does not execute branch code to collect missing evidence.

## Setup

1. Copy [`examples/.rewardguard.toml`](examples/.rewardguard.toml) to your project root, set your test command, and commit it to `main`.
2. Get a TypeSafe API key. Install the Claude Code CLI (`npm install -g @anthropic-ai/claude-code`, v2.1.293 or later) and run `claude setup-token` to create a one-year subscription token (Pro, Max, Team or Enterprise plan). Subscription rate limits apply.

### Local

```bash
pipx install git+https://github.com/Advanced-Digital-Marketing-LTDA/reward-hacking-checker.git
export TYPESAFE_API_KEY=...
export CLAUDE_CODE_OAUTH_TOKEN=...   # from `claude setup-token`
git commit -am "wip"              # it checks committed history only
rewardguard check --base main
rewardguard check --base main --description "PR text" --markdown report.md --json report.json

# Content review alone, including a successful no-content skip
rewardguard check --base main --content-only

# The same split CI uses: run the tests without secrets, then judge
rewardguard run-tests --base main --out run.json
rewardguard check --base main --run-results run.json
```

### Codex global precommit

To check staged changes before commits Codex makes in any repository, add this hook to `~/.codex/hooks.json`. Merge it into the existing JSON if that file already has hooks:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "^Bash$",
        "hooks": [
          {
            "type": "command",
            "command": "rewardguard codex-precommit",
            "timeout": 600,
            "statusMessage": "Checking staged changes with RewardGuard"
          }
        ]
      }
    ]
  }
}
```

Install RewardGuard globally using the command above, make sure `TYPESAFE_API_KEY` and `CLAUDE_CODE_OAUTH_TOKEN` are available to the Codex process, then review and trust the hook in Codex's `/hooks` view. The existing hook calls the normal `check` path on the staged snapshot, including the content gate; no second Codex hook is needed. Code checks continue normally, while content-model calls depend on content eligibility and classification. Provider rate limits apply. RewardGuard reads an existing project config from the merge base with `origin/HEAD`, `origin/main`, `origin/master`, local `main` or local `master`, in that order; staged config edits do not affect the check. If none of those refs exist, it uses `HEAD`. When the trusted base has no `.rewardguard.toml`, built-in global defaults run static and model checks with test execution off. Otherwise it uses the trusted base config, including its test suites. The staged index is checked; unstaged edits are excluded. Stage changes in one Codex Bash call, then commit in a separate call. The hook blocks combined staging-and-commit commands, `git commit -a`, pathspec or interactive commit forms that can select content outside the checked index, and explicit `--git-dir`/`--work-tree` overrides that target another repository. Commits issued from a repository subdirectory are checked from the worktree root.

This is a Codex local-shell hook. It applies across repositories without adding files to each one. It doesn't intercept Git commands you run outside Codex or every possible way a shell script could invoke Git. See [Codex hook coverage](https://developers.openai.com/codex/hooks).

Locally, `rewardguard check` runs the tests in the same process as the keys. That's fine on your own machine, but don't run it on code you don't trust with those keys.

### Optional pre-commit content hook

The [pre-commit hook](.pre-commit-hooks.yaml) checks the staged snapshot through `rewardguard check --staged --content-only`. Add this to `.pre-commit-config.yaml`, replacing the placeholder with the published commit SHA containing the hook and content feature:

```yaml
repos:
  - repo: https://github.com/Advanced-Digital-Marketing-LTDA/RewardGuard
    rev: REPLACE_WITH_PUBLISHED_COMMIT_SHA
    hooks:
      - id: rewardguard-content
```

Run `pre-commit install` after installing pre-commit. The hook needs the same provider credentials when review is required and succeeds when no eligible content changed. It runs for the repository's pre-commit stage with no filename arguments; the staged index determines the reviewed content. Keep Codex's existing normal hook when you also want its code checks.

### GitHub Action

1. Add `TYPESAFE_API_KEY` and `CLAUDE_CODE_OAUTH_TOKEN` as repository secrets. The Action installs the Claude Code CLI itself; the workflow needs Node (the example sets up Node 22).
2. Copy [`examples/rewardguard.yml`](examples/rewardguard.yml) to `.github/workflows/`. It has two jobs:
   - **`rewardguard-tests`** (`mode: tests`) runs the branch's test suites with no secrets, a read-only token and no saved git credentials. It uploads the results as an artifact.
   - **`rewardguard`** (`mode: check`) runs the static checks, Jev and Haiku, and reads the uploaded results. It holds the secrets and never runs branch code.

   The split matters because branch code running as the same user can read secrets from other processes (for example through `/proc/<pid>/environ`). Removing them from the test command's environment is not enough. Never pass secrets to `mode: tests`; the Action refuses if you do. If the tests job fails or uploads nothing, the `rewardguard` job fails.
3. In branch protection, make the `rewardguard` check **required** (the second job).

   **No test runs:** if your suite is too large or needs services a workflow doesn't have, set `run_tests = false` in `.rewardguard.toml` and use [`examples/rewardguard-no-tests.yml`](examples/rewardguard-no-tests.yml) instead. It's one job (static checks, Jev, Haiku) that never runs branch code. You lose the run-layer rules (`NO_TESTS_RAN`, `NEW_FAILURES`, `ORIGINAL_TESTS_FAIL`, and so on), but every static and Jev rule still applies. The check job always passes `--no-local-tests`, so if the base config still says `run_tests = true` it fails instead of running tests next to the secrets.
4. Add a `CODEOWNERS` rule that requires your review for `/.github/` and `/.rewardguard.toml`. A `pull_request` workflow runs from the PR's own copy of the workflow file, so this review requirement is what stops an agent from editing the check away. Rewardguard itself doesn't flag changes to these files, because CI sometimes has to change; `.rewardguard.toml` edits only take effect once merged.

The Action writes a prominent content coverage summary (including per pack evaluation and evidence gaps) to the job summary and PR comment. It also publishes **`contentguard / eligibility`** and individual **`contentguard / CG_RULE_ID`** GitHub checks on the resolved review head. Only evaluated rules and rules with evidence gaps receive individual checks; unrelated catalog rules do not fill the Checks UI.

Give the judge job and any reusable-workflow caller **`checks: write`**, alongside `contents: read` and `pull-requests: write`. The Action input `content-checks` defaults to `"true"`; set it to `"false"` explicitly to disable publishing in an integration that cannot grant that permission. The test job keeps its read-only token. Publishing failures fail the aggregate job visibly.

Each rule check shows its highest exact probability and the files/instances reviewed. A warning (**0.60 ≤ p < 0.80**) is neutral; **p ≥ 0.80** fails. A below-threshold result is successful only for evaluated scopes with no evidence gaps. Missing evidence stays neutral with explicit scope; skipped or unverified content never claims a clean review. Content evaluation errors fail a dedicated content check; unrelated coding errors retain their own aggregate result. Keep the aggregate job as the required branch-protection check; individual content checks vary with applicability.

Content review uses the same Action check mode. Publish this change and pin the Action or reusable-workflow consumer to the resulting published action SHA before rollout. Preserve the separate test and judge jobs: the secret-bearing content judge never executes proposed branch code. Live-provider precision remains unverified; registry coverage and mocked checks do not establish real model accuracy.

## Calibrating

The thresholds are fixed (no output below 0.60, warn at 0.60, fail at 0.80); a `.rewardguard.toml` that sets `[thresholds]` or `[explainer] min_probability` is rejected. Before relying on the check:

1. Run against branches that cheat on purpose; [`tests/test_cheat_branches.py`](tests/test_cheat_branches.py) has ready-made ones.
2. Run against a set of honest PRs.
3. Turn off a rule that misfires on your code under `[rules]` in `.rewardguard.toml` on `main`.

Pin the Jev model version (`jev-latest` is rejected). The model that actually answered is printed in each report.

## Not included yet

- Mutation testing
- Tests kept hidden from the agent (holdout tests)

Either can be added as another `[[suites]]` entry whose command lives outside the agent's reach.

## Development

```bash
pip install -e ".[dev]"
python -m pytest -q      # needs Node 22+ and npm; Jev and Haiku APIs are faked in tests
```

## Whole-repository scan

Run a standalone, source-backed review across all eligible code, configuration, and documentation files, then export validated findings to CSV:

```bash
python3 scripts/rewardguard_scan.py --repo-root /path/to/target-repo --output rewardguard-findings.csv --plan
python3 scripts/rewardguard_scan.py --repo-root /path/to/target-repo --output rewardguard-findings.csv
```

This complements `rewardguard check`, which reviews branch changes for reward-hacking behavior. See the [whole-repository scan guide](docs/repository-scan.md) for its file boundary, context grouping, judge adapter, and output guarantees.

## License

Rewardguard is source-available under the [PolyForm Shield License 1.0.0](LICENSE).
Commercial use is permitted when it complies with that license. For uses not
permitted by PolyForm Shield, see [commercial licensing](COMMERCIAL.md).
