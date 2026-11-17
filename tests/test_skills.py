"""The skills tree shipped inside the package is complete (an installed
package whose skills directory came out empty would be a landing
defect)."""

from __future__ import annotations

import subprocess
import sys
from importlib.resources import files


class TestSkillsTree:
    def test_skill_files_ship_as_package_data(self):
        root = files("stellarlib") / "skills"
        for relative in (
            "SKILL-DEPLOY.md",
            "stellarlib/skill/SKILL.md",
            "stellarlib/skill/references/families.md",
            "stellarlib/skill/CHANGELOG.md",
            "stellarlib/meta.yaml",
        ):
            assert (root / relative).is_file(), relative

    def test_discovery_entry_prints_the_root(self):
        from stellarlib.skills import root

        assert root.name == "skills"
        assert (root / "SKILL-DEPLOY.md").is_file()

    def test_python_dash_m_entry_point(self):
        out = subprocess.run(
            [sys.executable, "-m", "stellarlib.skills"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert "SKILL-DEPLOY.md" in out.stdout
