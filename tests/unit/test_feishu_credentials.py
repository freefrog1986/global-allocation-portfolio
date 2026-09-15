"""测试 src/global_allocation/feishu/credentials.py。"""

from __future__ import annotations

import json

import pytest

from global_allocation.feishu.credentials import (
    FeishuCredentials,
    _credentials_file,
    load_credentials,
)


class TestCredentialsFile:
    def test_uses_xdg_config_home(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        path = _credentials_file()
        assert str(tmp_path) in str(path)
        assert path.name == "credentials.json"


class TestLoadCredentials:
    def test_from_env_vars(self, monkeypatch, tmp_path) -> None:
        # Clear any config file
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setenv("FEISHU_APP_ID", "cli_test_id")
        monkeypatch.setenv("FEISHU_APP_SECRET", "cli_test_secret")
        monkeypatch.setenv("FEISHU_CHAT_ID", "oc_test_chat")

        creds = load_credentials()
        assert creds.app_id == "cli_test_id"
        assert creds.app_secret == "cli_test_secret"
        assert creds.chat_id == "oc_test_chat"

    def test_from_credentials_file(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        # 清除 env vars
        monkeypatch.delenv("FEISHU_APP_ID", raising=False)
        monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
        monkeypatch.delenv("FEISHU_CHAT_ID", raising=False)

        # 写文件
        cred_file = tmp_path / "gap" / "credentials.json"
        cred_file.parent.mkdir(parents=True, exist_ok=True)
        cred_file.write_text(
            json.dumps(
                {
                    "app_id": "file_id",
                    "app_secret": "file_secret",
                    "chat_id": "oc_file_chat",
                }
            ),
            encoding="utf-8",
        )

        creds = load_credentials()
        assert creds.app_id == "file_id"
        assert creds.app_secret == "file_secret"
        assert creds.chat_id == "oc_file_chat"

    def test_env_overrides_file(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setenv("FEISHU_APP_ID", "env_id")
        monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
        monkeypatch.delenv("FEISHU_CHAT_ID", raising=False)

        cred_file = tmp_path / "gap" / "credentials.json"
        cred_file.parent.mkdir(parents=True, exist_ok=True)
        cred_file.write_text(
            json.dumps(
                {
                    "app_id": "file_id",
                    "app_secret": "file_secret",
                    "chat_id": "oc_file_chat",
                }
            ),
            encoding="utf-8",
        )

        creds = load_credentials()
        # env wins
        assert creds.app_id == "env_id"
        # file fills the gap
        assert creds.app_secret == "file_secret"

    def test_missing_app_id_raises(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.delenv("FEISHU_APP_ID", raising=False)
        monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
        monkeypatch.delenv("FEISHU_CHAT_ID", raising=False)

        with pytest.raises(ValueError, match="FEISHU_APP_ID"):
            load_credentials()

    def test_missing_app_secret_raises(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setenv("FEISHU_APP_ID", "id")
        monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
        monkeypatch.delenv("FEISHU_CHAT_ID", raising=False)

        with pytest.raises(ValueError, match="FEISHU_APP_SECRET"):
            load_credentials()

    def test_missing_chat_id_raises(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setenv("FEISHU_APP_ID", "id")
        monkeypatch.setenv("FEISHU_APP_SECRET", "secret")
        monkeypatch.delenv("FEISHU_CHAT_ID", raising=False)

        with pytest.raises(ValueError, match="FEISHU_CHAT_ID"):
            load_credentials()


class TestFeishuCredentials:
    def test_immutable(self) -> None:
        from dataclasses import FrozenInstanceError

        creds = FeishuCredentials("a", "b", "c")
        with pytest.raises(FrozenInstanceError):
            creds.app_id = "x"  # type: ignore[misc]
