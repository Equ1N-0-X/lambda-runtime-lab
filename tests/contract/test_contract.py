"""稼働中の全言語のAPIへ全正規ケースを送り、公開契約が一致することを検証する。"""

from pathlib import Path

import pytest

from contract_harness import (
    assert_case_matches,
    configured_targets,
    invoke,
    load_cases,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.integration
def test_all_implementations_follow_canonical_contract() -> None:
    targets = configured_targets(REPOSITORY_ROOT / "spec" / "implementations.yaml")
    cases = load_cases(REPOSITORY_ROOT / "spec" / "testcases")

    for case in cases:
        responses = {target.label: invoke(target.url, case, target.transport) for target in targets}
        assert_case_matches(case, responses)
