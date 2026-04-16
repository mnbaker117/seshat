"""
Regression tests for the env-var → settings.json seeding layer.

This file exists because of a real production bug found during the
first deploy: `MAM_IRC_NICK`, `MAM_IRC_ACCOUNT`, and `MAM_IRC_PASSWORD`
were defined in `DEFAULT_SETTINGS` but never wired up in
`_apply_env_overrides`. The lifespan reads `settings.json` to decide
whether to start the IRC listener, so an unwired env var meant the
listener silently stayed off no matter what the user put in their
docker-compose.yml. The bug was invisible — there was no error, no
warning, no test failure, just a missing log line.

The fix is the dumbest-possible change (six lines in `_apply_env_overrides`)
but the discipline this file enforces is more valuable than the fix:
**every env var declared as a first-run seed at the top of config.py
MUST have a matching wire-up in `_apply_env_overrides`, and a test
here pinning down the relationship.** That way the next time someone
adds a new ENV_FOO without wiring it up, this test fails immediately.
"""
import importlib

import pytest


@pytest.fixture
def fresh_config(monkeypatch, tmp_path):
    """Reload `app.config` with a clean DATA_DIR and known env vars.

    Each test sets `monkeypatch.setenv(...)` for the env vars it
    cares about BEFORE the fixture body runs (using indirect
    parametrization isn't worth the complexity here — direct
    monkeypatching in the test body is simpler).

    Returns the freshly-imported `app.config` module so the test
    can call `load_settings()` against the temp DATA_DIR.
    """
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    # Force a fresh import of app.config so the module-level ENV_*
    # constants pick up our monkeypatched env vars instead of
    # whatever was set when the test session started.
    import app.config
    importlib.reload(app.config)
    return app.config


class TestEnvSeeding:
    """Every documented first-run env var must seed its settings key."""

    def test_mam_session_id_seeds(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("MAM_SESSION_ID", "test_cookie_value")
        import app.config
        importlib.reload(app.config)
        settings = app.config.load_settings()
        assert settings["mam_session_id"] == "test_cookie_value"

    def test_mam_irc_nick_seeds(self, monkeypatch, tmp_path):
        # The original production bug.
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("MAM_IRC_NICK", "test_bot")
        import app.config
        importlib.reload(app.config)
        settings = app.config.load_settings()
        assert settings["mam_irc_nick"] == "test_bot"

    def test_mam_irc_account_seeds(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("MAM_IRC_ACCOUNT", "test_account")
        import app.config
        importlib.reload(app.config)
        settings = app.config.load_settings()
        assert settings["mam_irc_account"] == "test_account"

    def test_mam_irc_password_seeds(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("MAM_IRC_PASSWORD", "secret_pw")
        import app.config
        importlib.reload(app.config)
        settings = app.config.load_settings()
        assert settings["mam_irc_password"] == "secret_pw"

    def test_qbit_url_seeds(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("QBIT_URL", "http://qbit.local:8080")
        import app.config
        importlib.reload(app.config)
        settings = app.config.load_settings()
        assert settings["qbit_url"] == "http://qbit.local:8080"

    def test_qbit_username_seeds(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("QBIT_USERNAME", "admin")
        import app.config
        importlib.reload(app.config)
        settings = app.config.load_settings()
        assert settings["qbit_username"] == "admin"

    def test_qbit_password_seeds(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("QBIT_PASSWORD", "adminadmin")
        import app.config
        importlib.reload(app.config)
        settings = app.config.load_settings()
        assert settings["qbit_password"] == "adminadmin"

    def test_qbit_watch_category_seeds(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("QBIT_WATCH_CATEGORY", "custom-category")
        import app.config
        importlib.reload(app.config)
        settings = app.config.load_settings()
        assert settings["qbit_watch_category"] == "custom-category"

    def test_calibre_library_path_seeds(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("CALIBRE_LIBRARY_PATH", "/my/calibre")
        import app.config
        importlib.reload(app.config)
        settings = app.config.load_settings()
        assert settings["calibre_library_path"] == "/my/calibre"

    def test_staging_path_seeds(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("STAGING_PATH", "/my/staging")
        import app.config
        importlib.reload(app.config)
        settings = app.config.load_settings()
        assert settings["staging_path"] == "/my/staging"

    def test_ntfy_url_seeds(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("NTFY_URL", "https://ntfy.sh/mytopic")
        import app.config
        importlib.reload(app.config)
        settings = app.config.load_settings()
        assert settings["ntfy_url"] == "https://ntfy.sh/mytopic"

    def test_verbose_logging_seeds(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("VERBOSE_LOGGING", "true")
        import app.config
        importlib.reload(app.config)
        settings = app.config.load_settings()
        assert settings["verbose_logging"] is True

    def test_dry_run_seeds(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("SESHAT_DRY_RUN", "true")
        import app.config
        importlib.reload(app.config)
        settings = app.config.load_settings()
        assert settings["dry_run"] is True


class TestEnvOverridePrecedence:
    """Env vars only seed FIRST-RUN settings — they never override
    a value already saved in settings.json. This is the rule
    documented in `config.py`'s module docstring; pin it down so
    a future refactor doesn't accidentally make env vars sticky."""

    def test_env_var_does_not_override_saved_settings(
        self, monkeypatch, tmp_path
    ):
        # First run: env var seeds the value into settings.json
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("MAM_IRC_NICK", "first_value")
        import app.config
        importlib.reload(app.config)
        first_settings = app.config.load_settings()
        assert first_settings["mam_irc_nick"] == "first_value"

        # Now simulate a restart with a different env var value.
        # The settings.json from the first run should win.
        monkeypatch.setenv("MAM_IRC_NICK", "second_value")
        importlib.reload(app.config)
        second_settings = app.config.load_settings()
        assert second_settings["mam_irc_nick"] == "first_value"


class TestNoSilentlyMissingSeeds:
    """The discipline test: every ENV_* constant defined as a
    first-run seed at the top of config.py must be referenced in
    `_apply_env_overrides`. Catches the exact bug that took down
    the IRC listener in production.

    This test scrapes both surfaces and asserts they line up. If
    you add a new ENV_FOO without a matching `if ENV_FOO and ...`
    line in _apply_env_overrides, this fails immediately.
    """

    def test_every_env_constant_is_wired_up(self):
        import inspect

        import app.config

        source = inspect.getsource(app.config._apply_env_overrides)

        # Find every ENV_* constant defined at module level. Skip
        # the ones that are explicitly NOT settings seeds (e.g.
        # WEBUI_HOST, WEBUI_PORT, AUTH_SECRET — those are read
        # directly by main.py / auth scaffolding, never written to
        # settings.json).
        not_seeds = {
            "ENV_WEBUI_HOST",
            "ENV_WEBUI_PORT",
            "ENV_AUTH_SECRET",
        }
        env_constants = [
            name
            for name in dir(app.config)
            if name.startswith("ENV_") and name not in not_seeds
        ]

        unwired = [
            name for name in env_constants if name not in source
        ]
        assert not unwired, (
            f"These ENV_* constants exist in app.config but are NOT "
            f"referenced in _apply_env_overrides — settings.json will "
            f"never get seeded from them: {unwired}"
        )
