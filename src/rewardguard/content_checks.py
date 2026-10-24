"""Publish scoped content checks from a trusted RewardGuard JSON report (stdlib only)."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import html
import json
import math
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


RULE_ID = re.compile(r"[A-Z][A-Z0-9_]{0,119}\Z")


def safe(value: object) -> str:
    """Render report data as inert Markdown text, including workflow command characters."""
    value = re.sub(r"[\x00-\x1f\x7f]", " ", str(value))
    value = html.escape(value, quote=True)
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", value)[:2000]


def probability(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and 0 <= value <= 1 else None


def _scope(row: dict) -> str:
    return "; ".join(f"{label}: {safe(row[key])}" for key, label in
                     (("file", "file"), ("line", "line"), ("instance", "instance"), ("reason", "reason"),
                      ("requires", "requires"), ("available", "available"),
                      ("missing_sources", "missing sources"), ("provenance", "provenance"),
                      ("source_only", "source only"), ("applicability", "applicability"))
                     if row.get(key) is not None) or "Scope unspecified in report"


def build_checks(report: dict) -> list[dict]:
    content = report.get("content") or {}
    if not isinstance(content, dict):
        raise ValueError("content must be an object")
    coverage = content.get("coverage", [])
    findings = report.get("findings", [])
    if not isinstance(coverage, list) or not all(isinstance(r, dict) for r in coverage):
        raise ValueError("coverage must be an array of objects")
    if not isinstance(findings, list) or not all(isinstance(r, dict) for r in findings):
        raise ValueError("findings must be an array of objects")
    counts = Counter(str(r.get("status", "unknown")) for r in coverage)
    classifiers = [r for r in coverage if r.get("stage") == "classification"]
    status = str(content.get("status", "unavailable"))
    summary = (f"Content status: {safe(status)}. Classification: {len(classifiers)} chunk(s), "
               f"{sum(r.get('status') == 'eligible' for r in classifiers)} eligible, "
               f"{sum(r.get('status') == 'skipped' for r in classifiers)} skipped.\n\n"
               + "Coverage: " + (", ".join(f"{safe(k)}: {v}" for k, v in sorted(counts.items())) or "no coverage recorded")
               + ".\n\nClassification at p >= 0.60 enters review; it is not a defect score. "
               "Skipped and missing evidence scopes are unverified, never a clean content verdict.")
    for row in classifiers[:20]:
        summary += f"\n- {_scope(row)}; classification p={safe(row.get('probability', 'unknown'))}; {safe(row.get('status', 'unknown'))}"
    general_gaps = [r for r in coverage if not r.get("rule") and r.get("status") == "not_evaluated"]
    for row in general_gaps[:10]:
        summary += f"\n- Unverified: {_scope(row)}"
    for key in ("eligibility", "provenance", "omissions", "missing_sources", "reason"):
        if key in content:
            summary += f"\n\n{safe(key)}: {safe(content[key])}"
    for note in report.get("notes", [])[:10]:
        if "content" in str(note).lower():
            summary += f"\n\n{safe(note)}"
    checks = [{"name": "contentguard / eligibility", "conclusion": "neutral",
               "output": {"title": "Content eligibility and coverage", "summary": summary}}]
    groups = defaultdict(list)
    for row in coverage:
        if row.get("rule") and row.get("status") in ("evaluated", "not_evaluated"):
            rule = row["rule"]
            if not isinstance(rule, str) or not RULE_ID.fullmatch(rule):
                raise ValueError("invalid content rule ID")
            groups[rule].append(row)
    content_findings = defaultdict(list)
    for row in findings:
        if row.get("source") != "content":
            continue
        rule = row.get("rule")
        if not isinstance(rule, str) or not RULE_ID.fullmatch(rule):
            raise ValueError("invalid content finding rule ID")
        content_findings[rule].append(row)
        groups.setdefault(rule, [])
    for rule, rows in sorted(groups.items()):
        evaluated = [r for r in rows if r.get("status") == "evaluated"]
        gaps = [r for r in rows if r.get("status") == "not_evaluated"]
        scores = [p for r in evaluated + content_findings[rule] if (p := probability(r.get("probability"))) is not None]
        peak = max(scores) if scores else None
        uncertain = bool(gaps) or any(probability(r.get("probability")) is None for r in evaluated + content_findings[rule])
        conclusion = "neutral"
        if peak is not None and peak >= .8:
            conclusion = "failure"
        elif peak is not None and peak < .6 and evaluated and not uncertain:
            conclusion = "success"
        title = f"{rule}: " + (f"highest p={peak!r}" if peak is not None else "unverified")
        summary = (f"{len(evaluated)} evaluated proposition(s); {len(gaps)} missing evidence scope(s). "
                   f"Highest defect probability: {peak!r}.\n\n"
                   "p < 0.60 ignored; 0.60 <= p < 0.80 warning (neutral); p >= 0.80 failure. "
                   "A success covers only the evaluated scopes listed here. Missing evidence is unverified.")
        for key in ("pack", "title", "skill"):
            values = sorted({str(row[key]) for row in rows if row.get(key) is not None})
            if values:
                summary += f"\n\n{safe(key)}: {safe(', '.join(values))}"
        for row in rows[:30]:
            summary += f"\n- {safe(row.get('status'))}: {_scope(row)}"
            if row.get("probability") is not None:
                summary += f"; p={safe(row['probability'])}"
        if len(rows) > 30:
            summary += f"\n- {len(rows) - 30} additional scopes; consult JSON report."
        for finding in content_findings[rule][:10]:
            summary += f"\n\nFinding: {_scope(finding)}; p={safe(finding.get('probability'))}: {safe(finding.get('message', ''))}"
            for detail in (finding.get("details") or [])[:5]:
                if isinstance(detail, dict):
                    summary += f"\n- Grounded line {safe(detail.get('line'))}: {safe(detail.get('problem', ''))}; fix: {safe(detail.get('fix', ''))}"
        checks.append({"name": f"contentguard / {rule}", "conclusion": conclusion,
                       "output": {"title": title, "summary": summary[:60000]}})
    if status == "error":
        checks.append(error_check("Content evaluation failed; reviewed scopes are incomplete. See the RewardGuard error report."))
    elif not content:
        checks.append({"name": "contentguard / evaluation", "conclusion": "neutral",
                       "output": {"title": "Content coverage unavailable", "summary": "No content result was recorded. The scope is unverified; see the aggregate check for preflight or coding errors."}})
    return checks


def error_check(message: str) -> dict:
    return {"name": "contentguard / evaluation", "conclusion": "failure",
            "output": {"title": "Content reporting or evaluation error", "summary": safe(message)}}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("GitHub API redirects are not permitted")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--repo", required=True)
    args = parser.parse_args(argv)
    try:
        if not re.fullmatch(r"[0-9a-fA-F]{40}", args.head):
            raise ValueError("head must be a resolved 40-character commit SHA")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo) or any(x in (".", "..") for x in args.repo.split("/")):
            raise ValueError("repo must be owner/name")
        base = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
        parsed = urllib.parse.urlsplit(base)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.port not in (None, 443)
                or parsed.path not in ("", "/api/v3")):
            raise ValueError("GitHub API URL must be an absolute HTTPS API host (optionally /api/v3)")
        token = os.environ.get("GH_TOKEN", "")
        if not token or any(ord(c) < 33 or ord(c) > 126 for c in token):
            print("::error::GH_TOKEN is missing or invalid; content checks require a token with checks:write.", file=sys.stderr)
            return 2
        invalid = False
        try:
            with open(args.report, encoding="utf-8") as stream:
                report = json.load(stream)
            if not isinstance(report, dict):
                raise ValueError("report must be an object")
            checks = build_checks(report)
        except (OSError, ValueError, TypeError):
            print("::error::RewardGuard JSON report is absent or malformed; content coverage is unavailable. Inspect the original judge error.", file=sys.stderr)
            invalid = True
            checks = [error_check("RewardGuard JSON report is absent or malformed. Content coverage is unavailable; inspect the original judge step error."),
                      {"name": "contentguard / eligibility", "conclusion": "neutral", "output": {"title": "Content eligibility unavailable", "summary": "No valid report; no content scope can be claimed reviewed."}}]
        opener = urllib.request.build_opener(NoRedirect)
        for check in checks:
            # GitHub caps text by bytes; bound every output even with multi-byte report text.
            check["output"]["summary"] = check["output"]["summary"].encode("utf-8")[:60000].decode("utf-8", errors="ignore")
            check["output"]["title"] = check["output"]["title"].encode("utf-8")[:250].decode("utf-8", errors="ignore")
            payload = dict(check, head_sha=args.head, status="completed")
            request = urllib.request.Request(f"{base}/repos/{args.repo}/check-runs", method="POST",
                                             data=json.dumps(payload, allow_nan=False).encode(),
                                             headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                                      "Content-Type": "application/json", "X-GitHub-Api-Version": "2022-11-28"})
            with opener.open(request, timeout=20) as response:
                if response.status != 201:
                    raise ValueError("GitHub check creation did not return HTTP 201")
                response_body = response.read(1_048_577)
                if len(response_body) > 1_048_576:
                    raise ValueError("GitHub check response exceeds the 1 MiB limit")
                result = json.loads(response_body)
                if not isinstance(result, dict) or type(result.get("id")) is not int or result["id"] <= 0:
                    raise ValueError("GitHub returned an invalid check ID")
        print(f"Published {len(checks)} scoped content checks.")
        return 2 if invalid else 0
    except urllib.error.HTTPError as exc:
        print(f"::error::Content check publication failed (HTTP {exc.code}); verify checks:write permission.", file=sys.stderr)
    except (OSError, ValueError, TypeError, urllib.error.URLError):
        print("::error::Content check publication failed: invalid inputs, API response, or unavailable API. No complete publication can be claimed.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
