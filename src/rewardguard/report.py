from __future__ import annotations

import json
from collections import Counter, defaultdict

from .content_checks import safe

from .models import FAIL, Report

MARKER = "<!-- rewardguard-report -->"


def _loc(f) -> str:
    if not f.file:
        return ""
    return f"{f.file}:{f.line}" if f.line else f.file


def terminal(report: Report) -> str:
    out = []
    for e in report.errors:
        out.append(f"ERROR  {e}")
    for f in sorted(report.findings, key=lambda x: (x.severity != FAIL, x.file or "", x.line or 0)):
        p = f" (p={f.probability:.2f})" if f.probability is not None else ""
        out.append(f"{f.severity.upper():5}  {f.rule}{p}  {_loc(f)}\n       {f.message}")
        if f.evidence:
            out.extend("       > " + l for l in f.evidence.splitlines()[:10])
        for d in f.details or []:
            out.append(f"       line {d['line']}: {d['problem']}")
            out.append(f"         fix: {d['fix']}")
        if f.details == []:
            out.append("       Haiku found no specific lines; review this chunk by hand")
    for n in report.notes:
        out.append(f"note   {n}")
    fails = sum(f.severity == FAIL for f in report.findings)
    warns = len(report.findings) - fails
    verdict = "FAILED" if report.failed else "PASSED"
    out.append(f"\nrewardguard {verdict}: {fails} fail, {warns} warn, {len(report.errors)} error")
    return "\n".join(out)


def _content_markdown(content: dict) -> list[str]:
    if not content:
        return []
    coverage = content.get("coverage", [])
    gaps = [r for r in coverage if r.get("status") == "not_evaluated"]
    evaluated = sum(r.get("status") == "evaluated" for r in coverage)
    omitted = sum(r.get("status") == "not_applicable" for r in coverage)
    classified = [r for r in coverage if r.get("stage") == "classification"]
    lines = ["### Content review and coverage", "",
             f"Status: **{safe(content.get('status', 'unknown'))}** · {evaluated} proposition(s) evaluated · "
             f"{len(gaps)} evidence gap(s) · {omitted} not applicable.",
             f"Classification: {len(classified)} chunk(s), "
             f"{sum(r.get('status') == 'eligible' for r in classified)} eligible, "
             f"{sum(r.get('status') == 'skipped' for r in classified)} skipped. "
             "Exactly **0.60** enters review. Defects warn at **0.60 ≤ p < 0.80** and fail at **p ≥ 0.80**.",
             "Skipped scopes and missing evidence are unverified; they do not claim a clean content review.", ""]
    packs = defaultdict(Counter)
    for row in coverage:
        if row.get("rule") and row.get("status") in ("evaluated", "not_evaluated"):
            packs[row.get("pack") or "unspecified"][row["status"]] += 1
    if packs:
        lines += ["| Content pack | Evaluated propositions | Evidence gaps |", "|---|---:|---:|"]
        for pack, counts in sorted(packs.items()):
            lines.append(f"| {safe(pack)} | {counts['evaluated']} | {counts['not_evaluated']} |")
        lines.append("")
    for key in ("provenance", "omissions", "sources", "missing_sources", "eligibility", "reason"):
        if content.get(key) is not None:
            lines += [f"**{safe(key)}:** {safe(content[key])}", ""]
    if gaps:
        lines += ["<details><summary>Content evidence gaps (scoped, unverified)</summary>", ""]
        for row in gaps[:15]:
            scope = "; ".join(f"{key}: {safe(row[key])}" for key in
                              ("file", "rule", "instance", "context", "reason", "requires", "missing_sources", "provenance")
                              if row.get(key) is not None)
            lines.append(f"- {scope or 'Required evidence unavailable'}")
        if len(gaps) > 15:
            lines.append(f"- {len(gaps) - 15} additional scoped gaps; see the JSON report and individual content checks.")
        lines += ["", "</details>", ""]
    return lines


def markdown(report: Report) -> str:
    fails = sum(f.severity == FAIL for f in report.findings)
    warns = len(report.findings) - fails
    head = "❌ **Failed**" if report.failed else "✅ **Passed**"
    lines = [MARKER, f"## rewardguard: {head}", "",
             f"{fails} fail · {warns} warn · {len(report.errors)} error", ""]
    lines += _content_markdown(report.content)
    if report.errors:
        lines += ["### Errors", ""] + [f"- {safe(e.splitlines()[0][:300])}" for e in report.errors] + [""]
    if report.findings:
        lines += ["| Severity | Rule | Where | Detail |", "|---|---|---|---|"]
        for f in sorted(report.findings, key=lambda x: (x.source != "content", x.severity != FAIL, x.file or "", x.line or 0)):
            p = f" (p={f.probability!r})" if f.probability is not None else ""
            detail = safe(f.message)
            if f.evidence:
                detail += "<br>" + safe(f.evidence.splitlines()[0][:120])
            for d in f.details or []:
                detail += f"<br>line {safe(d['line'])}: {safe(d['problem'])} Fix: {safe(d['fix'])}"
            if f.details == []:
                detail += "<br>Haiku found no specific lines; review this chunk by hand"
            where = safe(_loc(f)) if f.file else "—"
            lines.append(f"| {safe(f.severity)} | {safe(f.rule)}{p} | {where} | {detail} |")
        lines.append("")
    if report.notes:
        lines += [safe(n) + "  " for n in report.notes]
    return "\n".join(lines) + "\n"


def as_json(report: Report) -> str:
    return json.dumps({
        "failed": report.failed,
        "errors": report.errors,
        "findings": [f.to_dict() for f in report.findings],
        "notes": report.notes,
        "content": report.content,
    }, indent=2)
