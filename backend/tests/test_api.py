from __future__ import annotations

from fastapi.testclient import TestClient

from app import main


def fake_response(reply: str = "Hello!", *, cached: bool = False) -> dict:
    return {
        "choices": [{"message": {"content": reply}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 2},
        "_hidden_params": {"cache_hit": cached},
    }


def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(main, "KEYS", ["key-one-secret", "key-two-secret"])
    monkeypatch.setattr(main, "KEY_IDS", ["key_1", "key_2"])
    monkeypatch.setattr(main, "ENABLE_FAILURE_SIMULATION", True)
    return TestClient(main.app)


def test_first_turn_and_history_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "completion", lambda **kwargs: fake_response())
    with client(tmp_path, monkeypatch) as test_client:
        response = test_client.post("/chat", json={"session_id": "s-one", "message": "Hi"})
        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == "s-one"
        assert data["metadata"]["resolution_source"] == "LIVE_INFERENCE"
        assert data["metadata"]["input_tokens"] == 12
        assert data["metadata"]["output_tokens"] == 2
        history = test_client.get("/sessions/s-one/messages").json()["messages"]
        assert [message["role"] for message in history] == ["user", "assistant"]
        assert "metadata" in history[1]
        assert test_client.get("/sessions/nope/messages").status_code == 404


def test_simulated_fallback_and_all_keys_failure_are_not_persisted(tmp_path, monkeypatch):
    calls = []

    def complete(**kwargs):
        calls.append(kwargs["api_key"])
        return fake_response("Recovered")

    monkeypatch.setattr(main, "completion", complete)
    with client(tmp_path, monkeypatch) as test_client:
        response = test_client.post(
            "/chat", json={"session_id": "s-fallback", "message": "Unique prompt"},
            headers={"X-Simulate-Failure": "key_1=429"},
        )
        assert response.status_code == 200
        assert response.json()["metadata"]["key_id"] == "key_2"
        assert response.json()["metadata"]["fallback_attempts"] == 1
        assert calls == ["key-two-secret"]
        failed = test_client.post(
            "/chat", json={"session_id": "s-failed", "message": "Do not save"},
            headers={"X-Simulate-Failure": "*=429"},
        )
        assert failed.status_code == 503
        assert failed.json() == {"detail": "all providers unavailable"}
        assert test_client.get("/sessions/s-failed/messages").status_code == 404


def test_cache_metadata_and_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "completion", lambda **kwargs: fake_response(cached=True))
    with client(tmp_path, monkeypatch) as test_client:
        cached = test_client.post("/chat", json={"session_id": "s-cache", "message": "Same prompt"})
        assert cached.json()["metadata"]["resolution_source"] == "LLM_CACHE"
        assert cached.json()["metadata"]["key_id"] is None
        assert cached.json()["metadata"]["fallback_attempts"] == 0
        assert test_client.post("/chat", json={"session_id": "s", "message": "  "}).status_code == 422
        assert test_client.post("/chat", json={"session_id": "s", "message": "x" * 4001}).status_code == 422
