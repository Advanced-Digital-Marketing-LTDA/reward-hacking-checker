from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__, codex_hook, config, explainer, gitops, judge, report, runner, static_checks
from .models import CLEAR_CUT, FAIL, WARN, Finding, Report, RewardGuardError


def _description(args) -> str:
    if args.description:
        return args.description
    if args.description_file:
        return Path(args.description_file).read_text()
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if event_path and Path(event_path).exists():
        event = json.loads(Path(event_path).read_text())
        pr = event.get("pull_request") or {}
        return f"{pr.get('title', '')}\n\n{pr.get('body') or ''}".strip()
    return ""


def _resolve(repo: str, args):
    if getattr(args, "staged", False):
        base, head = gitops.staged_snapshot(repo)
        trusted_ref = gitops.default_base(repo, base)
        config_base = gitops.merge_base(repo, trusted_ref, base)
        args.config_base = config_base
        return base, head, config.load_from_commit(repo, config_base, allow_missing=True)
    if gitops.is_dirty(repo) and not args.allow_dirty:
        raise RewardGuardError("Uncommitted changes in tracked files. Commit or stash them; "
                               "rewardguard checks committed history only.")
    head = gitops.rev_parse(repo, args.head)
    base = gitops.merge_base(repo, gitops.rev_parse(repo, args.base), head)
    return base, head, config.load_from_commit(repo, base)


def _load_run_results(path: str, base: str, head: str) -> tuple[list[Finding], list[str]]:
    """Reads the file written by `rewardguard run-tests`. It must be for the same base and head."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise RewardGuardError(f"Cannot read run results {path}: {e}") from e
    if not isinstance(data, dict) or (data.get("base"), data.get("head")) != (base, head):
        raise RewardGuardError(f"Run results {path} are not for base {base[:12]} / head {head[:12]}")
    findings, errors = data.get("findings"), data.get("errors")
    if not isinstance(findings, list) or not isinstance(errors, list):
        raise RewardGuardError(f"Run results {path} are malformed")
    out = []
    for d in findings:
        try:
            f = Finding(**d)
        except TypeError as e:
            raise RewardGuardError(f"Run results {path} hold a malformed finding: {d}") from e
        if f.severity not in (FAIL, WARN) or f.source != "run":
            raise RewardGuardError(f"Run results {path} hold an invalid finding: {d}")
        out.append(f)
    return out, [str(e) for e in errors]


def run_tests(args) -> int:
    """Runs only the test suites (branch code) and writes the results for `check --run-results`.
    Needs no secrets, so in CI it runs in a job that has none."""
    repo = str(Path(args.repo).resolve())
    log = (lambda *_: None) if args.quiet else (lambda m: print(m, file=sys.stderr))
    try:
        base, head, cfg = _resolve(repo, args)
    except RewardGuardError as e:
        print(f"ERROR  {e}", file=sys.stderr)
        return 2
    findings, errors = [], []
    if cfg.run_tests and gitops.changed_files(repo, base, head):
        log("rewardguard: test runs")
        try:
            findings = runner.run(repo, base, head, cfg, log)
        except RewardGuardError as e:
            errors.append(f"Test run: {e}")
    Path(args.out).write_text(json.dumps({
        "base": base, "head": head, "findings": [f.to_dict() for f in findings], "errors": errors,
    }, indent=2))
    log(f"rewardguard: run results written to {args.out}")
    return 0


def check(args) -> int:
    repo = str(Path(args.repo).resolve())
    rep = Report()
    log = (lambda *_: None) if args.quiet else (lambda m: print(m, file=sys.stderr))

    try:
        base, head, cfg = _resolve(repo, args)
    except RewardGuardError as e:
        rep.errors.append(str(e))
        rep.content = {"status": "error", "coverage": [], "reason": "Review preflight failed: " + str(e)}
        return _finish(rep, args)

    if getattr(args, "staged", False):
        config_base = getattr(args, "config_base", base)
        if gitops.show_file(repo, config_base, config.CONFIG_FILE) is None:
            rep.notes.append(f"staged index against {base[:12]}; built-in global Codex defaults")
        else:
            rep.notes.append(f"staged index against {base[:12]}; config read from trusted base {config_base[:12]}")
    else:
        rep.notes.append(f"base {base[:12]} (merge-base with {args.base}), head {head[:12]}; config read from base")
    changed = gitops.changed_files(repo, base, head)
    if not changed:
        rep.notes.append("No changes between base and head.")
        rep.content = {"status": "skipped", "coverage": [], "reason": "No changes between base and head."}
        return _finish(rep, args)

    # Content reads only Git objects and trusted-base evidence. Its gate precedes
    # client construction, so content-only non-content commits need no credentials.
    from . import content_review
    try:
        content = content_review.review(
            repo, base, head, cfg, changed, _description(args),
            trusted_base=getattr(args, "config_base", base), log=log,
        )
        rep.findings.extend(content.findings)
        rep.notes.extend(content.notes)
        rep.content = content.to_dict()
    except RewardGuardError as e:
        rep.errors.append(f"Content review: {e}")
        rep.content = {"status": "error", "coverage": []}
    if getattr(args, "content_only", False):
        return _finish(rep, args)

    log("rewardguard: static checks")
    found, kinds = static_checks.run(static_checks.StaticContext(repo, base, head, cfg), changed)

    if not cfg.run_tests:
        if getattr(args, "staged", False) and gitops.show_file(
                repo, getattr(args, "config_base", base), config.CONFIG_FILE) is None:
            rep.notes.append("Test runs are off (no .rewardguard.toml on HEAD); built-in defaults run static checks, Jev and Haiku")
        else:
            rep.notes.append("Test runs are off (run_tests = false in .rewardguard.toml): static checks, Jev and Haiku only")
    elif args.run_results:
        log(f"rewardguard: test results from {args.run_results}")
        try:
            run_findings, run_errors = _load_run_results(args.run_results, base, head)
            found.extend(run_findings)
            rep.errors.extend(run_errors)
        except RewardGuardError as e:
            rep.errors.append(str(e))
    elif args.no_local_tests:
        # CI's secrets job: branch code must never run here
        rep.errors.append("The base config has run_tests = true, but this run may not execute tests "
                          "(--no-local-tests). Run them in a separate job and pass --run-results.")
    else:
        log("rewardguard: test runs")
        try:
            found.extend(runner.run(repo, base, head, cfg, log))
        except RewardGuardError as e:
            rep.errors.append(f"Test run: {e}")

    # Clear-cut findings keep their fixed severity; Jev grades every other finding and judges every
    # changed file, and Haiku explains everything Jev reports
    log("rewardguard: Jev judge, Haiku explainer")
    try:
        client = judge.JevClient(cfg)
        haiku = explainer.HaikuExplainer(cfg)
        judged, notes = judge.run(repo, base, head, cfg, changed, kinds, _description(args), client, haiku, log,
                                  to_confirm=found)
        rep.findings.extend(judged)
        rep.notes.extend(notes)
        if client.models_seen:
            rep.notes.append("Jev model: " + ", ".join(sorted(client.models_seen)))
    except RewardGuardError as e:
        rep.findings.extend(f for f in found if f.rule in CLEAR_CUT)
        rep.errors.append(f"Judge: {e}")

    return _finish(rep, args)


def _finish(rep: Report, args) -> int:
    print(report.terminal(rep))
    if args.markdown:
        Path(args.markdown).write_text(report.markdown(rep))
    if args.json:
        Path(args.json).write_text(report.as_json(rep))
    if rep.errors:
        return 2
    return 1 if rep.failed else 0


def codex_precommit() -> int:
    """Codex PreToolUse entry point. Non-commit Bash calls pass through unchanged."""
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError) as e:
        print(f"rewardguard: could not read Codex hook event ({e}); skipped", file=sys.stderr)
        return 0

    if not isinstance(event, dict):
        return 0
    if event.get("hook_event_name") != "PreToolUse" or event.get("tool_name") != "Bash":
        return 0
    tool_input = event.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    cwd = event.get("cwd")
    if not isinstance(command, str) or not isinstance(cwd, str):
        return 0
    context = codex_hook.commit_context(command, cwd)
    if context is None:
        return 0
    repo, blocked_reason = context
    if blocked_reason is not None:
        print(
            f"rewardguard: commit blocked: {blocked_reason}. "
            "Stage changes in a separate Codex Bash call, then commit in another call.",
            file=sys.stderr,
        )
        return 2

    try:
        repo = gitops.git(repo, "rev-parse", "--show-toplevel").stdout.strip()
    except RewardGuardError as e:
        print(f"rewardguard: could not find repository root for commit check: {e}", file=sys.stderr)
        return 2

    print(f"rewardguard: checking staged changes before commit in {repo}", file=sys.stderr)
    args = argparse.Namespace(
        base="HEAD", head="HEAD", repo=str(repo), description=None, description_file=None,
        markdown=None, json=None, allow_dirty=False, staged=True, run_results=None,
        no_local_tests=False, quiet=False,
    )
    try:
        status = check(args)
    except Exception as e:
        print(f"rewardguard: precommit check errored: {e}", file=sys.stderr)
        return 2
    if status != 0:
        print("rewardguard: commit blocked; see the check report above", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rewardguard", description="Detect reward hacking by AI coding agents on a branch.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="Check the branch against its base")
    c.add_argument("--base", default="main", help="Base branch or commit (default: main)")
    c.add_argument("--head", default="HEAD", help="Commit to check (default: HEAD)")
    c.add_argument("--repo", default=".", help="Repository path (default: .)")
    c.add_argument("--staged", action="store_true", help="Check the staged index against HEAD")
    c.add_argument("--content-only", action="store_true",
                   help="Run only content review; non-content changes skip without model credentials")
    c.add_argument("--description", help="PR description text, used to judge changed expected values")
    c.add_argument("--description-file", help="File containing the PR description")
    c.add_argument("--markdown", help="Write a Markdown report here")
    c.add_argument("--json", help="Write a JSON report here")
    c.add_argument("--allow-dirty", action="store_true",
                   help="Run even with uncommitted changes (they are NOT checked)")
    c.add_argument("--run-results",
                   help="Use test results written by `rewardguard run-tests` instead of running the tests here")
    c.add_argument("--no-local-tests", action="store_true",
                   help="Never run tests in this process; fail if the base config needs test runs and "
                        "--run-results is not given")
    c.add_argument("--quiet", action="store_true")
    t = sub.add_parser("run-tests", help="Run only the test suites and write the results to a file")
    t.add_argument("--base", default="main", help="Base branch or commit (default: main)")
    t.add_argument("--head", default="HEAD", help="Commit to check (default: HEAD)")
    t.add_argument("--repo", default=".", help="Repository path (default: .)")
    t.add_argument("--out", required=True, help="Where to write the run results (JSON)")
    t.add_argument("--allow-dirty", action="store_true",
                   help="Run even with uncommitted changes (they are NOT checked)")
    t.add_argument("--quiet", action="store_true")
    sub.add_parser("codex-precommit", help="Run as a Codex PreToolUse hook for git commit commands")
    args = p.parse_args(argv)
    if args.cmd == "check":
        return check(args)
    if args.cmd == "run-tests":
        return run_tests(args)
    if args.cmd == "codex-precommit":
        return codex_precommit()
    return 2


if __name__ == "__main__":
    sys.exit(main())
