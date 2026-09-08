import os
from pathlib import Path

import pytest

from contract_harness import (
    assert_case_matches,
    configured_api_urls,
    invoke,
    load_cases,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.integration
def test_python_and_rust_implement_canonical_contract() -> None:
    python_url, rust_url = configured_api_urls()
    default_transport = os.getenv("CONTRACT_TRANSPORT", "direct").strip()
    python_transport = os.getenv("PYTHON_CONTRACT_TRANSPORT", default_transport).strip()
    rust_transport = os.getenv("RUST_CONTRACT_TRANSPORT", default_transport).strip()
    cases = load_cases(REPOSITORY_ROOT / "spec" / "testcases")

    for case in cases:
        python_response = invoke(python_url, case, python_transport)
        rust_response = invoke(rust_url, case, rust_transport)
        assert_case_matches(case, python_response, rust_response)
