from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location("start_platform", SCRIPTS / "start_platform.py")
assert spec and spec.loader
start_platform = importlib.util.module_from_spec(spec)
spec.loader.exec_module(start_platform)


def test_agent_health_uses_direct_loopback_http(monkeypatch):
    calls: dict[str, object] = {}

    class FakeResponse:
        status = 200

        def read(self) -> bytes:
            return json.dumps({"status": "ok", "busy": False}).encode("utf-8")

    class FakeConnection:
        def __init__(self, host: str, port: int, timeout: float):
            calls["init"] = (host, port, timeout)

        def request(self, method: str, path: str, headers: dict[str, str]):
            calls["request"] = (method, path, headers)

        def getresponse(self):
            return FakeResponse()

        def close(self):
            calls["closed"] = True

    monkeypatch.setattr(start_platform.http.client, "HTTPConnection", FakeConnection)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")

    result = start_platform.agent_health("token-value", timeout=0.75)

    assert result == {"status": "ok", "busy": False}
    assert calls["init"] == ("127.0.0.1", 8765, 0.75)
    method, path, headers = calls["request"]
    assert method == "GET"
    assert path == "/health"
    assert headers["Authorization"] == "Bearer token-value"
    assert calls["closed"] is True



def test_agent_port_open_uses_raw_loopback_tcp(monkeypatch):
    calls: dict[str, object] = {}

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            calls["closed"] = True

    def fake_create_connection(address, timeout):
        calls["connection"] = (address, timeout)
        return FakeSocket()

    monkeypatch.setattr(start_platform.socket, "create_connection", fake_create_connection)

    assert start_platform.agent_port_open(timeout=0.4) is True
    assert calls["connection"] == (("127.0.0.1", 8765), 0.4)
    assert calls["closed"] is True

def test_configure_manufacturing_rotates_internal_token(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("DASHBOARD_PASSWORD=test\nMANUFACTURING_AGENT_TOKEN=old-token\n", encoding="utf-8")

    first = start_platform.configure_manufacturing(env_file)
    second = start_platform.configure_manufacturing(env_file)

    assert first != "old-token"
    assert second != first
    content = env_file.read_text(encoding="utf-8")
    assert f"MANUFACTURING_AGENT_TOKEN={second}" in content
    assert "MANUFACTURING_ENABLED=true" in content




def test_full_launcher_starts_complete_default_docker_stack() -> None:
    source = (SCRIPTS / "start_platform.py").read_text(encoding="utf-8")
    assert '["docker", "compose", "up", "-d", "--build", "--remove-orphans", "--force-recreate"]' in source
    assert '["docker", "compose", "ps"]' in source
    assert "verify_agent_auth_from_api_container()" in source


def test_manufacturing_failure_does_not_block_docker_startup() -> None:
    source = (SCRIPTS / "start_platform.py").read_text(encoding="utf-8")
    assert "Manufacturing Agent startup failed" in source
    assert "Docker startup will continue" in source
    assert "docker_up()" in source
    assert "Manufacturing Agent opened port 8765 but did not answer its /ready endpoint" not in source


def test_network_sync_always_validates_service_certificate_profile() -> None:
    source = (SCRIPTS / "start_platform.py").read_text(encoding="utf-8")
    assert "Validating service certificates for active Wi-Fi IPv4" in source
    assert "Always ask setup.py to validate the installed API/broker certificates" in source
