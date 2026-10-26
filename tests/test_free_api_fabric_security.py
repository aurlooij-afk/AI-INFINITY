from __future__ import annotations

import socket

import pytest

import free_api_fabric as fabric


@pytest.fixture
def isolated_fabric(monkeypatch, tmp_path):
    monkeypatch.setattr(fabric, "DB_PATH", tmp_path / "free_api_fabric.db")
    fabric._init()
    yield


def test_openapi_verification_registers_only_read_only_methods(isolated_fabric, monkeypatch):
    api_id = "security-test-api"
    fabric._upsert({
        "api_id": api_id,
        "source": "test",
        "name": "Security Test API",
        "spec_url": "https://api.example.test/openapi.json",
        "homepage": "https://api.example.test",
        "auth": "unknown",
        "status": "discovered",
    })
    spec = {
        "openapi": "3.0.0",
        "servers": [{"url": "https://api.example.test"}],
        "paths": {
            "/items": {
                "get": {"operationId": "listItems", "summary": "List items", "security": []},
                "post": {"operationId": "createItem", "summary": "Create item", "security": []},
                "put": {"operationId": "replaceItem", "summary": "Replace item", "security": []},
                "patch": {"operationId": "patchItem", "summary": "Patch item", "security": []},
                "delete": {"operationId": "deleteItem", "summary": "Delete item", "security": []},
            }
        },
    }
    monkeypatch.setattr(fabric, "_host_safe", lambda _: True)
    monkeypatch.setattr(fabric, "_get_json", lambda _: spec)

    result = fabric.verify_api(api_id)
    operations = fabric.operations(api_id)

    assert result["executable"] is True
    assert result["operations"] == 1
    assert [item["method"] for item in operations] == ["GET"]


def test_invoke_refuses_legacy_write_operation_before_network_call(isolated_fabric, monkeypatch):
    fabric._upsert({
        "api_id": "legacy-api",
        "source": "test",
        "name": "Legacy API",
        "spec_url": "https://api.example.test/openapi.json",
        "homepage": "https://api.example.test",
        "auth": "No",
        "status": "discovered",
    })
    with fabric.LOCK, fabric._connect() as connection:
        connection.execute(
            """INSERT INTO free_api_ops
               (op_id,api_id,method,path,url,operation_id,summary,parameters_json,
                request_body_json,security_json,free,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("legacy-write", "legacy-api", "POST", "/items", "https://api.example.test/items",
             "createItem", "Legacy create operation", "[]", "{}", "[]", 1, 1.0),
        )

    def must_not_send(*args, **kwargs):
        raise AssertionError("write-capable operation must be refused before network access")

    monkeypatch.setattr(fabric, "_host_safe", must_not_send)
    monkeypatch.setattr(fabric, "_public_https_request", must_not_send)

    with pytest.raises(ValueError, match="write-capable operations are disabled"):
        fabric.invoke("legacy-write", body={"name": "must not be sent"})


def test_invoke_rejects_bodies_even_for_get_operations(isolated_fabric, monkeypatch):
    fabric._upsert({
        "api_id": "read-api",
        "source": "test",
        "name": "Read API",
        "spec_url": "https://api.example.test/openapi.json",
        "homepage": "https://api.example.test",
        "auth": "No",
        "status": "discovered",
    })
    with fabric.LOCK, fabric._connect() as connection:
        connection.execute(
            """INSERT INTO free_api_ops
               (op_id,api_id,method,path,url,operation_id,summary,parameters_json,
                request_body_json,security_json,free,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("read-with-body", "read-api", "GET", "/items", "https://api.example.test/items",
             "listItems", "Read operation", "[]", "{}", "[]", 1, 1.0),
        )

    monkeypatch.setattr(fabric, "_public_https_request", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("request body should not be sent")))
    with pytest.raises(ValueError, match="request bodies are disabled"):
        fabric.invoke("read-with-body", body={"unexpected": True})


def test_outbound_https_pins_validated_public_ip_and_does_not_follow_redirects(monkeypatch):
    captured = {}

    def fake_dns(host, port, type):
        captured["dns_host"] = host
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    class Response:
        status = 200

        class Headers:
            @staticmethod
            def get(name, default=""):
                return "application/json" if name == "Content-Type" else default

        headers = Headers()

        def __init__(self):
            self.sent = False

        def read(self, size):
            if self.sent:
                return b""
            self.sent = True
            return b'{"ok":true}'

    class FakeConnection:
        def __init__(self, host, port, addresses, timeout):
            captured.update({"host": host, "port": port, "addresses": addresses, "timeout": timeout})

        def request(self, method, path, headers):
            captured.update({"method": method, "path": path, "headers": headers})

        def getresponse(self):
            return Response()

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(fabric.socket, "getaddrinfo", fake_dns)
    monkeypatch.setattr(fabric, "_PinnedHTTPSConnection", FakeConnection)

    status, content_type, raw = fabric._public_https_request(
        "https://example.org/search?q=hello", timeout=5, max_bytes=1024, method="GET"
    )

    assert status == 200
    assert content_type == "application/json"
    assert json.loads(raw) == {"ok": True}
    assert captured["dns_host"] == "example.org"
    assert captured["addresses"] == ["93.184.216.34"]
    assert captured["method"] == "GET"
    assert captured["path"] == "/search?q=hello"
    assert captured["closed"] is True


def test_outbound_https_rejects_private_dns_before_connecting(monkeypatch):
    def private_dns(host, port, type):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    def must_not_connect(*args, **kwargs):
        raise AssertionError("private DNS target must not be connected")

    monkeypatch.setattr(fabric.socket, "getaddrinfo", private_dns)
    monkeypatch.setattr(fabric, "_PinnedHTTPSConnection", must_not_connect)

    with pytest.raises(ValueError, match="non-public address"):
        fabric._public_https_request("https://example.org/private", timeout=3, max_bytes=100)


def test_openapi_verification_skips_required_unsupported_params_and_path_templates(isolated_fabric, monkeypatch):
    api_id = "unsupported-params-api"
    fabric._upsert({
        "api_id": api_id,
        "source": "test",
        "name": "Unsupported Params API",
        "spec_url": "https://api.example.test/openapi.json",
        "homepage": "https://api.example.test",
        "auth": "unknown",
        "status": "discovered",
    })
    spec = {
        "servers": [{"url": "https://api.example.test"}],
        "paths": {
            "/items/{item_id}": {"get": {"operationId": "getItem", "security": []}},
            "/search": {"get": {
                "operationId": "search",
                "security": [],
                "parameters": [{"name": "X-Account", "in": "header", "required": True}],
            }},
            "/list": {"get": {"operationId": "listItems", "security": [], "parameters": []}},
        },
    }
    monkeypatch.setattr(fabric, "_host_safe", lambda _: True)
    monkeypatch.setattr(fabric, "_get_json", lambda _: spec)

    result = fabric.verify_api(api_id)

    assert result["operations"] == 1
    assert [op["operation_id"] for op in fabric.operations(api_id)] == ["listItems"]
