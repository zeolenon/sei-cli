from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from sei_cli.config import orgao_to_value
from sei_cli.tls import create_verified_context
from sei_cli.models import Credentials, LoginStatus

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def create_http_client() -> httpx.Client:
    """Create an httpx client that does NOT auto-follow redirects.
    
    SEI's login flow involves 4 chained redirects, and the inicializar.php
    step sets a NEW PHPSESSID cookie. Auto-following loses this cookie
    in some HTTP clients, so we follow redirects manually.
    """
    return httpx.Client(
        follow_redirects=False,
        headers={"User-Agent": UA},
        timeout=60,
        verify=create_verified_context(),
    )


class SessionAccessError(RuntimeError):
    """Existing session/context cannot be used; never implies permission to log in."""


def _follow(client: httpx.Client, response: httpx.Response, base_url: str, *, stop_on_login: bool = False) -> httpx.Response:
    """Manually follow redirects, preserving cookies at each hop.
    
    Uses response.url as base for relative redirects, falling back to base_url.
    This is critical because SEI redirects cross domains (sip → sei).
    """
    max_hops = 10
    for _ in range(max_hops):
        if response.status_code not in (301, 302, 303, 307, 308):
            return response
        location = response.headers.get("location", "")
        if not location:
            return response
        # Try resolving against the current response URL first
        current_base = str(response.url)
        url = urljoin(current_base, location)
        # If it still looks relative and we have a base, try that
        if not url.startswith("http"):
            url = urljoin(base_url, location)
        if stop_on_login and ("/sip/login.php" in url or "acao=usuario_login" in url):
            raise SessionAccessError(
                "Sessão/contexto inacessível: redirecionamento para login; autenticação automática desativada."
            )
        response = client.get(url)
    return response


def login(client: httpx.Client, creds: Credentials) -> tuple[LoginStatus, str]:
    """Login to SEI, returns (status, page_html).
    
    Flow:
    1. GET /sei/controlador.php?acao=usuario_login → 302 → /sip/login.php
    2. GET /sip/login.php → 200 (login form)
    3. POST /sip/login.php → 302 → /sei/inicializar.php (sets new PHPSESSID!)
    4. GET /sei/inicializar.php → 302 → /sei/controlador.php?acao=procedimento_controlar
    5. GET controlador.php → 200 (Controle de Processos)
    """
    # Step 1-2: Get login page
    r1 = client.get(f"{creds.login_url}")
    r1 = _follow(client, r1, creds.login_url)

    # Parse form to get action URL
    soup = BeautifulSoup(r1.text, "lxml")
    form = soup.find("form")
    if not form:
        return LoginStatus(success=False, message="Login form not found"), ""

    form_action = form.get("action", "login.php")
    post_url = urljoin(str(r1.url), form_action)

    # Step 3: POST login
    r2 = client.post(
        post_url,
        data={
            "txtUsuario": creds.usuario,
            "pwdSenha": creds.senha,
            "selOrgao": orgao_to_value(creds.orgao),
            "hdnAcao": "2",  # CRITICAL: must be "2", not "1"
        },
    )

    # Step 4-5: Follow all redirects (inicializar.php → controlador.php)
    r_final = _follow(client, r2, "https://sei.rn.gov.br/sei/")

    page_html = r_final.text
    ok = "Controle de Processos" in page_html

    return (
        LoginStatus(
            success=ok,
            message="Login efetuado com sucesso" if ok else "Falha no login",
            current_url=str(r_final.url),
        ),
        page_html,
    )


def check_session(client: httpx.Client, control_url: str) -> tuple[bool, str]:
    """Check if current session is valid by accessing control page.
    Returns (is_valid, page_html).
    """
    r = client.get(control_url)
    r = _follow(client, r, control_url)
    html = r.text
    return "Controle de Processos" in html, html
