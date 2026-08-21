import gzip
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def fixture_bytes(name: str) -> bytes:
    return gzip.decompress((FIXTURE_DIR / f"{name}.html.gz").read_bytes())
