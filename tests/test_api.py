from fastapi.testclient import TestClient

from app import main
from app.config import get_settings
from app.main import app
from app.storage import AgentDatabase


def test_root_and_health_are_available() -> None:
    client = TestClient(app)
    root = client.get("/")
    assert root.status_code == 200
    assert "My Agent" in root.text
    assert "Projects" in root.text
    assert "Latest Review" in root.text
    assert root.headers["x-frame-options"] == "DENY"

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["ok"] is True
    assert set(health.json()["api_keys"]) == {"gemini", "openai"}

    usage = client.get("/api/usage")
    assert usage.status_code == 200
    assert usage.json()["requests"] >= 0


def test_chat_request_validation() -> None:
    client = TestClient(app)
    response = client.post("/api/chat", json={"message": "", "history": []})
    assert response.status_code == 422


def test_chat_does_not_require_a_workspace(tmp_path, monkeypatch) -> None:
    database = AgentDatabase(
        tmp_path / "agent.db",
        allowed_workspace_roots=(),
    )
    database.initialize()
    monkeypatch.setattr(main, "get_database", lambda: database)
    monkeypatch.setattr(main, "chat", lambda *args, **kwargs: "hello")

    response = TestClient(app).post(
        "/api/chat",
        json={"message": "hi", "history": []},
    )

    assert response.status_code == 200
    assert response.json() == {"answer": "hello"}


def test_public_llm_errors_are_actionable() -> None:
    rate_limit_error = type("RateLimitError", (Exception,), {})()
    auth_error = type("AuthenticationError", (Exception,), {})()
    connection_error = type("APIConnectionError", (Exception,), {})()

    assert "사용량 한도" in main._public_error(rate_limit_error)
    assert "API 키 인증" in main._public_error(auth_error)
    assert "네트워크" in main._public_error(connection_error)
    assert "logs/server.log" in main._public_error(Exception("unexpected"))


def test_agent_sse_includes_plan_progress(monkeypatch) -> None:
    def fake_prepare(request, mode):
        assert mode == "agent"
        return ([{"role": "user", "content": request.message}], None, get_settings())

    def fake_manager(context, settings, progress):
        del context, settings
        progress(
            {
                "type": "plan",
                "steps": [{"id": "step-1", "title": "Inspect", "status": "pending"}],
            }
        )
        progress({"type": "plan_step", "id": "step-1", "status": "running"})
        progress({"type": "plan_step", "id": "step-1", "status": "done"})
        return "finished"

    monkeypatch.setattr(main, "_prepare_request", fake_prepare)
    monkeypatch.setattr(main, "run_manager", fake_manager)
    response = TestClient(app).post("/api/agent", json={"message": "go"})

    assert response.status_code == 200
    assert '"type": "plan"' in response.text
    assert '"status": "done"' in response.text
    assert '"type": "final"' in response.text
