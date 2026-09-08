import runpy
from pathlib import Path


def _generate_items():
    script = Path(__file__).resolve().parents[2] / "scripts/seed-data.py"
    return runpy.run_path(script)["generate_items"]


def test_seed_data_is_deterministic_and_numbered():
    generate_items = _generate_items()

    first = generate_items("fixed-seed")
    second = generate_items("fixed-seed")

    assert first == second
    assert len(first) == 100
    assert first[0]["id"] == "item-001"
    assert first[-1]["id"] == "item-100"


def test_payloads_are_distinct_fixed_length_strings():
    items = _generate_items()("fixed-seed")

    payloads = [item["payload"] for item in items]
    assert all(isinstance(payload, str) for payload in payloads)
    assert len({len(payload) for payload in payloads}) == 1
    assert len(set(payloads)) == len(payloads)


def test_seeds_produce_different_data():
    generate_items = _generate_items()

    assert generate_items("seed-a") != generate_items("seed-b")
