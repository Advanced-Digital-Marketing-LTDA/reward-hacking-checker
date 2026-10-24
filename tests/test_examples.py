"""The example workflows are copied into every protected project, so their safety properties are
pinned here: branch code never runs next to the secrets, and the check job reads the PR head."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).parent.parent
HEAD_SHA = "${{ github.event.pull_request.head.sha || github.sha }}"
EVENT_BASE = "${{ github.event.pull_request.base.sha || github.event.before }}"
RESOLVED_BASE = "${{ needs.rewardguard-base.outputs.base }}"
RESOLVED_HEAD = "${{ needs.rewardguard-base.outputs.head }}"
SECRETS = {
    "typesafe-api-key": "${{ secrets.TYPESAFE_API_KEY }}",
    "claude-code-oauth-token": "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}",
}


def _load(rel: str) -> dict:
    return yaml.safe_load((ROOT / rel).read_text())


def _triggers(wf: dict) -> dict:
    # YAML 1.1 reads the bare key `on` as the boolean True
    return wf.get("on", wf.get(True)) or {}


def _step(job: dict, uses: str) -> dict:
    steps = [s for s in job["steps"] if uses in s.get("uses", "")]
    assert len(steps) == 1, f"expected one step using {uses}"
    return steps[0]


def _guard(job: dict) -> dict:
    return _step(job, "Advanced-Digital-Marketing-LTDA/RewardGuard@")


def test_no_tests_workflow():
    wf = _load("examples/rewardguard-no-tests.yml")
    assert "pull_request" in _triggers(wf)
    assert "push" in _triggers(wf)
    assert list(wf["jobs"]) == ["rewardguard-base", "rewardguard"]
    job = wf["jobs"]["rewardguard"]
    assert job["permissions"] == {"contents": "read", "pull-requests": "write", "checks": "write"}

    checkout = _step(job, "actions/checkout")["with"]
    assert checkout["ref"] == RESOLVED_HEAD
    assert checkout["fetch-depth"] == 0
    assert checkout["persist-credentials"] is False

    guard = _guard(job)["with"]
    assert guard["mode"] == "check"
    assert guard["tests"] == "false"
    assert guard["base"] == RESOLVED_BASE
    assert guard["comment"] == "${{ github.event_name == 'pull_request' }}"
    assert {k: guard[k] for k in SECRETS} == SECRETS


def test_two_job_workflow_keeps_secrets_away_from_branch_code():
    wf = _load("examples/rewardguard.yml")
    assert "pull_request" in _triggers(wf)
    assert "push" in _triggers(wf)
    tests_job, check_job = wf["jobs"]["rewardguard-tests"], wf["jobs"]["rewardguard"]

    # tests job runs branch code: no secrets, read-only token, no saved git credentials
    assert tests_job["permissions"] == {"contents": "read"}
    assert "secrets." not in yaml.safe_dump(tests_job)
    checkout = _step(tests_job, "actions/checkout")["with"]
    assert checkout["ref"] == RESOLVED_HEAD
    assert checkout["persist-credentials"] is False
    assert _guard(tests_job)["with"]["mode"] == "tests"
    assert _guard(tests_job)["with"]["base"] == RESOLVED_BASE

    # check job holds the secrets and must still report when the tests job fails
    assert check_job["needs"] == ["rewardguard-base", "rewardguard-tests"]
    assert "!cancelled()" in check_job["if"]
    assert check_job["permissions"] == {"contents": "read", "pull-requests": "write", "checks": "write"}
    assert _step(check_job, "actions/checkout")["with"]["ref"] == RESOLVED_HEAD
    assert _step(check_job, "actions/checkout")["with"]["persist-credentials"] is False
    guard = _guard(check_job)["with"]
    assert guard["mode"] == "check"
    assert guard["base"] == RESOLVED_BASE
    assert {k: guard[k] for k in SECRETS} == SECRETS


@pytest.mark.parametrize("rel", ["examples/rewardguard.yml", "examples/rewardguard-no-tests.yml"])
def test_examples_only_use_declared_action_inputs(rel):
    declared = set(_load("action.yml")["inputs"])
    assert {"mode", "tests", *SECRETS} <= declared
    for job in _load(rel)["jobs"].values():
        guards = [s for s in job["steps"] if "Advanced-Digital-Marketing-LTDA/RewardGuard@" in s.get("uses", "")]
        if not guards:
            continue
        used = set(guards[0]["with"])
        assert used <= declared, used - declared


@pytest.mark.parametrize("rel", ["examples/rewardguard.yml", "examples/rewardguard-no-tests.yml"])
def test_examples_resolve_immutable_commits_without_branch_code_or_secrets(rel):
    wf = _load(rel)
    baseline = wf["jobs"]["rewardguard-base"]
    assert baseline["permissions"] == {"contents": "read"}
    assert "secrets." not in yaml.safe_dump(baseline)
    checkout = _step(baseline, "actions/checkout")["with"]
    assert checkout["ref"] == HEAD_SHA
    assert checkout["persist-credentials"] is False
    resolver = next(s for s in baseline["steps"] if s.get("id") == "resolve")
    assert resolver["env"]["BASE_INPUT"] == EVENT_BASE
    for step in baseline["steps"]:
        assert "run" not in step or step is resolver
    assert baseline["outputs"] == {
        "base": "${{ steps.resolve.outputs.ref }}",
        "head": "${{ steps.resolve.outputs.head }}",
    }


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def action_repo(tmp_path):
    origin, repo = tmp_path / "origin.git", tmp_path / "repo"
    origin.mkdir()
    _git(origin, "init", "--bare", "-q")
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "Workflow test")
    _git(repo, "config", "user.email", "workflow@example.test")
    _git(repo, "remote", "add", "origin", str(origin))
    (repo / "file").write_text("base\n")
    _git(repo, "add", "file")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "push", "-qu", "origin", "main")
    _git(repo, "branch", "local-base")
    _git(repo, "checkout", "-qb", "feature")
    (repo / "file").write_text("head\n")
    _git(repo, "commit", "-qam", "head")
    return repo, base, _git(repo, "rev-parse", "HEAD")


RESOLVERS = ["action.yml", "examples/rewardguard.yml", "examples/rewardguard-no-tests.yml"]


def _resolve(rel: str, repo: Path, tmp_path: Path, **env):
    document = _load(rel)
    steps = document["runs"]["steps"] if rel == "action.yml" else document["jobs"]["rewardguard-base"]["steps"]
    script = next(s["run"] for s in steps if s.get("id") in {"base", "resolve"})
    output = tmp_path / "output"
    output.write_text("")
    defaults = dict.fromkeys(["BASE_INPUT", "BASE_REF", "PR_BASE_SHA", "PUSH_BEFORE", "EVENT_NAME",
                              "DEFAULT_BRANCH", "EVENT_REF", "EXPECTED_HEAD"], "")
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", script], cwd=repo,
                            env={**os.environ, **defaults, "GITHUB_OUTPUT": str(output), **env},
                            capture_output=True, text=True)
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    return result, values


@pytest.mark.parametrize("rel", RESOLVERS)
@pytest.mark.parametrize("kind", ["sha", "local-ref", "remote-ref", "pr-sha", "pr-branch", "push-before", "first-push"])
def test_resolver_uses_actual_commit_for_explicit_refs_and_pr_push_events(rel, kind, action_repo, tmp_path):
    repo, base, head = action_repo
    choices = {
        "sha": {"BASE_INPUT": base},
        "local-ref": {"BASE_INPUT": "local-base"},
        "remote-ref": {"BASE_INPUT": "origin/main"},
        "pr-sha": {"EVENT_NAME": "pull_request", "PR_BASE_SHA": base},
        "pr-branch": {"EVENT_NAME": "pull_request", "BASE_REF": "main"},
        "push-before": {"EVENT_NAME": "push", "PUSH_BEFORE": base},
        "first-push": {"EVENT_NAME": "push", "BASE_INPUT": "0" * 40,
                       "PUSH_BEFORE": "0" * 40, "DEFAULT_BRANCH": "main", "EVENT_REF": "refs/heads/feature"},
    }
    result, values = _resolve(rel, repo, tmp_path, EXPECTED_HEAD=head, **choices[kind])
    assert result.returncode == 0, result.stdout + result.stderr
    assert values == {"ref": base, "head": head}


@pytest.mark.parametrize("rel", RESOLVERS)
def test_first_push_to_default_branch_compares_parent_instead_of_empty_diff(rel, action_repo, tmp_path):
    repo, base, head = action_repo
    _git(repo, "update-ref", "refs/remotes/origin/main", head)
    result, values = _resolve(rel, repo, tmp_path, EVENT_NAME="push", PUSH_BEFORE="0" * 40,
                              DEFAULT_BRANCH="main", EVENT_REF="refs/heads/main")
    assert result.returncode == 0, result.stdout + result.stderr
    assert values == {"ref": base, "head": head}


@pytest.mark.parametrize("rel", RESOLVERS)
@pytest.mark.parametrize("case", ["missing-base", "invalid-explicit", "wrong-head", "initial-root"])
def test_resolver_fails_clearly_without_a_trusted_baseline_or_expected_head(rel, case, action_repo, tmp_path):
    repo, base, head = action_repo
    choices = {
        "missing-base": {},
        "invalid-explicit": {"BASE_INPUT": "nonexistent", "PR_BASE_SHA": base},
        "wrong-head": {"BASE_INPUT": base, "EXPECTED_HEAD": base},
        "initial-root": {"EVENT_NAME": "push", "PUSH_BEFORE": "0" * 40,
                         "DEFAULT_BRANCH": "main", "EVENT_REF": "refs/heads/main"},
    }
    if case == "initial-root":
        _git(repo, "checkout", "--detach", base)
    result, values = _resolve(rel, repo, tmp_path, **choices[case])
    assert result.returncode != 0
    assert "::error::" in result.stdout + result.stderr
    assert values == {}


@pytest.mark.parametrize("rel", RESOLVERS)
def test_resolver_fetches_a_missing_ref_as_a_commit(rel, action_repo, tmp_path):
    repo, base, head = action_repo
    _git(repo, "branch", "-D", "main")
    _git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    result, values = _resolve(rel, repo, tmp_path, BASE_INPUT="refs/heads/main")
    assert result.returncode == 0, result.stdout + result.stderr
    assert values == {"ref": base, "head": head}


def test_action_uses_resolved_commits_for_both_test_and_check_commands(tmp_path):
    action = _load("action.yml")
    steps = action["runs"]["steps"]
    executable = tmp_path / "python"
    executable.write_text("#!/usr/bin/env python3\nimport json, os, pathlib, sys\n"
                          "with open(os.environ['CALL_LOG'], 'a') as f: f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                          "if '--markdown' in sys.argv: pathlib.Path(sys.argv[sys.argv.index('--markdown')+1]).write_text('report')\n")
    executable.chmod(0o755)
    log = tmp_path / "calls"
    for name in ["Run test suites", "Run rewardguard"]:
        step = next(s for s in steps if s.get("name") == name)
        result = subprocess.run(["bash", "-euo", "pipefail", "-c", step["run"]],
                                env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
                                     "BASE": "a" * 40, "REVIEW_HEAD": "b" * 40, "TESTS": "false",
                                     "RUNNER_TEMP": str(tmp_path), "GITHUB_OUTPUT": str(tmp_path / "output"),
                                     "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"), "CALL_LOG": str(log)},
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
    import json
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(calls) == 2
    for args in calls:
        assert args[:3] == ["-I", "-m", "rewardguard.cli"]
        assert args[args.index("--base") + 1] == "a" * 40
        assert args[args.index("--head") + 1] == "b" * 40
    assert "--no-local-tests" in calls[1]
