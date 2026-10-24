from __future__ import annotations

from dataclasses import FrozenInstanceError
from importlib import resources
import json

import pytest

from rewardguard.models import RewardGuardError


def _registry():
    from rewardguard import content_rules

    return content_rules


def _rules():
    return {rule.id: rule for rule in _registry().all_rules()}


def test_packaged_catalog_loads_every_unique_rule_and_route():
    registry = _registry()
    packaged = json.loads(
        resources.files("rewardguard").joinpath("content_data/catalog.json").read_text(encoding="utf-8")
    )
    loaded = registry.catalog()
    rules = registry.all_rules()

    assert loaded == packaged
    assert len(rules) == 243
    assert len({rule.id for rule in rules}) == 243
    assert len(registry.pack_routes()) == 37
    assert {rule.pack for rule in rules} == set(registry.pack_routes())
    assert "CG_INDEX_UNINTENDED_NOINDEX" not in {rule.id for rule in rules}
    assert "CG_INDEX_POLICY_CONFLICT" in {rule.id for rule in rules}
    assert registry.pack_routes()["schema"].startswith("Implemented schema")


def test_callers_cannot_mutate_rules_or_the_next_catalog_read():
    registry = _registry()
    rule = registry.all_rules()[0]
    with pytest.raises(FrozenInstanceError):
        rule.id = "CHANGED"
    assert isinstance(rule.evidence_paths, tuple)
    assert all(isinstance(path, frozenset) for path in rule.evidence_paths)
    loaded = registry.catalog()
    loaded["packs"].clear()
    assert len(registry.catalog()["packs"]) == 37


@pytest.mark.parametrize(
    "rule_id,expected",
    [
        ("CG_QUANTIFIED_PROOF_BASIS", ({"D", "S"}, {"D", "P", "C"})),
        ("CG_AD_GROUNDING_CONTRACT", ({"D", "P", "S"}, {"D", "P", "C"})),
        ("CG_STRATEGY_RESEARCH_TRACE", ({"D", "P", "S"}, {"D", "P", "C"})),
        ("CG_SOURCE_CURRENTNESS", ({"D", "S"}, {"D", "H"})),
        ("CG_REDUNDANT_STAGING", ({"D", "P"}, {"D"})),
        ("CG_MEDIA_DELIVERY_PROFILE", ({"D", "P", "R"}, {"D", "P", "C"})),
    ],
)
def test_alternative_evidence_keeps_contract_omissions_evaluable_without_a_missing_source(rule_id, expected):
    rule = _rules()[rule_id]
    assert set(rule.evidence_paths) == {frozenset(path) for path in expected}


@pytest.mark.parametrize(
    "rule_id,expected",
    [
        ("CG_REQUIRED_SOURCE_PROVENANCE", ({"D", "P", "C"},)),
        ("CG_PUBLICATION_MODULE_CONTRACT", ({"D", "P", "R", "C"}, {"D", "P", "L"})),
        ("CG_SEARCH_ANSWER_COVERAGE", ({"D", "B", "C"}, {"D", "L"})),
        ("CG_SCHEMA_VISIBLE_CONTENT_REQUIREMENT", ({"D", "P", "R", "C"}, {"D", "P", "L"})),
        ("CG_ARTIFACT_FORMAT_CONTRACT", ({"D", "P", "C"}, {"D", "P", "L"})),
        ("CG_PRODUCT_FACT_CONFLICT", ({"D", "B"},)),
        ("CG_SCHEMA_SYNTAX", ({"D", "R"}, {"D", "L"})),
        ("CG_ADVISOR_SIMULATION_TRUTH", ({"D", "B"},)),
    ],
)
def test_complete_artifacts_and_first_evidence_clause_are_distinct_requirements(rule_id, expected):
    assert _rules()[rule_id].evidence_paths == tuple(frozenset(path) for path in expected)


def test_rubric_carries_one_specialist_and_exact_evidence_without_unloaded_guides():
    rule = _rules()["CG_SCHEMA_VISIBLE_CONTENT_REQUIREMENT"]
    assert rule.skill == "schema"
    assert rule.statement in rule.rubric
    assert rule.source in rule.rubric
    assert rule.rubric.count("skill://") == 1
    assert "D+P+R" in rule.rubric
    assert "complete relevant artifact" in rule.rubric
    assert "named field" in rule.rubric.lower()
    assert "not_evaluated" in rule.rubric
    assert "x-default" in rule.rubric
    assert "Positive example" in rule.rubric
    assert "Negative example" in rule.rubric
    assert "sms" not in rule.rubric.lower()
    assert "churn-prevention" not in rule.rubric
    assert "apply the exclusions in the source audit" not in rule.rubric.lower()


def test_freshness_and_conditional_evidence_survive_rubric_compilation():
    rules = _rules()
    assert "freshness where material" in rules["CG_COMPETITOR_FACT_CONFLICT"].rubric
    assert "S when judging an attributed quotation/endorsement" in rules["CG_ADVISOR_SIMULATION_TRUTH"].rubric
    assert "not a universal" in rules["CG_SOURCE_CURRENTNESS"].rubric


def _minimal_catalog():
    return {
        "schema_version": 1,
        "scope": {"canonical_rule_signatures": 1, "review_packs": 1},
        "packs": [
            {
                "key": "proof",
                "route": "Changed factual claims.",
                "rules": [
                    {
                        "id": "CG_FIXTURE_CLAIM",
                        "defect": "A changed claim contradicts trusted facts.",
                        "evidence": "D+B",
                        "haiku_skill": "copy-editing",
                        "haiku_skill_source": "skill://user/copy-editing/SKILL.md",
                        "haiku_excerpt_focus": "Compare only the changed claim with its supplied facts.",
                        "rubric_exclusions": ["Do not infer fabrication from absent evidence."],
                    }
                ],
            }
        ],
    }


@pytest.mark.parametrize(
    "change",
    [
        lambda data: data.update(schema_version=999),
        lambda data: data.update(packs="not a pack list"),
        lambda data: data["packs"].append(data["packs"][0]),
        lambda data: data["packs"][0]["rules"].append(data["packs"][0]["rules"][0]),
        lambda data: data["packs"][0]["rules"][0].pop("defect"),
        lambda data: data["packs"][0]["rules"][0].update(evidence="D+Z"),
        lambda data: data["packs"][0]["rules"][0].update(evidence="B+S"),
        lambda data: data["packs"][0]["rules"][0].update(haiku_skill_source="https://untrusted.example/guide"),
        lambda data: data["packs"][0]["rules"][0].update(evidence_paths=[{"needs": ["D", "unknown"]}]),
        lambda data: data["packs"][0]["rules"][0].update(evidence_paths=[]),
        lambda data: data["scope"].update(canonical_rule_signatures=2),
    ],
)
def test_bad_catalog_schema_fails_closed_at_the_resource_boundary(tmp_path, monkeypatch, change):
    registry = _registry()
    data = _minimal_catalog()
    change(data)
    resource = tmp_path / "content_data" / "catalog.json"
    resource.parent.mkdir()
    resource.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(registry.resources, "files", lambda package: tmp_path)

    with pytest.raises(RewardGuardError, match="content catalog"):
        registry.catalog()


def test_malformed_catalog_json_fails_closed(tmp_path, monkeypatch):
    registry = _registry()
    resource = tmp_path / "content_data" / "catalog.json"
    resource.parent.mkdir()
    resource.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(registry.resources, "files", lambda package: tmp_path)
    with pytest.raises(RewardGuardError, match="content catalog"):
        registry.all_rules()
