"""Tests for WhatsAppSettings configuration and .env file loading."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from pykoclaw_whatsapp.config import WhatsAppSettings


class TestWhatsAppSettingsDefaults:
    """Test WhatsAppSettings default values in isolated environment."""

    def test_whatsapp_settings_defaults_no_env_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test WhatsAppSettings uses defaults when no .env file exists."""
        monkeypatch.chdir(tmp_path)

        settings = WhatsAppSettings()

        assert settings.trigger_name == "Andy"
        assert settings.batch_window_seconds == 90
        assert "whatsapp" in str(settings.auth_dir)
        assert "session.db" in str(settings.session_db)

    def test_whatsapp_settings_auth_dir_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test WhatsAppSettings.auth_dir default path."""
        monkeypatch.chdir(tmp_path)

        settings = WhatsAppSettings()

        expected = Path.home() / ".pykoclaw" / "whatsapp" / "auth"
        assert settings.auth_dir == expected

    def test_whatsapp_settings_session_db_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test WhatsAppSettings.session_db default path."""
        monkeypatch.chdir(tmp_path)

        settings = WhatsAppSettings()

        expected = Path.home() / ".pykoclaw" / "whatsapp" / "session.db"
        assert settings.session_db == expected


class TestWhatsAppSettingsEnvFileLoading:
    """Test WhatsAppSettings loads from .env file in CWD."""

    def test_whatsapp_settings_loads_trigger_name_from_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test WhatsAppSettings loads PYKOCLAW_WA_TRIGGER_NAME from .env."""
        env_file = tmp_path / ".env"
        env_file.write_text("PYKOCLAW_WA_TRIGGER_NAME=Bot\n")

        monkeypatch.chdir(tmp_path)

        settings = WhatsAppSettings()

        assert settings.trigger_name == "Bot"

    def test_whatsapp_settings_loads_batch_window_from_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test WhatsAppSettings loads PYKOCLAW_WA_BATCH_WINDOW_SECONDS from .env."""
        env_file = tmp_path / ".env"
        env_file.write_text("PYKOCLAW_WA_BATCH_WINDOW_SECONDS=120\n")

        monkeypatch.chdir(tmp_path)

        settings = WhatsAppSettings()

        assert settings.batch_window_seconds == 120

    def test_whatsapp_settings_loads_auth_dir_from_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test WhatsAppSettings loads PYKOCLAW_WA_AUTH_DIR from .env."""
        custom_auth = tmp_path / "custom_auth"
        custom_auth.mkdir()

        env_file = tmp_path / ".env"
        env_file.write_text(f"PYKOCLAW_WA_AUTH_DIR={custom_auth}\n")

        monkeypatch.chdir(tmp_path)

        settings = WhatsAppSettings()

        assert settings.auth_dir == custom_auth

    def test_whatsapp_settings_loads_session_db_from_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test WhatsAppSettings loads PYKOCLAW_WA_SESSION_DB from .env."""
        custom_db = tmp_path / "custom_session.db"

        env_file = tmp_path / ".env"
        env_file.write_text(f"PYKOCLAW_WA_SESSION_DB={custom_db}\n")

        monkeypatch.chdir(tmp_path)

        settings = WhatsAppSettings()

        assert settings.session_db == custom_db

    def test_whatsapp_settings_loads_multiple_vars_from_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test WhatsAppSettings loads multiple PYKOCLAW_WA_* vars from .env."""
        custom_auth = tmp_path / "auth"
        custom_auth.mkdir()

        env_file = tmp_path / ".env"
        env_file.write_text(
            dedent(f"""\
                PYKOCLAW_WA_TRIGGER_NAME=MyBot
                PYKOCLAW_WA_BATCH_WINDOW_SECONDS=60
                PYKOCLAW_WA_AUTH_DIR={custom_auth}
                """)
        )

        monkeypatch.chdir(tmp_path)

        settings = WhatsAppSettings()

        assert settings.trigger_name == "MyBot"
        assert settings.batch_window_seconds == 60
        assert settings.auth_dir == custom_auth


class TestWhatsAppSettingsEnvVarOverride:
    """Test environment variables override .env file values."""

    def test_env_var_overrides_env_file_trigger_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test env var PYKOCLAW_WA_TRIGGER_NAME overrides .env value."""
        env_file = tmp_path / ".env"
        env_file.write_text("PYKOCLAW_WA_TRIGGER_NAME=FromFile\n")

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PYKOCLAW_WA_TRIGGER_NAME", "FromEnvVar")

        settings = WhatsAppSettings()

        assert settings.trigger_name == "FromEnvVar"

    def test_env_var_overrides_default_trigger_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test env var PYKOCLAW_WA_TRIGGER_NAME overrides default."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PYKOCLAW_WA_TRIGGER_NAME", "CustomBot")

        settings = WhatsAppSettings()

        assert settings.trigger_name == "CustomBot"

    def test_env_var_overrides_default_batch_window(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test env var PYKOCLAW_WA_BATCH_WINDOW_SECONDS overrides default."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PYKOCLAW_WA_BATCH_WINDOW_SECONDS", "45")

        settings = WhatsAppSettings()

        assert settings.batch_window_seconds == 45

    def test_precedence_env_var_over_env_file_over_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test full precedence: env var > .env file > default."""
        env_file = tmp_path / ".env"
        env_file.write_text("PYKOCLAW_WA_TRIGGER_NAME=FromFile\n")

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PYKOCLAW_WA_TRIGGER_NAME", "FromEnvVar")

        settings = WhatsAppSettings()

        assert settings.trigger_name == "FromEnvVar"


class TestWhatsAppSettingsIgnoresWrongPrefix:
    """Test WhatsAppSettings ignores env vars with wrong prefix."""

    def test_whatsapp_settings_ignores_pykoclaw_prefix(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test WhatsAppSettings rejects PYKOCLAW_MODEL (extra field with wrong prefix)."""
        env_file = tmp_path / ".env"
        env_file.write_text("PYKOCLAW_MODEL=should-be-rejected\n")

        monkeypatch.chdir(tmp_path)

        with pytest.raises(ValueError, match="Extra inputs are not permitted"):
            WhatsAppSettings()

    def test_whatsapp_settings_ignores_non_prefixed_vars(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test WhatsAppSettings ignores TRIGGER_NAME (no prefix)."""
        env_file = tmp_path / ".env"
        env_file.write_text("TRIGGER_NAME=should-be-ignored\n")

        monkeypatch.chdir(tmp_path)

        settings = WhatsAppSettings()

        assert settings.trigger_name == "Andy"

    def test_whatsapp_settings_ignores_wrong_prefix_in_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test WhatsAppSettings ignores OTHER_TRIGGER_NAME env var."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("OTHER_TRIGGER_NAME", "should-be-ignored")

        settings = WhatsAppSettings()

        assert settings.trigger_name == "Andy"


class TestWhatsAppSettingsMissingEnvFile:
    """Test WhatsAppSettings handles missing .env files gracefully."""

    def test_whatsapp_settings_works_without_env_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test WhatsAppSettings works fine when .env doesn't exist."""
        assert not (tmp_path / ".env").exists()

        monkeypatch.chdir(tmp_path)

        settings = WhatsAppSettings()

        assert settings.trigger_name == "Andy"
        assert settings.batch_window_seconds == 90

    def test_whatsapp_settings_works_with_empty_env_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test WhatsAppSettings works with empty .env file."""
        env_file = tmp_path / ".env"
        env_file.write_text("")

        monkeypatch.chdir(tmp_path)

        settings = WhatsAppSettings()

        assert settings.trigger_name == "Andy"

    def test_whatsapp_settings_works_with_comments_only_env_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test WhatsAppSettings works with .env file containing only comments."""
        env_file = tmp_path / ".env"
        env_file.write_text("# This is a comment\n# Another comment\n")

        monkeypatch.chdir(tmp_path)

        settings = WhatsAppSettings()

        assert settings.trigger_name == "Andy"
