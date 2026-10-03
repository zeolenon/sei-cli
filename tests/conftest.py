from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def offline_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests independent of local credentials and the Bitwarden vault."""
    monkeypatch.setenv("SEI_USUARIO", "test-user")
    monkeypatch.setenv("SEI_SENHA", "test-password")
    monkeypatch.setenv("SEI_ORGAO", "CBM")
    monkeypatch.setenv("SEI_LOGIN_URL", "https://sei.example.test/sip/login.php")


@pytest.fixture
def login_html() -> str:
    return (FIXTURES / "login_page.html").read_text(encoding="utf-8")


@pytest.fixture
def controle_html() -> str:
    return (FIXTURES / "controle_processos.html").read_text(encoding="utf-8")


@pytest.fixture
def recebidos_html() -> str:
    return (FIXTURES / "tblProcessosRecebidos.html").read_text(encoding="utf-8")


@pytest.fixture
def gerados_html() -> str:
    return (FIXTURES / "tblProcessosGerados.html").read_text(encoding="utf-8")
