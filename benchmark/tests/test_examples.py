"""環境例と資料が、実装一覧（4言語）とmakeの既定の環境名に一致することを検証する。"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
KEYS = list(yaml.safe_load((ROOT / "spec/implementations.yaml").read_text())["implementations"])


def test_local_env_example_lists_every_implementation():
    example = (ROOT / ".env.local.example").read_text(encoding="utf-8")

    # 「全言語について書く」と案内するため、実装一覧の全言語の接続先を例に含める。
    for key in KEYS:
        assert f"{key.upper()}_API_URL=" in example


def test_terraform_example_uses_the_same_environment_as_make():
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    tfvars = (ROOT / "infrastructure/terraform/terraform.tfvars.example").read_text()

    make_default = re.search(r"^ENVIRONMENT \?= (\S+)$", makefile, re.M).group(1)
    example = re.search(r'^environment\s*=\s*"([^"]+)"', tfvars, re.M).group(1)
    assert example == make_default


def test_canonical_documents_state_their_relation_to_four_languages():
    for name in ("01_REQUIREMENTS.md", "02_SPECIFICATION.md"):
        text = (ROOT / "docs" / name).read_text(encoding="utf-8")
        assert "spec/implementations.yaml" in text.split("## 1.")[0]
