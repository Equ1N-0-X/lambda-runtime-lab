"""稼働中の全言語のAPIへ全正規ケースを送り、公開契約が一致することを検証する。

検証の単位は「ケース × そのケースを実装済みの言語」とし、一つの不一致で残りの検証を止めない。
"""

from pathlib import Path

import pytest
import yaml

from contract_harness import (
    assert_case_matches,
    case_targets,
    configured_targets,
    invoke,
    load_cases,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REGISTRY = REPOSITORY_ROOT / "spec" / "implementations.yaml"
KEYS = tuple((yaml.safe_load(REGISTRY.read_text(encoding="utf-8")) or {})["implementations"])
PAIRS = case_targets(load_cases(REPOSITORY_ROOT / "spec" / "testcases", KEYS), KEYS)


@pytest.mark.integration
@pytest.mark.parametrize(("case", "key"), PAIRS, ids=[f"{case.name}[{key}]" for case, key in PAIRS])
def test_implementation_follows_canonical_contract(case, key) -> None:
    # 接続先は実行時に読む（統合テストを実行しない場合は環境変数がなくても収集できる）。
    target = next(target for target in configured_targets(REGISTRY) if target.key == key)
    assert_case_matches(case, {target.label: invoke(target.url, case, target.transport)})
