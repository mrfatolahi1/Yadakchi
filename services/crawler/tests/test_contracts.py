import hashlib
import json
from pathlib import Path

from crawler.producer import LISTINGS_TOPIC, validate_event

ROOT = Path(__file__).parents[1]


def test_published_contract_ownership_is_exact() -> None:
    published = sorted((ROOT / "contracts/published").glob("*.json"))
    assert [path.name for path in published] == ["yadakchi.listings.observed.v2.json"]
    assert not (ROOT / "contracts/published/yadakchi.review.requested.v1.json").exists()


def test_all_listings_examples_validate_and_hash_the_fragment() -> None:
    examples = sorted((ROOT / "contracts/examples/yadakchi.listings.observed.v2").glob("*.json"))
    assert examples
    for path in examples:
        event = json.loads(path.read_text(encoding="utf-8"))
        validate_event(LISTINGS_TOPIC, event)
        payload = event["payload"]
        assert (
            hashlib.sha256(payload["raw_fragment"].encode()).hexdigest() == payload["fragment_hash"]
        )
