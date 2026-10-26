from __future__ import annotations

import json

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
    monkeypatch.setattr(fabric.OPENER, "open", must_not_send)

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

    monkeypatch.setattr(fabric, "_host_safe", lambda _: True)
    with pytest.raises(ValueError, match="request bodies are disabled"):
        fabric.invoke("read-with-body", body={"unexpected": True})
