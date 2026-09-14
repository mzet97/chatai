import os
import tempfile

import pytest
from django.contrib.auth import get_user_model

# Banco de TESTE em arquivo (não :memory:): threads e sync_to_async enxergam o
# mesmo banco, e close_all() não destrói nada. Exigência da suíte (critérios
# 1, 11, 12 pedem SQLite em arquivo com conexões adequadas).
_TEST_DB_PATH = os.path.join(tempfile.gettempdir(), f"chat-test-{os.getpid()}.sqlite3")

from django.conf import settings as _settings  # noqa: E402

_settings.DATABASES["default"].setdefault("TEST", {}).update({"NAME": _TEST_DB_PATH})


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_db():
    yield
    try:
        from django.db import connections

        connections.close_all()
        if os.path.exists(_TEST_DB_PATH):
            os.unlink(_TEST_DB_PATH)
    except Exception:
        pass


@pytest.fixture(scope="session", autouse=True)
def _block_real_provider():
    """Rede real bloqueada nos testes padrão (SDD §12: mocks only).

    Ativo salvo com CHAT_LIVE_TEST=1 (mesma chave do test_live.py opt-in).
    O SDK simulado (tests/fakes.py) nunca toca em AsyncAnthropic.
    """
    import os

    if os.environ.get("CHAT_LIVE_TEST") == "1":
        yield
        return
    import anthropic

    orig = anthropic.AsyncAnthropic

    def _blocked(*args, **kwargs):
        raise AssertionError("Rede real bloqueada nos testes (use o SDK simulado).")

    anthropic.AsyncAnthropic = _blocked
    try:
        yield
    finally:
        anthropic.AsyncAnthropic = orig


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("alice", password="pw123456")


@pytest.fixture
def user2(db):
    return get_user_model().objects.create_user("bob", password="pw123456")


@pytest.fixture
def logged_client(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def conversation(user):
    from chat.models import Conversation

    return Conversation.objects.create(owner=user, title="Nova conversa")


@pytest.fixture
def file_db(tmp_path):
    """Banco SQLite em ARQUIVO (concorrência/restart de verdade, não :memory:).

    Restaura NAME manualmente: o fixture `settings` não rastreia mutação
    in-place de dicts aninhados.
    """
    from django.conf import settings as dj_settings
    from django.core.management import call_command
    from django.db import connections

    old_name = dj_settings.DATABASES["default"]["NAME"]
    path = str(tmp_path / "test-file.sqlite3")
    dj_settings.DATABASES["default"]["NAME"] = path
    try:
        connections.close_all()
        call_command("migrate", run_syncdb=True, verbosity=0)
        yield path
    finally:
        connections.close_all()
        dj_settings.DATABASES["default"]["NAME"] = old_name
        connections.close_all()
