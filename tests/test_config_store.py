"""Unit tests for config_store — no DB needed, uses monkeypatch."""
import os
import pytest
from unittest.mock import patch, MagicMock
from cryptography.fernet import Fernet


def _make_store(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("ENCRYPTION_KEY", key)
    import importlib
    import config_store
    importlib.reload(config_store)
    return config_store


def test_encrypt_decrypt_roundtrip(monkeypatch):
    cs = _make_store(monkeypatch)
    encrypted = cs.encrypt_value("my-secret-key")
    assert encrypted != "my-secret-key"
    assert cs.decrypt_value(encrypted) == "my-secret-key"


def test_decrypt_empty_returns_empty(monkeypatch):
    cs = _make_store(monkeypatch)
    assert cs.decrypt_value("") == ""


def test_get_config_hits_db_first(monkeypatch):
    cs = _make_store(monkeypatch)
    encrypted = cs.encrypt_value("db-value")
    mock_row = {"value": encrypted}

    with patch("config_store._fetch_row", return_value=mock_row):
        result = cs.get_config(1, "OPENAI_API_KEY")
    assert result == "db-value"


def test_get_config_falls_back_to_env(monkeypatch):
    cs = _make_store(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")

    with patch("config_store._fetch_row", return_value=None):
        result = cs.get_config(1, "OPENAI_API_KEY")
    assert result == "env-key"


def test_get_config_returns_empty_when_nothing_set(monkeypatch):
    cs = _make_store(monkeypatch)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with patch("config_store._fetch_row", return_value=None):
        result = cs.get_config(1, "OPENAI_API_KEY")
    assert result == ""
