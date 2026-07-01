import os
from pathlib import Path

from fastapi.testclient import TestClient

os.environ["TTS_FILTER_BEARER_TOKEN"] = "test-token"
os.environ["TTS_FILTER_CONFIG"] = str(Path(__file__).parent / "test-dictionary.yml")

from tts_filter.filter import save_config  # noqa: E402
from tts_filter_api.app import app  # noqa: E402

client = TestClient(app)


def setup_module() -> None:
    save_config(
        {
            "acronyms": {"LLM": "エルエルエム", "README": "リードミー"},
            "extensions": {"md": "エムディー"},
        },
        os.environ["TTS_FILTER_CONFIG"],
    )


def auth_header() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["message"] == "ok"


def test_normalize_requires_auth():
    response = client.post("/normalize", json={"text": "README.md"})
    assert response.status_code == 401


def test_normalize():
    response = client.post("/normalize", headers=auth_header(), json={"text": "README.md と LLM", "code_block_mode": "literal"})
    assert response.status_code == 200
    body = response.json()
    assert "リードミー エムディー" in body["normalized"]


def test_dictionary_crud():
    response = client.put("/dictionary", headers=auth_header(), json={"category": "acronyms", "key": "GITEA", "value": "ギテア"})
    assert response.status_code == 200
    assert response.json()["config"]["acronyms"]["GITEA"] == "ギテア"

    response = client.get("/dictionary", headers=auth_header())
    assert response.status_code == 200
    assert response.json()["config"]["acronyms"]["GITEA"] == "ギテア"

    response = client.request("DELETE", "/dictionary", headers=auth_header(), json={"category": "acronyms", "key": "GITEA"})
    assert response.status_code == 200
    assert "GITEA" not in response.json()["config"]["acronyms"]
