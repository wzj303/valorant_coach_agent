import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).with_name("fixtures")


@pytest.fixture
def load_case():
    def read(name):
        return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return read
