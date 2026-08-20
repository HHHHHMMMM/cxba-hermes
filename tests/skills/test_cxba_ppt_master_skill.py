from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = (
    ROOT
    / "profiles"
    / "cxba-production"
    / "skills"
    / "productivity"
    / "ppt-master"
)


def test_ppt_master_is_packaged_for_the_offline_run_sandbox():
    skill_text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")

    assert "name: ppt-master" in skill_text
    assert "/root/.hermes/skills/productivity/ppt-master" in skill_text
    assert "The Sandbox has no network" in skill_text
    assert "Never use `.venv`" in skill_text
    assert "import-sources <project_path> <source_files...> --copy" in skill_text
    assert "import-sources <project_path> <source_files...> --move" not in skill_text
    assert not (SKILL_DIR / ".venv").exists()
    assert (SKILL_DIR / "scripts" / "svg_quality_checker.py").is_file()
    assert (SKILL_DIR / "scripts" / "svg_to_pptx.py").is_file()
    assert (SKILL_DIR / "templates" / "layouts" / "layouts_index.json").is_file()
    assert (SKILL_DIR / "templates" / "charts" / "charts_index.json").is_file()
