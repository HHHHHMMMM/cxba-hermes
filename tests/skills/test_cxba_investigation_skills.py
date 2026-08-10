from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml


SKILLS_ROOT = (
    Path(__file__).resolve().parents[2]
    / "profiles"
    / "cxba-production"
    / "skills"
    / "cxba"
)
SKILL_NAMES = (
    "cxba-case-investigation",
    "cxba-material-profiling",
    "cxba-raw-material-investigation",
    "cxba-safe-tabular-analysis",
)


def parse_skill(name: str) -> tuple[dict, str]:
    content = (SKILLS_ROOT / name / "SKILL.md").read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", content, re.DOTALL)
    assert match, f"invalid frontmatter for {name}"
    return yaml.safe_load(match.group(1)), match.group(2)


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_cxba_skill_meets_frontmatter_and_section_contract(name: str) -> None:
    frontmatter, body = parse_skill(name)
    description = frontmatter["description"]
    assert len(description) <= 60
    assert description.endswith(".")
    # The Skill is selected on the macOS Gateway host and executed inside the
    # Linux Run Sandbox, so host-level platform filtering must stay disabled.
    assert "platforms" not in frontmatter
    for section in (
        "## When to Use",
        "## Prerequisites",
        "## How to Run",
        "## Quick Reference",
        "## Procedure",
        "## Pitfalls",
        "## Verification",
    ):
        assert section in body


def test_case_skill_preserves_agent_autonomy_and_optional_review() -> None:
    _, body = parse_skill("cxba-case-investigation")
    normalized = body.casefold()
    assert "a full-case read is optional" in normalized
    assert "optional independent check" in normalized
    assert "multiple tool calls may be combined" in normalized


def test_skills_use_spring_material_identity_and_confirmed_dictionaries() -> None:
    contents = "\n".join(parse_skill(name)[1] for name in SKILL_NAMES)
    assert "materialId" in contents
    for account_type in ("BANK_ACCOUNT", "BANK_CARD", "WECHAT", "ALIPAY"):
        assert account_type in contents
    for relation in ("OWNS", "USES", "CONTROLS", "OPERATES"):
        assert relation in contents
    for relation in (
        "NATURAL_SHAREHOLDER",
        "KEY_PERSON",
        "LEGAL_REP",
        "FINANCIAL_OFFICER",
        "WORKS_FOR",
        "ACTS_FOR",
    ):
        assert relation in contents


def test_skills_forbid_runtime_installation() -> None:
    contents = "\n".join(parse_skill(name)[1] for name in SKILL_NAMES)
    assert "Never install packages during a Run" in contents
    assert "Do not use `pip`, `npm`, `apt`" in contents


def test_account_completeness_remains_a_skill_and_material_contract_not_a_boolean_claim() -> None:
    _, body = parse_skill("cxba-safe-tabular-analysis")
    assert "submit the complete enumerated list" in body
    assert "cite the source `materialId`" in body
    assert "accountEnumerationComplete" not in body
