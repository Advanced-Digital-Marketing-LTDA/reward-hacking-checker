"""Configuration, always read from the base commit so a branch cannot loosen its own check."""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import PureWindowsPath

from . import gitops
from .models import RewardGuardError

CONFIG_FILE = ".rewardguard.toml"

DEFAULT_TEST_GLOBS = [
    "**/*.{test,spec}.{js,jsx,ts,tsx,mjs,cjs,mts,cts}",
    "**/__tests__/**",
    "**/{test,tests,e2e}/**",
]
DEFAULT_CONFIG_GLOBS = [
    "**/{jest,vitest,vite,playwright}.config.*",
    "**/vitest.workspace.*",
    "**/{jest,vitest}.setup.*",
    "**/setupTests.*",
    "**/.{c8rc,nycrc}{,.*}",
    ".github/**",
    CONFIG_FILE,
]
DEFAULT_SNAPSHOT_GLOBS = ["**/__snapshots__/**", "**/*-snapshots/**", "**/*.snap"]
DEFAULT_JUDGE_INCLUDE = ["**/*.{js,jsx,ts,tsx,mjs,cjs,mts,cts,vue,svelte}"]
DEFAULT_EXCLUDE = [
    "**/node_modules/**",
    "**/dist/**",
    "**/build/**",
    "**/coverage/**",
    "**/*.min.js",
    "**/{package-lock.json,yarn.lock,pnpm-lock.yaml}",
]
DEFAULT_CONTENT_INCLUDE = [
    "**/*.{md,mdx,html,htm,astro,vue,svelte,jsx,tsx,svg,vtt,srt}",
    "**/{content,contents,copy,data,locales,locale,i18n,translations,messages}/**/*.{json,jsonc,yaml,yml,txt,text}",
    "**/*.{content,copy,locale,locales,i18n,translations,messages}.{json,jsonc,yaml,yml,txt,text}",
    "**/robots.{txt,js,ts,mjs,cjs,mts,cts}",
    "**/sitemap*.{xml,txt,json,js,ts,mjs,cjs,mts,cts}",
    "**/{metadata,seo}.{json,jsonc,yaml,yml,js,jsx,ts,tsx,mjs,cjs,mts,cts}",
    "**/*.{metadata,seo}.{json,jsonc,yaml,yml,js,jsx,ts,tsx,mjs,cjs,mts,cts}",
    "**/{metadata,seo}/**/*.{json,jsonc,yaml,yml,js,ts,mjs,cjs,mts,cts}",
    "**/{redirects,_redirects}",
    "**/redirects.{json,jsonc,yaml,yml,toml,js,ts,mjs,cjs,mts,cts}",
]
DEFAULT_CONTENT_SOURCE_INCLUDE = ["**/*.{js,ts,mjs,cjs,mts,cts}"]
DEFAULT_CONTENT_EXCLUDE = [
    *DEFAULT_EXCLUDE,
    "**/{vendor,.git,.venv,venv,.next,.nuxt,.svelte-kit,.astro,out,.output}/**",
    "**/{test,tests,e2e,__tests__,__snapshots__,test-results}/**",
    "**/*.{test,spec}.*",
    "**/*.snap",
    "**/*.lock",
    "**/{bun.lockb,Pipfile.lock}",
]
DEFAULT_CONTENT_CONTEXT = {
    "B": [".agents/product-marketing.md", ".rewardguard/content-context.md"],
    "P": [".rewardguard/content-policy.md", ".rewardguard/content-policy.json"],
    "I": [".rewardguard/content-inventory.md", ".rewardguard/content-inventory.json"],
    "S": [".rewardguard/content-sources.md", ".rewardguard/content-sources.json"],
    "R": [".rewardguard/content-rendered.md", ".rewardguard/content-rendered.json"],
    "M": [".rewardguard/content-measurements.md", ".rewardguard/content-measurements.json"],
    "H": [".rewardguard/content-history.md", ".rewardguard/content-history.json"],
}


def _default_content_context() -> dict[str, list[str]]:
    return {code: list(paths) for code, paths in DEFAULT_CONTENT_CONTEXT.items()}


@dataclass
class ContentConfig:
    enabled: bool = True
    include: list[str] = field(default_factory=lambda: list(DEFAULT_CONTENT_INCLUDE))
    source_include: list[str] = field(default_factory=lambda: list(DEFAULT_CONTENT_SOURCE_INCLUDE))
    exclude: list[str] = field(default_factory=lambda: list(DEFAULT_CONTENT_EXCLUDE))
    context: dict[str, list[str]] = field(default_factory=_default_content_context)
    packs: list[str] = field(default_factory=list)  # Empty selects all supported packs.


@dataclass
class Suite:
    name: str
    command: str


@dataclass
class Config:
    install: str | None
    suites: list[Suite]
    run_tests: bool = True  # false: skip the test-run layer (static checks, Jev and Haiku only)
    test_globs: list[str] = field(default_factory=lambda: list(DEFAULT_TEST_GLOBS))
    config_globs: list[str] = field(default_factory=lambda: list(DEFAULT_CONFIG_GLOBS))
    snapshot_globs: list[str] = field(default_factory=lambda: list(DEFAULT_SNAPSHOT_GLOBS))
    judge_include: list[str] = field(default_factory=lambda: list(DEFAULT_JUDGE_INCLUDE))
    exclude: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE))
    model: str = "jev-1.13.0"
    api_url: str = "https://api.typesafe.ai/v1/systemone"
    max_state_chars: int = 90_000  # ~22k tokens; Jev allows 32k for state + longest question
    context_lines: int = 20
    concurrency: int = 4
    timeout_seconds: int = 60
    explainer_model: str = "claude-haiku-5-5"
    explainer_effort: str = "low"
    explainer_timeout_seconds: int = 300
    test_timeout_seconds: int = 1800
    rule_severity: dict[str, str] = field(default_factory=dict)
    content: ContentConfig = field(default_factory=ContentConfig)

    def severity(self, rule: str, default: str) -> str:
        return self.rule_severity.get(rule, default)


def _list(section: dict, key: str, default: list[str]) -> list[str]:
    v = section.get(key, default)
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise RewardGuardError(f"{CONFIG_FILE}: '{key}' must be a list of strings")
    return v


def _content_list(section: dict, key: str, default: list[str], *, scope: str = "content",
                  paths: bool = True) -> list[str]:
    values = section.get(key, default)
    label = f"{scope}.{key}"
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise RewardGuardError(f"{CONFIG_FILE}: '{label}' must be a list of strings")
    if paths:
        for path in values:
            if (not path.strip() or "\0" in path or PureWindowsPath(path).anchor
                    or ".." in re.split(r"[/\\{},]", path)):
                raise RewardGuardError(
                    f"{CONFIG_FILE}: '{label}' must contain relative repository paths "
                    "without absolute paths or '..' components")
    return list(values)


def _parse_content(section: object) -> ContentConfig:
    if not isinstance(section, dict):
        raise RewardGuardError(f"{CONFIG_FILE}: [content] must be a table")
    allowed = {"enabled", "include", "source_include", "exclude", "context", "packs"}
    unknown = set(section) - allowed
    if unknown:
        raise RewardGuardError(f"{CONFIG_FILE}: unknown content setting '{min(unknown)}'")
    enabled = section.get("enabled", True)
    if not isinstance(enabled, bool):
        raise RewardGuardError(f"{CONFIG_FILE}: content.enabled must be true or false")

    context_raw = section.get("context", {})
    if not isinstance(context_raw, dict):
        raise RewardGuardError(f"{CONFIG_FILE}: [content.context] must be a table")
    context = _default_content_context()
    for code in context_raw:
        if code not in context:
            raise RewardGuardError(f"{CONFIG_FILE}: unknown content.context code '{code}'")
        context[code] = _content_list(context_raw, code, [], scope="content.context")

    return ContentConfig(
        enabled=enabled,
        include=_content_list(section, "include", DEFAULT_CONTENT_INCLUDE),
        source_include=_content_list(section, "source_include", DEFAULT_CONTENT_SOURCE_INCLUDE),
        exclude=_content_list(section, "exclude", DEFAULT_CONTENT_EXCLUDE),
        context=context,
        packs=_content_list(section, "packs", [], paths=False),
    )


def parse(text: str) -> Config:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise RewardGuardError(f"{CONFIG_FILE} is not valid TOML: {e}") from e

    run_tests = data.get("run_tests", True)
    if not isinstance(run_tests, bool):
        raise RewardGuardError(f"{CONFIG_FILE}: run_tests must be true or false")
    suites_raw = data.get("suites", [])
    if not isinstance(suites_raw, list) or (run_tests and not suites_raw):
        raise RewardGuardError(f"{CONFIG_FILE} must define at least one [[suites]] entry, or set run_tests = false")
    suites = []
    for s in suites_raw:
        if not isinstance(s, dict) or not s.get("name") or not s.get("command"):
            raise RewardGuardError(f"{CONFIG_FILE}: every [[suites]] needs 'name' and 'command'")
        suites.append(Suite(name=str(s["name"]), command=str(s["command"])))

    paths = data.get("paths", {})
    judge = data.get("judge", {})
    explainer = data.get("explainer", {})
    rules = data.get("rules", {})
    for rule, sev in rules.items():
        if sev != "off":
            raise RewardGuardError(
                f"{CONFIG_FILE}: rule {rule} can only be set to \"off\"; severity comes from the Jev probability "
                "(warn at 0.60, fail at 0.80) or, for clear-cut rules, is fixed")
    if "thresholds" in data:
        raise RewardGuardError(f"{CONFIG_FILE}: [thresholds] is no longer configurable "
                               "(fixed: no output below 0.60, warn at 0.60, fail at 0.80); remove it")
    if "min_probability" in explainer:
        raise RewardGuardError(f"{CONFIG_FILE}: [explainer] min_probability is no longer configurable "
                               "(fixed at 0.60); remove it")

    cfg = Config(
        install=data.get("install"),
        suites=suites,
        run_tests=run_tests,
        test_globs=_list(paths, "test_globs", DEFAULT_TEST_GLOBS),
        config_globs=_list(paths, "config_globs", DEFAULT_CONFIG_GLOBS),
        snapshot_globs=_list(paths, "snapshot_globs", DEFAULT_SNAPSHOT_GLOBS),
        judge_include=_list(paths, "judge_include", DEFAULT_JUDGE_INCLUDE),
        exclude=_list(paths, "exclude", DEFAULT_EXCLUDE),
        model=str(judge.get("model", "jev-1.13.0")),
        api_url=str(judge.get("api_url", "https://api.typesafe.ai/v1/systemone")),
        max_state_chars=int(judge.get("max_state_chars", 90_000)),
        context_lines=int(judge.get("context_lines", 20)),
        concurrency=int(judge.get("concurrency", 4)),
        timeout_seconds=int(judge.get("timeout_seconds", 60)),
        explainer_model=str(explainer.get("model", "claude-haiku-5-5")),
        explainer_effort=str(explainer.get("effort", "low")),
        explainer_timeout_seconds=int(explainer.get("timeout_seconds", 300)),
        test_timeout_seconds=int(data.get("test_timeout_seconds", 1800)),
        rule_severity=dict(rules),
        content=_parse_content(data.get("content", {})),
    )
    _validate(cfg)
    return cfg


def _validate(cfg: Config) -> None:
    if cfg.model == "jev-latest":
        raise RewardGuardError(f"{CONFIG_FILE}: pin a versioned Jev model, not jev-latest")
    if cfg.explainer_effort not in ("low", "medium", "high", "xhigh", "max"):
        raise RewardGuardError(f"{CONFIG_FILE}: explainer effort must be low, medium, high, xhigh or max")


def load_from_commit(repo: str, commit: str, *, allow_missing: bool = False) -> Config:
    text = gitops.show_file(repo, commit, CONFIG_FILE)
    if text is None:
        if allow_missing:
            # Global Codex precommit: every repository gets a baseline, without requiring
            # a project file. Test suites need project-specific commands, so they stay off.
            return Config(install=None, suites=[], run_tests=False)
        raise RewardGuardError(
            f"{CONFIG_FILE} not found on the base commit {commit[:12]}. "
            "Commit it to the base branch first; it is never read from the branch being checked."
        )
    return parse(text)
