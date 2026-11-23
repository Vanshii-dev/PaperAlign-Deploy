import os
import tempfile
from pathlib import Path

import pytest

TMP = Path(tempfile.mkdtemp(prefix="paperalign_tests_"))
os.environ["PAPERALIGN_TMP"] = str(TMP / "work")
os.environ["ALLOWED_ORIGINS"] = "https://paperalign.example"
os.environ["RATE_LIMIT_PER_MIN"] = "1000"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from tests.build_fixtures import build_all  # noqa: E402


@pytest.fixture(scope="session")
def fx():
    return build_all(TMP / "fixtures")


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def workroot():
    return TMP / "work"


def post(client, path: Path, name=None, **data):
    with open(path, "rb") as f:
        return client.post("/api/format", files={"file": (name or path.name, f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}, data=data)
