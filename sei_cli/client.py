"""SEI HTTP Client — full workflow operations.

Handles login, process listing, document creation/editing, signing,
and unit switching via pure HTTP (no browser needed).

Key insight: After login, the initial page load is the ONLY guaranteed
request that works. Subsequent requests to controlador.php fail because
SEI validates infra_hash per-session. So we cache the control page HTML
and extract all navigation URLs from it.

Cookie flow: Login creates PHPSESSID on /sip/, then inicializar.php
creates a NEW PHPSESSID for /sei/. Must follow redirects manually
(hop-by-hop) to capture both cookies.
"""

from __future__ import annotations

import contextlib
import html
import json
import re
import time
import unicodedata
from typing import Any, Iterator
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from sei_cli import auth


def _sanitize_for_iso_8859_1(text: str) -> str:
    """Sanitize text to ISO-8859-1 by replacing unsupported Unicode chars.
    
    SEI form submissions use ISO-8859-1 encoding. Characters like em-dash (—, U+2014)
    or fancy quotes (" ", '') don't exist in this charset and cause UnicodeEncodeError.
    This function replaces them with ASCII equivalents.
    
    Args:
        text: Text that may contain non-ISO-8859-1 characters.
        
    Returns:
        Text with unsafe characters replaced by ISO-8859-1 equivalents.
    """
    replacements = {
        '—': '-',      # em-dash → hyphen
        '–': '-',      # en-dash → hyphen
        '\u201c': '"',  # left double quote → ASCII quote
        '\u201d': '"',  # right double quote → ASCII quote
        '\u2018': "'",  # left single quote → ASCII apostrophe
        '\u2019': "'",  # right single quote → ASCII apostrophe
        '…': '...',    # ellipsis → three dots
    }
    result = text
    for unicode_char, ascii_equiv in replacements.items():
        result = result.replace(unicode_char, ascii_equiv)
    return result


def _normalize_error_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return normalized.casefold()


def _extract_sei_error_message(soup: BeautifulSoup, html_text: str) -> str | None:
    error_keywords = (
        "erro",
        "falha",
        "nao foi possivel",
        "invalido",
        "obrigatorio",
        "insuficient",
        "suficient",
        "rejeit",
    )

    def is_error_message(text: str, marker: str = "") -> bool:
        normalized_text = _normalize_error_text(text)
        normalized_marker = _normalize_error_text(marker)
        if "erro" in normalized_marker or "danger" in normalized_marker:
            return bool(text.strip())
        return any(keyword in normalized_text for keyword in error_keywords)

    candidates: list[tuple[str, str]] = []
    for node in soup.find_all(
        attrs={
            "class": re.compile(r"(alert|erro|infraMsg|infraMensagem)", re.I),
        }
    ):
        classes = " ".join(node.get("class", []))
        candidates.append((node.get_text(" ", strip=True), classes))

    for node in soup.find_all(id=re.compile(r"(divInfraMsg|infraMsg|erro)", re.I)):
        candidates.append((node.get_text(" ", strip=True), str(node.get("id", ""))))

    for match in re.finditer(
        r"alert\((?P<quote>['\"])(?P<text>.*?)(?P=quote)\)",
        html_text,
        re.DOTALL,
    ):
        text = (
            html.unescape(match.group("text"))
            .replace("\\n", " ")
            .replace("\\'", "'")
            .replace('\\"', '"')
        )
        candidates.append((text, "script-alert"))

    for text, marker in candidates:
        compact = re.sub(r"\s+", " ", text).strip()
        if compact and is_error_message(compact, marker):
            return compact[:500]

    return None


def _serialize_lupa_select_hidden(select: Any) -> str:
    """Mirror SEI's infraLupaSelect hidden field serialization."""
    items: list[str] = []
    for option in select.find_all("option"):
        value = option.get("value", "")
        if not value:
            continue
        label = option.get_text(" ", strip=True)
        items.append(f"{value}±{label}")
    return "¥".join(items)


from sei_cli.config import load_credentials, orgao_to_value, SESSION_PATH
from sei_cli.models import (
    Block, BlockDocument, Document, DocumentCreated, DocumentType,
    EditorSection, Marcador, Process, ProcessHistoryEntry, ProcessList, SystemStatus,
    TramitarForm, TreeDocument, TreeFolder, Unit,
)
from sei_cli.relatorio_parser import (
    RelatorioServico,
    parse_relatorio,
    summarize_batch,
    summarize as summarize_relatorio,
)
from sei_cli.parsers import (
    parse_block_documents,
    parse_blocks,
    parse_document_tree,
    parse_expanded_folder,
    parse_marcador_form,
    parse_marcadores_list,
    parse_menu_links,
    parse_processes,
    parse_process_history,
    parse_system_status,
    parse_tramitar_form,
    parse_tree_folders,
    parse_tree_signatures,
    parse_unit_switch_form,
    parse_unit_switch_link,
    parse_units_switch_page,
)


class SEIClient:
    BASE = "https://sei.rn.gov.br"

    # Control page path (session-independent)
    _CONTROL_PATH = (
        "controlador.php?acao=procedimento_controlar&infra_sistema=100000100"
    )

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or self.BASE).rstrip("/")
        self.client = auth.create_http_client()
        self._control_html: str | None = None
        self._menu_links: dict[str, str] = {}
        self._editor_hiddens: dict[str, str] = {}
        self._current_unit_id: str | None = None
        # Session management
        self._last_login: float = 0.0
        self._batch_active: bool = False
        # infra_hash pool: acao_name -> [hash1, hash2, ...]
        self._hash_pool: dict[str, list[str]] = {}
        # Load persisted session cookie
        self._restore_session()

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "SEIClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # --- Internal HTTP helpers ---

    def _get(self, url: str) -> httpx.Response:
        r = self.client.get(url)
        r = auth._follow(self.client, r, self.base_url)
        with contextlib.suppress(Exception):
            r.encoding = "iso-8859-1"
        self._harvest_hashes(r.text)
        return r

    @staticmethod
    def _sanitize_form_value_for_encoding(value: Any, encoding: str) -> str:
        text = "" if value is None else str(value)
        replacements = {
            "\u2014": "-",
            "\u2013": "-",
            "\u2018": "'",
            "\u2019": "'",
            "\u201c": "\"",
            "\u201d": "\"",
            "\u00a0": " ",
            "\u2026": "...",
        }
        for src, dst in replacements.items():
            text = text.replace(src, dst)
        try:
            text.encode(encoding)
            return text
        except UnicodeEncodeError:
            normalized = unicodedata.normalize("NFKD", text)
            ascii_like = normalized.encode(encoding, errors="replace").decode(encoding)
            return ascii_like

    def _post(self, url: str, data: dict, *, encoding: str = "iso-8859-1") -> httpx.Response:
        """POST form data, defaulting to ISO-8859-1 (SEI's native encoding).

        SEI pages declare charset=ISO-8859-1 and expect form submissions
        in the same encoding. Using UTF-8 would corrupt accented characters.
        """
        from urllib.parse import urlencode as _urlencode
        sanitized_items = [
            (key, self._sanitize_form_value_for_encoding(value, encoding))
            for key, value in data.items()
        ]
        body = _urlencode(sanitized_items, encoding=encoding)
        r = self.client.post(
            url,
            content=body.encode(encoding),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        r = auth._follow(self.client, r, self.base_url)
        with contextlib.suppress(Exception):
            r.encoding = "iso-8859-1"
        self._harvest_hashes(r.text)
        return r

    def _post_pairs(
        self,
        url: str,
        pairs: "list[tuple[str, str]]",
        *,
        encoding: str = "iso-8859-1",
    ) -> httpx.Response:
        """POST form data from a list of (key, value) tuples.

        Unlike ``_post`` (which takes a dict), this supports repeated keys
        — needed for ``<select multiple>`` fields.
        """
        from urllib.parse import urlencode as _urlencode
        sanitized_pairs = [
            (key, self._sanitize_form_value_for_encoding(value, encoding))
            for key, value in pairs
        ]
        body = _urlencode(sanitized_pairs, encoding=encoding)
        r = self.client.post(
            url,
            content=body.encode(encoding),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        r = auth._follow(self.client, r, self.base_url)
        with contextlib.suppress(Exception):
            r.encoding = "iso-8859-1"
        self._harvest_hashes(r.text)
        return r

    def _sei_url(self, path: str) -> str:
        return f"{self.base_url}/sei/{path}"

    def _extract_action_url(self, html: str, token: str) -> str | None:
        """Extract action URL from href or JS onclick snippets.

        Uses BeautifulSoup first, then falls back to regex for malformed HTML
        (SEI often has unclosed tags that confuse lxml).
        """
        soup = BeautifulSoup(html, "lxml")
        for a in soup.find_all("a"):
            href = a.get("href", "")
            onclick = a.get("onclick", "")
            if token in href:
                return urljoin(self._sei_url(""), href)
            if token in onclick:
                m = re.search(r"'([^']*" + re.escape(token) + r"[^']*)'", onclick)
                if m:
                    return urljoin(self._sei_url(""), m.group(1))
                m = re.search(r'"([^"]*' + re.escape(token) + r'[^"]*)"', onclick)
                if m:
                    return urljoin(self._sei_url(""), m.group(1))

        # Fallback: regex on raw HTML (handles malformed tags lxml can't parse)
        m = re.search(r'href=["\']([^"\']*' + re.escape(token) + r'[^"\']*)["\']', html)
        if m:
            return urljoin(self._sei_url(""), m.group(1))
        return None

    def _find_process_link(self, control_html: str, id_procedimento: str) -> str | None:
        soup = BeautifulSoup(control_html, "lxml")
        for a in soup.find_all("a"):
            href = a.get("href", "")
            if "procedimento_trabalhar" in href and f"id_procedimento={id_procedimento}" in href:
                return urljoin(self._sei_url(""), href)
        return None

    def _navigate_process_page(self, id_procedimento: str) -> httpx.Response:
        html = self._ensure_control()
        proc_link = self._find_process_link(html, id_procedimento)
        if not proc_link:
            raise RuntimeError(
                f"Processo {id_procedimento} não encontrado na unidade ativa. "
                "Verifique se o processo está visível nessa unidade."
            )
        return self._get(proc_link)

    # --- Auth ---

    def login(self) -> SystemStatus:
        creds = load_credentials()
        status, html = auth.login(self.client, creds)
        if not status.success:
            raise RuntimeError(status.message)
        self._control_html = html
        self._menu_links = parse_menu_links(html, self._sei_url(""))
        self._last_login = time.time()
        self._persist_session()
        return parse_system_status(html)

    def _restore_session(self) -> None:
        """Load persisted PHPSESSID from disk into httpx client."""
        from sei_cli.config import load_session

        data = load_session()
        if data and data.get("phpsessid"):
            self.client.cookies.set(
                "PHPSESSID", data["phpsessid"],
                domain="sei.rn.gov.br", path="/",
            )
            self._current_unit_id = data.get("unit_id")

    def _persist_session(self) -> None:
        """Save current PHPSESSID + unit to disk for reuse."""
        from sei_cli.config import save_session

        cookie = self.client.cookies.get("PHPSESSID")
        if cookie:
            save_session(cookie, self._current_unit_id)

    def _try_inicializar(self) -> str | None:
        """Fast session refresh via inicializar.php (~0.2s).

        If the PHPSESSID is still valid server-side, inicializar.php
        redirects to the control page with fresh infra_hash values.
        Returns control page HTML on success, None on failure.
        """
        r = self.client.get(self._sei_url("inicializar.php"))
        if r.status_code != 302:
            return None
        location = r.headers.get("location", "")
        if not location or "login.php" in location:
            return None
        # Resolve relative URL
        full_url = urljoin(self._sei_url("inicializar.php"), location)
        r2 = self.client.get(full_url)
        if r2.status_code != 200:
            return None

        # Direct hit: already on the process control page
        if "frmProcedimentoControlar" in r2.text:
            return r2.text

        # SEI 4+ often lands on acao=principal (wrapper page).
        # Extract the procedimento_controlar link with its valid infra_hash.
        import re as _re
        # Try iframe src first (some SEI versions embed the control page)
        iframe_match = _re.search(
            r'<iframe[^>]+src=["\']([^"\'>]*procedimento_controlar[^"\'>]*)["\']',
            r2.text,
        )
        if iframe_match:
            ctrl_url = urljoin(self._sei_url(""), iframe_match.group(1).replace("&amp;", "&"))
        else:
            # Fallback: find any procedimento_controlar link in the HTML
            link_match = _re.search(
                r'controlador\.php\?acao=procedimento_controlar[^"\'\'\s]*',
                r2.text,
            )
            if not link_match:
                return None
            ctrl_url = self._sei_url(link_match.group(0).replace("&amp;", "&"))

        r3 = self.client.get(ctrl_url)
        if r3.status_code == 200 and "frmProcedimentoControlar" in r3.text:
            return r3.text
        return None

    def _ensure_session(self) -> str:
        """Restore session with minimal overhead.

        Strategy (fast → slow):
        1. Try inicializar.php with existing cookie (~0.2s)
        2. Full POST login as fallback (~1.5s)

        After successful login, persists cookie to disk for reuse
        by other agents/processes.
        """
        # Fast path: reuse existing PHPSESSID via inicializar.php
        if self.client.cookies.get("PHPSESSID"):
            html = self._try_inicializar()
            if html:
                self._control_html = html
                self._menu_links = parse_menu_links(html, self._sei_url(""))
                return html

        # Slow path: full login (clear stale cookies first)
        self.client.cookies.clear()
        creds = load_credentials()
        status, html = auth.login(self.client, creds)
        if not status.success:
            raise RuntimeError(status.message)
        self._control_html = html
        self._menu_links = parse_menu_links(html, self._sei_url(""))
        self._last_login = time.time()
        self._persist_session()
        return html

    def _ensure_control(self) -> str:
        """Return cached control page HTML, refreshing session if needed.

        KEY OPTIMIZATION: When _control_html is None (set after each
        navigation), this now calls _ensure_session() which tries a GET
        of the control page before falling back to full POST login.
        This reduces ~40 re-logins per session to near zero.
        """
        if self._control_html:
            if self._is_valid_control_html(self._control_html):
                return self._control_html
            self._control_html = None
        return self._ensure_session()

    def _fresh_control(self) -> str:
        """Force refresh of control page (smart: GET if session valid, login if not)."""
        self._control_html = None
        return self._ensure_session()

    def _is_valid_control_html(self, html: str) -> bool:
        if not html or "pwdSenha" in html or "login.php" in html:
            return False
        with contextlib.suppress(Exception):
            return parse_system_status(html).valid
        return False

    @contextlib.contextmanager
    def batch_mode(self) -> "Iterator[SEIClient]":
        """Context manager for batch operations requiring session stability.

        Ensures a single login at the start and keeps the session alive
        across multiple operations. Particularly useful for reading 7+
        documents in sequence without triggering re-logins.

        Usage::

            with client.batch_mode() as c:
                for doc_id in doc_ids:
                    c.read_relatorio(doc_id, proc_id)

        Inside batch_mode:
        - Login is performed once at entry if not already logged in
        - _ensure_session() is called on each _ensure_control() miss
          (fast GET, not slow POST login)
        - Session state is restored on exit
        """
        prev_batch = self._batch_active
        self._batch_active = True
        # Ensure we have a valid session at the start
        if not self._control_html:
            self._ensure_session()
        try:
            yield self
        finally:
            self._batch_active = prev_batch

    def _navigate_with_retry(
        self,
        fn: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Execute fn(*args, **kwargs), retrying once on session failure.

        Detects session expiry by checking for login redirects or
        'pwdSenha' indicators in HTTP responses. On first failure,
        refreshes the session and retries. Maximum 2 attempts.

        Args:
            fn: Callable to execute.
            *args, **kwargs: Arguments passed to fn.

        Returns:
            Result of fn(*args, **kwargs).

        Raises:
            RuntimeError: If retry also fails (re-raises original error).
        """
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                result = fn(*args, **kwargs)
                # Detect session expiry in HTTP response
                if isinstance(result, httpx.Response):
                    url_str = str(result.url)
                    if "login.php" in url_str or "pwdSenha" in result.text:
                        raise RuntimeError(
                            "Session expired during navigation (login redirect detected)"
                        )
                return result
            except RuntimeError as exc:
                last_exc = exc
                msg = str(exc).lower()
                is_session_error = any(
                    kw in msg
                    for kw in (
                        "session expired", "login", "sessão expirou",
                        "expirou", "não encontrado na unidade",
                    )
                )
                if attempt == 0 and is_session_error:
                    # Refresh session and retry
                    self._control_html = None
                    self._ensure_session()
                    continue
                raise
        raise last_exc  # type: ignore[misc]

    def _harvest_hashes(self, response_text: str) -> None:
        """Extract infra_hash values from an HTML response and cache them.

        Parses ``acao=xxx&...infra_hash=<64-hex>`` patterns and stores
        hashes keyed by their action name. Keeps up to 5 hashes per action.
        These can be used as fallback CSRF tokens when a page's hash expires.

        Args:
            response_text: HTML content to extract hashes from.
        """
        for match in re.finditer(
            r"acao=([a-z_]+)[^\"']*infra_hash=([a-f0-9]{64})",
            response_text,
        ):
            acao, h = match.group(1), match.group(2)
            pool = self._hash_pool.setdefault(acao, [])
            if h not in pool:
                pool.append(h)
                if len(pool) > 5:
                    pool.pop(0)

    # --- Status ---

    def status(self) -> SystemStatus:
        html = self._ensure_control()
        return parse_system_status(html)

    # --- Processes ---

    def list_processes(self, unit: str | None = None) -> ProcessList:
        """List processes in the control page.

        Args:
            unit: Optional unit keyword to switch to before listing.
        """
        if unit:
            self.switch_unit(unit)
        html = self._ensure_control()
        return parse_processes(html, base_url=self._sei_url(""))

    def get_process_documents(
        self, id_procedimento: str, *, process_html: str | None = None
    ) -> list[Document]:
        """Get document tree for a process by parsing the JS tree.

        .. deprecated::
            Use :meth:`get_full_document_tree` instead, which automatically
            expands lazy-loaded folders and returns richer TreeDocument objects.

        Args:
            id_procedimento: The internal SEI procedure ID.
            process_html: If provided, skip navigation and extract the
                ifrArvore iframe directly from this HTML (e.g. the page
                returned by ``search()``).  This avoids hash-invalidation
                issues that occur when navigating from the control page.
        """
        if process_html is not None:
            # Fast path: caller already has the process page HTML
            psoup = BeautifulSoup(process_html, "lxml")
        else:
            # Legacy path: navigate from control page
            psoup = self._navigate_to_process_page(id_procedimento)
            if psoup is None:
                return []

        # Find ifrArvore iframe
        iframe = psoup.find("iframe", {"name": "ifrArvore"})
        if not iframe or not iframe.get("src"):
            return []

        arvore_url = urljoin(self._sei_url(""), iframe["src"])
        ra = self._get(arvore_url)

        # Invalidate control page cache after navigating away
        self._control_html = None

        return parse_document_tree(ra.text, base_url=self._sei_url(""))

    def _navigate_to_process_page(self, id_procedimento: str):
        """Navigate to a process page and return its BeautifulSoup, or None.

        Strategy order:
        1. Look for a direct link with valid hash in the control page
           (works when the process is in the inbox).
        2. Use pesquisa rápida (search) which generates its own valid hash
           (works for formatted process numbers like 08810198.000066/2026-91,
           but NOT for bare numeric id_procedimento values).
        """
        # Strategy 1: direct link from control page (preserves valid hash)
        html = self._ensure_control()
        soup = BeautifulSoup(html, "lxml")
        for a in soup.find_all("a"):
            href = a.get("href", "")
            if "procedimento_trabalhar" in href and id_procedimento in href:
                proc_link = urljoin(self._sei_url(""), href)
                r = self._get(proc_link)
                if "ifrArvore" in r.text:
                    self._control_html = None
                    return BeautifulSoup(r.text, "lxml")
                break

        # Strategy 2: pesquisa rápida — valid for formatted SEI numbers
        try:
            result_html = self.search(id_procedimento)
            if "ifrArvore" in result_html:
                return BeautifulSoup(result_html, "lxml")
        except Exception:
            pass

        return None

    # --- Process creation ---

    def _load_process_creation_form(
        self,
        tipo_processo_id: str,
    ) -> tuple[BeautifulSoup, dict[str, str], str]:
        """Load the process creation form for a specific process type."""
        html = self._ensure_control()

        href_match = re.search(
            r'href="(controlador\.php\?acao=procedimento_escolher_tipo[^"]+)"',
            html,
        )
        if not href_match:
            raise RuntimeError("Link 'Iniciar Processo' não encontrado")

        tipo_url = self._sei_url(href_match.group(1))
        r_tipo = self._get(tipo_url)
        expanded_html, soup_tipo, form_tipo = self._expand_chooser_form(
            r_tipo.text,
            form_id="frmProcedimentoEscolherTipo",
            filter_field="hdnFiltroTipoProcedimento",
        )
        if form_tipo is None:
            soup_tipo = BeautifulSoup(expanded_html, "lxml")
            form_tipo = soup_tipo.find("form", id="frmProcedimentoEscolherTipo")
        if not form_tipo:
            raise RuntimeError("Formulário de escolha de tipo não encontrado")

        action_tipo = urljoin(self._sei_url(""), form_tipo["action"])
        data_tipo: dict[str, str] = {}
        for inp in form_tipo.find_all("input"):
            n = inp.get("name", "")
            if n and inp.get("type") != "checkbox":
                data_tipo[n] = inp.get("value", "")
        data_tipo["hdnIdTipoProcedimento"] = tipo_processo_id

        r_cad = self._post(action_tipo, data_tipo)
        soup_cad = BeautifulSoup(r_cad.text, "lxml")
        form_cad = soup_cad.find("form", id="frmProcedimentoCadastro")
        if not form_cad:
            raise RuntimeError("Formulário de cadastro de processo não encontrado")

        action_cad = urljoin(self._sei_url(""), form_cad["action"])

        data_cad: dict[str, str] = {}
        for inp in form_cad.find_all("input"):
            n = inp.get("name", "")
            if not n:
                continue
            if inp.get("type") == "radio":
                if inp.get("checked"):
                    data_cad[n] = inp.get("value", "")
            else:
                data_cad[n] = inp.get("value", "")
        for sel in form_cad.find_all("select"):
            n = sel.get("name", "")
            if n:
                selected = sel.find("option", selected=True)
                data_cad[n] = selected["value"] if selected else ""
        for ta in form_cad.find_all("textarea"):
            n = ta.get("name", "")
            if n:
                data_cad[n] = ta.get_text()

        return soup_cad, data_cad, action_cad

    def _parse_chooser_types(self, html: str) -> list[DocumentType]:
        """Parse chooser pages that use `escolher(ID)` links.

        This captures visible and collapsed entries present in the HTML.
        """
        soup = BeautifulSoup(html, "lxml")
        types: list[DocumentType] = []
        seen: set[str] = set()
        for a in soup.find_all("a"):
            onclick = a.get("onclick", "")
            m = re.search(r"escolher\((-?\d+)\)", onclick)
            if not m:
                continue
            type_id = m.group(1)
            if type_id in seen:
                continue
            name = a.get_text(" ", strip=True)
            if not name:
                continue
            seen.add(type_id)
            types.append(DocumentType(id_serie=type_id, nome=name))

        if types:
            return types

        for match in re.finditer(
            r"""<a[^>]+onclick=["']escolher\((-?\d+)\)["'][^>]*>(.*?)</a>""",
            html,
            re.IGNORECASE | re.DOTALL,
        ):
            type_id = match.group(1)
            if type_id in seen:
                continue
            name = re.sub(r"<[^>]+>", " ", match.group(2))
            name = " ".join(name.split()).strip()
            if not name:
                continue
            seen.add(type_id)
            types.append(DocumentType(id_serie=type_id, nome=name))
        return types

    def _expand_chooser_form(
        self,
        html: str,
        *,
        form_id: str,
        filter_field: str,
        expanded_value: str = "T",
    ) -> tuple[str, BeautifulSoup, Any | None]:
        """Expand chooser pages that use form submit behind the green '+'."""
        soup = BeautifulSoup(html, "lxml")
        form = soup.find("form", id=form_id)
        if not form:
            return html, soup, None
        current_input = form.find(attrs={"name": filter_field})
        current_value = ""
        if current_input is not None:
            current_value = str(current_input.get("value", ""))
        if current_value == expanded_value:
            return html, soup, form
        action = form.get("action")
        if not action:
            return html, soup, form
        action_url = urljoin(self._sei_url(""), action)
        data = self._collect_form_defaults(form)
        data[filter_field] = expanded_value
        response = self._post(action_url, data)
        expanded_html = response.text
        expanded_soup = BeautifulSoup(expanded_html, "lxml")
        expanded_form = expanded_soup.find("form", id=form_id)
        return expanded_html, expanded_soup, expanded_form

    def list_process_types(self) -> list[DocumentType]:
        """List available process types from the chooser page."""
        html = self._ensure_control()
        href_match = re.search(
            r'href="(controlador\.php\?acao=procedimento_escolher_tipo[^"]+)"',
            html,
        )
        if not href_match:
            return []
        tipo_url = self._sei_url(href_match.group(1))
        r_tipo = self._get(tipo_url)
        expanded_html, _expanded_soup, _expanded_form = self._expand_chooser_form(
            r_tipo.text,
            form_id="frmProcedimentoEscolherTipo",
            filter_field="hdnFiltroTipoProcedimento",
        )
        return self._parse_chooser_types(expanded_html)

    def _collect_form_defaults(self, form: Any) -> dict[str, str]:
        data: dict[str, str] = {}
        for inp in form.find_all("input"):
            n = inp.get("name", "")
            if not n:
                continue
            if inp.get("type") == "radio":
                if inp.get("checked"):
                    data[n] = inp.get("value", "")
            else:
                data[n] = inp.get("value", "")
        for sel in form.find_all("select"):
            n = sel.get("name", "")
            if n:
                selected = sel.find("option", selected=True)
                data[n] = selected["value"] if selected else ""
        for ta in form.find_all("textarea"):
            n = ta.get("name", "")
            if n:
                data[n] = ta.get_text()
        return data

    def _extract_access_hypotheses_from_form(
        self,
        soup_cad: BeautifulSoup,
        data_cad: dict[str, str],
        *,
        nivel_acesso: str,
    ) -> dict[str, Any]:
        form_cad = soup_cad.find("form")
        assert form_cad is not None

        def field_label(tag: Any) -> str:
            tag_id = tag.get("id")
            if tag_id:
                label = soup_cad.find("label", attrs={"for": tag_id})
                if label:
                    return label.get_text(" ", strip=True)
            return tag.get("title") or tag.get("aria-label") or tag.get("name") or tag.get("id") or ""

        access_hypotheses: list[dict[str, Any]] = []
        for sel in form_cad.find_all("select"):
            name = sel.get("name", "")
            if not name:
                continue
            haystack = " ".join(
                filter(
                    None,
                    [
                        name,
                        sel.get("id", ""),
                        sel.get("title", ""),
                        sel.get("aria-label", ""),
                        field_label(sel),
                    ],
                )
            ).lower()
            if not any(token in haystack for token in ("hipot", "hipót", "motivo", "fundamento", "base legal", "restr", "sigil")):
                continue
            options = [
                {
                    "value": opt.get("value", ""),
                    "label": opt.get_text(" ", strip=True),
                    "selected": bool(opt.get("selected")),
                }
                for opt in sel.find_all("option")
                if opt.get("value")
            ]
            if not options:
                continue
            hidden_name = None
            if name.startswith("sel"):
                candidate_hidden = f"hdn{name[3:]}"
                if candidate_hidden in data_cad:
                    hidden_name = candidate_hidden
            access_hypotheses.append(
                {
                    "field_name": name,
                    "field_id": sel.get("id"),
                    "field_label": field_label(sel),
                    "hidden_field_name": hidden_name,
                    "options": options,
                }
            )

        by_level = {
            "1": {"selHipoteseLegal"},
            "2": {"selGrauSigilo", "selHipoteseLegal"},
        }
        expected_fields = by_level.get(nivel_acesso)
        warnings: list[str] = []
        if expected_fields:
            filtered = [item for item in access_hypotheses if item["field_name"] in expected_fields]
            if filtered:
                access_hypotheses = filtered

        for item in access_hypotheses:
            option_values = [opt["value"] for opt in item["options"]]
            if item["field_name"] == "selHipoteseLegal" and option_values in ([], ["null"]):
                ajax_options = self._fetch_hipotese_legal_options(soup_cad, nivel_acesso)
                if ajax_options:
                    item["options"] = ajax_options
                    item["ajax_resolved"] = True
                else:
                    warnings.append("Hipóteses legais de acesso parecem ser carregadas via AJAX; preview incompleto.")

        return {
            "nivel_acesso_field": "rdoNivelAcesso",
            "access_hypotheses": access_hypotheses,
            "warnings": warnings,
        }

    def _parse_select_options_response(self, text: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(text, "lxml")
        options = [
            {
                "value": opt.get("value", ""),
                "label": opt.get_text(" ", strip=True),
                "selected": bool(opt.get("selected")),
            }
            for opt in soup.find_all("option")
            if opt.get("value") and opt.get("value") != "null"
        ]
        if options:
            return options

        matches = re.findall(
            r"new\s+Option\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]",
            text,
        )
        return [
            {"label": label.strip(), "value": value.strip(), "selected": False}
            for label, value in matches
            if value.strip() and value.strip() != "null"
        ]

    def _fetch_hipotese_legal_options(
        self,
        soup_cad: BeautifulSoup,
        nivel_acesso: str,
    ) -> list[dict[str, Any]]:
        script_text = "\n".join(
            script.get_text("\n", strip=False)
            for script in soup_cad.find_all("script")
            if "selHipoteseLegal" in script.get_text() and "infraAjaxMontarSelect" in script.get_text()
        )
        if not script_text:
            return []

        ajax_match = re.search(
            r"new\s+infraAjaxMontarSelect\(\s*'selHipoteseLegal'\s*,\s*'([^']+)'\)",
            script_text,
        )
        if not ajax_match:
            return []

        ajax_url = self._sei_url(ajax_match.group(1).replace("&amp;", "&"))
        sugestao = ""
        hidden = soup_cad.find("input", id="hdnIdHipoteseLegalSugestao")
        if hidden:
            sugestao = hidden.get("value", "")

        resp = self.client.post(
            ajax_url,
            data={
                "q": "",
                "id": "null",
                "idSugestao": sugestao,
                "staNivelAcesso": nivel_acesso,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        return self._parse_select_options_response(resp.text)

    def get_process_creation_metadata(
        self,
        tipo_processo_id: str,
        *,
        nivel_acesso: str = "0",
    ) -> dict[str, Any]:
        """Inspect the creation form and expose relevant access hypotheses."""
        soup_cad, data_cad, _action_cad = self._load_process_creation_form(tipo_processo_id)
        access_metadata = self._extract_access_hypotheses_from_form(
            soup_cad,
            data_cad,
            nivel_acesso=nivel_acesso,
        )
        return {
            "tipo_processo_id": tipo_processo_id,
            **access_metadata,
        }

    def get_process_access_metadata(self, id_procedimento: str) -> dict[str, Any]:
        """Read the current access policy from the process alter form.

        Returns the active access level plus the selected legal hypothesis fields
        in the same structure expected by the canonical writing operations.
        """
        arvore_html = self._navigate_to_arvore(id_procedimento)
        if not arvore_html:
            raise RuntimeError("Não foi possível acessar a árvore do processo")

        start = arvore_html.find('Nos[0]')
        end = arvore_html.find('Nos[1]', start)
        nos0 = arvore_html[start:end].replace('\\"', '"')

        alt_m = re.search(
            r'href="(controlador\.php\?acao=procedimento_alterar[^"]*)"',
            nos0,
        )
        if not alt_m:
            raise RuntimeError("Alterar Processo action not found in toolbar")

        alt_url = self._sei_url(alt_m.group(1).replace('&amp;', '&'))
        r_form = self._get(alt_url)
        soup = BeautifulSoup(r_form.text, "lxml")
        form = soup.find("form", id="frmProcedimentoCadastro")
        if not form:
            raise RuntimeError("Alter process form not found")

        data = self._collect_form_defaults(form)
        nivel_acesso = data.get("rdoNivelAcesso", "0")
        access_metadata = self._extract_access_hypotheses_from_form(
            soup,
            data,
            nivel_acesso=nivel_acesso,
        )

        selected_items: list[dict[str, Any]] = []
        selected_extra_fields: dict[str, str] = {}
        for field in access_metadata.get("access_hypotheses", []):
            field_name = field["field_name"]
            selected_value = data.get(field_name, "")
            if not selected_value:
                continue
            option = next(
                (item for item in field["options"] if item["value"] == selected_value),
                None,
            )
            if not option:
                continue
            selected_items.append(
                {
                    "field_name": field_name,
                    "field_label": field.get("field_label"),
                    "hidden_field_name": field.get("hidden_field_name"),
                    "value": option["value"],
                    "label": option["label"],
                }
            )
            selected_extra_fields[field_name] = option["value"]
            hidden_field = field.get("hidden_field_name")
            if hidden_field:
                selected_extra_fields[hidden_field] = option["value"]

        if nivel_acesso == "2":
            selected_hypothesis: dict[str, Any] | list[dict[str, Any]] | None = selected_items or None
        else:
            selected_hypothesis = selected_items[0] if selected_items else None

        return {
            "id_procedimento": id_procedimento,
            "nivel_acesso": nivel_acesso,
            "available_hypotheses": access_metadata.get("access_hypotheses", []),
            "selected_hypothesis": selected_hypothesis,
            "selected_extra_fields": selected_extra_fields,
            "warnings": access_metadata.get("warnings", []),
        }

    def _load_document_creation_form(
        self,
        id_procedimento: str,
        tipo: str,
    ) -> tuple[BeautifulSoup, dict[str, str], str, str]:
        normalized = tipo.lower().replace(" ", "_")
        id_serie = self.DOC_TYPES.get(normalized, tipo)
        arvore_html = self._navigate_to_arvore(id_procedimento)
        if not arvore_html:
            raise RuntimeError("Não foi possível acessar a árvore do processo")

        href_match = re.search(
            r'href="([^"]+)"[^>]*>\s*<img\s+[^>]*documento_incluir',
            arvore_html,
        )
        if not href_match:
            raise RuntimeError("Link 'Incluir Documento' não encontrado na árvore")

        incl_url = urljoin(self._sei_url(""), href_match.group(1))
        rtype = self._get(incl_url)
        expanded_html, tsoup, form = self._expand_chooser_form(
            rtype.text,
            form_id="frmDocumentoEscolherTipo",
            filter_field="hdnFiltroSerie",
        )
        if form is None:
            tsoup = BeautifulSoup(expanded_html, "lxml")
            form = tsoup.find("form", id="frmDocumentoEscolherTipo")
        if not form:
            raise RuntimeError("Formulário de escolha de tipo não encontrado")

        available_types = self._parse_chooser_types(expanded_html)
        by_id = next((item for item in available_types if item.id_serie == id_serie), None)
        if by_id is None and not str(id_serie).isdigit():
            normalized_name = tipo.strip().lower()
            by_name = next(
                (
                    item
                    for item in available_types
                    if item.nome.strip().lower() == normalized_name
                ),
                None,
            )
            if by_name:
                id_serie = by_name.id_serie

        form_action = urljoin(self._sei_url(""), form["action"])
        fdata: dict[str, str] = {}
        for inp in form.find_all("input"):
            n = inp.get("name", "")
            if n:
                fdata[n] = inp.get("value", "")
        fdata["hdnIdSerie"] = id_serie

        rcadastro = self._post(form_action, fdata)
        csoup = BeautifulSoup(rcadastro.text, "lxml")
        cform = csoup.find("form", id="frmDocumentoCadastro")
        if not cform:
            raise RuntimeError(
                "Formulário de cadastro não encontrado. "
                f"Tipo '{tipo}' (id_serie={id_serie}) pode não estar disponível."
            )

        cform_action = urljoin(self._sei_url(""), cform["action"])
        cdata = self._collect_form_defaults(cform)
        return csoup, cdata, cform_action, id_serie

    def get_document_creation_metadata(
        self,
        id_procedimento: str,
        tipo: str,
        *,
        nivel_acesso: str | None = None,
    ) -> dict[str, Any]:
        soup_cad, data_cad, _action, id_serie = self._load_document_creation_form(id_procedimento, tipo)
        default_nivel_acesso = data_cad.get("rdoNivelAcesso", "0")
        access_metadata = self._extract_access_hypotheses_from_form(
            soup_cad,
            data_cad,
            nivel_acesso=nivel_acesso or default_nivel_acesso,
        )
        text_options = []
        for value, label in (("N", "Nenhum"), ("T", "Texto Padrão"), ("D", "Documento Modelo")):
            text_options.append({"value": value, "label": label})
        return {
            "id_procedimento": id_procedimento,
            "tipo_documento_id": id_serie,
            "tipo_documento": tipo,
            "default_nivel_acesso": default_nivel_acesso,
            "texto_inicial_options": text_options,
            **access_metadata,
        }

    def create_process(
        self,
        tipo_processo_id: str,
        *,
        especificacao: str = "",
        interessados: str = "",
        observacoes: str = "",
        nivel_acesso: str = "0",  # 0=Público, 1=Restrito, 2=Sigiloso
        extra_fields: dict[str, str] | None = None,
    ) -> dict:
        """Create a new process (Iniciar Processo).

        Args:
            tipo_processo_id: The process type ID (e.g. '100000506' for Diárias).
            especificacao: Description/specification field.
            interessados: Interested party text.
            observacoes: Notes for the unit.
            nivel_acesso: '0' (Público), '1' (Restrito), '2' (Sigiloso).

        Returns:
            Dict with 'numero' (process number), 'id_procedimento', 'link'.
        """
        soup_cad, data_cad, action_cad = self._load_process_creation_form(tipo_processo_id)

        # Set our values
        data_cad["hdnFlagProcedimentoCadastro"] = "2"  # Critical: triggers save
        data_cad["rdoProtocolo"] = "A"  # Automático
        data_cad["selTipoProcedimento"] = tipo_processo_id
        data_cad["rdoNivelAcesso"] = nivel_acesso
        if especificacao:
            data_cad["txtDescricao"] = especificacao
        if observacoes:
            data_cad["txaObservacoes"] = observacoes

        # --- Fill required hidden fields from their select counterparts ---
        # SEI JS normally syncs these on form submit; we must do it manually.

        # hdnAssuntos: mandatory — JS validation rejects empty value.
        # Mirror SEI's infraLupaSelect hidden format: value±label¥value±label.
        if not data_cad.get("hdnAssuntos"):
            sel_a = soup_cad.find("select", id="selAssuntos")
            if sel_a:
                vals = [
                    o.get("value", "")
                    for o in sel_a.find_all("option")
                    if o.get("value")
                ]
                if vals:
                    data_cad["hdnAssuntos"] = _serialize_lupa_select_hidden(sel_a)
                    data_cad["selAssuntos"] = vals[0]

        # hdnInteressadosProcedimento: mirror from selInteressadosProcedimento
        if not data_cad.get("hdnInteressadosProcedimento"):
            sel_i = soup_cad.find("select", id="selInteressadosProcedimento")
            if sel_i:
                vals = [
                    o.get("value", "")
                    for o in sel_i.find_all("option")
                    if o.get("value")
                ]
                if vals:
                    data_cad["hdnInteressadosProcedimento"] = "|".join(vals) + "|"

        if extra_fields:
            data_cad.update(extra_fields)

        # Submit creation
        r_create = self._post(action_cad, data_cad)
        self._control_html = None
        csoup = BeautifulSoup(r_create.text, "lxml")

        # Detect silent form reload (server-side validation failure).
        # When SEI rejects the form, it re-renders "Iniciar Processo" with
        # hdnFlagProcedimentoCadastro back on the page — not a redirect.
        if "frmProcedimentoCadastro" in r_create.text and "Iniciar Processo" in r_create.text:
            raise RuntimeError(
                "SEI create_process: server reloaded the form instead of "
                "saving. Likely missing required field (hdnAssuntos, "
                "rdoNivelAcesso, or interessado). Check form data."
            )

        error_message = _extract_sei_error_message(csoup, r_create.text)
        if error_message:
            raise RuntimeError(f"SEI create_process: {error_message}")

        # Parse response for process number
        url_str = str(r_create.url)
        id_proc_match = re.search(r"id_procedimento=(\d+)", url_str)
        if not id_proc_match:
            id_proc_match = re.search(r"id_procedimento=(\d+)", r_create.text)

        # Extract process number from page
        title = csoup.title.get_text() if csoup.title else ""
        # Title is usually "SEI - 08810xxx.xxxxxx/2026-xx"
        num_match = re.search(r"(\d{8}\.\d+/\d{4}-\d{2})", title)
        if not num_match:
            num_match = re.search(
                r"(\d{8}\.\d+/\d{4}-\d{2})", r_create.text
            )

        if not id_proc_match and not num_match:
            raise RuntimeError(
                "SEI create_process: servidor nao confirmou a criacao do processo "
                "nem retornou id_procedimento ou numero do processo."
            )

        return {
            "numero": num_match.group(1) if num_match else "",
            "id_procedimento": id_proc_match.group(1) if id_proc_match else "",
            "link": url_str,
        }

    # Process type constants for convenience
    PROC_TYPES: dict[str, str] = {
        "diarias": "100000506",
        "diarias_passagens": "100000506",
        "curso_proprio": "100000515",
        "curso_outra": "100000205",
        "ferias": "100000182",
        "escala_plantao": "100000191",
        "informacao": "100000595",
        "requerimento": "100000268",
        "suprimento_fundos": "100000815",
    }

    # --- Enhanced document tree with folder expansion ---

    def get_actions(
        self,
        id_procedimento: str,
        id_documento: str | None = None,
    ) -> dict[str, str]:
        """Return available actions for a process or document.

        Navigates to ``arvore_visualizar`` (optionally with a document
        selected) and extracts the ``var linkXxx = '...'`` JS variables
        that define the action menu URLs.

        Args:
            id_procedimento: Process ID.
            id_documento: If given, returns document-level actions.
                If None, returns process-level actions.

        Returns:
            Dict mapping JS variable names (e.g. ``linkExcluirDocumento``,
            ``linkAssinarDocumento``) to their full URL paths.
        """
        arvore_html = self._navigate_to_arvore(id_procedimento)
        if not arvore_html:
            return {}

        # Find the arvore_visualizar URL for the target
        target_id = id_documento or id_procedimento
        pattern = (
            rf'(controlador\.php\?acao=arvore_visualizar[^"]*'
            rf'id_{"documento" if id_documento else "procedimento"}={re.escape(target_id)}[^"]*)'
        )

        sel_url = None
        # Search in main tree and expanded folders
        m = re.search(pattern, arvore_html)
        if m:
            sel_url = self._sei_url(m.group(1).replace('&amp;', '&'))
            # For process-level, strip id_documento if present
            if not id_documento:
                sel_url = re.sub(r'&id_documento=\d+', '', sel_url)

        if not sel_url:
            # Try expanding lazy folders
            from sei_cli.parsers import parse_tree_folders
            for folder in parse_tree_folders(arvore_html):
                if folder.carregado:
                    continue
                r = self._post(self._sei_url(folder.link), {
                    'hdnArvore': '',
                    'hdnPastaAtual': folder.folder_id,
                    'hdnProtocolos': folder.protocolos,
                })
                m = re.search(pattern, r.text)
                if m:
                    sel_url = self._sei_url(m.group(1).replace('&amp;', '&'))
                    break

        if not sel_url:
            return {}

        r_sel = self._get(sel_url)

        # Extract var linkXxx = 'url' patterns
        links = re.findall(r"var\s+(link\w+)\s*=\s*'([^']+)'", r_sel.text)
        return {name: url for name, url in links}

    def alter_process(
        self,
        id_procedimento: str,
        *,
        descricao: str | None = None,
        observacoes: str | None = None,
    ) -> bool:
        """Update a process's description and/or observations.

        Navigates to ``procedimento_alterar``, reads the current form
        values, updates the specified fields, and POSTs back.

        Args:
            id_procedimento: Process ID.
            descricao: New description/specification (``txtDescricao``).
            observacoes: New observations (``txaObservacoes``).

        Returns:
            True if the update succeeded (302 redirect).

        Raises:
            RuntimeError: If the alter form is unavailable.
        """
        arvore_html = self._navigate_to_arvore(id_procedimento)
        if not arvore_html:
            raise RuntimeError("Could not navigate to process tree")

        # Find procedimento_alterar URL from Nos[0] acoes
        start = arvore_html.find('Nos[0]')
        end = arvore_html.find('Nos[1]', start)
        nos0 = arvore_html[start:end].replace('\\"', '"')

        alt_m = re.search(
            r'href="(controlador\.php\?acao=procedimento_alterar[^"]*)"',
            nos0,
        )
        if not alt_m:
            raise RuntimeError("Alterar Processo action not found in toolbar")

        alt_url = self._sei_url(alt_m.group(1).replace('&amp;', '&'))
        r_form = self._get(alt_url)

        # Parse form
        soup = BeautifulSoup(r_form.text, 'lxml')
        form = soup.find('form', id='frmProcedimentoCadastro')
        if not form:
            raise RuntimeError("Alter process form not found")

        action_url = urljoin(self._sei_url(""), form['action'])

        # Collect all current field values
        data: dict[str, str] = {}
        for inp in form.find_all('input'):
            name = inp.get('name', '')
            if not name:
                continue
            typ = inp.get('type', '')
            if typ == 'radio':
                if inp.get('checked'):
                    data[name] = inp.get('value', '')
            elif typ != 'button' and typ != 'submit':
                data[name] = inp.get('value', '')

        for sel in form.find_all('select'):
            name = sel.get('name', '')
            if name:
                selected = sel.find('option', selected=True)
                data[name] = selected.get('value', '') if selected else ''

        for ta in form.find_all('textarea'):
            name = ta.get('name', '')
            if name:
                data[name] = ta.string or ''

        # Set flag to "alterar" mode
        data['hdnFlagProcedimentoCadastro'] = '2'

        # Apply changes
        if descricao is not None:
            data['txtDescricao'] = descricao
        if observacoes is not None:
            data['txaObservacoes'] = observacoes

        # --- Fill required hidden fields from their select counterparts ---
        # SEI JS normally syncs these; we must do it manually.

        # Assuntos: hdnAssuntos must mirror selAssuntos options
        if not data.get('hdnAssuntos'):
            sel_a = soup.find('select', id='selAssuntos')
            if sel_a:
                vals = [o.get('value', '') for o in sel_a.find_all('option') if o.get('value')]
                data['hdnAssuntos'] = vals[0] if vals else ''
                if vals:
                    data['selAssuntos'] = vals[0]

        # Interessados: hdnInteressadosProcedimento must mirror selInteressadosProcedimento
        if not data.get('hdnInteressadosProcedimento'):
            sel_i = soup.find('select', id='selInteressadosProcedimento')
            if sel_i:
                vals = [o.get('value', '') for o in sel_i.find_all('option') if o.get('value')]
                data['hdnInteressadosProcedimento'] = vals[0] if vals else ''
                if vals:
                    data['selInteressadosProcedimento'] = vals[0]

        # POST — use _post which handles redirects
        r_save = self._post(action_url, data)
        self._control_html = None

        # After _follow, a successful save redirects to arvore_visualizar
        # Check the final URL or look for the process tree
        final_url = str(r_save.url)
        if 'arvore_visualizar' in final_url or 'procedimento_trabalhar' in final_url:
            return True
        # If still on the alter page, check for flag=1 (means form re-rendered = error)
        if 'hdnFlagProcedimentoCadastro' in r_save.text:
            flag_m = re.search(
                r'name="hdnFlagProcedimentoCadastro"[^>]*value="(\d)"',
                r_save.text,
            )
            if flag_m and flag_m.group(1) == '1':
                return False
        return True

    def get_full_document_tree(
        self, id_procedimento: str, *, expand_all: bool = True
    ) -> list[TreeDocument]:
        """Get complete document tree for a process, expanding lazy-loaded folders.

        Unlike get_process_documents(), this method:
        1. Parses folder metadata (Pastas[]) from the arvore JS
        2. POSTs to expand any folders with carregado=false
        3. Returns TreeDocument objects with download/view URLs (.src_url)

        Args:
            id_procedimento: Process ID.
            expand_all: If True, expand all unloaded folders. If False,
                        only return already-loaded documents.

        Returns:
            List of TreeDocument with src_url for download/viewing.
        """
        self._last_tree_warnings: list[str] = []
        arvore_html = self._navigate_to_arvore(id_procedimento)
        if not arvore_html:
            return []

        all_docs: list[TreeDocument] = []
        all_signatures: dict[str, list[Any]] = {}

        def _merge_signatures(signature_map: dict[str, list[Any]]) -> None:
            for doc_id, infos in signature_map.items():
                if doc_id not in all_signatures:
                    all_signatures[doc_id] = list(infos)
                else:
                    existing = {
                        (sig.signer, sig.role, sig.unit, sig.kind, sig.icon)
                        for sig in all_signatures[doc_id]
                    }
                    for sig in infos:
                        key = (sig.signer, sig.role, sig.unit, sig.kind, sig.icon)
                        if key not in existing:
                            all_signatures[doc_id].append(sig)
                            existing.add(key)

        # Parse folders from the tree JS
        folders = parse_tree_folders(arvore_html)

        # Parse already-loaded documents and signatures from root
        _merge_signatures(parse_tree_signatures(arvore_html))
        loaded_docs = parse_expanded_folder(arvore_html, self._sei_url(""))
        all_docs.extend(loaded_docs)

        if expand_all:
            # Expand each unloaded folder via POST
            for folder in folders:
                if folder.carregado:
                    continue

                post_url = self._sei_url(folder.link)
                try:
                    r = self._post(post_url, {
                        'hdnArvore': '',
                        'hdnPastaAtual': folder.folder_id,
                        'hdnProtocolos': folder.protocolos,
                    })
                except Exception as exc:
                    self._last_tree_warnings.append(
                        f"Falha ao expandir a pasta {folder.folder_id}: {exc}"
                    )
                    continue

                if r.text.startswith('OK'):
                    _merge_signatures(parse_tree_signatures(r.text))
                    folder_docs = parse_expanded_folder(
                        r.text, self._sei_url("")
                    )
                    # Tag docs with their parent folder
                    for doc in folder_docs:
                        if not doc.parent_folder:
                            doc.parent_folder = folder.folder_id
                    all_docs.extend(folder_docs)
                else:
                    self._last_tree_warnings.append(
                        f"A pasta {folder.folder_id} não pôde ser expandida no contexto atual."
                    )

        for doc in all_docs:
            signatures = all_signatures.get(doc.id_documento, [])
            if signatures:
                doc.assinaturas = signatures
                doc.assinado = any(sig.kind == "assinatura" for sig in signatures)
                doc.autenticado = any(sig.kind == "autenticacao" for sig in signatures)

        return all_docs

    def download_document(
        self,
        doc: TreeDocument,
        output_path: str | None = None,
    ) -> bytes | str:
        """Download a document's content.

        For PDF/external documents (src_url contains documento_download_anexo):
            Returns binary PDF content. If output_path given, saves to file.
        For internal SEI documents (src_url contains documento_visualizar):
            Returns the rendered HTML content as text.

        Args:
            doc: TreeDocument from get_full_document_tree().
            output_path: Optional file path to save binary content.

        Returns:
            bytes for PDF downloads, str for HTML documents.
        """
        if not doc.src_url:
            raise ValueError(
                f"Document {doc.id_documento} ({doc.nome}) has no download URL"
            )

        r = self._get(doc.src_url)

        content_type = r.headers.get('content-type', '')

        if 'pdf' in content_type or 'octet-stream' in content_type:
            if output_path:
                with open(output_path, 'wb') as f:
                    f.write(r.content)
            return r.content

        # HTML content (internal SEI document)
        if 'html' in content_type:
            soup = BeautifulSoup(r.text, 'lxml')
            # Check if it's a login page (session expired)
            if soup.find('input', {'name': 'pwdSenha'}):
                raise RuntimeError(
                    "Session expired during document download. Re-login needed."
                )
            return r.text

        # Unknown type — return raw
        if output_path:
            with open(output_path, 'wb') as f:
                f.write(r.content)
        return r.content

    def read_document_content(
        self,
        doc: TreeDocument,
    ) -> str:
        """Read a document and return plain text content.

        Works for both internal SEI documents (HTML → text) and
        external PDFs (requires pdftotext).

        Args:
            doc: TreeDocument from get_full_document_tree().

        Returns:
            Plain text content of the document.
        """
        result = self.download_document(doc)

        if isinstance(result, str):
            # HTML content — extract text
            soup = BeautifulSoup(result, 'lxml')
            return soup.get_text('\n', strip=True)

        # Binary (PDF) — try pdftotext
        import subprocess
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=True) as tmp:
            tmp.write(result)
            tmp.flush()
            proc = subprocess.run(
                ['pdftotext', '-layout', tmp.name, '-'],
                capture_output=True, text=True,
            )
            if proc.returncode == 0:
                return proc.stdout
            raise RuntimeError(
                f"pdftotext failed for {doc.nome}: {proc.stderr}"
            )

    def delete_document(
        self,
        id_documento: str,
        id_procedimento: str,
    ) -> bool:
        """Delete an unsigned document from a process.

        Navigates to arvore_visualizar with the document selected, which
        exposes JS link variables including ``linkExcluirDocumento``.
        That URL is the direct delete action (the browser JS just does
        ``confirm()`` then ``location.href = linkExcluirDocumento``).

        Only works for documents created by the current unit that have NOT
        been signed.

        Args:
            id_documento: Document ID to delete.
            id_procedimento: Process ID containing the document.

        Returns:
            True if deletion succeeded.

        Raises:
            RuntimeError: If the document cannot be deleted.
        """
        # Step 1: Navigate to tree and find the doc link
        arvore_html = self._navigate_to_arvore(id_procedimento)
        if not arvore_html:
            raise RuntimeError("Could not navigate to process tree")

        # The doc might be inside a lazy-loaded folder — expand all
        from sei_cli.parsers import parse_tree_folders

        search_sources = [arvore_html]
        for folder in parse_tree_folders(arvore_html):
            if folder.carregado:
                continue
            r = self._post(self._sei_url(folder.link), {
                'hdnArvore': '',
                'hdnPastaAtual': folder.folder_id,
                'hdnProtocolos': folder.protocolos,
            })
            if id_documento in r.text:
                search_sources.append(r.text)

        # Step 2: Find arvore_visualizar link for this document
        sel_url = None
        for src in search_sources:
            m = re.search(
                rf'(controlador\.php\?acao=arvore_visualizar[^"]*'
                rf'id_documento={re.escape(id_documento)}[^"]*)',
                src,
            )
            if m:
                sel_url = self._sei_url(m.group(1).replace('&amp;', '&'))
                break

        if not sel_url:
            raise RuntimeError(
                f"Document {id_documento} not found in process tree. "
                "It may not exist or may be in a different unit."
            )

        # Step 3: Load arvore_visualizar with doc selected → get JS links
        r_sel = self._get(sel_url)

        # Step 4: Extract linkExcluirDocumento (direct delete URL)
        link_match = re.search(
            r"linkExcluirDocumento\s*=\s*'(controlador\.php[^']+)'",
            r_sel.text,
        )
        if not link_match:
            raise RuntimeError(
                f"Delete action not available for document {id_documento}. "
                "It may be signed or belong to another unit."
            )

        delete_url = self._sei_url(link_match.group(1).replace('&amp;', '&'))

        # Step 5: Execute the delete (single GET)
        r_delete = self._get(delete_url)
        self._control_html = None

        if 'erro' in r_delete.text.lower():
            raise RuntimeError("Delete failed: check SEI for details")

        return True

    def list_acompanhamento_especial(self) -> list[Process]:
        """List processes in 'Acompanhamento Especial'.

        Returns list of Process objects found on the acompanhamento page.
        """
        html = self._ensure_control()
        soup = BeautifulSoup(html, "lxml")

        acomp_url = None
        for a in soup.find_all("a", href=True):
            if "acompanhamento_listar" in a["href"]:
                acomp_url = urljoin(self._sei_url(""), a["href"])
                break

        if not acomp_url:
            acomp_url = self._menu_links.get("acompanhamento")
        if not acomp_url:
            return []

        r = self._get(acomp_url)
        self._control_html = None

        # Parse process links from the page
        procs: list[Process] = []
        asoup = BeautifulSoup(r.text, "lxml")
        for a in asoup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)
            if "procedimento_trabalhar" not in href:
                continue
            id_m = re.search(r"id_procedimento=(\d+)", href)
            if not id_m:
                continue
            # Extract process number from link text (e.g. "08810035.004097/2025-01")
            numero = text.strip()
            if not re.match(r"\d{8}\.\d+/\d{4}-\d{2}", numero):
                continue
            procs.append(Process(
                numero=numero,
                tipo="Acompanhamento Especial",
                especificacao="",
                id_procedimento=id_m.group(1),
                link=urljoin(self._sei_url(""), href),
                novo=False,
            ))

        return procs

    def listar_grupos_acompanhamento(self) -> list[dict]:
        """Lists all grupos de acompanhamento in current unit.
        
        Returns:
            List of {"id": str, "nome": str}.
        """
        html = self._ensure_control()
        
        acomp_list = self._extract_action_url(html, "acompanhamento_listar")
        if not acomp_list:
            acomp_match = re.search(r'(controlador\.php\?acao=acompanhamento_listar[^"\']+)', html)
            if acomp_match:
                acomp_list = self._sei_url(acomp_match.group(1).replace("&amp;", "&"))
            else:
                return []
                
        r_list = self._get(acomp_list)
        
        cad = re.search(
            r'(controlador\.php\?acao=acompanhamento_cadastrar[^"\']+)', r_list.text
        )
        if not cad:
            cad = re.search(
                r'(controlador\.php\?acao=acompanhamento_cadastrar[^"\']+)', html
            )
        
        if not cad:
            return []
            
        r_cad = self._get(self._sei_url(cad.group(1).replace("&amp;", "&")))
        soup = BeautifulSoup(r_cad.text, "lxml")
        sel = soup.find("select", {"name": "selGrupoAcompanhamento"})
        if not sel:
            return []
            
        result = []
        for opt in sel.find_all("option"):
            val = opt.get("value", "")
            name = opt.get_text(strip=True)
            if val and val != "null" and name:
                result.append({"id": val, "nome": name})
        return result

    def list_grupos_acompanhamento(self) -> list[tuple[str, str]]:
        """Legacy alias."""
        return [(g["id"], g["nome"]) for g in self.listar_grupos_acompanhamento()]

    def criar_grupo_acompanhamento(self, nome: str) -> str:
        """Creates a new grupo de acompanhamento especial in the current unit.
        
        Args:
            nome: Group name.
            
        Returns:
            The new group ID.
        """
        from urllib.parse import urljoin
        html = self._ensure_control()
        
        acomp_list = self._extract_action_url(html, "acompanhamento_listar")
        if not acomp_list:
            acomp_match = re.search(r'(controlador\.php\?acao=acompanhamento_listar[^"\']+)', html)
            if acomp_match:
                acomp_list = self._sei_url(acomp_match.group(1).replace("&amp;", "&"))
            else:
                raise RuntimeError("Link acompanhamento_listar não encontrado")

        r_list = self._get(acomp_list)
        
        grupo_list_match = re.search(
            r'(controlador\.php\?acao=grupo_acompanhamento_listar[^"\']+)', r_list.text
        )
        if not grupo_list_match:
            raise RuntimeError("Link grupo_acompanhamento_listar não encontrado")
            
        r_grupo_list = self._get(self._sei_url(grupo_list_match.group(1).replace("&amp;", "&")))
        
        cad_match = re.search(
            r"location\.href='([^']+grupo_acompanhamento_cadastrar[^']+)'", r_grupo_list.text
        )
        if not cad_match:
            raise RuntimeError("Link grupo_acompanhamento_cadastrar não encontrado")
            
        r_form = self._get(self._sei_url(cad_match.group(1).replace("&amp;", "&")))
        soup = BeautifulSoup(r_form.text, "lxml")
        form = soup.find("form", id="frmGrupoAcompanhamentoCadastro")
        if not form:
            raise RuntimeError("Formulário frmGrupoAcompanhamentoCadastro não encontrado")
            
        action = urljoin(self._sei_url(""), form.get("action", "").replace("&amp;", "&"))
        data = {
            "hdnInfraTipoPagina": "1",
            "txtNome": nome,
            "hdnIdGrupoAcompanhamento": "",
            "sbmCadastrarGrupoAcompanhamento": "Salvar",
        }
        
        before_groups = {g["id"] for g in self.listar_grupos_acompanhamento()}
        
        r_save = self._post(action, data)
        self._control_html = None
        
        if "grupo_acompanhamento_listar" not in str(r_save.url):
            raise RuntimeError("Falha ao criar grupo, redirecionamento inesperado")
            
        after_groups = self.listar_grupos_acompanhamento()
        for g in after_groups:
            if g["id"] not in before_groups:
                return g["id"]
                
        for g in after_groups:
            if g["nome"] == nome:
                return g["id"]
                
        raise RuntimeError("Grupo criado mas ID não pôde ser identificado")

    def create_grupo_acompanhamento(self, nome: str) -> bool:
        """Legacy alias."""
        try:
            self.criar_grupo_acompanhamento(nome)
            return True
        except Exception:
            return False

    def add_acompanhamento_especial(
        self, id_procedimento: str, grupo_id: str, observacao: str
    ) -> bool:
        """Add a process to Acompanhamento Especial.
        
        New approach: Uses acompanhamento_cadastrar from control page and POSTs
        directly with hdnIdProtocolo. Falls back to arvore navigation if needed.
        """
        from urllib.parse import urljoin
        html = self._ensure_control()
        
        # --- NEW APPROACH (Direct POST) ---
        cad_match = re.search(
            r'(controlador\.php\?acao=acompanhamento_cadastrar[^"\']+)', html
        )
        if cad_match:
            cad_url = self._sei_url(cad_match.group(1).replace("&amp;", "&"))
            r_cad = self._get(cad_url)
            
            soup = BeautifulSoup(r_cad.text, "lxml")
            form = soup.find("form", id="frmAcompanhamentoCadastro")
            if form:
                action = urljoin(self._sei_url(""), form.get("action", "").replace("&amp;", "&"))
                data = {
                    "hdnInfraTipoPagina": "1",
                    "selGrupoAcompanhamento": str(grupo_id),
                    "txaObservacao": observacao,
                    "hdnIdAcompanhamento": "",
                    "hdnIdProtocolo": str(id_procedimento),
                    "sbmCadastrarAcompanhamento": "Salvar",
                }
                
                r_add = self._post(action, data)
                self._control_html = None
                
                if "procedimento_controlar" in str(r_add.url) or "Controle de Processos" in r_add.text:
                    return True

        # --- FALLBACK (Old approach via tree) ---
        arvore_html = self._navigate_to_arvore(id_procedimento)
        if not arvore_html:
            raise RuntimeError(f"Processo {id_procedimento} não encontrado ou sessão expirada")
            
        start = arvore_html.find('Nos[0]')
        end = arvore_html.find('Nos[1]', start) if start != -1 else -1
        nos0 = arvore_html[start:end].replace('\\"', '"') if start != -1 else ""
        
        acomp = re.search(
            r'href="(controlador\.php\?acao=acompanhamento_gerenciar[^"]*)"',
            nos0,
        )
        if not acomp:
            raise RuntimeError("Link acompanhamento_gerenciar não encontrado na barra de ferramentas")
        
        r_ger = self._get(self._sei_url(acomp.group(1).replace("&amp;", "&")))

        soup = BeautifulSoup(r_ger.text, "lxml")
        form = soup.find("form", id="frmAcompanhamentoCadastro")
        if not form:
            raise RuntimeError("Formulário frmAcompanhamentoCadastro não encontrado (processo pode já estar em acompanhamento)")

        action = urljoin(self._sei_url(""), form.get("action", "").replace("&amp;", "&"))
        data = {
            "hdnInfraTipoPagina": "2",
            "selGrupoAcompanhamento": str(grupo_id),
            "txaObservacao": observacao,
            "hdnIdAcompanhamento": "",
            "hdnIdProtocolo": str(id_procedimento),
            "sbmCadastrarAcompanhamento": "Salvar",
        }

        r_add = self._post(action, data)
        self._control_html = None

        if "Lista de Acompanhamentos" in r_add.text or "Acompanhamentos Especiais" in r_add.text or "procedimento_controlar" in str(r_add.url):
            return True

        return False

    def get_process_acompanhamento_especial(self, id_procedimento: str) -> dict[str, Any]:
        """Read acompanhamento especial from the process-local management page.

        ``acompanhamento_listar`` can be paginated/filtered and miss old
        processes. The process tree toolbar links to ``acompanhamento_gerenciar``
        for the selected process, which is the authoritative local verification.
        """
        arvore_html = self._navigate_to_arvore(id_procedimento)
        if not arvore_html:
            raise RuntimeError(f"Processo {id_procedimento} não encontrado ou sessão expirada")

        start = arvore_html.find('Nos[0]')
        end = arvore_html.find('Nos[1]', start) if start != -1 else -1
        nos0 = arvore_html[start:end].replace('\\"', '"') if start != -1 else ""
        acomp = re.search(
            r'href="(controlador\.php\?acao=acompanhamento_gerenciar[^"]*)"',
            nos0,
        )
        if not acomp:
            return {
                "tracked": False,
                "source": "acompanhamento_gerenciar",
                "reason": "link_acompanhamento_gerenciar_not_found",
            }

        r_ger = self._get(self._sei_url(acomp.group(1).replace("&amp;", "&")))
        ger_html = r_ger.text
        alterar_match = re.search(
            r'(controlador\.php\?acao=acompanhamento_alterar[^"\']+)',
            ger_html,
        )
        if not alterar_match:
            return {
                "tracked": False,
                "source": "acompanhamento_gerenciar",
                "reason": "acompanhamento_alterar_not_found",
            }

        r_alt = self._get(self._sei_url(alterar_match.group(1).replace("&amp;", "&")))
        soup = BeautifulSoup(r_alt.text, "lxml")
        form = soup.find("form", id="frmAcompanhamentoCadastro")
        if not form:
            return {
                "tracked": True,
                "source": "acompanhamento_gerenciar",
                "id_procedimento": str(id_procedimento),
            }

        id_acomp_input = form.find("input", {"name": "hdnIdAcompanhamento"})
        obs_field = form.find("textarea", {"name": "txaObservacao"})
        group_select = form.find("select", {"name": "selGrupoAcompanhamento"})
        selected_option = None
        if group_select:
            selected_option = group_select.find("option", selected=True)
            if selected_option is None:
                selected_value = group_select.get("value")
                if selected_value:
                    selected_option = group_select.find("option", value=selected_value)
        grupo_id = str(selected_option.get("value", "")) if selected_option else ""
        grupo_nome = selected_option.get_text(" ", strip=True) if selected_option else ""
        observacao = obs_field.get_text("\n", strip=True) if obs_field else ""

        return {
            "tracked": True,
            "source": "acompanhamento_gerenciar",
            "id_procedimento": str(id_procedimento),
            "id_acompanhamento": id_acomp_input.get("value", "") if id_acomp_input else "",
            "grupo_id": grupo_id,
            "grupo_nome": grupo_nome,
            "observacao": observacao,
        }

    def alterar_acompanhamento_especial(
        self, id_procedimento: str, grupo_id: str, observacao: str
    ) -> bool:
        """Alters an existing acompanhamento (change group and/or observation).
        
        Args:
            id_procedimento: Process ID.
            grupo_id: New group ID.
            observacao: New observation text.
            
        Returns:
            True if altered successfully.
        """
        from urllib.parse import urljoin
        
        arvore_html = self._navigate_to_arvore(id_procedimento)
        if not arvore_html:
            raise RuntimeError(f"Processo {id_procedimento} não encontrado ou sessão expirada")
            
        start = arvore_html.find('Nos[0]')
        end = arvore_html.find('Nos[1]', start) if start != -1 else -1
        nos0 = arvore_html[start:end].replace('\\"', '"') if start != -1 else ""
        
        acomp = re.search(
            r'href="(controlador\.php\?acao=acompanhamento_gerenciar[^"]*)"',
            nos0,
        )
        if not acomp:
            raise RuntimeError("Link acompanhamento_gerenciar não encontrado na barra de ferramentas")
        
        r_ger = self._get(self._sei_url(acomp.group(1).replace("&amp;", "&")))
        
        # In gerenciar page, look for existing acompanhamento to alter
        alterar_match = re.search(
            r'(controlador\.php\?acao=acompanhamento_alterar[^"\']+)',
            r_ger.text,
        )
        if not alterar_match:
            raise RuntimeError("Link acompanhamento_alterar não encontrado. O processo está em acompanhamento?")
            
        r_alt = self._get(self._sei_url(alterar_match.group(1).replace("&amp;", "&")))
        
        soup = BeautifulSoup(r_alt.text, "lxml")
        form = soup.find("form", id="frmAcompanhamentoCadastro")
        if not form:
            raise RuntimeError("Formulário de alteração não encontrado")
            
        action = urljoin(self._sei_url(""), form.get("action", "").replace("&amp;", "&"))
        
        # Get existing hdnIdAcompanhamento
        id_acomp_input = form.find("input", {"name": "hdnIdAcompanhamento"})
        id_acomp = id_acomp_input.get("value", "") if id_acomp_input else ""
        
        data = {
            "hdnInfraTipoPagina": "2",
            "selGrupoAcompanhamento": str(grupo_id),
            "txaObservacao": observacao,
            "hdnIdAcompanhamento": id_acomp,
            "sbmAlterarAcompanhamento": "Salvar",
        }
        
        r_save = self._post(action, data)
        self._control_html = None
        
        # POST returns 302 redirect to acompanhamento_gerenciar
        if "acompanhamento_gerenciar" in str(r_save.url) or r_save.status_code == 200:
            return True
            
        return False

    # --- Blocks ---

    # Well-known SEI unit IDs for block creation (CBM scope)
    UNIT_IDS: dict[str, str] = {
        # === 3º GBM (Leo's units) ===
        "CMDO 3GBM": "110003475",
        "SECRETARIA 3GBM": "110003476",
        "LOGISTICA 3GBM": "110010205",
        "OP 3GBM": "110003486",
        "CMDO 1SGB/3GBM": "110003477",
        "SEC 1SGB/3GBM": "110003478",
        "CMDO 2SGB/3GBM": "110003483",
        "SEC 2SGB/3GBM": "110003484",
        "CMDO PABM APODI": "110008367",
        "CMDO PABM ASSU": "110007343",
        "PABM PATU": "110009015",
        # === 4º BBM (Santa Cruz) ===
        "CMDO 4CIA/4BBM": "110010522",
        "SEC 4CIA/4BBM": "110010523",
        # === 1º GBM (João Câmara) ===
        "CMDO CIA JC/1GBM": "110010413",
        "SEC CIA JC/1GBM": "110010414",
        "OP CIA JC/1GBM": "110010415",
        "PAD-PDF": "110006929",
        # === DAT (Atividades Técnicas) ===
        "DAT-1CAT": "110005347",
        "DAT-1CAT CHEFIA": "110007609",
        "DAT-1CAT CATA": "110007610",
        "1SAT/1CAT SEC": "110007086",
        "DAT DIRETOR": "110001958",
        "DAT SECRETARIA": "110002271",
        "DAT CARIP": "110002269",
        "DAT CARIP CHEFIA": "110004789",
        "DAT VISTORIAS": "110002268",
        "DAT CENTRO FISC": "110004845",
        "DAT CEPI": "110009020",
        "DAT PROJETOS": "110001961",
        "DAT 2CAT": "110003482",
        "DAT 2CAT CHEFIA": "110001897",
        "DAT SEÇ OP FISCAL 1CAT": "110009194",
        "DAT CENTRO FISC 1CAT": "110009193",
        "DAT NOTIFICAÇÃO 1CAT": "110009195",
        # === DPSGP (Gestão de Pessoas / RH) ===
        "DPSGP SECRETARIA": "110009493",
        "DPSGP DIRETOR": "110004357",
        "DPSGP CRH": "110001851",
        "DPSGP CRH CHEFIA": "110006947",
        "DPSGP CRH SAG": "110001880",
        "DPSGP CPS": "110007426",
        "DPSGP CPS CHEFIA": "110007985",
        "DPSGP CPS SAG": "110001879",
        "DPSGP SAUDE": "110002266",
        "DPSGP AGREGADOS": "110005356",
        # === Comando Geral ===
        "AJUD SEC GERAL": "110001966",
        "GAB CMDO": "110001848",
        "SUB CMDO": "110001865",
        "PROTOCOLO": "110002233",
        "ASSEADM": "110001867",
        "ASSECOM": "110001970",
        "ASSEINT": "110003540",
        "ASPAR": "110005776",
        "UCI": "110001862",
        "CPED": "110001869",
        "CPO": "110001870",
        "CPP": "110001871",
        # === COBM (Comando Operacional) ===
        "COBM SECRETARIA": "110001854",
        "COBM COMANDANTE": "110003118",
        "EMOP": "110002265",
        # === DLOF (Logística/Finanças) ===
        "DLOF SECRETARIA": "110001850",
        "DLOF DIRETOR": "110001959",
        "DLOF CAFO": "110001852",
        "DLOF CTIC": "110001853",
        "DLOF CLOG SEC": "110001858",
        "DLOF CPIPC": "110002042",
        # === DEI (Ensino) ===
        "DEI DIRETOR": "110006894",
        # === Outros GBMs ===
        "CMDO 1GBM": "110003445",
        "CMDO 2GBM": "110007309",
        "CMDO 4GBM": "110002231",
        "CMDO 1SGB/1GBM": "110003448",
        "CMDO 2SGB/1GBM": "110003450",
        "CMDO 1SGB/2GBM": "110003452",
        "CMDO 1SGB/4GBM": "110003480",
        "CMDO 2SGB/4GBM": "110001965",
        # === Operacional Especial ===
        "GBSA CMDO": "110003461",
        "GBSA SECRETARIA": "110001893",
        "SSU": "110001899",
        "SIDAM": "110003462",
    }

    def create_block(
        self,
        descricao: str,
        unidade_destino_id: str,
        *,
        grupo: str = "null",
    ) -> str:
        """Create a new bloco de assinatura.

        Args:
            descricao: Block description.
            unidade_destino_id: SEI unit ID for the destination unit.
                Use UNIT_IDS class dict for known units, or query the
                unit selector for other IDs.
            grupo: Optional group (default "null" = Nenhum).

        Returns:
            The new block number (str).

        Raises:
            RuntimeError: If block creation fails.
        """
        # Navigate to blocos page and find the cadastrar URL
        _, soup = self._get_blocos_page()
        cadastrar_url = None
        for inp in soup.find_all(["input", "button"]):
            onclick = inp.get("onclick", "")
            if "bloco_assinatura_cadastrar" in onclick:
                url_match = re.search(r"location\.href='([^']+)'", onclick)
                if url_match:
                    cadastrar_url = self._sei_url(
                        url_match.group(1).replace("&amp;", "&")
                    )
                    break

        if not cadastrar_url:
            raise RuntimeError("URL de cadastro de bloco não encontrada")

        # Load the form to get a valid hash
        r_cad = self._get(cadastrar_url)
        cad_soup = BeautifulSoup(r_cad.text, "lxml")
        form = cad_soup.find("form", id="frmBlocoCadastro")
        if not form:
            raise RuntimeError("Formulário frmBlocoCadastro não encontrado")

        form_action = form.get("action", "").replace("&amp;", "&")
        submit_url = self._sei_url(form_action)

        # Get block count before creation to detect new block
        before_blocks = {b.numero for b in self.list_blocks()}

        data = {
            "hdnInfraTipoPagina": "1",
            "txtIdBloco": "",
            "txtDescricao": descricao,
            "selGrupoBloco": grupo,
            "txtUnidade": "",
            "hdnIdUnidade": "",
            "selUnidades": unidade_destino_id,
            "hdnIdBloco": "",
            "hdnUnidades": unidade_destino_id,
            "sbmCadastrarBloco": "Salvar",
        }

        r_save = self._post(submit_url, data=data)

        if "bloco_assinatura_listar" not in str(r_save.url):
            raise RuntimeError(
                "Criação de bloco falhou — servidor não redirecionou para lista"
            )

        # Find the new block number
        after_blocks = self.list_blocks()
        for b in after_blocks:
            if b.numero not in before_blocks:
                return b.numero

        raise RuntimeError("Bloco criado mas número não identificado")

    def delete_block(self, block_numero: str) -> bool:
        """Delete a bloco de assinatura (must be empty — no documents).

        Args:
            block_numero: The block number to delete.

        Returns:
            True if deletion succeeded.

        Raises:
            RuntimeError: If block has documents or deletion fails.
        """
        # Check if block has documents first
        docs = self.get_block_documents(block_numero)
        if docs:
            raise RuntimeError(
                f"Bloco {block_numero} contém {len(docs)} documento(s). "
                "Remova-os antes de excluir."
            )

        # Navigate directly to blocos page (need fresh hash)
        html = self._ensure_control()
        blocos_url = self._menu_links.get("blocos_assinatura")
        if not blocos_url:
            raise RuntimeError("Link de blocos de assinatura não encontrado")

        r = self._get(blocos_url)
        self._control_html = None

        # Extract the bloco_excluir URL with fresh hash
        match = re.search(
            r"controlador\.php\?acao=bloco_excluir[^'\"]+", r.text
        )
        if not match:
            raise RuntimeError("URL de exclusão de bloco não encontrada")

        excluir_url = self._sei_url(match.group().replace("&amp;", "&"))

        # Extract form fields for proper submission
        soup = BeautifulSoup(r.text, "lxml")
        form = soup.find("form", id="frmBlocoLista")
        if not form:
            raise RuntimeError("Formulário frmBlocoLista não encontrado")

        def _field(name: str) -> str:
            el = form.find("input", {"name": name})
            return el.get("value", "") if el else ""

        data = {
            "hdnInfraTipoPagina": "1",
            "hdnInfraItemId": block_numero,
            "hdnMeusBlocos": _field("hdnMeusBlocos"),
            "hdnFlagBlocos": _field("hdnFlagBlocos"),
            "hdnInfraNroItens": _field("hdnInfraNroItens"),
            "hdnInfraItens": _field("hdnInfraItens"),
            "hdnInfraItensHash": _field("hdnInfraItensHash"),
            "hdnInfraItensSelecionados": "",
            "hdnInfraSelecoes": "Infra",
            "hdnInfraCampoOrd": "IdBloco",
            "hdnInfraTipoOrd": "DESC",
            "hdnInfraPaginaAtual": "0",
            "hdnInfraHashCriterios": _field("hdnInfraHashCriterios"),
        }

        rd = self._post(excluir_url, data=data)

        if "login" in str(rd.url):
            raise RuntimeError("Sessão expirou durante exclusão")

        if "bloco_assinatura_listar" not in str(rd.url):
            raise RuntimeError("Exclusão falhou — redirecionamento inesperado")

        # Verify deletion
        remaining = parse_blocks(rd.text, base_url=self._sei_url(""))
        if any(b.numero == block_numero for b in remaining):
            raise RuntimeError(f"Bloco {block_numero} ainda existe após tentativa de exclusão")

        return True

    def list_blocks(self) -> list[Block]:
        """List blocos de assinatura."""
        html = self._ensure_control()
        
        blocos_url = self._menu_links.get("blocos_assinatura")
        if not blocos_url:
            return []
        
        r = self._get(blocos_url)
        # Invalidate control cache since we navigated away
        self._control_html = None
        
        return parse_blocks(r.text, base_url=self._sei_url(""))

    def get_block(self, block_numero: str) -> Block:
        """Get one bloco and its documents (legacy compatibility helper)."""
        block = next((item for item in self.list_blocks() if item.numero == block_numero), None)
        if block is None:
            raise ValueError(f"Bloco {block_numero} nao encontrado na unidade atual.")
        block.documentos = self.get_block_documents(block_numero)
        return block

    def get_block_documents(self, block_numero: str) -> list[BlockDocument]:
        """List documents inside a specific bloco de assinatura."""
        detail = self._get_block_detail_page(block_numero)
        if detail is None:
            return []

        rd, _soup = detail
        return parse_block_documents(rd.text, base_url=self._sei_url(""))

    def _get_blocos_page(self) -> tuple[str, "BeautifulSoup"]:
        """Navigate to blocos de assinatura page, return (url, soup)."""
        html = self._ensure_control()
        blocos_url = self._menu_links.get("blocos_assinatura")
        if not blocos_url:
            raise RuntimeError("Link de blocos de assinatura não encontrado no menu")
        r = self._get(blocos_url)
        self._control_html = None
        soup = BeautifulSoup(r.text, "lxml")
        return blocos_url, soup

    def _get_block_detail_page(self, block_numero: str) -> tuple[httpx.Response, "BeautifulSoup"] | None:
        """Open a bloco detail page (`rel_bloco_protocolo_listar`)."""
        _blocos_url, blocos_soup = self._get_blocos_page()

        detail_url = None
        for a in blocos_soup.find_all("a"):
            href = a.get("href", "")
            if "rel_bloco_protocolo_listar" in href and a.get_text(strip=True) == block_numero:
                detail_url = urljoin(self._sei_url(""), href.replace("&amp;", "&"))
                break

        if not detail_url:
            list_match = re.search(
                rf"controlador\.php\?acao=rel_bloco_protocolo_listar[^'\"]*id_bloco={block_numero}[^'\"]*",
                str(blocos_soup),
            )
            if list_match:
                detail_url = self._sei_url(list_match.group(0).replace("&amp;", "&"))

        if not detail_url:
            return None

        rd = self._get(detail_url)
        return rd, BeautifulSoup(rd.text, "lxml")

    def _extract_block_document_entries(self, detail_html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(detail_html, "lxml")
        rows = soup.find_all("tr", class_=re.compile(r"infraTr(Clara|Escura)"))
        entries: list[dict[str, Any]] = []
        generic_sign_url: str | None = None

        # Extract per-document sign URLs from JS arrays (arrSequencial / arrLinkAssinatura).
        # SEI stores these in <script> blocks, indexed by 0-based position.
        _js_seq: dict[int, str] = {}  # position → seq
        _js_sign: dict[int, str] = {}  # position → sign url
        for m in re.finditer(r'arrSequencial\[(\d+)\]\s*=\s*"([^"]+)"', detail_html):
            _js_seq[int(m.group(1))] = m.group(2)
        for m in re.finditer(r'arrLinkAssinatura\[(\d+)\]\s*=\s*"([^"]+)"', detail_html):
            _js_sign[int(m.group(1))] = m.group(2).replace("&amp;", "&")
        # Build seq → sign_url map
        _seq_to_sign_url: dict[str, str] = {}
        for pos, seq_val in _js_seq.items():
            if pos in _js_sign:
                _seq_to_sign_url[seq_val] = urljoin(
                    self._sei_url(""), _js_sign[pos]
                )
        generic_sign_match = re.search(
            r"""['"]([^'"]*controlador\.php\?[^'"]*acao=documento_assinar[^'"]*)['"]""",
            detail_html,
        )
        if generic_sign_match:
            generic_sign_url = urljoin(
                self._sei_url(""),
                generic_sign_match.group(1).replace("&amp;", "&"),
            )

        def _extract_action_url(tag: Any) -> str | None:
            onclick = tag.get("onclick", "")
            match = re.search(r"""['"]([^'"]*controlador\.php\?[^'"]+)['"]""", onclick)
            if match:
                return urljoin(self._sei_url(""), match.group(1).replace("&amp;", "&"))
            href = tag.get("href", "")
            if href and href != "#" and not href.startswith("#ID-"):
                return urljoin(self._sei_url(""), href.replace("&amp;", "&"))
            return None

        for row in rows:
            checkbox = row.find("input", {"type": "checkbox"})
            if not checkbox:
                continue
            tds = row.find_all("td")
            if len(tds) < 6:
                continue
            seq = tds[1].get_text(" ", strip=True)
            processo = tds[2].get_text(" ", strip=True)
            numero_sei = tds[3].get_text(" ", strip=True)
            tipo_documento = tds[4].get_text(" ", strip=True)
            assinante = tds[5].get_text(" ", strip=True)
            assinantes = [
                text.strip()
                for text in tds[5].stripped_strings
                if text and text.strip()
            ]
            documento_id = None
            preview_url = None
            sign_url = None
            for a in row.find_all("a"):
                label = a.get_text(" ", strip=True)
                href = a.get("href", "")
                if href.startswith("#ID-"):
                    match = re.search(r"#ID-(\d+)-", href)
                    if match:
                        documento_id = match.group(1)
                if numero_sei and numero_sei in label:
                    preview_url = _extract_action_url(a)
                candidate_sign_url = _extract_action_url(a)
                if candidate_sign_url and "acao=documento_assinar" in candidate_sign_url:
                    sign_url = candidate_sign_url
            for tag in row.find_all(True):
                if sign_url:
                    break
                onclick = tag.get("onclick", "")
                match = re.search(r"""['"]([^'"]*controlador\.php\?[^'"]*acao=documento_assinar[^'"]+)['"]""", onclick)
                if match:
                    sign_url = urljoin(self._sei_url(""), match.group(1).replace("&amp;", "&"))
            # Fallback: use sign URL from JS arrays if not found in row HTML
            if not sign_url and seq and seq in _seq_to_sign_url:
                sign_url = _seq_to_sign_url[seq]
            if not sign_url and generic_sign_url and checkbox.get("value"):
                sign_url = generic_sign_url
            imgs = row.find_all("img", title=True)
            has_signature_marker = any("Assinatura" in (img.get("title") or "") for img in imgs)
            can_sign = bool(sign_url)
            assinado = has_signature_marker and not can_sign
            entries.append(
                {
                    "seq": seq,
                    "processo": processo,
                    "documento_id": documento_id or numero_sei,
                    "numero_sei": numero_sei or None,
                    "numero_documento": numero_sei or None,
                    "tipo_documento": tipo_documento,
                    "assinante": assinante,
                    "assinantes": assinantes,
                    "assinado": assinado,
                    "can_sign": can_sign,
                    "checkbox_name": checkbox.get("name"),
                    "checkbox_value": checkbox.get("value"),
                    "preview_url": preview_url,
                    "sign_url": sign_url,
                }
            )
        return entries

    def _execute_block_sign(
        self,
        sign_url: str,
        doc_id: str,
        *,
        detail_html: str,
        checkbox_value: str | None,
    ) -> dict:
        if "id_documento=" in sign_url:
            return self._execute_sign(sign_url, doc_id)

        soup = BeautifulSoup(detail_html, "lxml")
        form = soup.find("form", {"id": "frmRelBlocoProtocoloLista"})
        if not form:
            return {"error": "Formulário de documentos do bloco não encontrado"}

        data: dict[str, str] = {}
        for inp in form.find_all("input"):
            name = inp.get("name", "")
            if name:
                data[name] = inp.get("value", "")
        item_value = checkbox_value or doc_id
        data["hdnInfraItemId"] = item_value
        data["hdnInfraItensSelecionados"] = item_value
        response = self._post(sign_url, data)
        ssoup = BeautifulSoup(response.text, "lxml")
        sign_form = ssoup.find("form", {"id": "frmAssinaturas"})
        if not sign_form:
            return {"error": "Formulário de assinatura não encontrado"}
        return self._execute_sign_form(sign_form, response.text)

    def preview_block_document(self, block_numero: str, doc_sei_number: str) -> str:
        """Preview a document from a block detail page without leaving block context."""
        detail = self._get_block_detail_page(block_numero)
        if detail is None:
            raise RuntimeError(f"Bloco {block_numero} não encontrado")
        rd, soup = detail
        entries = self._extract_block_document_entries(rd.text)
        target = next(
            (
                entry
                for entry in entries
                if entry.get("numero_sei") == doc_sei_number
                or entry.get("numero_documento") == doc_sei_number
                or entry.get("documento_id") == doc_sei_number
            ),
            None,
        )
        if not target or not target.get("preview_url"):
            raise RuntimeError(f"Documento {doc_sei_number} não encontrado no bloco {block_numero}")

        rp = self._get(str(target["preview_url"]))
        modal_soup = BeautifulSoup(rp.text, "lxml")
        modal = (
            modal_soup.find(id="divInfraSparklingModal")
            or modal_soup.find("div", class_=re.compile(r"InfraSparklingModal", re.I))
            or modal_soup.find("div", id=re.compile(r"divInfra.*Modal", re.I))
        )
        if modal:
            return modal.get_text("\n", strip=True)
        iframe = modal_soup.find("iframe")
        if iframe and iframe.get("src"):
            rf = self._get(urljoin(self._sei_url(""), iframe["src"].replace("&amp;", "&")))
            return BeautifulSoup(rf.text, "lxml").get_text("\n", strip=True)
        return modal_soup.get_text("\n", strip=True)

    def _blocos_form_action(self, soup: "BeautifulSoup", action_name: str) -> str:
        """Extract a JS action URL from the blocos page (e.g. bloco_disponibilizar)."""
        pattern = rf"(controlador\.php\?acao={action_name}[^'\"]+)"
        match = re.search(pattern, str(soup))
        if not match:
            raise RuntimeError(f"URL de {action_name} não encontrada na página de blocos")
        return self._sei_url(match.group(1).replace("&amp;", "&"))

    def add_document_to_block(
        self,
        id_procedimento: str,
        id_documento: str,
        block_numero: str,
        *,
        disponibilizar: bool = False,
    ) -> dict:
        """Include a document in a bloco de assinatura.

        Uses the bloco_escolher page (accessible from the process tree) which
        lists all documents with checkboxes and a dropdown to select the target
        block. This approach works reliably because the URL hash is generated
        server-side in the tree.

        Args:
            id_procedimento: Process ID containing the document.
            id_documento: Document ID to include (SEI internal ID).
            block_numero: Block number (e.g. '871299').
            disponibilizar: If True, include AND make available in one step
                           (uses 'Incluir e Disponibilizar' button).

        Returns:
            dict with 'ok' bool and 'message'.

        Note:
            If the block is already disponibilizado, you must cancel the
            disponibilização first before editing or signing documents in it.
        """
        # Navigate to process → tree → bloco_escolher
        r = self._navigate_process_page(id_procedimento)
        soup = BeautifulSoup(r.text, "lxml")
        iframe = soup.find("iframe", {"name": "ifrArvore"})
        if not iframe:
            return {"ok": False, "message": "Árvore não encontrada"}

        arv_url = urljoin(self._sei_url(""), iframe["src"])
        r_arv = self._get(arv_url)

        # Find bloco_escolher URL (has valid hash with id_documento)
        chooser_match = re.search(
            r"(controlador\.php\?acao=bloco_escolher[^'\"]+)",
            r_arv.text,
        )
        if not chooser_match:
            return {"ok": False, "message": "Ação 'Incluir em Bloco de Assinatura' não encontrada na árvore"}

        chooser_url = self._sei_url(chooser_match.group(1).replace("&amp;", "&"))
        r_chooser = self._get(chooser_url)
        chooser_soup = BeautifulSoup(r_chooser.text, "lxml")

        # Find the form
        form = chooser_soup.find("form")
        if not form:
            return {"ok": False, "message": "Formulário 'Incluir em Bloco' não encontrado"}

        form_action = form.get("action", "")
        if not form_action:
            return {"ok": False, "message": "Form action não encontrado"}
        form_url = self._sei_url(form_action.replace("&amp;", "&"))

        # Verify the target block is in the dropdown
        sel = chooser_soup.find("select", {"name": "selBloco"})
        if sel:
            options = {opt.get("value"): opt.get_text(strip=True) for opt in sel.find_all("option")}
            if block_numero not in options:
                available = ", ".join(f"{v} ({k})" for k, v in options.items() if k != "null")
                return {
                    "ok": False,
                    "message": f"Bloco {block_numero} não disponível. Disponíveis: {available}",
                }

        # Find the checkbox for this document
        doc_checkbox = None
        for cb in chooser_soup.find_all("input", {"type": "checkbox"}):
            if cb.get("value") == id_documento:
                doc_checkbox = cb
                break

        # Build POST data from hidden inputs
        post_data = {}
        for inp in form.find_all("input"):
            name = inp.get("name", "")
            if not name:
                continue
            inp_type = inp.get("type", "")
            if inp_type == "hidden":
                post_data[name] = inp.get("value", "")
            elif inp_type == "checkbox" and inp.get("value") == id_documento:
                post_data[name] = id_documento

        post_data["selBloco"] = block_numero
        post_data["hdnDocumentosItensSelecionados"] = id_documento

        # Choose button: include only vs include and disponibilizar
        if disponibilizar:
            post_data["sbmIncluirDisponibilizar"] = "Incluir e Disponibilizar"
        else:
            post_data["sbmIncluir"] = "Incluir"

        r_submit = self._post(form_url, post_data)
        self._control_html = None

        # Verify success by checking if the block number appears in the Blocos column
        result_soup = BeautifulSoup(r_submit.text, "lxml")

        # Check for validation errors
        val = result_soup.find("textarea", {"id": "txaInfraValidacao"})
        if val and val.get_text(strip=True):
            return {"ok": False, "message": val.get_text(strip=True)[:200]}

        # Check if block_numero appears in result page
        for td in result_soup.find_all("td"):
            if block_numero in td.get_text():
                action = "incluído e disponibilizado" if disponibilizar else "incluído"
                return {
                    "ok": True,
                    "message": f"Documento {id_documento} {action} no bloco {block_numero}",
                }

        return {"ok": True, "message": f"Documento incluído no bloco {block_numero} (verificar)"}

    def devolver_block(self, block_numero: str) -> dict:
        """Return (devolver) a received bloco de assinatura to the sender unit.

        Only works on blocks that were received from another unit (estado=Recebido).
        After returning, the block goes back to the sender unit.

        Args:
            block_numero: Block number (e.g. '869251').

        Returns:
            dict with 'ok' bool and 'message'.
        """
        _, soup = self._get_blocos_page()
        form = soup.find("form", {"id": "frmBlocoLista"})
        if not form:
            return {"ok": False, "message": "Formulário de blocos não encontrado"}

        # Check if this block can be returned (has acaoRetornarBloco)
        retornar_pattern = re.compile(rf"acaoRetornarBloco\('{block_numero}'\)")
        if not retornar_pattern.search(str(soup)):
            # Check if block exists at all
            if block_numero not in str(soup):
                return {"ok": False, "message": f"Bloco {block_numero} não encontrado"}
            return {
                "ok": False,
                "message": f"Bloco {block_numero} não pode ser devolvido (somente blocos recebidos)",
            }

        retornar_url = self._blocos_form_action(soup, "bloco_retornar")

        fdata = {}
        for inp in form.find_all("input", type="hidden"):
            name = inp.get("name", "")
            if name:
                fdata[name] = inp.get("value", "")

        fdata["hdnInfraItemId"] = block_numero

        r = self._post(retornar_url, fdata)
        self._control_html = None

        if r.status_code == 200:
            rsoup = BeautifulSoup(r.text, "lxml")

            # Check for validation/error
            val = rsoup.find("textarea", {"id": "txaInfraValidacao"})
            if val and val.get_text(strip=True):
                return {"ok": False, "message": val.get_text(strip=True)[:200]}

            # Check if block disappeared (returned successfully)
            if block_numero not in rsoup.get_text():
                return {"ok": True, "message": f"Bloco {block_numero} devolvido com sucesso"}

            # Check state
            for row in rsoup.find_all("tr", class_=["infraTrClara", "infraTrEscura"]):
                if block_numero in row.get_text():
                    tds = row.find_all("td")
                    if len(tds) > 4:
                        estado = tds[4].get_text(strip=True)
                        return {"ok": True, "message": f"Bloco {block_numero} devolvido — estado: {estado}"}

            return {"ok": True, "message": f"Bloco {block_numero} devolvido"}

        return {"ok": False, "message": f"Erro ao devolver bloco (status {r.status_code})"}

    def disponibilizar_block(self, block_numero: str) -> dict:
        """Make a bloco de assinatura available to the destination unit.

        Args:
            block_numero: Block number (e.g. '871299').

        Returns:
            dict with 'ok' bool and 'message'.
        """
        _, soup = self._get_blocos_page()
        form = soup.find("form", {"id": "frmBlocoLista"})
        if not form:
            return {"ok": False, "message": "Formulário de blocos não encontrado"}

        # Get the disponibilizar action URL from JS
        disp_url = self._blocos_form_action(soup, "bloco_disponibilizar")

        # Build form data
        fdata = {}
        for inp in form.find_all("input", type="hidden"):
            name = inp.get("name", "")
            if name:
                fdata[name] = inp.get("value", "")

        fdata["hdnInfraItemId"] = block_numero

        r = self._post(disp_url, fdata)
        self._control_html = None

        # Check if we're back on the list page
        if r.status_code == 200:
            rsoup = BeautifulSoup(r.text, "lxml")
            # After disponibilizar, the block state changes to "Disponibilizado"
            for row in rsoup.find_all("tr", class_=["infraTrClara", "infraTrEscura"]):
                if block_numero in row.get_text():
                    tds = row.find_all("td")
                    if len(tds) > 4:
                        estado = tds[4].get_text(strip=True)
                        return {"ok": True, "message": f"Bloco {block_numero} — estado: {estado}"}

            return {"ok": True, "message": f"Bloco {block_numero} disponibilizado"}

        return {"ok": False, "message": f"Erro ao disponibilizar (status {r.status_code})"}

    def cancelar_disponibilizacao_block(self, block_numero: str) -> dict:
        """Cancel availability of a bloco de assinatura (retract from destination unit).

        Args:
            block_numero: Block number (e.g. '871299').

        Returns:
            dict with 'ok' bool and 'message'.
        """
        _, soup = self._get_blocos_page()
        form = soup.find("form", {"id": "frmBlocoLista"})
        if not form:
            return {"ok": False, "message": "Formulário de blocos não encontrado"}

        cancel_url = self._blocos_form_action(soup, "bloco_cancelar_disponibilizacao")

        fdata = {}
        for inp in form.find_all("input", type="hidden"):
            name = inp.get("name", "")
            if name:
                fdata[name] = inp.get("value", "")

        fdata["hdnInfraItemId"] = block_numero

        r = self._post(cancel_url, fdata)
        self._control_html = None

        if r.status_code == 200:
            rsoup = BeautifulSoup(r.text, "lxml")
            for row in rsoup.find_all("tr", class_=["infraTrClara", "infraTrEscura"]):
                if block_numero in row.get_text():
                    tds = row.find_all("td")
                    if len(tds) > 4:
                        estado = tds[4].get_text(strip=True)
                        return {"ok": True, "message": f"Bloco {block_numero} — estado: {estado}"}

            return {"ok": True, "message": f"Disponibilização do bloco {block_numero} cancelada"}

        return {"ok": False, "message": f"Erro ao cancelar disponibilização (status {r.status_code})"}

    def remove_document_from_block(
        self,
        id_documento: str,
        block_numero: str,
    ) -> dict:
        """Remove a document from a bloco de assinatura.

        Navigates to the block's document list and submits the removal form.
        The block must NOT be disponibilizado — cancel first if needed.

        Args:
            id_documento: Document ID (SEI internal ID, e.g. '48218774').
            block_numero: Block number (e.g. '871303').

        Returns:
            dict with 'ok' bool and 'message'.
        """
        _, blocos_soup = self._get_blocos_page()

        # Find rel_bloco_protocolo_listar URL for this block
        list_pattern = re.compile(
            rf"controlador\.php\?acao=rel_bloco_protocolo_listar[^'\"]*id_bloco={block_numero}[^'\"]*"
        )
        list_match = list_pattern.search(str(blocos_soup))
        if not list_match:
            return {"ok": False, "message": f"Bloco {block_numero} não encontrado na lista de blocos"}

        doc_list_url = self._sei_url(list_match.group(0).replace("&amp;", "&"))
        r_docs = self._get(doc_list_url)
        docs_soup = BeautifulSoup(r_docs.text, "lxml")

        # Verify document is in this block
        item_key = f"{id_documento}-{block_numero}"
        cb = docs_soup.find("input", {"type": "checkbox", "value": item_key})
        if not cb:
            return {
                "ok": False,
                "message": f"Documento {id_documento} não encontrado no bloco {block_numero}",
            }

        # Extract rel_bloco_protocolo_excluir URL
        excluir_match = re.search(
            r"(controlador\.php\?acao=rel_bloco_protocolo_excluir[^'\"]+)",
            r_docs.text,
        )
        if not excluir_match:
            return {"ok": False, "message": "URL de remoção não encontrada"}

        excluir_url = self._sei_url(excluir_match.group(1).replace("&amp;", "&"))

        # Build form data
        form = docs_soup.find("form", {"id": "frmRelBlocoProtocoloLista"})
        if not form:
            for f in docs_soup.find_all("form"):
                if f.find("input", {"name": "hdnInfraItemId"}):
                    form = f
                    break

        if not form:
            return {"ok": False, "message": "Formulário de documentos do bloco não encontrado"}

        fdata = {}
        for inp in form.find_all("input", type="hidden"):
            name = inp.get("name", "")
            if name:
                fdata[name] = inp.get("value", "")

        fdata["hdnInfraItemId"] = item_key

        r = self._post(excluir_url, fdata)
        self._control_html = None

        if r.status_code == 200:
            result_soup = BeautifulSoup(r.text, "lxml")

            val = result_soup.find("textarea", {"id": "txaInfraValidacao"})
            if val and val.get_text(strip=True):
                return {"ok": False, "message": val.get_text(strip=True)[:200]}

            if id_documento not in result_soup.get_text():
                return {
                    "ok": True,
                    "message": f"Documento {id_documento} removido do bloco {block_numero}",
                }

            return {"ok": True, "message": f"Remoção submetida (verificar bloco {block_numero})"}

        return {"ok": False, "message": f"Erro ao remover documento (status {r.status_code})"}

    # --- Units ---

    def list_units(self) -> list[Unit]:
        """List available units for switching."""
        html = self._ensure_control()
        switch_url = parse_unit_switch_link(html, self._sei_url(""))
        if not switch_url:
            return []
        
        r = self._get(switch_url)
        self._control_html = None
        
        return parse_units_switch_page(r.text, base_url=self._sei_url(""))

    def switch_unit(self, keyword: str) -> SystemStatus:
        """Switch active unit. Keyword matches against sigla or descricao.
        
        The unit.link stores the unit ID (not a URL). We POST the switch form
        with selInfraUnidades=<unit_id> to replicate the JS selecionarUnidade().
        
        After switching, we need a fresh login because the infra_hash changes.
        """
        # Always get a fresh control page for valid switch URL
        html = self._fresh_control()
        switch_url = parse_unit_switch_link(html, self._sei_url(""))
        if not switch_url:
            raise RuntimeError("Link de troca de unidade não encontrado")
        
        r = self._get(switch_url)
        units = parse_units_switch_page(r.text, self._sei_url(""))
        form_action, hiddens = parse_unit_switch_form(r.text)
        
        current_unit_id = parse_qs(urlparse(str(r.url)).query).get("infra_unidade_atual", [None])[0]

        kw = keyword.lower()
        target = None
        for u in units:
            if kw == (u.link or "").lower():
                target = u
                break
            if kw in u.sigla.lower() or kw in u.descricao.lower():
                target = u
                break
        
        if not target or not target.link:
            available = ", ".join(u.sigla for u in units)
            raise RuntimeError(f"Unidade '{keyword}' não encontrada. Disponíveis: {available}")

        if current_unit_id and target.link == current_unit_id:
            status = self.status()
            self._current_unit_id = target.link
            return status
        
        # POST form with selInfraUnidades (the key JS creates dynamically)
        post_url = urljoin(str(r.url), form_action) if form_action else switch_url
        data = {**hiddens, "selInfraUnidades": target.link}

        r2 = self._post(post_url, data)

        # After switching, the response is a confirmation page, not the control
        # page. We need to explicitly load the control page to get process lists.
        post_status = parse_system_status(r2.text)
        self._current_unit_id = target.link
        control_url = self._sei_url(
            f"controlador.php?acao=procedimento_controlar"
            f"&infra_sistema=100000100&infra_unidade_atual={target.link}"
        )
        rc = self._get(control_url)
        if self._is_valid_control_html(rc.text):
            self._control_html = rc.text
            self._menu_links = parse_menu_links(rc.text, self._sei_url(""))
        else:
            self._control_html = None
            refreshed = self._ensure_session()
            if self._is_valid_control_html(refreshed):
                self._control_html = refreshed
                self._menu_links = parse_menu_links(refreshed, self._sei_url(""))
        self._persist_session()
        control_status = parse_system_status(self._control_html or rc.text)
        if control_status.valid:
            return control_status
        if post_status.valid:
            return post_status
        return control_status

    # --- Search ---

    def search(self, query: str) -> str:
        """Quick search (pesquisa rápida).

        When given a SEI protocol/document number, navigates directly
        to that document or process. Returns raw HTML of the result page.
        """
        html = self._ensure_control()
        soup = BeautifulSoup(html, "lxml")

        pesq_form = soup.find("form", {"id": "frmProtocoloPesquisaRapida"})
        if not pesq_form:
            pesq_input = soup.find("input", {"id": "txtPesquisaRapida"})
            if pesq_input:
                pesq_form = pesq_input.find_parent("form")

        if not pesq_form:
            raise RuntimeError("Formulário de pesquisa não encontrado")

        action = pesq_form.get("action", "")
        search_url = urljoin(self._sei_url(""), action)

        r = self._post(search_url, data={"txtPesquisaRapida": query})
        self._control_html = None

        # Check if we were redirected to a process/document page
        final_url = str(r.url)
        if "procedimento_trabalhar" in final_url:
            # Landed on a process page — return it
            return r.text
        if "documento_visualizar" in final_url:
            # Landed on a document view — return it
            return r.text

        # Check for frameset — SEI often uses framesets
        rsoup = BeautifulSoup(r.text, "lxml")
        frame = rsoup.find("iframe", {"id": "ifrVisualizacao"})
        if frame and frame.get("src"):
            # Follow the iframe to get actual content
            iframe_url = urljoin(self._sei_url(""), frame["src"])
            ri = self._get(iframe_url)
            return ri.text

        return r.text

    def search_document(self, protocolo: str) -> tuple[str, str] | None:
        """Search for a document by its SEI protocol number.

        Uses pesquisa rápida to find a document and extracts
        id_documento and id_procedimento from the resulting page.

        Args:
            protocolo: SEI protocol number (e.g. '39701977').

        Returns:
            Tuple of (id_documento, id_procedimento) or None if not found.
        """
        html = self.search(protocolo)
        soup = BeautifulSoup(html, "lxml")

        # Check URL patterns in the page
        final_url = ""
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "id_documento" in href:
                id_doc = re.search(r"id_documento=(\d+)", href)
                id_proc = re.search(r"id_procedimento=(\d+)", href)
                if id_doc and id_proc:
                    return (id_doc.group(1), id_proc.group(1))

        # Try extracting from any script, hidden field, or iframe src
        # Order varies: sometimes id_documento before id_procedimento, sometimes reversed
        for pattern in [
            r'"id_documento":"(\d+)".*?"id_procedimento":"(\d+)"',
            r'id_documento=(\d+).*?id_procedimento=(\d+)',
        ]:
            m = re.search(pattern, html)
            if m:
                return (m.group(1), m.group(2))

        # Try reversed order (SEI often puts id_procedimento before id_documento)
        for pattern in [
            r'id_procedimento=(\d+).*?id_documento=(\d+)',
        ]:
            m = re.search(pattern, html)
            if m:
                return (m.group(2), m.group(1))  # swap: return (id_doc, id_proc)

        return None

    # --- New processes check ---

    def check_new_processes(self) -> list[Process]:
        """Return only processes marked as 'novo' (unread)."""
        procs = self.list_processes()
        return [p for p in procs.recebidos if p.novo]

    # --- Tramitação ---

    def _open_process_page(self, id_procedimento: str) -> str:
        """Navigate to process page and return the inner visualization HTML.

        Uses direct URL to reach the frameset, then follows the
        procedimento_visualizar iframe where action links live.
        """
        # Direct URL approach — works without the process being in the list
        self._ensure_session()
        url = self._sei_url(
            f"controlador.php?acao=procedimento_trabalhar"
            f"&id_procedimento={id_procedimento}"
        )
        r = self._get(url)
        outer_html = r.text

        if "ifrArvore" not in outer_html and "procedimento_trabalhar" not in outer_html:
            raise RuntimeError(
                f"Não foi possível abrir processo {id_procedimento}. "
                "Verifique se está na unidade correta."
            )

        # Follow the procedimento_visualizar iframe to get action links
        iframe_m = re.search(
            r'<(?:i?frame)[^>]+src=["\']([^"\']*procedimento_visualizar[^"\']*)["\']',
            outer_html, re.I,
        )
        if iframe_m:
            iframe_url = iframe_m.group(1)
            if not iframe_url.startswith("http"):
                iframe_url = urljoin(self._sei_url(""), iframe_url)
            ri = self._get(iframe_url)
            return ri.text

        # No iframe found — page might already be the inner content
        return outer_html

    def _open_process_wrapper(self, id_procedimento: str) -> str:
        """Open procedimento_trabalhar and return the wrapper HTML."""
        self._ensure_session()
        url = self._sei_url(
            f"controlador.php?acao=procedimento_trabalhar"
            f"&id_procedimento={id_procedimento}"
        )
        r = self._get(url)
        return r.text

    def get_tramitar_form(self, id_procedimento: str, _proc_html: str | None = None) -> TramitarForm:
        """Open 'Enviar Processo' form and parse destination units/fields.

        Navigates to process page, follows iframe, extracts action URL.
        Pass _proc_html to avoid double navigation.
        """
        proc_html = _proc_html or self._open_process_page(id_procedimento)
        send_url = self._extract_action_url(proc_html, "procedimento_enviar")
        if not send_url:
            raise RuntimeError("Ação 'Enviar Processo' não encontrada na página do processo")

        rsend = self._get(send_url)
        tramitar = parse_tramitar_form(rsend.text, self._sei_url(""), str(rsend.url))
        return tramitar

    def _resolve_unit_id_ajax(
        self,
        unidade: str,
        ajax_url: str | None,
        unit_descriptions: dict[str, str],
    ) -> str:
        """Resolve a unit name/alias to its SEI ID using AJAX as source of truth.

        Priority:
        1. If numeric ID → return as-is
        2. AJAX auto-complete search (live data from SEI)
        3. Fallback to static UNIT_IDS (only when AJAX unavailable)

        Also populates *unit_descriptions* with id→description mappings
        found via AJAX for later use in hdnUnidades.
        """
        import re as _re

        kw = unidade.strip()
        # If it's a numeric ID already, keep it
        if kw.isdigit():
            return kw

        # --- Try AJAX resolution (source of truth) --------------------
        if ajax_url:
            # Build search keywords from the user-provided name.
            # Replace hyphens/slashes with spaces (SEI AJAX searches by words).
            kw_clean = kw.replace("-", " ").replace("/", " ").strip()
            search_terms = [kw_clean]
            # Also add the raw name in case it works
            if kw_clean != kw:
                search_terms.append(kw)
            # Try individual meaningful words from UNIT_IDS alias
            for name, uid in self.UNIT_IDS.items():
                if kw.lower() == name.lower():
                    words = [
                        w for w in name.replace("-", " ").replace("/", " ").split()
                        if len(w) > 2
                        and w.upper() not in ("CBM", "COBM", "SEC", "RN")
                    ]
                    search_terms.extend(words)
                    break

            searched: set[str] = set()
            kw_lower = kw.lower().replace("-", " ").replace("/", " ")
            for term in search_terms:
                if term.lower() in searched:
                    continue
                searched.add(term.lower())
                # Try orgao=28 (CBM) first, then 0 (Todos) — CBM units
                # only appear under orgao=28 in some SEI configurations.
                for orgao in ("28", "0"):
                    resp = self.client.post(
                        ajax_url,
                        content=(
                            f"palavras_pesquisa={term}"
                            f"&id_orgao={orgao}&unidade_atual=0"
                        ).encode("latin-1"),
                        headers={
                            "Content-Type": "application/x-www-form-urlencoded",
                            "X-Requested-With": "XMLHttpRequest",
                        },
                    )
                    items = _re.findall(
                        r'<item[^>]*id="([^"]+)"[^>]*descricao="([^"]+)"',
                        resp.text,
                    )
                    # Store all discovered descriptions
                    for item_id, desc in items:
                        unit_descriptions[item_id] = desc

                    # Try to match: normalize both sides for comparison
                    matches = []
                    for iid, desc in items:
                        desc_lower = desc.lower()
                        # Normalize description: collapse " - " separators
                        # so "PAD - PDF" matches "PAD PDF"
                        desc_norm = _re.sub(r"\s*-\s*", " ", desc_lower)
                        # Match if search keyword appears in description
                        if kw_lower in desc_lower or kw_lower in desc_norm:
                            matches.append((iid, desc))

                    # Prefer CBM items
                    cbm_matches = [
                        (iid, desc) for iid, desc in matches if "CBM" in desc
                    ]
                    if len(cbm_matches) == 1:
                        return cbm_matches[0][0]
                    if len(cbm_matches) > 1:
                        options = ", ".join(sorted({desc for _, desc in cbm_matches}))
                        raise RuntimeError(
                            f"Unidade '{unidade}' é ambígua. Especifique uma destas: {options}"
                        )
                    if len(matches) == 1:
                        return matches[0][0]
                    if len(matches) > 1:
                        options = ", ".join(sorted({desc for _, desc in matches}))
                        raise RuntimeError(
                            f"Unidade '{unidade}' é ambígua. Especifique uma destas: {options}"
                        )

        # --- Fallback to static UNIT_IDS ------------------------------
        return self._resolve_unit_id(unidade)

    def _resolve_unit_id(self, unidade: str) -> str:
        """Resolve a unit name/alias to its SEI ID (static fallback).

        Priority: exact ID → exact name → contains match.
        """
        kw = unidade.lower().strip()
        # If it's a numeric ID already
        if kw.isdigit():
            return kw
        # Exact name match first
        for name, uid in self.UNIT_IDS.items():
            if kw == name.lower():
                return uid
        # Contains match (prefer shorter names = more specific)
        matches = [
            (name, uid) for name, uid in self.UNIT_IDS.items()
            if kw in name.lower()
        ]
        if len(matches) == 1:
            return matches[0][1]
        if len(matches) > 1:
            options = ", ".join(sorted({name for name, _ in matches}))
            raise RuntimeError(
                f"Unidade '{unidade}' é ambígua. Especifique uma destas: {options}"
            )
        raise RuntimeError(
            f"Unidade '{unidade}' não encontrada. "
            f"Opções: {', '.join(self.UNIT_IDS.keys())}"
        )

    def list_unidades_destino_tramitacao(self, id_procedimento: str) -> list[Unit]:
        """List known destination units for forwarding.

        Note: SEI's forwarding form uses infraLupaSelect (AJAX),
        so we return known units from UNIT_IDS instead.
        """
        return [
            Unit(sigla=name, descricao=name, link=uid)
            for name, uid in self.UNIT_IDS.items()
        ]

    def listar_unidades_usuario(self) -> list[dict[str, str]]:
        """List SEI units the logged-in user has access to.

        Navigates the unit-switch form (infra_trocar_unidade), POSTs with
        the CBM orgão (28), and parses the resulting table of sub-units.

        Returns:
            List of dicts with keys: id, sigla, current (bool).
        """
        # 1. Get main page to extract trocar link with valid hash
        r_main = self._get(self._sei_url("inicializar.php?infra_sistema=100000100"))
        trocar_match = re.search(
            r"window\.location\.href='(controlador\.php\?acao=infra_trocar_unidade[^']+)'",
            r_main.text,
        )
        if not trocar_match:
            raise RuntimeError("Link de troca de unidade não encontrado")

        r_trocar = self._get(self._sei_url(trocar_match.group(1)))

        # 2. Find the form action and POST with CBM orgão
        form_match = re.search(
            r'<form[^>]*id="frmInfraSelecaoUnidade"[^>]*action="([^"]*)"',
            r_trocar.text,
        )
        if not form_match:
            raise RuntimeError("Formulário de seleção de unidade não encontrado")

        action = form_match.group(1).replace("&amp;", "&")
        r_units = self._post(self._sei_url(action), data={"selInfraOrgaoUnidade": "28"})

        # 3. Parse selecionarUnidade(id) from table rows
        # Pattern: selecionarUnidade(ID)"/></td><td ...>SIGLA</td>
        links = re.findall(
            r'selecionarUnidade\((\d+)\).*?</td>\s*<td[^>]*>([^<]+)<',
            r_units.text,
        )

        current_id = self._current_unit_id or ""
        return [
            {"id": uid, "sigla": name.strip(), "current": uid == current_id}
            for uid, name in links
        ]

    def trocar_unidade(self, unit_id_or_name: str) -> bool:
        """Switch the SEI session to a different unit.

        Args:
            unit_id_or_name: Numeric unit ID or a name from UNIT_IDS/listar_unidades.

        Returns:
            True if the switch succeeded.
        """
        # Resolve name → ID
        target_id = unit_id_or_name
        if not target_id.isdigit():
            # Try UNIT_IDS first
            resolved = self.UNIT_IDS.get(target_id)
            if not resolved:
                # Try listing user units and matching sigla
                units = self.listar_unidades_usuario()
                for u in units:
                    if target_id.lower() in u["sigla"].lower():
                        resolved = u["id"]
                        break
            if not resolved:
                raise RuntimeError(f"Unidade '{target_id}' não encontrada")
            target_id = resolved

        # Navigate to trocar form and submit
        r_main = self._get(self._sei_url("inicializar.php?infra_sistema=100000100"))
        trocar_match = re.search(
            r"window\.location\.href='(controlador\.php\?acao=infra_trocar_unidade[^']+)'",
            r_main.text,
        )
        if not trocar_match:
            raise RuntimeError("Link de troca de unidade não encontrado")

        r_trocar = self._get(self._sei_url(trocar_match.group(1)))
        form_match = re.search(
            r'<form[^>]*id="frmInfraSelecaoUnidade"[^>]*action="([^"]*)"',
            r_trocar.text,
        )
        if not form_match:
            raise RuntimeError("Formulário de seleção de unidade não encontrado")

        action = form_match.group(1).replace("&amp;", "&")

        # Submit the unit selection (same as selecionarUnidade JS function)
        r = self._post(
            self._sei_url(action),
            data={"selInfraUnidades": target_id},
        )

        self._current_unit_id = target_id
        self._control_html = None

        # Check success: should redirect to main/controlar page
        url_str = str(r.url)
        if "login" in url_str:
            raise RuntimeError("Falha ao trocar unidade — sessão expirou")

        # Load the control page for the new unit.
        # After unit switch the infra_hash changes, so we use
        # inicializar.php to get a valid redirect with fresh hashes.
        html = self._try_inicializar()
        if html and self._is_valid_control_html(html):
            self._control_html = html
            self._menu_links = parse_menu_links(html, self._sei_url(""))
            self._persist_session()
        else:
            # Fallback: full re-login (gets control page with correct unit)
            self.login()

        return True

    # Legacy compatibility helper kept for callers that still expect the
    # boolean-returning unit switch flow.
    switch_unit_legacy = trocar_unidade

    def check_reopen_available(self, id_procedimento: str) -> bool:
        """Check if 'Reabrir Processo' is available without executing it.

        Returns:
            True if the reopen action is available.
        """
        try:
            arvore_html = self._navigate_to_arvore_visualizar(id_procedimento)
        except Exception:
            return False
        if not arvore_html:
            return False
        return bool(
            re.search(
                r"var\s+linkReabrirProcesso\s*=\s*'[^']+'",
                arvore_html,
            )
        )

    def reabrir_processo(self, id_procedimento: str) -> bool:
        """Reopen a process that was closed/concluded in the current unit.

        Source of truth: ``arvore_visualizar`` defines the inline JS variable
        ``linkReabrirProcesso`` when the current unit can reopen the concluded
        process. ``procedimento_visualizar`` does not expose this action.

        Args:
            id_procedimento: Process internal ID.

        Returns:
            True if the reopen succeeded.

        Raises:
            RuntimeError: If the reopen action is not available or fails.
        """
        arvore_html = self._navigate_to_arvore_visualizar(id_procedimento)
        if not arvore_html:
            raise RuntimeError(
                f"Não foi possível acessar a árvore do processo {id_procedimento}."
            )

        link_m = re.search(
            r"var\s+linkReabrirProcesso\s*=\s*'([^']+)'",
            arvore_html,
        )
        if not link_m:
            raise RuntimeError(
                f"Ação 'Reabrir Processo' não encontrada para o processo {id_procedimento}. "
                "O processo pode já estar aberto nessa unidade ou você não tem permissão."
            )

        reabrir_url = link_m.group(1).replace("&amp;", "&")
        r = self._get(self._sei_url(reabrir_url))
        self._control_html = None
        rsoup = BeautifulSoup(r.text, "lxml")
        validation = rsoup.find("textarea", {"id": "txaInfraValidacao"})
        if validation and validation.get_text(strip=True):
            raise RuntimeError(validation.get_text(" ", strip=True))

        post_html = self._open_process_page(id_procedimento)
        if (
            self._extract_action_url(post_html, "procedimento_concluir")
            or self._extract_action_url(post_html, "procedimento_enviar")
        ):
            return True

        raise RuntimeError("SEI não confirmou a reabertura do processo.")

    def get_process_history(
        self,
        id_procedimento: str,
        *,
        full: bool = False,
    ) -> list[ProcessHistoryEntry]:
        """Read process history, optionally requesting the complete view.

        The SEI page exposes a normal history table and a ``Ver histórico
        completo`` form.  The form details are deliberately kept here so
        callers only need to request ``full=True``.
        """
        arvore_html = self._navigate_to_arvore(id_procedimento)
        if not arvore_html:
            return []
        hist_url_match = re.search(
            r'(controlador\.php\?acao=procedimento_consultar_historico[^"\'<>\s]+)',
            arvore_html,
        )
        if not hist_url_match:
            return []

        history_url = self._sei_url(hist_url_match.group(1).replace("&amp;", "&"))
        response = self._get(history_url)
        if full:
            soup = BeautifulSoup(response.text, "lxml")
            form = soup.find("form", id="frmProcedimentoHistorico")
            if form is None:
                form = next(
                    (
                        candidate
                        for candidate in soup.find_all("form")
                        if "procedimento_consultar_historico" in candidate.get("action", "")
                    ),
                    None,
                )
            if form is not None:
                data: dict[str, str] = {}
                for field in form.find_all("input"):
                    name = field.get("name")
                    field_type = (field.get("type") or "").casefold()
                    if not name or field_type in {"submit", "button", "image", "reset"}:
                        continue
                    if field_type in {"checkbox", "radio"} and not field.has_attr("checked"):
                        continue
                    data[name] = field.get("value", "")
                for field in form.find_all("select"):
                    name = field.get("name")
                    if not name:
                        continue
                    selected = field.find("option", selected=True)
                    data[name] = selected.get("value", "") if selected else ""
                for field in form.find_all("textarea"):
                    name = field.get("name")
                    if name:
                        data[name] = field.get_text()
                data["hdnTipoHistorico"] = "P"
                action = form.get("action") or history_url
                response = self._post(
                    urljoin(self._sei_url(""), action.replace("&amp;", "&")),
                    data,
                )

        return parse_process_history(response.text)

    def list_process_history_units(self, id_procedimento: str) -> list[str]:
        """Backward-compatible unit-only history lookup."""
        units: list[str] = []
        seen: set[str] = set()
        for entry in self.get_process_history(id_procedimento, full=True):
            if entry.unit and entry.unit not in seen:
                seen.add(entry.unit)
                units.append(entry.unit)
        return units


    # ------------------------------------------------------------------
    # Concluir Processo
    # ------------------------------------------------------------------

    def concluir_processos(
        self,
        id_procedimentos: "str | list[str]",
        *,
        reabrir_em: str | None = None,
        reabrir_dias: str | None = None,
        reabrir_dias_uteis: bool = False,
    ) -> dict:
        """Conclude one or more processes in the current unit.

        Uses the Controle de Processos form to select processes and
        POST to ``procedimento_concluir``, then confirms via the
        ``frmDesentranharDocumento`` form (rdoConcluir=S).

        Args:
            id_procedimentos: Single process ID or list of process IDs.

        Returns:
            dict with keys: concluded (list), failed (list), errors (dict).
        """
        if isinstance(id_procedimentos, str):
            id_procedimentos = [id_procedimentos]

        result: dict = {"concluded": [], "failed": [], "errors": {}}

        # Work in batches — control page only shows ~50 processes per section
        remaining = set(id_procedimentos)

        while remaining:
            # Try cached control first, fall back to fresh login
            html = self._ensure_control()
            soup = BeautifulSoup(html, "lxml")
            form = soup.find("form", id="frmProcedimentoControlar")
            if not form:
                # Session stale — re-login to get fresh control page
                self.login()
                html = self._ensure_control()
                soup = BeautifulSoup(html, "lxml")
                form = soup.find("form", id="frmProcedimentoControlar")
            if not form:
                for pid in remaining:
                    result["failed"].append(pid)
                    result["errors"][pid] = "Control form not found"
                break

            # Build form data from hidden fields
            data: dict[str, str] = {}
            for inp in form.find_all("input", type="hidden"):
                name = inp.get("name", "")
                val = inp.get("value", "")
                if name:
                    data[name] = val

            # Determine which processes are on this page (recebidos + gerados)
            recebidos = set(
                data.get("hdnRecebidosItens", "").split(",")
            ) - {""}
            gerados = set(
                data.get("hdnGeradosItens", "").split(",")
            ) - {""}

            # Select processes that are on this page
            batch_recebidos = remaining & recebidos
            batch_gerados = remaining & gerados
            batch = batch_recebidos | batch_gerados

            if not batch:
                # None of the remaining processes are on page 1
                for pid in remaining:
                    result["failed"].append(pid)
                    result["errors"][pid] = "Process not on control page"
                break

            data["hdnRecebidosItensSelecionados"] = ",".join(batch_recebidos)
            data["hdnGeradosItensSelecionados"] = ",".join(batch_gerados)

            # Find the procedimento_concluir URL (has valid infra_hash)
            concluir_m = re.search(
                r"controlador\.php\?acao=procedimento_concluir[^'\"]+", html
            )
            if not concluir_m:
                for pid in batch:
                    result["failed"].append(pid)
                    result["errors"][pid] = "Concluir URL not found"
                remaining -= batch
                continue

            concluir_url = concluir_m.group().replace("&amp;", "&")

            # POST to concluir — should return confirmation page
            r_confirm = self._post(self._sei_url(concluir_url), data)

            if "frmDesentranharDocumento" not in r_confirm.text:
                for pid in batch:
                    result["failed"].append(pid)
                    result["errors"][pid] = "Confirmation page not received"
                remaining -= batch
                continue

            # Parse confirmation form
            soup2 = BeautifulSoup(r_confirm.text, "lxml")
            form2 = soup2.find("form", id="frmDesentranharDocumento")
            if not form2:
                for pid in batch:
                    result["failed"].append(pid)
                    result["errors"][pid] = "Confirmation form not found"
                remaining -= batch
                continue

            confirm_action = form2["action"].replace("&amp;", "&")
            confirm_data: dict[str, str] = {}
            for inp in form2.find_all("input"):
                name = inp.get("name", "")
                val = inp.get("value", "")
                if name and name not in confirm_data:
                    confirm_data[name] = val

            # Set conclusion type (S = definitive, V = with scheduled reopening)
            if reabrir_em or reabrir_dias:
                confirm_data["rdoConcluir"] = "V"
            else:
                confirm_data["rdoConcluir"] = "S"
            if reabrir_em or reabrir_dias:
                fields = {"radio": "rdoPrazoReaberturaProgramada"}
                for inp in form2.find_all("input"):
                    name = inp.get("name", "")
                    lname = name.lower()
                    if "txtprazoreaberturaprogramada" in lname:
                        fields["data"] = name
                    elif "txtdiasreaberturaprogramada" in lname:
                        fields["dias"] = name
                    elif "chksindiasuteisreaberturaprogramada" in lname:
                        fields["uteis"] = name
                extra_pairs: list[tuple[str, str]] = []
                self._apply_schedule_fields(
                    extra_pairs,
                    fields,
                    data=reabrir_em,
                    dias=reabrir_dias,
                    dias_uteis=reabrir_dias_uteis,
                    label="reabertura programada",
                )
                for key, value in extra_pairs:
                    confirm_data[key] = value
            confirm_data["sbmSalvar"] = "Salvar"

            # Submit confirmation
            r_save = self._post(self._sei_url(confirm_action), confirm_data)
            self._control_html = None

            # Check success — redirect or no error
            if r_save.status_code in (200, 302):
                for pid in batch:
                    result["concluded"].append(pid)
            else:
                for pid in batch:
                    result["failed"].append(pid)
                    result["errors"][pid] = f"HTTP {r_save.status_code}"

            remaining -= batch

        return result

    def concluir_processo(
        self,
        id_procedimento: str,
        *,
        reabrir_em: str | None = None,
        reabrir_dias: str | None = None,
        reabrir_dias_uteis: bool = False,
    ) -> bool:
        """Conclude a single process. Convenience wrapper around concluir_processos.

        Args:
            id_procedimento: Process ID.

        Returns:
            True if concluded successfully.

        Raises:
            RuntimeError: If conclusion failed.
        """
        result = self.concluir_processos(
            id_procedimento,
            reabrir_em=reabrir_em,
            reabrir_dias=reabrir_dias,
            reabrir_dias_uteis=reabrir_dias_uteis,
        )
        if id_procedimento in result["concluded"]:
            return True
        error = result["errors"].get(id_procedimento, "Unknown error")
        raise RuntimeError(f"Failed to conclude process {id_procedimento}: {error}")

    def enviar_processo(
        self,
        id_procedimento: str,
        unidades_destino: "str | list[str]",
        manter_aberto: bool = True,
        retorno_em: str | None = None,
        retorno_dias: str | None = None,
        retorno_dias_uteis: bool = False,
        reabrir_em: str | None = None,
        reabrir_dias: str | None = None,
        reabrir_dias_uteis: bool = False,
    ) -> bool:
        """Send process to one or more destination units.

        Accepts a single unit name/ID or a list of units for simultaneous
        forwarding (SEI's form supports multiple selections).

        The SEI form uses ``infraLupaSelect`` (JS-driven) to populate
        ``selUnidades`` (a ``<select multiple>``) and ``hdnUnidades``
        (a hidden field).  The server expects ``hdnUnidades`` in
        infraLupa format::

            ID±DESCRIPTION¥ID±DESCRIPTION¥…

        Where ``±`` (0xB1) separates ID from description inside each
        entry, and ``¥`` (0xA5) separates entries.  We fetch descriptions
        from the AJAX auto-complete endpoint used by the form.

        Args:
            id_procedimento: Process internal ID.
            unidades_destino: Unit name, alias or ID — or a list of them.
            manter_aberto: If True, keep the process open in the current unit
                after forwarding.  If False, the process is closed here.

        Returns:
            True if the send succeeded.
        """
        import re as _re

        # Normalize to list
        if isinstance(unidades_destino, str):
            unidades_destino = [unidades_destino]

        # Navigate to the enviar form FIRST (we need AJAX to resolve IDs)
        proc_html = self._open_process_page(id_procedimento)
        send_url = self._extract_action_url(proc_html, "procedimento_enviar")
        if not send_url:
            raise RuntimeError("Ação 'Enviar Processo' não encontrada")

        r_form = self._get(send_url)
        soup = BeautifulSoup(r_form.text, "lxml")
        form = soup.find("form")
        if not form:
            raise RuntimeError("Formulário de enviar não encontrado")

        form_action = form.get("action", "").replace("&amp;", "&")
        submit_url = urljoin(self._sei_url(""), form_action)
        tramitar_form = parse_tramitar_form(r_form.text, self._sei_url(""), str(r_form.url))

        # --- Resolve units via AJAX auto-complete (source of truth) ----
        # Extract the AJAX URL from the form page JS
        ajax_match = _re.search(
            r"(controlador_ajax\.php\?acao_ajax="
            r"unidade_auto_completar_envio_processo[^']+)",
            r_form.text,
        )
        ajax_url = (
            self.base_url + "/" + ajax_match.group(1) if ajax_match else None
        )

        # Resolve each unit name/alias to its numeric SEI ID via AJAX,
        # falling back to UNIT_IDS only when AJAX is unavailable.
        unit_ids: list[str] = []
        unit_descriptions: dict[str, str] = {}
        for dest_name in unidades_destino:
            resolved_id = self._resolve_unit_id_ajax(
                dest_name, ajax_url, unit_descriptions,
            )
            unit_ids.append(resolved_id)

        # Enrich descriptions for numeric IDs that skipped AJAX resolution
        if ajax_url:
            for uid in unit_ids:
                if uid not in unit_descriptions:
                    keywords = self._unit_search_keywords(uid)
                    for kw in keywords:
                        resp = self.client.post(
                            ajax_url,
                            content=(
                                f"palavras_pesquisa={kw}"
                                f"&id_orgao=0&unidade_atual=0"
                            ).encode("latin-1"),
                            headers={
                                "Content-Type": "application/x-www-form-urlencoded",
                                "X-Requested-With": "XMLHttpRequest",
                            },
                        )
                        for item_id, desc in _re.findall(
                            r'<item[^>]*id="([^"]+)"[^>]*descricao="([^"]+)"',
                            resp.text,
                        ):
                            unit_descriptions[item_id] = desc
                        if uid in unit_descriptions:
                            break

        # infraLupa separators (ISO-8859-1)
        SEP_ENTRY = "\xa5"  # ¥ — between entries
        SEP_KV = "\xb1"     # ± — between ID and description

        hdn_parts = []
        for uid in unit_ids:
            desc = unit_descriptions.get(uid, uid)
            hdn_parts.append(f"{uid}{SEP_KV}{desc}")
        hdn_value = SEP_ENTRY.join(hdn_parts)

        # --- Build form data as list of tuples -------------------------
        pairs: list[tuple[str, str]] = []

        # Collect hidden fields (skip hdnUnidades — we build it ourselves)
        for inp in form.find_all("input", {"type": "hidden"}):
            name = inp.get("name", "")
            if name and name != "hdnUnidades":
                pairs.append((name, inp.get("value", "")))

        # selProcedimentos — the <select> that lists the process(es)
        sel_proc = form.find("select", {"name": "selProcedimentos"})
        if sel_proc:
            for opt in sel_proc.find_all("option"):
                pairs.append(("selProcedimentos", opt.get("value", "")))

        # Unit selection — one entry per unit for multi-select
        for uid in unit_ids:
            pairs.append(("selUnidades", uid))

        # hdnUnidades in infraLupa format
        pairs.append(("hdnUnidades", hdn_value))

        # Manter aberto checkbox
        if manter_aberto:
            chk = form.find("input", {"name": "chkSinManterAberto"})
            chk_value = chk.get("value") if chk and chk.get("value") else "on"
            pairs.append(("chkSinManterAberto", chk_value))

        self._apply_schedule_fields(
            pairs,
            tramitar_form.retorno_programado_fields,
            data=retorno_em,
            dias=retorno_dias,
            dias_uteis=retorno_dias_uteis,
            label="retorno programado",
        )
        self._apply_schedule_fields(
            pairs,
            tramitar_form.reabertura_programada_fields,
            data=reabrir_em,
            dias=reabrir_dias,
            dias_uteis=reabrir_dias_uteis,
            label="reabertura programada",
        )

        # Submit button
        pairs.append(("sbmEnviar", "Enviar"))

        r = self._post_pairs(submit_url, pairs)
        self._control_html = None

        # Check result: SEI redirects to the process tree on success.
        # On failure, the page shows "Enviar Processo" title and/or alerts.
        lower = r.text.lower()
        if "enviar processo" in lower:
            return False
        return True

    def _apply_schedule_fields(
        self,
        pairs: list[tuple[str, str]],
        fields: dict[str, str],
        *,
        data: str | None = None,
        dias: str | None = None,
        dias_uteis: bool = False,
        label: str,
    ) -> None:
        if not data and not dias:
            return
        if data and dias:
            raise RuntimeError(f"{label.capitalize()} aceita data ou dias, não ambos.")
        if not fields:
            raise RuntimeError(f"Formulário não expôs campos para {label}.")
        radio = fields.get("radio")
        if not radio:
            raise RuntimeError(f"Formulário sem rádio de seleção para {label}.")
        if data:
            data_field = fields.get("data")
            if not data_field:
                raise RuntimeError(f"Formulário sem campo de data para {label}.")
            pairs.append((radio, "1"))
            pairs.append((data_field, data))
        elif dias:
            dias_field = fields.get("dias")
            if not dias_field:
                raise RuntimeError(f"Formulário sem campo de dias para {label}.")
            pairs.append((radio, "2"))
            pairs.append((dias_field, dias))
            if dias_uteis and fields.get("uteis"):
                pairs.append((fields["uteis"], "S"))

    def get_concluir_form(self, id_procedimento: str) -> dict[str, Any]:
        """Open conclude form and expose supported scheduled-reopen fields."""
        html = self._ensure_control()
        data: dict[str, str] = {}
        soup = BeautifulSoup(html, "lxml")
        form = soup.find("form", id="frmProcedimentoControlar")
        if not form:
            raise RuntimeError("Control form not found")
        for inp in form.find_all("input", type="hidden"):
            name = inp.get("name", "")
            if name:
                data[name] = inp.get("value", "")

        recebidos = set(data.get("hdnRecebidosItens", "").split(",")) - {""}
        gerados = set(data.get("hdnGeradosItens", "").split(",")) - {""}
        if id_procedimento in recebidos:
            data["hdnRecebidosItensSelecionados"] = id_procedimento
            data["hdnGeradosItensSelecionados"] = ""
        elif id_procedimento in gerados:
            data["hdnRecebidosItensSelecionados"] = ""
            data["hdnGeradosItensSelecionados"] = id_procedimento
        else:
            raise RuntimeError("Process not on control page")

        concluir_m = re.search(r"controlador\.php\?acao=procedimento_concluir[^'\"]+", html)
        if not concluir_m:
            raise RuntimeError("Concluir URL not found")
        r_confirm = self._post(self._sei_url(concluir_m.group().replace("&amp;", "&")), data)
        soup2 = BeautifulSoup(r_confirm.text, "lxml")
        form2 = soup2.find("form", id="frmDesentranharDocumento")
        if not form2:
            raise RuntimeError("Confirmation form not found")

        fields = {"radio": "rdoPrazoReaberturaProgramada"}
        for inp in form2.find_all("input"):
            name = inp.get("name", "")
            lname = name.lower()
            if "txtprazoreaberturaprogramada" in lname:
                fields["data"] = name
            elif "txtdiasreaberturaprogramada" in lname:
                fields["dias"] = name
            elif "chksindiasuteisreaberturaprogramada" in lname:
                fields["uteis"] = name

        return {
            "action": form2.get("action", "").replace("&amp;", "&"),
            "hidden_fields": {
                inp.get("name", ""): inp.get("value", "")
                for inp in form2.find_all("input")
                if inp.get("name")
            },
            "reabertura_programada_fields": fields if any(k in fields for k in ("data", "dias", "uteis")) else {},
            "supports_reopen_schedule": bool(any(k in fields for k in ("data", "dias"))),
        }

    def list_process_open_units(self, id_procedimento: str) -> list[str]:
        """Best-effort list of units where the process is currently open."""
        arvore_html = self._navigate_to_arvore(id_procedimento)
        if not arvore_html:
            return []
        return self._find_open_units_from_history(arvore_html)

    def _unit_search_keywords(self, unit_id: str) -> list[str]:
        """Generate search keywords to find a unit via AJAX by ID.

        The AJAX endpoint searches by description text, not by numeric ID.
        We reverse-lookup known names from ``UNIT_IDS`` and extract
        meaningful keywords.
        """
        # Reverse lookup from UNIT_IDS
        for name, uid in self.UNIT_IDS.items():
            if uid == unit_id:
                words = [w for w in name.split() if len(w) > 2]
                keywords = [name]
                for w in words:
                    if w.upper() not in ("CBM", "COBM", "CMDO", "SEC", "RN"):
                        keywords.append(w)
                return keywords
        # Fallback: return the ID itself (unlikely to match, but harmless)
        return [unit_id]

    def search_units(
        self, keyword: str, orgao: str = "0"
    ) -> list[tuple[str, str]]:
        """Search for SEI units by keyword using the enviar AJAX endpoint.

        Returns list of (unit_id, description) tuples.
        Requires an active enviar form page (call from enviar_processo context).
        """
        import re as _re

        # We need a valid process page to get the AJAX hash.
        # This is a lightweight helper — callers should have already
        # loaded the enviar form.
        return []  # Placeholder — actual search done inline in enviar_processo

    def tramitar_processo(
        self,
        id_procedimento: str,
        unidade_destino: "str | list[str]",
        manter_aberto: bool = True,
    ) -> bool:
        """Alias de enviar_processo para API mais explícita."""
        return self.enviar_processo(
            id_procedimento=id_procedimento,
            unidades_destino=unidade_destino,
            manter_aberto=manter_aberto,
        )

    # --- Marcadores ---

    MARCADOR_CORES = {
        "preto": "0",
        "branco": "1",
        "cinza": "2",
        "vermelho": "3",
        "amarelo": "4",
        "verde": "5",
        "azul": "6",
        "rosa": "7",
        "roxo": "8",
        "ciano": "9",
        "bege": "10",
        "champagne": "11",
        "cinza_escuro": "12",
        "laranja": "13",
        "lilas": "14",
        "marrom": "15",
        "ouro": "16",
        "prata": "17",
        "rosa_claro": "18",
        "tijolo": "19",
        "verde_agua": "20",
        "verde_escuro": "21",
        "verde_amazonas": "22",
        "azul_ceu": "23",
        "bronze": "24",
        "amarelo_ouro": "25",
        "vinho": "26",
        "azul_riviera": "27",
        "verde_abacate": "28",
        "amarelo_claro": "29",
        "verde_turquesa": "30",
        "azul_marinho": "31",
    }

    def _resolve_cor(self, cor: str | None) -> str:
        """Resolve a color name or numeric ID to SEI's selStaIcone value.

        Args:
            cor: Color name (e.g. 'vermelho', 'azul_ceu') or numeric string ID. None → amarelo (4).

        Returns:
            Numeric string ID for selStaIcone.
        """
        if cor is None:
            return "4"
        if cor.isdigit():
            return cor
        key = cor.lower().replace(" ", "_").replace("-", "_")
        if key in self.MARCADOR_CORES:
            return self.MARCADOR_CORES[key]
        raise ValueError(
            f"Cor desconhecida: {cor}. Válidas: {', '.join(self.MARCADOR_CORES.keys())}"
        )

    def list_marcadores(self) -> list[Marcador]:
        """List marker catalog available to current unit."""
        html = self._ensure_control()
        # Always prefer _extract_action_url — _menu_links may have truncated URL (no hash)
        marcadores_url = self._extract_action_url(html, "marcador_listar")
        if not marcadores_url:
            marcadores_url = self._menu_links.get("marcadores")
        if not marcadores_url:
            return []

        r = self._get(marcadores_url)
        # Do NOT clear _control_html here — callers may need it after listing
        return parse_marcadores_list(r.text, self._sei_url(""))
    def criar_marcador(self, nome: str, cor: str | None = "amarelo", icone: str | None = None) -> str:
        """Creates a new marcador in the current unit.

        Args:
            nome: Name of the marker.
            cor: Color name (e.g. 'vermelho', 'azul') or numeric ID string. Defaults to 'amarelo'.
            icone: Legacy numeric icon ID — takes precedence over cor if provided.

        Returns:
            The new marcador ID.
        """
        icon_id = icone if icone is not None else self._resolve_cor(cor)

        html = self._ensure_control()

        # Always prefer _extract_action_url — _menu_links may have truncated URL (no hash)
        marcadores_url = self._extract_action_url(html, "marcador_listar")
        if not marcadores_url:
            marcadores_url = self._menu_links.get("marcadores")
        if not marcadores_url:
            raise RuntimeError("Link marcador_listar não encontrado")

        r_list = self._get(marcadores_url)

        cad_match = re.search(r"location\.href='([^']+marcador_cadastrar[^']+)'", r_list.text)
        if not cad_match:
            raise RuntimeError("Link marcador_cadastrar não encontrado")

        from urllib.parse import urljoin
        cad_url = self._sei_url(cad_match.group(1).replace("&amp;", "&"))
        r_cad = self._get(cad_url)

        soup = BeautifulSoup(r_cad.text, "lxml")
        form = soup.find("form", id="frmMarcadorCadastro")
        if not form:
            raise RuntimeError("Formulário frmMarcadorCadastro não encontrado")

        action = urljoin(self._sei_url(""), form.get("action", "").replace("&amp;", "&"))

        data = {
            "hdnInfraTipoPagina": "1",
            "selStaIcone": icon_id,
            "hdnStaIcone": icon_id,
            "txtNome": nome,
            "hdnIdMarcador": "",
            "sbmCadastrarMarcador": "Salvar",
        }
        
        before_ids = {m["id"] for m in self.listar_marcadores()}
        
        r_save = self._post(action, data)
        self._control_html = None
        
        if "marcador_listar" not in str(r_save.url):
            raise RuntimeError("Falha ao criar marcador, redirecionamento inesperado.")
            
        after_list = self.listar_marcadores()
        for m in after_list:
            if m["id"] not in before_ids:
                return m["id"]
                
        for m in after_list:
            if m["nome"] == nome:
                return m["id"]
                
        raise RuntimeError("Marcador criado, mas ID não pôde ser verificado.")

    def listar_marcadores(self) -> list[dict]:
        """Lists all marcadores in current unit.
        
        Returns:
            List of {"id": str, "nome": str}.
        """
        html = self._ensure_control()
        # Always prefer _extract_action_url — _menu_links may have truncated URL (no hash)
        marcadores_url = self._extract_action_url(html, "marcador_listar")
        if not marcadores_url:
            marcadores_url = self._menu_links.get("marcadores")
        if not marcadores_url:
            return []
            
        r_list = self._get(marcadores_url)
        # Do NOT clear _control_html — callers may need it after listing
        
        # Extract from checkbox title + value attributes
        # Pattern: title="NAME" type="checkbox" value="ID"
        result = []
        for m in re.finditer(r'title="([^"]+)"\s+type="checkbox"\s+value="(\d+)"', r_list.text):
            result.append({"id": m.group(2), "nome": m.group(1)})
                    
        return result

    def editar_marcador(
        self,
        marcador_id: str,
        nome: str | None = None,
        cor: str | None = None,
    ) -> bool:
        """Edit an existing marcador (name and/or color).

        Args:
            marcador_id: The numeric ID of the marcador to edit.
            nome: New name (if None, keeps current name).
            cor: New color name or numeric ID (if None, keeps current color).

        Returns:
            True if saved successfully (redirected to marcador_listar).
        """
        from urllib.parse import urljoin

        html = self._ensure_control()

        marcadores_url = self._extract_action_url(html, "marcador_listar")
        if not marcadores_url:
            marcadores_url = self._menu_links.get("marcadores")
        if not marcadores_url:
            raise RuntimeError("Link marcador_listar não encontrado")

        r_list = self._get(marcadores_url)

        # Find the edit link for this marcador_id (href="..." with double quotes)
        alt_match = re.search(
            r'href="([^"]*marcador_alterar[^"]*id_marcador=' + re.escape(marcador_id) + r'[^"]*)"',
            r_list.text,
        )
        if not alt_match:
            raise RuntimeError(f"Link marcador_alterar não encontrado para id={marcador_id}")

        alt_url = self._sei_url(alt_match.group(1).replace("&amp;", "&"))
        r_form = self._get(alt_url)

        soup = BeautifulSoup(r_form.text, "lxml")
        form = soup.find("form", id="frmMarcadorCadastro")
        if not form:
            raise RuntimeError("Formulário frmMarcadorCadastro não encontrado")

        action = urljoin(self._sei_url(""), form.get("action", "").replace("&amp;", "&"))

        # Parse current values
        txt_nome_tag = form.find(id="txtNome") or form.find(attrs={"name": "txtNome"})
        current_nome = txt_nome_tag["value"] if txt_nome_tag and txt_nome_tag.get("value") else ""

        hdn_icone_tag = form.find(attrs={"name": "hdnStaIcone"})
        current_icone = hdn_icone_tag["value"] if hdn_icone_tag and hdn_icone_tag.get("value") else "4"

        desc_tag = form.find(attrs={"name": "txaDescricao"})
        current_desc = desc_tag.get_text() if desc_tag else ""

        resolved_nome = nome if nome is not None else current_nome
        resolved_icone = self._resolve_cor(cor) if cor is not None else current_icone

        data = {
            "hdnInfraTipoPagina": "1",
            "selStaIcone": resolved_icone,
            "hdnStaIcone": resolved_icone,
            "txtNome": resolved_nome,
            "txaDescricao": current_desc,
            "hdnIdMarcador": marcador_id,
            "sbmAlterarMarcador": "Salvar",
        }

        r_save = self._post(action, data)
        self._control_html = None

        return "marcador_listar" in str(r_save.url) or "marcador_alterar" in str(r_save.url)

    def set_marcador(self, id_procedimento: str, marcador_id: str, texto: str = "") -> bool:
        """Apply marker to a process.

        Navigates: process tree → Nos[0] toolbar → andamento_marcador_gerenciar
        → "Adicionar" button → andamento_marcador_cadastrar form → POST.

        Args:
            id_procedimento: Process ID.
            marcador_id: Marker ID from the select (e.g. '64956' for Diárias).
            texto: Description text for the marker.

        Returns:
            True if the marker was applied successfully.
        """
        # Step 1: Navigate to tree and get gerenciar URL from toolbar
        arvore_html = self._navigate_to_arvore(id_procedimento)
        if not arvore_html:
            raise RuntimeError("Could not navigate to process tree")

        start = arvore_html.find('Nos[0]')
        end = arvore_html.find('Nos[1]', start)
        nos0 = arvore_html[start:end].replace('\\"', '"')

        ger_m = re.search(
            r'href="(controlador\.php\?acao=andamento_marcador_gerenciar[^"]*)"',
            nos0,
        )
        if not ger_m:
            raise RuntimeError("Gerenciar Marcador action not found in toolbar")

        # Step 2: Go to gerenciar page (lists existing marcadores)
        r_ger = self._get(self._sei_url(ger_m.group(1).replace('&amp;', '&')))

        # Step 3: Find the "Adicionar" button → andamento_marcador_cadastrar
        add_m = re.search(
            r'(controlador\.php\?acao=andamento_marcador_cadastrar[^"\']+)',
            r_ger.text,
        )
        if not add_m:
            raise RuntimeError("Adicionar marcador link not found")

        # Step 4: Load the cadastrar form
        r_cad = self._get(self._sei_url(add_m.group(1).replace('&amp;', '&')))

        soup = BeautifulSoup(r_cad.text, 'lxml')
        form = soup.find('form', id='frmAndamentoMarcadorCadastro')
        if not form:
            raise RuntimeError("Marcador cadastro form not found")

        action_url = urljoin(self._sei_url(""), form['action'])

        # Step 5: POST with marcador data
        data = {
            'hdnInfraTipoPagina': '2',
            'selMarcador': marcador_id,
            'hdnIdMarcador': marcador_id,
            'txaTexto': texto,
            'hdnIdProtocolo': id_procedimento,
            'sbmSalvar': 'Salvar',
        }

        r_save = self._post(action_url, data)
        self._control_html = None

        # Success: redirects to gerenciar (200 with table showing the marker)
        # or shows the form again (error)
        return marcador_id in r_save.text or 'andamento_marcador_gerenciar' in str(r_save.url)

    def remove_marcador(self, id_procedimento: str, marcador_id: str | None = None) -> bool:
        """Remove marker from a process.

        SEI's removal flow uses the ``frmGerenciarMarcador`` form on the
        ``andamento_marcador_gerenciar`` page.  The JS ``acaoRemover()`` sets
        ``hdnInfraItemId`` to the marker ID, changes the form action to
        ``andamento_marcador_remover``, and submits.  We replicate that here.
        """
        self._navigate_process_page(id_procedimento)
        control_html = self._ensure_control()

        # Find the gerenciar URL specific to this process.
        ger_url = None
        for m in re.finditer(
            r'controlador\.php\?acao=andamento_marcador_gerenciar[^"]*'
            r'id_procedimento=' + re.escape(id_procedimento) + r'[^"]*',
            control_html,
        ):
            ger_url = urljoin(self._sei_url(""), m.group().replace("&amp;", "&"))
            break
        if not ger_url:
            raise RuntimeError(
                "Processo não possui marcador para remover "
                f"(andamento_marcador_gerenciar para {id_procedimento} não encontrado)"
            )

        rr = self._get(ger_url)
        soup = BeautifulSoup(rr.text, "lxml")
        form = soup.find("form", id="frmGerenciarMarcador")
        if not form:
            raise RuntimeError("Formulário frmGerenciarMarcador não encontrado")

        # Extract the remove action URL from JS: acaoRemover sets form.action
        rm_action_match = re.search(
            r"acaoRemover.*?action='([^']+)'", rr.text, re.DOTALL
        )
        if not rm_action_match:
            raise RuntimeError("URL de remoção não encontrada no JS acaoRemover")
        rm_action = urljoin(self._sei_url(""), rm_action_match.group(1).replace("&amp;", "&"))

        # Find the marker checkbox to remove. If marcador_id is not provided,
        # preserve current behavior and remove the first available marker.
        checkbox = None
        if marcador_id:
            checkbox = form.find("input", {"type": "checkbox", "value": marcador_id})
            if checkbox is None:
                raise RuntimeError(
                    f"Marcador {marcador_id} não encontrado no formulário de gerenciamento"
                )
        if checkbox is None:
            checkbox = form.find("input", {"type": "checkbox"})
        if not checkbox:
            raise RuntimeError("Nenhum marcador encontrado no formulário de gerenciamento")
        marker_id = checkbox.get("value", "")

        # Build form data
        data: dict[str, str] = {}
        for inp in form.find_all("input"):
            n = inp.get("name", "")
            if n:
                data[n] = inp.get("value", "")
        data["hdnInfraItemId"] = marker_id

        rr_post = self._post(rm_action, data)
        self._control_html = None

        # Verify: the response should be the gerenciar page with 0 markers,
        # or the URL should contain resultado=1.
        if "resultado=1" in str(rr_post.url):
            return True
        # Fallback: check if marker still in gerenciar page
        soup_resp = BeautifulSoup(rr_post.text, "lxml")
        form_resp = soup_resp.find("form", id="frmGerenciarMarcador")
        if form_resp and not form_resp.find("input", {"type": "checkbox"}):
            return True
        return False

    def _get_process_marker_manage_page(
        self,
        id_procedimento: str,
    ) -> tuple[str, BeautifulSoup, Any]:
        self._navigate_process_page(id_procedimento)
        control_html = self._ensure_control()

        ger_url = None
        for m in re.finditer(
            r'controlador\.php\?acao=andamento_marcador_gerenciar[^"]*'
            r'id_procedimento=' + re.escape(id_procedimento) + r'[^"]*',
            control_html,
        ):
            ger_url = urljoin(self._sei_url(""), m.group().replace("&amp;", "&"))
            break
        if not ger_url:
            raise RuntimeError(
                "Processo não expôs ação de gerenciamento de marcador "
                f"para {id_procedimento}"
            )

        rr = self._get(ger_url)
        soup = BeautifulSoup(rr.text, "lxml")
        form = soup.find("form", id="frmGerenciarMarcador")
        if not form:
            raise RuntimeError("Formulário frmGerenciarMarcador não encontrado")
        return rr.text, soup, form

    @staticmethod
    def _extract_marker_manage_action(content: str, action_name: str) -> str | None:
        base = "https://sei.rn.gov.br/sei/"
        patterns = [
            rf"{re.escape(action_name)}.*?action='([^']+)'",
            rf"{re.escape(action_name)}.*?action=\"([^\"]+)\"",
            rf"acao=.*?andamento_marcador_{re.escape(action_name.lower().replace('acao', ''))}[^\"']+",
        ]
        for pattern in patterns:
            match = re.search(pattern, content, re.DOTALL)
            if not match:
                continue
            value = match.group(1) if match.groups() else match.group(0)
            return urljoin(base, value.replace("&amp;", "&"))
        return None

    @staticmethod
    def _collect_marker_row_texts(row: Any) -> list[str]:
        texts: list[str] = []
        for chunk in row.stripped_strings:
            value = " ".join(str(chunk).split()).strip()
            if not value:
                continue
            if value not in texts:
                texts.append(value)
        return texts

    def list_process_markers(self, id_procedimento: str) -> list[dict[str, Any]]:
        try:
            content, _soup, form = self._get_process_marker_manage_page(id_procedimento)
        except RuntimeError:
            # No marker management action available → process has no markers.
            return []
        history_action = self._extract_marker_manage_action(content, "acaoHistorico")
        alter_action = self._extract_marker_manage_action(content, "acaoAlterar")
        items: list[dict[str, Any]] = []

        for checkbox in form.find_all("input", {"type": "checkbox"}):
            marker_id = (checkbox.get("value") or "").strip()
            if not marker_id:
                continue
            row = checkbox.find_parent("tr")
            texts = self._collect_marker_row_texts(row or form)
            texts = [item for item in texts if item != marker_id]
            nome = texts[0] if texts else None
            texto = texts[1] if len(texts) > 1 else ""
            items.append(
                {
                    "marcador_id": marker_id,
                    "id": marker_id,
                    "nome": nome,
                    "texto": texto,
                    "history_url": history_action,
                    "edit_url": alter_action,
                }
            )
        return items

    def update_marcador(
        self,
        id_procedimento: str,
        marcador_id: str,
        texto: str,
    ) -> bool:
        content, soup, form = self._get_process_marker_manage_page(id_procedimento)
        checkbox = form.find("input", {"type": "checkbox", "value": marcador_id})
        if checkbox is None:
            raise RuntimeError(
                f"Marcador {marcador_id} não encontrado no formulário de gerenciamento"
            )

        # The alterar URL is an <a href> on the gerenciar page, NOT a JS form action.
        alter_url = None
        pattern = (
            r'controlador\.php\?acao=andamento_marcador_alterar[^"]*'
            r'id_marcador=' + re.escape(marcador_id) + r'[^"]*'
        )
        match = re.search(pattern, content)
        if match:
            alter_url = urljoin(self._sei_url(""), match.group().replace("&amp;", "&"))
        if not alter_url:
            raise RuntimeError("URL de alteração não encontrada para marcador " + marcador_id)

        rr_edit = self._get(alter_url)
        soup_edit = BeautifulSoup(rr_edit.text, "lxml")
        edit_form = soup_edit.find("form", id="frmAndamentoMarcadorCadastro")
        if not edit_form:
            raise RuntimeError("Formulário de alteração do marcador não encontrado")

        action_attr = edit_form.get("action")
        if not action_attr:
            raise RuntimeError("Formulário de alteração sem action")
        save_url = urljoin(self._sei_url(""), action_attr)

        data: dict[str, str] = {}
        for inp in edit_form.find_all("input"):
            name = inp.get("name", "")
            if name:
                data[name] = inp.get("value", "")
        for area in edit_form.find_all("textarea"):
            name = area.get("name", "")
            if name:
                data[name] = area.text or ""
        select = edit_form.find("select", {"name": "selMarcador"})
        if select is not None:
            selected = select.find("option", selected=True) or select.find("option", {"value": marcador_id})
            if selected is not None:
                data["selMarcador"] = selected.get("value", marcador_id)
        data["hdnIdMarcador"] = marcador_id
        data["txaTexto"] = texto
        data["sbmSalvar"] = data.get("sbmSalvar") or "Salvar"

        rr_save = self._post(save_url, data)
        self._control_html = None
        if "andamento_marcador_gerenciar" in str(rr_save.url):
            return True
        return texto.strip() in rr_save.text

    def marker_history(
        self,
        id_procedimento: str,
        marcador_id: str | None = None,
    ) -> list[dict[str, str]]:
        content, soup, form = self._get_process_marker_manage_page(id_procedimento)

        # The history page is accessed via the "Listar" button which navigates to
        # andamento_marcador_listar (a location.href redirect, not a JS function).
        listar_url = None
        match = re.search(
            r"controlador\.php\?acao=andamento_marcador_listar[^'\"]+",
            content,
        )
        if match:
            listar_url = urljoin(self._sei_url(""), match.group().replace("&amp;", "&"))
        if not listar_url:
            raise RuntimeError(
                "URL de histórico (andamento_marcador_listar) não encontrada "
                f"para processo {id_procedimento}"
            )

        rr = self._get(listar_url)
        soup_history = BeautifulSoup(rr.text, "lxml")
        entries: list[dict[str, str]] = []

        # Columns: Data/Hora | Usuário | Operação | Marcador | Texto
        for row in soup_history.find_all("tr"):
            tds = row.find_all("td")
            if len(tds) < 4:
                continue
            texts = [td.get_text(strip=True) for td in tds]
            # Skip header row
            lower = " ".join(texts).casefold()
            if "data" in lower and ("usuario" in lower or "usuário" in lower):
                continue

            timestamp = texts[0]
            usuario = texts[1] if len(texts) > 1 else ""
            acao_raw = texts[2] if len(texts) > 2 else ""
            marcador_nome = texts[3] if len(texts) > 3 else ""
            marker_text = texts[4] if len(texts) > 4 else ""

            # Filter by marcador_id: resolve id → name from current markers
            if marcador_id:
                checkbox = form.find("input", {"type": "checkbox", "value": marcador_id})
                if checkbox:
                    row_el = checkbox.find_parent("tr")
                    if row_el:
                        row_texts = self._collect_marker_row_texts(row_el)
                        target_name = next(
                            (t for t in row_texts if t and not t.isdigit()),
                            "",
                        )
                        if target_name and marcador_nome != target_name:
                            continue

            # Parse operation code
            acao_map = {"I": "Inclusão", "A": "Alteração", "R": "Remoção"}
            acao = acao_map.get(acao_raw.strip(), acao_raw)

            date_only = ""
            time_only = ""
            ts_match = re.match(
                r"(\d{2}/\d{2}/\d{4})(?:\s+(\d{2}:\d{2}(?::\d{2})?))?", timestamp
            )
            if ts_match:
                date_only = ts_match.group(1) or ""
                time_only = ts_match.group(2) or ""

            entry = {
                "data": timestamp,
                "data_only": date_only,
                "hora": time_only,
                "usuario": usuario,
                "acao": acao,
                "acao_raw": acao_raw.strip(),
                "marcador": marcador_nome,
                "marker_text": marker_text,
                "raw_columns": texts,
            }
            entries.append(entry)
        return entries

    # --- Document creation & editing ---

    # Maps friendly name → SEI id_serie (from frmDocumentoEscolherTipo)
    DOC_TYPES: dict[str, str] = {
        "externo": "-1",
        "anexo": "263",
        "boletim": "34",
        "laudo": "54",
        "laudo_medico": "435",
        "analise_riscos": "220",
        "autorizacao": "305",
        "declaracao": "83",
        "despacho_diligencial": "377",
        "despacho": "5",
        "dfd": "970",
        "encaminhamento": "327",
        "etp": "1170",
        "informacao": "92",
        "justificativa": "307",
        "memorando": "12",
        "minuta_portaria": "235",
        "oficio": "11",
        "parecer": "191",
        "parte_generica": "292",
        "plano_trabalho": "669",
        "relatorio_viagem": "326",
        "solicitacao_providencias": "347",
        "solicitacao": "178",
        "termo_referencia": "214",
    }

    def list_document_types(self, id_procedimento: str) -> list[DocumentType]:
        """List available document types for a process.

        Navigates to the process → extracts 'Incluir Documento' URL from
        the arvore toolbar → GETs the type selection page → parses the
        onclick escolher(id) calls.
        """
        arvore_html = self._navigate_to_arvore(id_procedimento)
        if not arvore_html:
            return []

        # Find 'Incluir Documento' href (next to documento_incluir.svg icon)
        href_match = re.search(
            r'href="([^"]+)"[^>]*>\s*<img\s+[^>]*documento_incluir',
            arvore_html,
        )
        if not href_match:
            return []

        incl_url = urljoin(self._sei_url(""), href_match.group(1))
        rtype = self._get(incl_url)
        expanded_html, _expanded_soup, _expanded_form = self._expand_chooser_form(
            rtype.text,
            form_id="frmDocumentoEscolherTipo",
            filter_field="hdnFiltroSerie",
        )
        return self._parse_chooser_types(expanded_html)

    def create_document(
        self,
        id_procedimento: str,
        tipo: str,
        *,
        nivel_acesso: str | None = None,  # None = keep form default / process pattern
        texto_inicial: str = "N",  # N=Nenhum, T=Texto Padrão, D=Documento Modelo
        documento_modelo: str = "",
        descricao: str = "",
        interessados: str = "",
        extra_fields: dict[str, str] | None = None,
    ) -> DocumentCreated:
        """Create a new document inside a process.

        Args:
            id_procedimento: Process ID.
            tipo: Document type key (e.g. 'despacho', 'oficio') or numeric id_serie.
            nivel_acesso: '0' (Público), '1' (Restrito), '2' (Sigiloso).
            texto_inicial: 'N' (Nenhum), 'T' (Texto Padrão), 'D' (Documento Modelo).
            documento_modelo: Optional SEI protocol number to use as Documento Modelo.
            descricao: Optional description field.
            interessados: Optional interested party.

        Returns:
            DocumentCreated with id_documento and editor URL.
        """
        csoup, cdata, cform_action, id_serie = self._load_document_creation_form(id_procedimento, tipo)

        # Set our values
        # CRITICAL: submeter() JS sets hdnFlagDocumentoCadastro='2' before submit.
        # Without this, SEI just re-renders the form instead of creating the doc.
        cdata["hdnFlagDocumentoCadastro"] = "2"
        cdata["rdoTextoInicial"] = texto_inicial
        if documento_modelo:
            cdata["rdoTextoInicial"] = "D"
            cdata["txtProtocoloDocumentoTextoBase"] = documento_modelo
            cdata.setdefault("hdnIdDocumentoTextoBase", "")
        if nivel_acesso is not None:
            cdata["rdoNivelAcesso"] = nivel_acesso
        cdata["rdoFormato"] = "N"  # Nato-digital
        if descricao:
            cdata["txtDescricao"] = descricao
        if interessados:
            cdata["txtInteressado"] = interessados
        if extra_fields:
            cdata.update(extra_fields)

        # Submit creation
        rcreated = self._post(cform_action, cdata)

        # After creation, SEI redirects to the editor page or back to arvore.
        # The response should contain the new document ID.
        self._control_html = None

        id_doc, editor_url = self._extract_created_document_result(
            rcreated.text,
            response_url=str(rcreated.url),
            id_procedimento=id_procedimento,
        )

        if not id_doc:
            created_soup = BeautifulSoup(rcreated.text, "lxml")
            validation = created_soup.find("textarea", {"id": "txaInfraValidacao"})
            if validation and validation.get_text(strip=True):
                raise RuntimeError(validation.get_text(strip=True))

            form_still_present = created_soup.find("form", id="frmDocumentoCadastro")
            if form_still_present:
                raise RuntimeError(
                    "SEI reexibiu o formulário de criação do documento sem informar o id_documento."
                )

            raise RuntimeError(
                "SEI não retornou id_documento após a criação do documento."
            )

        tipo_nome = next(
            (dt.nome for dt in [DocumentType(id_serie, tipo)]
             if dt.id_serie == id_serie),
            tipo,
        )

        return DocumentCreated(
            id_documento=id_doc,
            id_procedimento=id_procedimento,
            tipo=tipo_nome,
            editor_url=editor_url,
        )

    def _extract_created_document_result(
        self,
        response_html: str,
        *,
        response_url: str,
        id_procedimento: str,
    ) -> tuple[str, str | None]:
        """Extract created document id/editor URL from a document creation response."""
        id_doc = ""
        editor_url = None

        id_doc_match = re.search(r"id_documento=(\d+)", response_url)
        if id_doc_match:
            id_doc = id_doc_match.group(1)
            if "acao=editor_montar" in response_url:
                editor_url = response_url

        patterns = [
            r"var\s+linkEditarConteudo\s*=\s*'([^']+)'",
            r'(controlador\.php\?acao=editor_montar[^"\']+)',
            r'(controlador\.php\?acao=arvore_visualizar[^"\']*id_documento=\d+[^"\']*)',
        ]
        for pattern in patterns:
            match = re.search(pattern, response_html)
            if not match:
                continue
            candidate = match.group(1).replace("&amp;", "&")
            candidate_url = urljoin(self._sei_url(""), candidate)
            if not editor_url and "acao=editor_montar" in candidate_url:
                editor_url = candidate_url
            if not id_doc:
                id_doc_match = re.search(r"id_documento=(\d+)", candidate_url)
                if id_doc_match:
                    id_doc = id_doc_match.group(1)

        if not editor_url and id_doc:
            fallback_editor = self._get_editor_url(id_doc, id_procedimento)
            if fallback_editor:
                editor_url = fallback_editor

        return id_doc, editor_url

    def get_editor_sections(
        self, id_documento: str, id_procedimento: str
    ) -> tuple[str, list[EditorSection]]:
        """Load the editor page for a document and return its sections.

        Returns:
            Tuple of (editor_save_url, list of EditorSection).
            Each section has name, content (HTML), and section_id.
        """
        editor_url = self._get_editor_url(id_documento, id_procedimento)
        if not editor_url:
            raise RuntimeError(
                f"Editor URL não encontrada para documento {id_documento}"
            )

        re_edit = self._get(editor_url)

        # Check if document is signed (not editable)
        if "assinado" in re_edit.text and "não pode" in re_edit.text:
            raise RuntimeError("Documento já assinado — não pode ser editado")

        # Extract form action (editor_salvar URL with infra_hash)
        form_match = re.search(
            r'action="(editor/editor_processar\.php\?acao=editor_salvar[^"]+)"',
            re_edit.text,
        )
        if not form_match:
            raise RuntimeError("URL de salvamento do editor não encontrada")
        save_url = urljoin(self._sei_url(""), form_match.group(1))

        # Extract textarea sections
        sections: list[EditorSection] = []
        for m in re.finditer(
            r'<textarea[^>]*name="(txaEditor_(\d+))"[^>]*>(.*?)</textarea>',
            re_edit.text,
            re.DOTALL,
        ):
            sections.append(EditorSection(
                name=m.group(1),
                content=m.group(3),
                section_id=m.group(2),
            ))

        # Extract hidden fields too (needed for save)
        self._editor_hiddens: dict[str, str] = {}
        esoup = BeautifulSoup(re_edit.text, "lxml")
        form = esoup.find("form", id="frmEditor")
        if form:
            for inp in form.find_all("input", {"type": "hidden"}):
                n = inp.get("name", "")
                if n:
                    self._editor_hiddens[n] = inp.get("value", "")

        return save_url, sections

    def save_document(
        self,
        save_url: str,
        sections: list[EditorSection],
    ) -> bool:
        """Save document content by POSTing the editor form.

        Args:
            save_url: The editor_salvar URL (from get_editor_sections).
            sections: List of EditorSection with modified content.

        **ESCAPE RULES (critical):**

        Section content must be raw SEI HTML when it is posted. The HTTP form
        layer URL-encodes transport characters; callers must not HTML-escape
        tags before saving. Escaped structural tags such as ``&lt;p&gt;`` are
        normalized back to ``<p>`` here to avoid deterministic double-escape
        failures in the CKEditor.

        **Common mistakes:**

        - Passing ``html.escape(raw_html)`` makes tags visible as text.
        - Reposting untouched escaped textarea content can preserve
          ``&lt;p&gt;`` literally.
        - Raw HTML tags are correct; non-Latin-1 characters are converted to
          numeric HTML entities before the ISO-8859-1 POST.

        Returns:
            True if save succeeded.
        """
        data: dict[str, str] = {}

        # Include hidden fields from editor form
        data.update(getattr(self, "_editor_hiddens", {}))

        # Add all sections as textarea values. Preserve the full document
        # structure, but normalize every section to raw HTML before transport.
        for sec in sections:
            data[sec.name] = self._prepare_editor_content_for_save(sec.content)

        r = self._post(save_url, data)

        # editor_salvar typically returns a small page loaded into ifrEditorSalvar
        # with a success indicator or error message
        self._control_html = None

        # Check for errors
        if "erro" in r.text.lower() or "falha" in r.text.lower():
            return False

        return True

    @staticmethod
    def escape_for_sei(raw_html: str) -> str:
        """Legacy helper that produces 1-level escaped SEI editor HTML.

        Do not use this for normal ``document-edit-*`` body saves. The
        canonical editor path now posts raw HTML and ``save_document``
        normalizes escaped structural tags back to raw HTML defensively.

        SEI uses ISO-8859-1 encoding for form submissions. Characters
        outside Latin-1 (e.g. ``—``, ``'``, ``"``) must be preserved
        as HTML entities (``&mdash;``, ``&rsquo;``), not as literal
        Unicode characters, or the POST will fail with encoding errors.

        This method:
        1. Converts non-Latin-1 characters to HTML numeric entities
        2. Escapes HTML structural characters (``<``, ``>``, ``&``, ``"``)
           to 1-level escape (``&lt;``, ``&gt;``, ``&amp;``, ``&quot;``)

        If this legacy escaped output is accidentally passed to
        ``save_document()``, ``save_document`` will unescape structural tags
        before posting to prevent visible ``&lt;p&gt;`` in the document.

        Args:
            raw_html: HTML with literal tags (``<p>text</p>``).
                May contain HTML entities like ``&mdash;`` — these are
                preserved correctly.

        Returns:
            1-level escaped string ready for save_document.
        """
        from html import escape as _escape

        # Step 1: Escape HTML structure (< > & ") — standard 1-level
        escaped = _escape(raw_html, quote=True)

        # Step 2: Replace non-Latin-1 characters with numeric entities.
        # After step 1, all & are now &amp;, so new &#nnn; entities
        # won't be double-escaped. We need to produce &amp;#nnn; so that
        # when SEI unescapes once, it gets &#nnn; which renders correctly.
        #
        # Actually: the textarea content IS the 1-level escaped form.
        # SEI stores "&lt;p&gt;" and renders it as "<p>" in the editor.
        # For entities like &#8212; (em-dash), the stored form must be
        # "&amp;#8212;" so that after one unescape it becomes "&#8212;"
        # and the browser renders "—".
        result = []
        for ch in escaped:
            try:
                ch.encode("iso-8859-1")
                result.append(ch)
            except UnicodeEncodeError:
                # Character not in Latin-1 — use numeric entity
                # Must be &amp;#nnn; because we're at 1-level escape
                result.append(f"&amp;#{ord(ch)};")
        return "".join(result)

    @staticmethod
    def _has_escaped_structural_html(value: str) -> bool:
        candidate = value or ""
        for _ in range(6):
            if re.search(r"&lt;\s*/?\s*(?:p|div|span|table|tbody|tr|td|th|strong|em|br|ol|ul|li|a)\b", candidate, re.IGNORECASE):
                return True
            updated = html.unescape(candidate)
            if updated == candidate:
                break
            candidate = updated
        return False

    @staticmethod
    def _prepare_editor_content_for_save(raw_html: str) -> str:
        """Prepare editor HTML for posting to SEI.

        SEI stores the textarea value literally on the server — what you POST
        is what gets saved and rendered. Therefore:
        - Raw HTML tags (<p>, <strong>, etc.) must be posted as-is.
        - urlencode (done by _post) handles transport encoding (%3C, etc.).
        - The server receives <p> and stores/renders it as HTML.

        The ONLY transformation needed: non-Latin-1 characters must be
        converted to numeric HTML entities (&#nnn;) because _post encodes
        the body as ISO-8859-1 and would fail on codepoints > 255.

        NOTE: Do NOT use html.escape() here — that would post &lt;p&gt;
        which the server stores literally and renders as text, not HTML.

        If content came back from the editor with escaped structural tags
        (``&lt;p&gt;``), normalize it back to raw HTML before saving. This also
        protects preserved non-target sections from being reposted escaped.
        """
        if raw_html is None:
            return ""
        if SEIClient._has_escaped_structural_html(raw_html):
            for _ in range(8):
                unescaped = html.unescape(raw_html)
                if unescaped == raw_html:
                    break
                raw_html = unescaped
                if "<" in raw_html and "&lt;" not in raw_html and "&amp;lt;" not in raw_html:
                    break

        result = []
        for ch in raw_html:
            if ch == "\xa0":
                result.append("&nbsp;")
                continue
            try:
                ch.encode("iso-8859-1")
                result.append(ch)
            except UnicodeEncodeError:
                result.append(f"&#{ord(ch)};")
        return "".join(result)

    @staticmethod
    def _prepare_free_section(raw_html: str) -> str:
        """Backward-compatible wrapper for raw editable section content."""
        return SEIClient._prepare_editor_content_for_save(raw_html)

    def edit_document_section(
        self,
        id_documento: str,
        id_procedimento: str,
        section_id: str,
        new_raw_html: str,
    ) -> bool:
        """High-level helper: replace one editor section with new content.

        Handles the full workflow:
        1. Opens editor (get_editor_sections)
        2. Finds the target section by section_id
        3. Normalizes new_raw_html for SEI's ISO-8859-1 form POST
        4. Replaces only the target section, preserving all others
        5. Saves via save_document

        Args:
            id_documento: SEI document ID.
            id_procedimento: SEI process ID.
            section_id: Numeric section ID (e.g. '422' for body).
            new_raw_html: Raw HTML content (``<p>text</p>``). It must contain
                real tags, not ``&lt;p&gt;`` escaped tags.

        Returns:
            True if save succeeded.

        Raises:
            RuntimeError: If section not found or editor unavailable.
        """
        save_url, sections = self.get_editor_sections(id_documento, id_procedimento)

        target = None
        for sec in sections:
            if sec.section_id == section_id:
                target = sec
                break

        if target is None:
            available = [s.section_id for s in sections]
            raise RuntimeError(
                f"Section '{section_id}' not found. Available: {available}"
            )

        target.content = self._prepare_free_section(new_raw_html)

        return self.save_document(save_url, sections)

    def _view_document_html_core(
        self, id_documento: str, arvore_html: str,
    ) -> str:
        """Core: extract and fetch document HTML from pre-fetched arvore."""
        # Find the documento_imprimir_web URL with infra_hash from the tree
        print_match = re.search(
            rf'(controlador\.php\?acao=documento_imprimir_web[^"]*'
            rf'id_documento={id_documento}[^"]*)',
            arvore_html,
        )

        if not print_match:
            # Fallback: try documento_visualizar
            vis_match = re.search(
                rf'(controlador\.php\?acao=documento_visualizar[^"]*'
                rf'id_documento={id_documento}[^"]*)',
                arvore_html,
            )
            if vis_match:
                r = self._get(self._sei_url(vis_match.group(1)))
                if "login.php" not in str(r.url):
                    return r.text
            raise RuntimeError(
                f"Link de visualização não encontrado para documento {id_documento}"
            )

        r = self._get(self._sei_url(print_match.group(1)))

        # Check for login redirect
        if "login.php" in str(r.url) or "pwdSenha" in r.text:
            raise RuntimeError(
                "Session expired viewing document. Re-login needed."
            )

        return r.text

    def view_document_html(
        self, id_documento: str, id_procedimento: str
    ) -> str:
        """View a document's rendered HTML (works for signed documents too).

        Uses documento_imprimir_web which renders the full document
        including signed ones that can't be opened in the editor.
        Automatically switches to the correct unit if needed.

        Args:
            id_documento: Internal document ID.
            id_procedimento: Process ID containing the document.

        Returns:
            Raw HTML content of the document body.
        """
        arvore_html = self._navigate_to_arvore(id_procedimento)
        if not arvore_html:
            raise RuntimeError(
                f"Processo {id_procedimento} não encontrado"
            )

        with self._auto_unit_switch(arvore_html) as switched_to:
            if switched_to:
                arvore_html = self._navigate_to_arvore(id_procedimento)
                if not arvore_html:
                    raise RuntimeError(
                        f"Processo {id_procedimento} não acessível na unidade {switched_to}"
                    )
            try:
                return self._view_document_html_core(id_documento, arvore_html)
            except RuntimeError as exc:
                if "Link de visualização não encontrado" not in str(exc):
                    raise

                docs = self.get_full_document_tree(id_procedimento, expand_all=True)
                target = next(
                    (doc for doc in docs if doc.id_documento == id_documento and doc.src_url),
                    None,
                )
                if target is None:
                    raise
                if target.tipo.lower() == "pdf":
                    raise RuntimeError("PDF documents have no HTML representation")

                r = self._get(target.src_url)
                if "login.php" in str(r.url) or "pwdSenha" in r.text:
                    raise RuntimeError(
                        "Session expired viewing document. Re-login needed."
                    )
                return r.text

    def view_document(
        self, id_documento: str, id_procedimento: str
    ) -> str:
        """View a document as plain text (works for signed documents too).

        Args:
            id_documento: Internal document ID.
            id_procedimento: Process ID containing the document.

        Returns:
            Plain text content of the document.
        """
        html = self.view_document_html(id_documento, id_procedimento)
        soup = BeautifulSoup(html, "lxml")

        # Remove timbre/header images
        for img in soup.find_all("img"):
            img.decompose()

        # Remove navigation/chrome elements
        for tag_id in ["divInfraAreaGlobal", "navInfraBarraNavegacao"]:
            tag = soup.find(id=tag_id)
            if tag:
                tag.decompose()

        return soup.get_text("\n", strip=True)

    def view_document_sections(
        self, id_documento: str, id_procedimento: str
    ) -> list[EditorSection]:
        """Extract document sections from the print view (works for signed docs).

        Returns sections similar to get_editor_sections() but read-only,
        extracted from the rendered HTML rather than the editor form.

        Args:
            id_documento: Internal document ID.
            id_procedimento: Process ID.

        Returns:
            List of EditorSection with content (HTML of each section).
        """
        html = self.view_document_html(id_documento, id_procedimento)
        soup = BeautifulSoup(html, "lxml")

        sections: list[EditorSection] = []

        # documento_imprimir_web renders sections as divs with id="txaEditor_NNN"
        # or directly as content divs
        for div in soup.find_all(
            lambda tag: tag.get("id", "").startswith("txaEditor_")
                        or tag.get("name", "").startswith("txaEditor_")
        ):
            sec_id = re.search(r'(\d+)', div.get("id", div.get("name", "")))
            if sec_id:
                inner = "".join(str(child) for child in div.children)
                sections.append(EditorSection(
                    name=f"txaEditor_{sec_id.group(1)}",
                    content=inner,
                    section_id=sec_id.group(1),
                ))

        # Fallback: if no txaEditor divs found, extract from the print layout
        if not sections:
            # The print view has the document body in a specific structure
            # Look for the main content area
            body = soup.find("body")
            if body:
                inner = "".join(str(child) for child in body.children)
                sections.append(EditorSection(
                    name="body",
                    content=inner,
                    section_id="0",
                ))

        return sections

    def read_document(
        self, id_documento: str, id_procedimento: str
    ) -> str:
        """Read a document's body content as plain text.

        Tries the editor first (for unsigned docs), falls back to
        the print view (for signed docs).
        """
        from html import unescape as _unescape
        try:
            save_url, sections = self.get_editor_sections(
                id_documento, id_procedimento
            )
            if not sections:
                raise RuntimeError("empty")
            body = max(sections, key=lambda s: len(s.content))
            content = _unescape(body.content)
            soup = BeautifulSoup(content, "lxml")
            for img in soup.find_all("img"):
                img.decompose()
            return soup.get_text("\n", strip=True)
        except RuntimeError:
            # Fallback: signed document — use print view
            return self.view_document(id_documento, id_procedimento)

    def read_relatorio(
        self, id_documento: str, id_procedimento: str
    ) -> RelatorioServico:
        """Read and parse a Relatório de Serviço Operacional.

        Returns a structured RelatorioServico with personnel, vehicles,
        occurrences, etc.  Works for both unsigned (editor) and signed
        (print view) documents.
        """
        body_html: str | None = None

        # Try editor first (unsigned docs)
        try:
            save_url, sections = self.get_editor_sections(id_documento, id_procedimento)
            if sections:
                body_candidates = [s for s in sections if len(s.content) > 1000]
                if not body_candidates:
                    body_candidates = sections
                body_sec = None
                for s in body_candidates:
                    if "Fiscal" in s.content or "RELAT" in s.content:
                        body_sec = s
                        break
                if not body_sec:
                    body_sec = max(body_candidates, key=lambda s: len(s.content))
                body_html = body_sec.content
        except RuntimeError:
            pass  # signed doc — fall through to print view

        # Fallback: use print view (works for signed docs)
        if body_html is None:
            body_html = self.view_document_html(id_documento, id_procedimento)

        relatorio = parse_relatorio(body_html)

        # Check signature status from the print/view HTML
        try:
            view_html = self.view_document_html(id_documento, id_procedimento)
            import re as _re
            # Strip HTML tags for cleaner regex matching
            _clean = _re.sub(r'<[^>]+>', '', view_html)
            _clean = _clean.replace('&ordm;', 'º').replace('&agrave;', 'à')
            _clean = _clean.replace('&aacute;', 'á').replace('&iacute;', 'í')
            sig_match = _re.search(
                r'assinado\s+eletronicamente\s+por\s+'
                r'(.+?)\s*,\s*[^,]+?,\s*em\s+'
                r'(\d{2}/\d{2}/\d{4})\s*,\s*[àa]s?\s*(\d{2}:\d{2})',
                _clean,
                _re.IGNORECASE,
            )
            if sig_match:
                relatorio.assinado = True
                relatorio.assinado_por = sig_match.group(1).strip()
                relatorio.assinado_em = f"{sig_match.group(2)} {sig_match.group(3)}"
        except Exception:
            pass  # signature check is best-effort

        return relatorio

    def batch_read_relatorios(
        self,
        id_procedimento: str,
        unit: str = "OP 3",
    ) -> dict[str, Any]:
        """Read and summarize all relatório-like documents from a process.

        Uses batch_mode() to avoid repeated full re-logins between documents.
        The session is established once at the start and refreshed via fast
        GET requests (not POST logins) between each document read.
        """
        if unit:
            self.switch_unit(unit)

        docs = self.get_process_documents(id_procedimento)
        rel_docs = [
            d for d in docs
            if any(
                k in (d.nome or "").upper()
                for k in ("RELAT", "LIVRO", "FISCAL")
            )
        ]

        parsed: list[RelatorioServico] = []
        failures: list[dict[str, str]] = []

        with self.batch_mode():
            for doc in rel_docs:
                if not doc.id_documento:
                    continue
                try:
                    def _read(did=doc.id_documento):
                        return self.read_relatorio(did, id_procedimento)
                    parsed.append(self._navigate_with_retry(_read))
                except Exception as exc:
                    failures.append(
                        {
                            "id_documento": doc.id_documento,
                            "nome": doc.nome,
                            "erro": str(exc),
                        }
                    )

        md = summarize_batch(parsed)
        return {
            "id_procedimento": id_procedimento,
            "unit": unit,
            "total_documentos": len(docs),
            "relatorios_encontrados": len(rel_docs),
            "relatorios_lidos": len(parsed),
            "falhas": failures,
            "resumo_markdown": md,
            "relatorios": [r.__dict__ for r in parsed],
        }

    def _gerar_pdf_flow(
        self,
        arvore_html: str,
        id_procedimento: str,
        output_path: str,
        *,
        id_documento: str | None = None,
    ) -> str:
        """Core 7-step PDF generation flow (no unit switching).

        Steps:
        1. (arvore already provided)
        2. Find procedimento_gerar_pdf URL in tree HTML
        3. GET the gerar confirmation page
        4. Extract form action URL
        5. POST with hdnFlagGerar=1, rdoTipo=T
        6. Extract exibir_arquivo URL from iframe .src in JS response
        7. GET the actual PDF binary

        Args:
            arvore_html: Pre-fetched arvore HTML.
            id_procedimento: Process internal ID.
            output_path: Where to save the PDF.
            id_documento: If set, download only this document's PDF.

        Returns:
            Path to the downloaded PDF file.
        """
        from html import unescape

        # Step 2: Find the right procedimento_gerar_pdf URL
        candidates = re.findall(
            r'(controlador\.php\?acao=procedimento_gerar_pdf[^"\'<>\s]+)',
            arvore_html,
        )
        page_url_raw: str | None = None

        use_apenas_mode = False  # Track if we need rdoTipo=A fallback

        if id_documento:
            # Single document: try URL with id_documento first
            for url in candidates:
                if f'id_documento={id_documento}' in url:
                    page_url_raw = url
                    break
            if page_url_raw is None:
                # Fallback: use process-level gerar_pdf URL with rdoTipo=A (Apenas)
                # The id_documento-specific link only exists for the currently
                # selected document in the SEI tree. For others, we use the
                # process-level link and POST with hdnDocumentosApenas.
                for url in candidates:
                    if f'id_procedimento={id_procedimento}' in url and 'id_documento=' not in url:
                        page_url_raw = url
                        use_apenas_mode = True
                        break
            if page_url_raw is None:
                raise RuntimeError(
                    f"Link 'Gerar PDF' não encontrado para documento {id_documento}. "
                    "Verifique se o documento existe e se você tem permissão."
                )
        else:
            # Whole process: find URL WITHOUT id_documento
            for url in candidates:
                if f'id_procedimento={id_procedimento}' in url and 'id_documento=' not in url:
                    page_url_raw = url
                    break
            if page_url_raw is None:
                # Fallback: accept any procedimento_gerar_pdf URL for this process
                for url in candidates:
                    if f'id_procedimento={id_procedimento}' in url:
                        page_url_raw = url
                        break
            if page_url_raw is None:
                raise RuntimeError(
                    f"Link 'Gerar PDF' não encontrado na árvore do processo {id_procedimento}. "
                    "Verifique se você tem permissão para gerar PDF deste processo."
                )

        page_url = self._sei_url(unescape(page_url_raw))

        # Step 3: GET the gerar page
        r_page = self._get(page_url)
        if "login.php" in str(r_page.url) or "pwdSenha" in r_page.text:
            raise RuntimeError("Sessão expirada ao acessar página de geração de PDF")

        # Step 4: Extract form action
        form_match = re.search(r'<form[^>]+action="([^"]+)"', r_page.text)
        if not form_match:
            raise RuntimeError(
                "Formulário de geração de PDF não encontrado na página de confirmação"
            )
        action_url = self._sei_url(unescape(form_match.group(1)))

        # Step 5: POST with Gerar flag
        post_data: dict[str, str] = {
            'hdnInfraTipoPagina': '2',
            'hdnFlagGerar': '1',
            'rdoTipo': 'T',
        }
        if use_apenas_mode:
            # Single-doc fallback: use "Apenas" mode to generate only this document
            post_data['rdoTipo'] = 'A'
            post_data['hdnDocumentosApenas'] = id_documento  # type: ignore[assignment]
        r_post = self._post(action_url, post_data)

        # Step 6: Extract iframe src from JavaScript
        iframe_match = re.search(
            r"\.src\s*=\s*'(controlador\.php\?acao=exibir_arquivo[^']+)'",
            r_post.text,
        )
        if not iframe_match:
            label = f"documento {id_documento}" if id_documento else f"processo {id_procedimento}"
            raise RuntimeError(
                f"URL do PDF não encontrada na resposta para {label}. "
                "SEI pode estar gerando o arquivo."
            )
        raw_url = iframe_match.group(1)
        # Clean non-printable and whitespace chars (WAF blocks dirty URLs)
        clean_url = ''.join(
            ch for ch in raw_url if ch.isprintable() and ch not in ' \t\n\r'
        )
        full_url = self._sei_url(unescape(clean_url))

        # Step 7: GET the PDF
        r_pdf = self._get(full_url)
        pdf_bytes = r_pdf.content

        if not pdf_bytes:
            raise RuntimeError(
                f"Resposta vazia ao gerar PDF ({id_procedimento})"
            )

        with open(output_path, "wb") as f:
            f.write(pdf_bytes)

        self._control_html = None
        return output_path

    def download_pdf(
        self,
        id_procedimento: str,
        output_path: str | None = None,
        id_documento: str | None = None,
    ) -> str:
        """Download a process as PDF via procedimento_gerar_pdf.

        Automatically switches to the correct unit if the process is
        restricted to another unit, then switches back after download.

        Args:
            id_procedimento: Process internal ID.
            output_path: Where to save the PDF. If None, uses /tmp/sei_<id>.pdf.
            id_documento: Unused (kept for API compatibility).

        Returns:
            Path to the downloaded PDF file.

        Raises:
            RuntimeError: If session expired, PDF URL not found, no access to unit,
                or response is not a PDF.
        """
        if output_path is None:
            output_path = f"/tmp/sei_{id_procedimento}.pdf"

        arvore_html = self._navigate_to_arvore(id_procedimento)
        if not arvore_html:
            raise RuntimeError(
                f"Processo {id_procedimento} não encontrado ou sessão expirada"
            )

        with self._auto_unit_switch(arvore_html) as switched_to:
            if switched_to:
                # Re-fetch arvore from the correct unit
                arvore_html = self._navigate_to_arvore(id_procedimento)
                if not arvore_html:
                    raise RuntimeError(
                        f"Processo {id_procedimento} não acessível mesmo na unidade {switched_to}"
                    )
            return self._gerar_pdf_flow(
                arvore_html, id_procedimento, output_path,
            )

    def download_document_pdf(
        self,
        id_documento: str,
        id_procedimento: str,
        output_path: str | None = None,
    ) -> str:
        """Download a single document as PDF via procedimento_gerar_pdf.

        Automatically switches to the correct unit if needed.
        Note: acao is ALWAYS procedimento_gerar_pdf even for single-document PDFs.

        Args:
            id_documento: Document internal ID.
            id_procedimento: Process ID containing the document.
            output_path: Where to save the PDF. If None, uses /tmp/sei_doc_<id>.pdf.

        Returns:
            Path to the downloaded PDF file.

        Raises:
            RuntimeError: If session expired, PDF URL not found, no access to unit,
                or response is not a PDF.
        """
        if output_path is None:
            output_path = f"/tmp/sei_doc_{id_documento}.pdf"

        arvore_html = self._navigate_to_arvore(id_procedimento)
        if not arvore_html:
            raise RuntimeError(
                f"Processo {id_procedimento} não encontrado ou sessão expirada"
            )

        with self._auto_unit_switch(arvore_html) as switched_to:
            if switched_to:
                arvore_html = self._navigate_to_arvore(id_procedimento)
                if not arvore_html:
                    raise RuntimeError(
                        f"Processo {id_procedimento} não acessível mesmo na unidade {switched_to}"
                    )
            return self._gerar_pdf_flow(
                arvore_html, id_procedimento, output_path,
                id_documento=id_documento,
            )

    # --- Unit auto-switch helpers ---

    def _detect_unit_restriction(self, arvore_html: str) -> str | None:
        """Detect if process/documents are restricted to another unit.

        Parses the arvore HTML for the SEI restriction message:
        'Processo aberto somente na unidade <a class="ancoraSigla">SIGLA</a>'

        Also checks for documents with about:blank URLs (non-viewable from
        current unit) paired with UNIDADE_GERADORA actions.

        Returns:
            The required unit sigla if a restriction is detected, None otherwise.
        """
        # Primary: explicit process-level restriction message
        unit_msg = re.search(
            r'Processo aberto somente na unidade.*?'
            r'class="ancoraSigla">([^<]+)</a>',
            arvore_html,
        )
        if unit_msg:
            return unit_msg.group(1).strip()

        # Secondary: documents with about:blank + UNIDADE_GERADORA
        has_blank_docs = bool(
            re.search(r'"about:blank","ifrConteudoVisualizacao"', arvore_html)
        )
        if has_blank_docs and not self._tree_has_current_unit_process_action(arvore_html):
            # Extract the unit sigla from UNIDADE_GERADORA actions (last param)
            ug_siglas = re.findall(
                r'new infraArvoreAcao\("UNIDADE_GERADORA"[^)]*,"([^"]+)"\)',
                arvore_html,
            )
            if ug_siglas:
                # All docs in a restricted process usually share the same unit
                return ug_siglas[0].strip()

        return None

    def _tree_has_current_unit_process_action(self, arvore_html: str) -> bool:
        """Return True when the current tree exposes process actions in this unit.

        Forwarded processes may contain foreign ``about:blank`` documents while
        still allowing the receiving unit to include/edit its own documents.
        These action tokens are a stronger authority signal than document
        origin-unit metadata.
        """
        return any(
            token in arvore_html
            for token in (
                "linkIncluirDocumento",
                "documento_escolher_tipo",
                "documento_incluir.svg",
                "linkAlterarProcesso",
                "procedimento_alterar",
                "andamento_marcador_gerenciar",
                "procedimento_enviar",
            )
        )

    def _detect_document_units(self, arvore_html: str) -> dict[str, str]:
        """Map document IDs to their origin unit siglas.

        Parses UNIDADE_GERADORA actions from the arvore tree.
        Useful when a process spans multiple units.

        Returns:
            Dict of {doc_id: unit_sigla}.
        """
        results: dict[str, str] = {}
        for m in re.finditer(
            r'new infraArvoreAcao\("UNIDADE_GERADORA",'
            r'"UG(\d+)","(\d+)","#",null,"([^"]+)",null,true,"([^"]+)"\)',
            arvore_html,
        ):
            doc_id = m.group(1)
            unit_sigla = m.group(4)
            results[doc_id] = unit_sigla
        return results

    def _can_switch_to(self, unit_sigla: str) -> bool:
        """Check if the current user has access to a given unit."""
        try:
            units = self.list_units()
            return any(u.sigla == unit_sigla for u in units)
        except Exception:
            return False

    def _find_open_units_from_history(
        self, arvore_html: str,
    ) -> list[str]:
        """Parse process history to find units where the process is currently open.

        Uses ``procedimento_consultar_historico`` from the arvore tree to
        read the andamento table, then determines open units by tracking
        'recebido na unidade' vs 'remetido pela unidade' events.

        Returns:
            List of unit siglas where the process is currently open.
        """
        hist_url_match = re.search(
            r'(controlador\.php\?acao=procedimento_consultar_historico[^"\'<>\s]+)',
            arvore_html,
        )
        if not hist_url_match:
            return []

        try:
            r = self._get(self._sei_url(hist_url_match.group(1)))
        except Exception:
            return []

        soup = BeautifulSoup(r.text, "lxml")
        table = soup.find("table")
        if not table:
            return []

        rows = table.find_all("tr")[1:]  # skip header
        # Track state per unit: most recent event wins (list is newest-first)
        unit_state: dict[str, str] = {}
        for row in reversed(rows):  # process oldest first
            cells = row.find_all("td")
            if len(cells) < 4:
                continue
            unidade = cells[1].get_text(strip=True)
            desc = cells[3].get_text(strip=True)

            if "recebido na unidade" in desc.lower():
                unit_state[unidade] = "open"
            elif "remetido pela unidade" in desc.lower():
                # Extract the source unit from description
                source = desc.replace("Processo remetido pela unidade", "").strip()
                if source:
                    unit_state[source] = "sent"

        return [u for u, state in unit_state.items() if state == "open"]

    def _find_accessible_unit(
        self,
        arvore_html: str,
        *,
        candidates: list[str] | None = None,
    ) -> str | None:
        """Find an accessible unit for a restricted process.

        Strategy (ordered by preference):
        1. Direct restriction message ('Processo aberto somente na unidade X')
        2. Explicit candidates list (if provided)
        3. UNIDADE_GERADORA actions (document origin units)
        4. Process history (procedimento_consultar_historico)

        Returns:
            A unit sigla the user can switch to, or None if no accessible unit found.
        """
        available = {u.sigla for u in self.list_units()}

        # 1. Direct restriction
        required = self._detect_unit_restriction(arvore_html)
        if required and required in available:
            return required

        # 2. Explicit candidates
        if candidates:
            for c in candidates:
                if c in available:
                    return c

        # 3. UNIDADE_GERADORA siglas
        doc_units = set(self._detect_document_units(arvore_html).values())
        for u in doc_units:
            if u in available:
                return u

        # 4. Process history (heavier — makes an HTTP request)
        open_units = self._find_open_units_from_history(arvore_html)
        for u in open_units:
            if u in available:
                return u

        return None

    def _is_process_inaccessible(self, arvore_html: str) -> bool:
        """Check if documents in the arvore are inaccessible (about:blank URLs).

        Returns True only when document nodes exist and none exposes a
        contextual tree/view URL or process action for the current unit.
        Received processes can legitimately contain documents from other origin
        units; origin-unit metadata must not block reading or writing when the
        current tree already proves the current unit can act.
        """
        if self._tree_has_current_unit_process_action(arvore_html):
            return False

        document_nodes = list(
            re.finditer(
                r'new\s+infraArvoreNo\("DOCUMENTO",(.+?)\);?',
                arvore_html,
            )
        )
        if not document_nodes:
            return False

        has_blank_doc = False
        for match in document_nodes:
            params = re.findall(r'"([^"]*)"', match.group(1))
            if len(params) < 3:
                continue
            arvore_url = params[2].strip()
            if arvore_url and arvore_url.casefold() != "about:blank":
                return False
            has_blank_doc = True

        src_urls = re.findall(r"Nos\[\d+\]\.src\s*=\s*'([^']*)'", arvore_html)
        if any(src.strip() and src.strip().casefold() != "about:blank" for src in src_urls):
            return False

        return has_blank_doc

    @contextlib.contextmanager
    def _auto_unit_switch(
        self, arvore_html: str, *, target_unit: str | None = None
    ) -> Iterator[str | None]:
        """Context manager: auto-switch to the required unit, restore on exit.

        Handles two scenarios:
        1. Simple: 'Processo aberto somente na unidade X' → switch to X
        2. Complex: Documents with about:blank (restricted process) → find
           an accessible unit via history/UNIDADE_GERADORA/candidates

        Usage::

            arvore = self._navigate_to_arvore(proc_id)
            with self._auto_unit_switch(arvore) as switched_to:
                if switched_to:
                    arvore = self._navigate_to_arvore(proc_id)  # re-fetch
                # ... do work with arvore ...

        Args:
            arvore_html: The arvore HTML to inspect for restrictions.
            target_unit: Override: switch to this unit instead of auto-detecting.

        Yields:
            The unit sigla we switched to, or None if no switch was needed.
        """
        # Determine if we need to switch
        if target_unit:
            required = target_unit
        elif self._is_process_inaccessible(arvore_html):
            required = self._find_accessible_unit(arvore_html)
            if not required:
                # Can't find any accessible unit — yield None, let caller deal with it
                yield None
                return
        else:
            yield None
            return

        # Save current unit
        try:
            current_status = self.status()
            original_sigla = current_status.unidade_sigla
        except Exception:
            try:
                fresh_control = self._fresh_control()
                current_status = parse_system_status(fresh_control)
                original_sigla = current_status.unidade_sigla
            except Exception:
                original_sigla = None

        # Already on the right unit?
        if original_sigla and required.casefold() in original_sigla.casefold():
            yield None
            return

        # Check access and switch
        if not self._can_switch_to(required):
            explicit_restriction = self._detect_unit_restriction(arvore_html)
            if explicit_restriction and not self._tree_has_current_unit_process_action(arvore_html):
                raise RuntimeError(
                    f"Processo requer unidade '{required}' mas você não tem acesso. "
                    f"Unidades disponíveis: {', '.join(u.sigla for u in self.list_units())}"
                )
            yield None
            return

        self.switch_unit(required)
        try:
            yield required
        finally:
            # Restore original unit
            if original_sigla:
                try:
                    self.switch_unit(original_sigla)
                    try:
                        restored_status = self.status()
                        restored_sigla = restored_status.unidade_sigla or ""
                    except Exception:
                        restored_sigla = ""
                    if restored_sigla and original_sigla.casefold() not in restored_sigla.casefold():
                        self.switch_unit(original_sigla)
                except Exception:
                    pass  # Best effort — don't mask the real exception

    def _tree_access_quality(self, arvore_html: str) -> int:
        """Score a tree so a partial direct response is not final.

        SEI can return a syntactically valid tree whose document nodes all use
        ``about:blank``.  That tree is useful as a last-resort inventory, but
        it is weaker than a contextual tree returned by the quick search.  A
        positive score means that at least one document URL or current-unit
        process action is usable.
        """
        if not arvore_html or "login.php" in arvore_html or "pwdSenha" in arvore_html:
            return -1
        if self._tree_has_current_unit_process_action(arvore_html):
            return 100

        document_nodes = list(
            re.finditer(
                r'new\s+infraArvoreNo\("DOCUMENTO",(.+?)\);?',
                arvore_html,
            )
        )
        usable_documents = 0
        for match in document_nodes:
            params = re.findall(r'"([^"]*)"', match.group(1))
            if len(params) >= 3 and params[2].strip().casefold() != "about:blank":
                usable_documents += 1
        usable_documents += sum(
            1
            for src in re.findall(r"Nos\[\d+\]\.src\s*=\s*'([^']*)'", arvore_html)
            if src.strip().casefold() != "about:blank"
        )
        if usable_documents:
            return 50 + usable_documents
        if document_nodes:
            return 1
        # A process-only tree can still be a valid navigation result.
        return 10 if "infraArvoreNo" in arvore_html else 0

    def _arvore_from_process_page(self, page_html: str) -> str | None:
        """Load the tree iframe from a process page HTML response."""
        if not page_html or "login.php" in page_html or "pwdSenha" in page_html:
            return None
        soup = BeautifulSoup(page_html, "lxml")
        iframe = soup.find("iframe", {"name": "ifrArvore"})
        if not iframe or not iframe.get("src"):
            return None
        arvore_url = urljoin(self._sei_url(""), iframe["src"])
        response = self._get(arvore_url)
        self._control_html = None
        return response.text

    def _navigate_to_arvore(self, id_procedimento: str) -> str | None:
        """Navigate to a process and return the best available tree HTML.

        Strategy (fast → slow): direct URL, hashed process-page URL, then
        quick search.  Unlike the old implementation, a direct tree containing
        only inaccessible ``about:blank`` document nodes is not accepted before
        the contextual search is attempted.
        """
        self._ensure_session()
        best_html: str | None = None
        best_quality = -1

        def consider(candidate: str | None, *, stop_at: int = 50) -> str | None:
            nonlocal best_html, best_quality
            if not candidate:
                return None
            quality = self._tree_access_quality(candidate)
            if quality > best_quality:
                best_html = candidate
                best_quality = quality
            if quality >= stop_at:
                return candidate
            return None

        # Strategy 1: direct URL
        url = self._sei_url(
            f"controlador.php?acao=procedimento_trabalhar"
            f"&id_procedimento={id_procedimento}"
        )
        try:
            direct = self._get(url)
            candidate = self._arvore_from_process_page(direct.text)
            if consider(candidate):
                return candidate
        except Exception:
            pass

        # Strategy 2: hashed process-page URL
        try:
            process_page = self._navigate_to_process_page(id_procedimento)
            if process_page is not None:
                candidate = self._arvore_from_process_page(str(process_page))
                if consider(candidate):
                    return candidate
        except Exception:
            pass

        # Strategy 3: quick search, which often supplies the current unit hash
        try:
            result_html = self.search(id_procedimento)
            candidate = self._arvore_from_process_page(result_html)
            if consider(candidate):
                return candidate
        except Exception:
            pass

        return best_html

    def _navigate_to_arvore_visualizar(self, id_procedimento: str) -> str | None:
        """Navigate from ``ifrArvore`` to the process ``arvore_visualizar`` page.

        Chain:
        ``procedimento_trabalhar`` -> ``ifrArvore`` -> ``arvore_visualizar``

        The returned HTML is the page that contains inline JS variables such as
        ``linkReabrirProcesso`` and ``linkCienciaProcesso``.
        """
        arvore_html = self._navigate_to_arvore(id_procedimento)
        if not arvore_html:
            return None

        # Some callers may already have the selected arvore_visualizar HTML.
        # Check for characteristic JS vars from arvore_visualizar (e.g.
        # linkReabrirProcesso, linkConcluirProcesso, linkEncaminharEmail).
        # linkArvoreNavegar lives in procedimento_visualizar and must NOT
        # trigger a short-circuit.
        if re.search(
            r"var\s+link(?:Reabrir|Concluir|Encaminhar|Assinar|Excluir)\w*\s*=\s*'[^']+'",
            arvore_html,
        ):
            return arvore_html

        vis_match = re.search(
            rf"(controlador\.php\?acao=arvore_visualizar[^\"'\s]*"
            rf"id_procedimento={re.escape(id_procedimento)}[^\"'\s]*)",
            arvore_html,
        )
        if not vis_match:
            return None

        vis_url_raw = vis_match.group(1).replace("&amp;", "&")
        vis_url_raw = re.sub(r"&id_documento=\d+", "", vis_url_raw)
        r_vis = self._get(urljoin(self._sei_url(""), vis_url_raw))
        return r_vis.text

    def _find_in_acompanhamento(self, id_procedimento: str) -> str | None:
        """Search for a process link in Acompanhamento Especial."""
        html = self._ensure_control()
        soup = BeautifulSoup(html, "lxml")

        acomp_url = None
        for a in soup.find_all("a", href=True):
            if "acompanhamento_listar" in a["href"]:
                acomp_url = urljoin(self._sei_url(""), a["href"])
                break

        if not acomp_url:
            return None

        r = self._get(acomp_url)
        asoup = BeautifulSoup(r.text, "lxml")

        for a in asoup.find_all("a", href=True):
            href = a["href"]
            if "procedimento_trabalhar" in href and id_procedimento in href:
                self._control_html = None
                return urljoin(self._sei_url(""), href)

        self._control_html = None
        return None

    def _build_arvore_visualizar_url(
        self,
        id_documento: str,
        id_procedimento: str,
    ) -> str | None:
        """Build an ``arvore_visualizar`` URL using the current unit context.

        In forwarded processes, the tree returned by the SEI may include
        ``about:blank`` for documents owned by the receiving/current unit when
        the frame was rendered with another unit's context. Re-navigating to the
        process page lets us reuse the current session's ``infra_unidade_atual``
        and ``infra_hash`` to select the document directly.
        """
        url = self._sei_url(
            f"controlador.php?acao=procedimento_trabalhar"
            f"&id_procedimento={id_procedimento}"
        )
        response = self._get(url)
        soup = BeautifulSoup(response.text, "lxml")
        iframe = soup.find("iframe", {"name": "ifrArvore"})
        if not iframe or not iframe.get("src"):
            return None

        arvore_src = str(iframe["src"]).replace("&amp;", "&")
        unit_match = re.search(r"[?&]infra_unidade_atual=(\d+)", arvore_src)
        hash_match = re.search(r"[?&]infra_hash=([A-Za-z0-9]+)", arvore_src)
        if not unit_match or not hash_match:
            return None

        return urljoin(
            self._sei_url(""),
            "controlador.php?acao=arvore_visualizar"
            "&acao_origem=arvore_inicializar"
            f"&id_procedimento={id_procedimento}"
            f"&id_documento={id_documento}"
            "&infra_sistema=100000100"
            f"&infra_unidade_atual={unit_match.group(1)}"
            f"&infra_hash={hash_match.group(1)}",
        )

    def _get_editor_url(
        self, id_documento: str, id_procedimento: str
    ) -> str | None:
        """Get the editor_montar URL for a document.

        Automatically switches to the correct unit if the document
        is not accessible from the current unit.
        """
        arvore_html = self._navigate_to_arvore(id_procedimento)
        if not arvore_html:
            return None

        with self._auto_unit_switch(arvore_html) as switched_to:
            if switched_to:
                arvore_html = self._navigate_to_arvore(id_procedimento)
                if not arvore_html:
                    return None

            # Find the document's arvore_visualizar URL
            doc_pattern = re.compile(
                rf'controlador\.php\?acao=arvore_visualizar[^"]*id_documento={id_documento}[^"]*'
            )
            doc_match = doc_pattern.search(arvore_html)
            if not doc_match:
                all_docs = self.get_full_document_tree(id_procedimento, expand_all=True)
                tree_doc = next(
                    (doc for doc in all_docs if doc.id_documento == id_documento),
                    None,
                )
                if tree_doc and tree_doc.arvore_url and tree_doc.arvore_url.casefold() != "about:blank":
                    doc_url = tree_doc.arvore_url
                else:
                    doc_url = self._build_arvore_visualizar_url(id_documento, id_procedimento)
                    if not doc_url:
                        return None
            else:
                doc_url = urljoin(self._sei_url(""), doc_match.group().replace("&amp;", "&"))
            rd = self._get(doc_url)

            # Extract linkEditarConteudo
            edit_match = re.search(
                r"var\s+linkEditarConteudo\s*=\s*'([^']+)'", rd.text
            )
            if not edit_match:
                return None

            return urljoin(self._sei_url(""), edit_match.group(1))

    # --- Signing ---

    def sign_block(self, block_numero: str, doc_indices: list[int] | None = None) -> dict:
        """Sign all pending documents in a bloco de assinatura.

        Returns dict with 'signed', 'already_signed', 'errors' lists.
        """
        return self._sign_from_blocos(block_numero, doc_indices=doc_indices)

    # --- Ciência (acknowledgement) ---

    def give_notice_document(
        self,
        id_documento: str,
        id_procedimento: str,
    ) -> dict:
        """Dar ciência em um documento específico (acknowledge a document).

        Navigates to arvore_visualizar for the document, extracts the
        ``linkCienciaDocumento`` JS variable, and GETs that URL.  If the
        server returns a confirmation form, it is submitted automatically.

        Supports _auto_unit_switch so it works across units.

        Args:
            id_documento: Document internal ID.
            id_procedimento: Process ID containing the document.

        Returns:
            dict with 'ok' (bool), 'message' (str), 'id_documento'.
        """
        arvore_html = self._navigate_to_arvore(id_procedimento)
        if not arvore_html:
            raise RuntimeError(
                f"Processo {id_procedimento} não encontrado ou sessão expirada"
            )

        with self._auto_unit_switch(arvore_html) as switched_to:
            if switched_to:
                arvore_html = self._navigate_to_arvore(id_procedimento)
                if not arvore_html:
                    raise RuntimeError(
                        f"Processo {id_procedimento} não acessível na unidade {switched_to}"
                    )

            # Find arvore_visualizar URL for this document
            doc_pattern = re.compile(
                rf'controlador\.php\?acao=arvore_visualizar[^"]*'
                rf'id_documento={re.escape(id_documento)}[^"]*'
            )
            doc_match = doc_pattern.search(arvore_html)
            if not doc_match:
                return {
                    "ok": False,
                    "message": f"Documento {id_documento} não encontrado na árvore do processo",
                    "id_documento": id_documento,
                }

            sel_url = self._sei_url(doc_match.group().replace('&amp;', '&'))
            r_sel = self._get(sel_url)

            # Extract linkCienciaDocumento from JS
            # Pattern: var linkCienciaDocumento = 'controlador.php?acao=documento_ciencia&...&id_documento=';
            # The id_documento= is empty in the variable (appended dynamically by JS)
            ciencia_match = re.search(
                r"var\s+linkCienciaDocumento\s*=\s*'([^']+)'",
                r_sel.text,
            )
            if not ciencia_match:
                return {
                    "ok": False,
                    "message": f"Ação 'Dar Ciência' não disponível para o documento {id_documento}. "
                               "O documento pode já ter sido lido ou não permite ciência.",
                    "id_documento": id_documento,
                }

            # Build full URL: variable ends with 'id_documento=' (no value)
            ciencia_url_raw = ciencia_match.group(1).replace('&amp;', '&')
            # Append the actual id_documento value if not already present
            if ciencia_url_raw.endswith('id_documento='):
                ciencia_url_raw = ciencia_url_raw + id_documento
            elif 'id_documento=' not in ciencia_url_raw:
                ciencia_url_raw = ciencia_url_raw + f'&id_documento={id_documento}'

            ciencia_url = urljoin(self._sei_url(""), ciencia_url_raw)
            r_ciencia = self._get(ciencia_url)
            self._control_html = None

            return self._handle_ciencia_response(r_ciencia, id_documento)

    def give_notice_process(self, id_procedimento: str) -> dict:
        """Dar ciência no processo inteiro (acknowledge entire process).

        Navigates to arvore_visualizar for the process root, extracts
        ``linkCienciaProcesso`` JS variable, and GETs that URL.  If the
        server returns a confirmation form, it is submitted automatically.

        Supports _auto_unit_switch so it works across units.

        Args:
            id_procedimento: Process internal ID.

        Returns:
            dict with 'ok' (bool), 'message' (str), 'id_procedimento'.
        """
        arvore_html = self._navigate_to_arvore(id_procedimento)
        if not arvore_html:
            raise RuntimeError(
                f"Processo {id_procedimento} não encontrado ou sessão expirada"
            )

        with self._auto_unit_switch(arvore_html) as switched_to:
            if switched_to:
                arvore_html = self._navigate_to_arvore(id_procedimento)
                if not arvore_html:
                    raise RuntimeError(
                        f"Processo {id_procedimento} não acessível na unidade {switched_to}"
                    )

            # Load arvore_visualizar for the process root (no id_documento)
            proc_vis_match = re.search(
                rf'controlador\.php\?acao=arvore_visualizar[^"]*'
                rf'id_procedimento={re.escape(id_procedimento)}[^"]*',
                arvore_html,
            )
            if proc_vis_match:
                # Remove id_documento if present (we want the process-level view)
                vis_url_raw = proc_vis_match.group().replace('&amp;', '&')
                vis_url_raw = re.sub(r'&id_documento=\d+', '', vis_url_raw)
                vis_url = self._sei_url(vis_url_raw)
                r_vis = self._get(vis_url)
                tree_html = r_vis.text
            else:
                tree_html = arvore_html

            # Extract linkCienciaProcesso from JS
            ciencia_match = re.search(
                r"var\s+linkCienciaProcesso\s*=\s*'([^']+)'",
                tree_html,
            )
            if not ciencia_match:
                # Also check for linkCienciaProcessoAnexado as fallback
                ciencia_match = re.search(
                    r"var\s+linkCienciaProcessoAnexado\s*=\s*'([^']+)'",
                    tree_html,
                )
            if not ciencia_match:
                return {
                    "ok": False,
                    "message": f"Ação 'Dar Ciência' não disponível para o processo {id_procedimento}. "
                               "O processo pode já ter sido lido ou não permite ciência.",
                    "id_procedimento": id_procedimento,
                }

            ciencia_url_raw = ciencia_match.group(1).replace('&amp;', '&')
            ciencia_url = urljoin(self._sei_url(""), ciencia_url_raw)
            r_ciencia = self._get(ciencia_url)
            self._control_html = None

            result = self._handle_ciencia_response(r_ciencia, id_procedimento)
            result['id_procedimento'] = id_procedimento
            return result

    def _handle_ciencia_response(self, response: "httpx.Response", entity_id: str) -> dict:
        """Parse the server response after a ciência GET request.

        SEI may return:
        - A simple success redirect (back to arvore/process page)
        - A confirmation form (if confirmation required)
        - An error message

        Args:
            response: HTTP response from the ciência GET.
            entity_id: Document or process ID (for error messages).

        Returns:
            dict with 'ok', 'message', and optionally other fields.
        """
        final_url = str(response.url)

        # If there's a confirmation form, submit it
        soup = BeautifulSoup(response.text, 'lxml')
        confirm_form = soup.find('form', id='frmCiencia')
        if not confirm_form:
            # Try generic form with ciência-related action
            for form in soup.find_all('form'):
                action = form.get('action', '')
                if 'ciencia' in action.lower():
                    confirm_form = form
                    break

        if confirm_form:
            action_url = urljoin(self._sei_url(""), confirm_form.get('action', ''))
            form_data: dict[str, str] = {}
            for inp in confirm_form.find_all('input'):
                name = inp.get('name', '')
                if name and inp.get('type') != 'button':
                    form_data[name] = inp.get('value', '')
            # Add submit button value if present
            submit_btn = confirm_form.find('input', {'type': 'submit'})
            if not submit_btn:
                submit_btn = confirm_form.find('button', {'type': 'submit'})
            if submit_btn and submit_btn.get('name'):
                form_data[submit_btn['name']] = submit_btn.get('value', 'Confirmar')

            r2 = self._post(action_url, form_data)
            response = r2
            final_url = str(r2.url)
            soup = BeautifulSoup(r2.text, 'lxml')

        # Check for error messages
        error_patterns = ['erro', 'falha', 'não foi possível', 'nao foi possivel']
        page_text_lower = response.text.lower()
        for pat in error_patterns:
            if pat in page_text_lower:
                # Check if it's actually an error section
                err_div = soup.find(class_=re.compile(r'(alert|erro|infraMsg)', re.I))
                if err_div:
                    return {
                        "ok": False,
                        "message": err_div.get_text(strip=True)[:200],
                        "id": entity_id,
                    }

        # Success: redirected to process/arvore or received a success message
        if ('arvore_visualizar' in final_url
                or 'procedimento_trabalhar' in final_url
                or 'procedimento_controlar' in final_url):
            return {"ok": True, "message": "Ciência registrada com sucesso", "id": entity_id}

        # Check for SEI success messages in the page
        success_msgs = soup.find_all(string=re.compile(r'ciência', re.I))
        if success_msgs:
            return {"ok": True, "message": "Ciência registrada com sucesso", "id": entity_id}

        # Default: assume success (no error = OK for SEI)
        return {"ok": True, "message": "Ciência registrada (verificar no SEI)", "id": entity_id}

    # --- Upload external document ---

    def upload_external_document(
        self,
        id_procedimento: str,
        file_path: str,
        tipo: str = "boletim",
        *,
        nivel_acesso: str = "0",
        descricao: str = "",
        data_elaboracao: str | None = None,
        tipo_conferencia: str = "4",
        numero: str = "",
    ) -> str:
        """Upload a PDF as an external document (Documento Externo) to a process.

        Flow:
          1. Navigate to arvore → get "Incluir Documento" URL
          2. Submit the type-selection form with id_serie=-1 (Externo path)
             + hdnInfraItensSelecionados/-1 so SEI opens documento_receber form
          3. Fill the documento_receber form (date, description, access level, etc.)
             with the ACTUAL document type (tipo arg, e.g. 'boletim'=34, 'anexo'=263)
          4. Upload file via multipart POST to documento_upload_anexo
             (field name: filArquivo)
          5. Build hdnAnexos string with ± (Latin-1 \\xb1) separator
          6. Submit the cadastro form with hdnAnexos

        CRITICAL: The ± separator (U+00B1) MUST be encoded as Latin-1 \\xb1.
        The SEI server uses ISO-8859-1 throughout.

        NOTE on tipo:
            The 'Externo' choice (-1) only opens the upload form; the actual
            document type must be a real id_serie from the selSerie dropdown
            (e.g. 'boletim'=34, 'anexo'=263, 'laudo'=54, 'relatorio'=408).
            The default changed from 'externo' to 'boletim' because -1 is not
            a valid selSerie value in the cadastro form.

        Args:
            id_procedimento: Process ID.
            file_path: Path to the PDF file to upload.
            tipo: Document type key or numeric id_serie string.
                  Defaults to 'boletim' (34). Other useful values:
                  'anexo'=263, 'laudo'=54, 'relatorio'=408, 'oficio'=11.
            nivel_acesso: '0' (Público), '1' (Restrito), '2' (Sigiloso).
            descricao: Document description.
            data_elaboracao: Date in DD/MM/YYYY format. Defaults to today.
            tipo_conferencia: Type of copy:
                '1' = Documento Original (default)
                '3' = Cópia Autenticada Administrativamente
                '2' = Cópia Autenticada por Cartório
                '4' = Cópia Simples
            numero: Optional document number.

        Returns:
            id_documento of the newly created external document.

        Raises:
            RuntimeError: If any step fails.
            FileNotFoundError: If file_path does not exist.
        """
        import os
        import mimetypes

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Arquivo não encontrado: {file_path}")

        if data_elaboracao is None:
            from datetime import date
            data_elaboracao = date.today().strftime('%d/%m/%Y')

        # Resolve tipo to id_serie (actual document type for selSerie)
        # 'externo' (-1) is NOT a valid selSerie — it only triggers the upload path.
        id_serie = self.DOC_TYPES.get(tipo.lower().replace(' ', '_'), tipo)
        if id_serie == '-1':
            # Legacy: if caller passes tipo='externo', default to 'boletim'
            id_serie = self.DOC_TYPES.get('boletim', '34')

        # Step 1: Navigate to arvore, find "Incluir Documento" URL
        arvore_html = self._navigate_to_arvore(id_procedimento)
        if not arvore_html:
            raise RuntimeError(
                f"Processo {id_procedimento} não encontrado ou sessão expirada"
            )

        href_match = re.search(
            r'href="([^"]+)"[^>]*>\s*<img\s+[^>]*documento_incluir',
            arvore_html,
        )
        if not href_match:
            raise RuntimeError("Link 'Incluir Documento' não encontrado na árvore")

        incl_url = urljoin(self._sei_url(""), href_match.group(1))
        rtype = self._get(incl_url)

        # Step 2: Submit type selection form
        tsoup = BeautifulSoup(rtype.text, 'lxml')
        form = tsoup.find('form', id='frmDocumentoEscolherTipo')
        if not form:
            raise RuntimeError("Formulário de escolha de tipo não encontrado")

        form_action = urljoin(self._sei_url(""), form['action'])
        fdata: dict[str, str] = {}
        for inp in form.find_all('input'):
            n = inp.get('name', '')
            if n:
                fdata[n] = inp.get('value', '')
        # The type-selection form uses a checkbox list (chkInfraItem*).
        # For external documents we ALWAYS select item -1 ("Externo") here —
        # the actual document type (id_serie) is set later in the cadastro form.
        fdata['hdnInfraItensSelecionados'] = '-1'
        fdata['hdnInfraItemId'] = '-1'
        fdata['hdnIdSerie'] = '-1'

        rcadastro = self._post(form_action, fdata)
        csoup = BeautifulSoup(rcadastro.text, 'lxml')

        # Step 3: Parse cadastro form (frmDocumentoCadastro)
        cform = csoup.find('form', id='frmDocumentoCadastro')
        if not cform:
            raise RuntimeError(
                f"Formulário de cadastro não encontrado para tipo '{tipo}' "
                f"(id_serie={id_serie})"
            )

        cform_action = urljoin(self._sei_url(""), cform['action'])

        # Collect current form values
        cdata: dict[str, str] = {}
        for inp in cform.find_all('input'):
            n = inp.get('name', '')
            if not n:
                continue
            t = inp.get('type', '')
            if t == 'radio':
                if inp.get('checked'):
                    cdata[n] = inp.get('value', '')
            elif t not in ('button', 'submit'):
                cdata[n] = inp.get('value', '')
        for sel in cform.find_all('select'):
            n = sel.get('name', '')
            if n:
                selected = sel.find('option', selected=True)
                cdata[n] = selected['value'] if selected else ''

        # Override with our values
        cdata['hdnFlagDocumentoCadastro'] = '2'
        # For external docs the form (documento_receber) only offers D=Digitalizado
        # or N=Nato-digital. 'E' (Externo) is not a valid rdoFormato here.
        cdata['rdoFormato'] = 'D'              # Digitalizado (escaneado/uploaded)
        cdata['rdoNivelAcesso'] = nivel_acesso
        # selSerie and hdnIdSerie must be the actual document type (e.g. 34=Boletim)
        # NOT -1 (which is only valid in the type-selection step, not here).
        cdata['selSerie'] = id_serie
        cdata['hdnIdSerie'] = id_serie
        cdata['txtDataElaboracao'] = data_elaboracao
        cdata['selTipoConferencia'] = tipo_conferencia
        cdata['hdnAnexos'] = ''                 # Will be set after upload

        if descricao:
            cdata['txtDescricao'] = descricao
        if numero:
            cdata['txtNumero'] = numero

        # Step 4: Upload file via AJAX multipart POST
        # The actual upload endpoint is documento_upload_anexo (NOT upload_arquivo).
        # The URL with its infra_hash is embedded in the JS of the cadastro page:
        #   new infraUpload('frmAnexos','controlador.php?acao=documento_upload_anexo&...&infra_hash=XXX', ...)
        upload_url_match = re.search(
            r"new infraUpload\([^,]+,'([^']+documento_upload_anexo[^']+)'",
            rcadastro.text,
        )
        if upload_url_match:
            upload_url = urljoin(self._sei_url(""), upload_url_match.group(1))
        else:
            # Fallback: build URL manually (will likely fail with "Hash inválido")
            upload_url = self._sei_url(
                'controlador.php?acao=documento_upload_anexo'
                f'&arvore=1&id_procedimento={id_procedimento}'
                f'&id_serie={id_serie}'
                f'&infra_sistema=100000100'
            )

        file_name = os.path.basename(file_path)
        mime_type = mimetypes.guess_type(file_path)[0] or 'application/octet-stream'

        with open(file_path, 'rb') as f:
            file_content = f.read()

        # Build multipart form data
        # SEI uses field name 'filArquivo' for the file upload input.
        import httpx as _httpx
        files = {'filArquivo': (file_name, file_content, mime_type)}
        r_upload = self.client.post(upload_url, files=files)
        r_upload = self._follow_upload(r_upload)

        upload_response = r_upload.text.strip()
        # Response format: hash#filename#mimetype#size#datetime#
        # or error indicator
        if not upload_response or '#' not in upload_response:
            raise RuntimeError(
                f"Upload falhou. Resposta do servidor: {upload_response!r}"
            )

        # Step 5: Build hdnAnexos string with ± separator in Latin-1
        # Format: hash±filename±datetime±size±pages±user_id±unit_name
        # Parse upload response: hash#filename#mimetype#size#datetime#
        parts = upload_response.rstrip('#').split('#')
        if len(parts) < 5:
            raise RuntimeError(
                f"Resposta de upload inesperada: {upload_response!r}"
            )
        hash_val = parts[0]
        fname = parts[1]
        # mimetype = parts[2]
        fsize = parts[3]
        fdatetime = parts[4] if len(parts) > 4 else data_elaboracao

        # Gather user_id and unit from the form's hidden fields
        user_id = cdata.get('hdnIdUsuario', '')
        unit_name = ''  # SEI fills this from session; leave empty if unknown

        # Build hdnAnexos with ± (U+00B1 = Latin-1 \xb1) separator
        # The value must be a Latin-1 string (will be encoded by _post)
        sep = '\xb1'  # Latin-1 ± character
        hdn_anexos = sep.join([
            hash_val,
            fname,
            fdatetime,
            fsize,
            '0',        # pages (unknown at this point)
            user_id,
            unit_name,
        ])
        cdata['hdnAnexos'] = hdn_anexos

        # Step 6: Submit the cadastro form with all fields
        r_created = self._post(cform_action, cdata)
        self._control_html = None

        # Parse response for new document ID
        url_str = str(r_created.url)
        id_doc_match = re.search(r'id_documento=(\d+)', url_str)
        if not id_doc_match:
            id_doc_match = re.search(r'id_documento=(\d+)', r_created.text)

        if id_doc_match:
            return id_doc_match.group(1)

        # Check for error
        esoup = BeautifulSoup(r_created.text, 'lxml')
        val = esoup.find('textarea', {'id': 'txaInfraValidacao'})
        if val and val.get_text(strip=True):
            raise RuntimeError(
                f"SEI rejeitou o documento externo: {val.get_text(strip=True)[:200]}"
            )

        raise RuntimeError(
            "Documento externo submetido mas id_documento não encontrado na resposta. "
            f"URL final: {url_str}"
        )

    def _follow_upload(self, response: "httpx.Response") -> "httpx.Response":
        """Follow redirects for an upload response (no hash harvesting needed)."""
        r = auth._follow(self.client, response, self.base_url)
        with contextlib.suppress(Exception):
            r.encoding = "iso-8859-1"
        return r

    def sign_document(self, id_documento: str, id_procedimento: str) -> dict:
        """Sign a single document by ID (from process tree).

        Uses the linkAssinarDocumento URL from arvore_visualizar.
        Works for both internal (sign) and external (authenticate) documents —
        SEI uses the same form action ``documento_assinar`` for both.
        """
        return self._sign_or_authenticate(
            id_documento,
            id_procedimento,
            expected_kind="assinatura",
        )

    def authenticate_document(self, id_documento: str, id_procedimento: str) -> dict:
        """Authenticate an external (uploaded) document in a process.

        In SEI, authenticating an external document uses the exact same
        ``documento_assinar`` form as signing an internal document.
        The page title changes to "Autenticação de Documento" but the
        mechanics are identical.

        Returns dict with 'doc_ids', 'signed', 'already_signed', 'errors'.
        """
        return self._sign_or_authenticate(
            id_documento,
            id_procedimento,
            expected_kind="autenticacao",
        )

    def authenticate_documents(
        self, id_documentos: list[str], id_procedimento: str
    ) -> list[dict]:
        """Authenticate multiple external documents in a process.

        Navigates to the process once and authenticates each document
        sequentially.  Returns a list of result dicts (one per document).
        """
        results = []
        for i, doc_id in enumerate(id_documentos):
            result = self._sign_or_authenticate(
                doc_id,
                id_procedimento,
                _reuse_process=(i > 0),
                expected_kind="autenticacao",
            )
            result["id_documento"] = doc_id
            results.append(result)
        return results

    def get_document_sign_form_info(self, id_documento: str, id_procedimento: str) -> dict[str, Any]:
        """Inspect the sign form without submitting it.

        Returns the pre-populated signer fields the SEI exposes for the
        current session/user on that specific document.
        """
        arvore_html = self._navigate_to_arvore(id_procedimento)
        if not arvore_html:
            return {"ok": False, "error": f"Processo {id_procedimento} não encontrado ou sessão expirada"}

        with self._auto_unit_switch(arvore_html) as switched_to:
            if switched_to:
                arvore_html = self._navigate_to_arvore(id_procedimento)
                if not arvore_html:
                    return {"ok": False, "error": f"Processo {id_procedimento} não acessível na unidade {switched_to}"}

            doc_pattern = re.compile(
                rf'controlador\.php\?acao=arvore_visualizar[^"]*id_documento={id_documento}[^"]*'
            )
            doc_match = doc_pattern.search(arvore_html)
            if not doc_match:
                return {"ok": False, "error": f"Documento {id_documento} não encontrado na árvore"}

            doc_url = urljoin(self._sei_url(""), doc_match.group())
            rd = self._get(doc_url)
            sign_match = re.search(r"var\s+linkAssinarDocumento\s*=\s*'([^']+)'", rd.text)
            if not sign_match:
                return {"ok": False, "error": "Link de assinatura/autenticação não encontrado para este documento"}

            sign_url = urljoin(self._sei_url(""), sign_match.group(1))
            rs = self._get(sign_url)
            ssoup = BeautifulSoup(rs.text, "lxml")
            form = ssoup.find("form", {"id": "frmAssinaturas"})
            if not form:
                return {
                    "ok": False,
                    "error": "Formulário de assinatura não encontrado",
                    "url": str(rs.url),
                }

            def _field(name: str) -> str:
                field = form.find(attrs={"name": name})
                if not field:
                    return ""
                if getattr(field, "name", "") == "select":
                    selected = field.find("option", selected=True)
                    if selected is None:
                        selected = field.find("option")
                    return (selected.get("value", "") if selected else "").strip()
                return (field.get("value", "") or "").strip()

            txt_usuario = _field("txtUsuario")
            hdn_id_usuario = _field("hdnIdUsuario")
            sel_cargo = _field("selCargoFuncao")

            return {
                "ok": True,
                "sign_url": sign_url,
                "txtUsuario": txt_usuario,
                "hdnIdUsuario": hdn_id_usuario,
                "selCargoFuncao": sel_cargo,
                "switched_to": switched_to,
            }

    def _verify_document_finalization_by_tree(
        self,
        id_documento: str,
        id_procedimento: str,
        *,
        expected_kind: str,
    ) -> dict[str, Any]:
        """Re-read the full tree and confirm signature/authentication by NosAcoes[].

        expected_kind:
            "assinatura" for internal sign
            "autenticacao" for external authenticate
        """
        try:
            docs = self.get_full_document_tree(id_procedimento, expand_all=True)
        except Exception as exc:
            return {
                "ok": False,
                "verified": False,
                "method": "tree_nos_acoes",
                "error": str(exc),
            }

        target = next((doc for doc in docs if doc.id_documento == id_documento), None)
        if target is None:
            return {
                "ok": False,
                "verified": False,
                "method": "tree_nos_acoes",
                "error": f"Documento {id_documento} não encontrado na árvore após releitura.",
            }

        signatures = [
            {
                "signer": sig.signer,
                "role": sig.role,
                "unit": sig.unit,
                "kind": sig.kind,
                "icon": sig.icon,
            }
            for sig in target.assinaturas
        ]
        if expected_kind == "autenticacao":
            verified = bool(target.autenticado)
        else:
            verified = bool(target.assinado)

        return {
            "ok": True,
            "verified": verified,
            "method": "tree_nos_acoes",
            "document": {
                "id_documento": target.id_documento,
                "sei_number": target.sei_number,
                "nome": target.nome,
                "assinado": bool(target.assinado),
                "autenticado": bool(target.autenticado),
                "assinaturas": signatures,
            },
        }

    def _sign_or_authenticate(
        self,
        id_documento: str,
        id_procedimento: str,
        *,
        _reuse_process: bool = False,
        expected_kind: str = "assinatura",
    ) -> dict:
        """Internal: navigate to a document in the process tree and sign/authenticate it.

        Now supports _auto_unit_switch: if the document belongs to another unit,
        automatically switches to that unit and restores the original after signing.
        """
        # Navigate to arvore using _navigate_to_arvore (handles session refresh)
        arvore_html = self._navigate_to_arvore(id_procedimento)
        if not arvore_html:
            return {"error": f"Processo {id_procedimento} não encontrado ou sessão expirada"}

        with self._auto_unit_switch(arvore_html) as switched_to:
            if switched_to:
                # Re-fetch arvore from the correct unit
                arvore_html = self._navigate_to_arvore(id_procedimento)
                if not arvore_html:
                    return {
                        "error": f"Processo {id_procedimento} não acessível na unidade {switched_to}"
                    }

            # Find the specific document's arvore_visualizar URL
            doc_pattern = re.compile(
                rf'controlador\.php\?acao=arvore_visualizar[^"]*id_documento={id_documento}[^"]*'
            )
            doc_match = doc_pattern.search(arvore_html)
            _expanded_arvore_url: str | None = None
            if not doc_match:
                # Document may be inside a lazy-loaded folder — expand all and retry
                try:
                    full_docs = self.get_full_document_tree(id_procedimento, expand_all=True)
                    target_doc = next((d for d in full_docs if d.id_documento == id_documento), None)
                    if target_doc and target_doc.arvore_url:
                        _expanded_arvore_url = target_doc.arvore_url
                except Exception:
                    pass
            if not doc_match and not _expanded_arvore_url:
                return {"error": f"Documento {id_documento} não encontrado na árvore"}

            if doc_match:
                doc_url = urljoin(self._sei_url(""), doc_match.group())
            else:
                doc_url = _expanded_arvore_url  # type: ignore[assignment]
            rd = self._get(doc_url)

            # Extract linkAssinarDocumento
            sign_match = re.search(r"var\s+linkAssinarDocumento\s*=\s*'([^']+)'", rd.text)
            if not sign_match:
                return {"error": "Link de assinatura/autenticação não encontrado para este documento"}

            sign_url = urljoin(self._sei_url(""), sign_match.group(1))
            self._control_html = None

            result = self._execute_sign(sign_url, id_documento)

            tree_verification = self._verify_document_finalization_by_tree(
                id_documento,
                id_procedimento,
                expected_kind=expected_kind,
            )
            result["post_verification"] = {"tree": tree_verification}
            if tree_verification.get("verified"):
                result["signed"] = [id_documento]
                result["errors"] = []
                return result

            if (
                not result.get("signed")
                and not result.get("already_signed")
                and (result.get("errors") or result.get("returned_to_sign_form"))
            ):
                with contextlib.suppress(Exception):
                    view_html = self.view_document_html(id_documento, id_procedimento)
                    pattern = (
                        r"autenticad[oa]\s+(?:eletronicamente\s+)?por"
                        if expected_kind == "autenticacao"
                        else r"assinado\s+eletronicamente\s+por"
                    )
                    if re.search(pattern, view_html, re.IGNORECASE):
                        result["signed"] = [id_documento]
                        result["errors"] = []
                        result["post_verification"]["fallback"] = {
                            "verified": True,
                            "method": "view_document_html",
                            "expected_kind": expected_kind,
                        }
            return result

    def _sign_from_blocos(self, block_numero: str, *, doc_indices: list[int] | None = None) -> dict:
        """Sign documents from a block detail page using per-document sign URLs."""
        detail = self._get_block_detail_page(block_numero)
        if detail is None:
            return {"error": f"Bloco {block_numero} não encontrado"}

        rd, _docs_soup = detail
        entries = self._extract_block_document_entries(rd.text)
        pending_entries = [
            entry
            for entry in entries
            if entry.get("can_sign")
        ]
        if not pending_entries:
            return {"error": f"Bloco {block_numero} não possui documentos pendentes assináveis"}

        selected_entries = pending_entries
        if doc_indices:
            target_indices = {int(idx) for idx in doc_indices}
            selected_entries = [entry for entry in pending_entries if int(entry.get("seq") or 0) in target_indices]
            if not selected_entries:
                return {"error": f"Nenhum documento pendente corresponde aos índices {sorted(target_indices)} no bloco {block_numero}"}

        result: dict[str, Any] = {"doc_ids": [], "signed": [], "already_signed": [], "errors": []}
        for entry in selected_entries:
            sign_url = entry.get("sign_url")
            doc_ref = entry.get("documento_id") or entry.get("numero_sei") or entry.get("numero_documento")
            if not sign_url or not doc_ref:
                result["errors"].append(
                    f"Documento do bloco sem URL de assinatura: seq={entry.get('seq')}"
                )
                continue
            item_result = self._execute_block_sign(
                str(sign_url),
                str(doc_ref),
                detail_html=rd.text,
                checkbox_value=entry.get("checkbox_value"),
            )
            result["doc_ids"].append(str(doc_ref))
            result["signed"].extend([str(item) for item in item_result.get("signed", []) if item])
            result["already_signed"].extend(
                [str(item) for item in item_result.get("already_signed", []) if item]
            )
            if item_result.get("error"):
                result["errors"].append(str(item_result["error"]))
            for error in item_result.get("errors", []) or []:
                if error:
                    result["errors"].append(str(error))

        verification_docs = self.get_block_documents(block_numero)
        selected_refs = {
            ref
            for entry in selected_entries
            for ref in (
                entry.get("documento_id"),
                entry.get("numero_sei"),
                entry.get("numero_documento"),
                entry.get("checkbox_value"),
            )
            if ref
        }
        still_signable = [
            doc
            for doc in verification_docs
            if doc.can_sign
            and any(
                ref and ref in {
                    doc.documento_id or "",
                    doc.numero_sei or "",
                    doc.numero_documento or "",
                }
                for ref in selected_refs
            )
        ]
        result["post_verification"] = {
            "selected_refs": sorted(selected_refs),
            "remaining_signable_refs": [
                doc.documento_id or doc.numero_sei or doc.numero_documento
                for doc in still_signable
            ],
            "method": "block_documents_can_sign",
        }
        if not still_signable:
            result["errors"] = []
            for entry in selected_entries:
                doc_ref = (
                    entry.get("documento_id")
                    or entry.get("numero_sei")
                    or entry.get("numero_documento")
                )
                if doc_ref and doc_ref not in result["signed"]:
                    result["signed"].append(str(doc_ref))
        elif not result.get("already_signed"):
            result["errors"].append("Documentos permanecem assináveis após releitura do bloco.")
        return result

    def _execute_sign(self, sign_url: str, doc_id: str) -> dict:
        """GET the sign page and submit it."""
        rs = self._get(sign_url)
        ssoup = BeautifulSoup(rs.text, "lxml")
        form = ssoup.find("form", {"id": "frmAssinaturas"})
        if not form:
            return {"error": "Formulário de assinatura não encontrado"}
        return self._execute_sign_form(form, rs.text)

    def _execute_sign_form(self, form: Any, page_html: str) -> dict:
        """Submit a sign form with credentials.

        The SEI sign form uses ISO-8859-1 encoding (º → \\xba).
        """
        import logging
        from urllib.parse import urlencode as _urlencode

        _log = logging.getLogger(__name__)

        creds = load_credentials()
        form_action = urljoin(self._sei_url(""), form.get("action", ""))

        # Collect form fields (SEI pre-populates txtUsuario/hdnIdUsuario and
        # may also preselect selCargoFuncao in a <select>).
        sign_data = {}
        for inp in form.find_all("input"):
            n = inp.get("name", "")
            if n:
                sign_data[n] = inp.get("value", "")
        for select in form.find_all("select"):
            name = select.get("name", "")
            if not name:
                continue
            selected = select.find("option", selected=True)
            if selected is None:
                for option in select.find_all("option"):
                    value = (option.get("value") or "").strip()
                    if value and value.casefold() != "null":
                        selected = option
                        break
            if selected is not None:
                sign_data[name] = selected.get("value", "")

        # selCargoFuncao: use value from credentials.json (campo "cargo").
        # Must be encoded as ISO-8859-1 (º → \xba).
        # Example: "Tenente-Coronel QOEM BM"
        cargo = creds.cargo if creds.cargo else sign_data.get("selCargoFuncao", "")

        sign_data.update({
            "pwdSenha": creds.senha,
            "selOrgao": orgao_to_value(creds.orgao),
            "selCargoFuncao": cargo,
            "hdnFormaAutenticacao": "S",
        })

        doc_ids = sign_data.get("hdnIdDocumentos", "")

        # Encode as ISO-8859-1
        body = _urlencode(
            list(sign_data.items()),
            encoding="iso-8859-1",
        )

        r = self.client.post(
            form_action,
            content=body.encode("iso-8859-1"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        r = auth._follow(self.client, r, self.base_url)
        with contextlib.suppress(Exception):
            r.encoding = "iso-8859-1"

        # Debug logging for diagnosis
        _log.debug("[sign] response URL: %s", r.url)
        _log.debug("[sign] response status: %s", r.status_code)
        _log.debug("[sign] response text (first 500): %s", r.text[:500])

        # Parse response for messages
        result: dict = {"doc_ids": doc_ids, "signed": [], "already_signed": [], "errors": []}

        # Check for server messages — SEI uses multiple patterns:
        #   <div class="alert...">  (newer SEI)
        #   <div id="divInfraMsg..."> or class="infraMensagem" (older SEI)
        #   Plain text with "já foi assinado" anywhere on page
        msg_patterns = [
            r'<div[^>]*class="alert[^"]*"[^>]*>.*?</div>',
            r'<div[^>]*class="infraMensagem[^"]*"[^>]*>.*?</div>',
            r'<div[^>]*id="divInfraMsg[^"]*"[^>]*>.*?</div>',
        ]
        found_messages = set()
        for pattern in msg_patterns:
            for msg in re.findall(pattern, r.text, re.DOTALL):
                text = BeautifulSoup(msg, "lxml").text.strip()
                if text and text not in found_messages:
                    found_messages.add(text)

        plain_text = BeautifulSoup(r.text, "lxml").get_text(" ", strip=True)

        # Also check for inline "já foi assinado" which may not be in a standard div
        if "já foi assinado" in plain_text.lower() and not any("já foi assinado" in m.lower() for m in found_messages):
            match = re.search(
                r"\bDocumento\s+\d+\s+já\s+foi\s+assinado.*?(?:\.|$)",
                plain_text,
                re.IGNORECASE,
            )
            if match:
                found_messages.add(match.group().strip())
            else:
                found_messages.add("Documento já foi assinado.")

        conference_match = re.search(
            r"Documento\s+\d+\s+não\s+possui\s+Tipo\s+de\s+Confer[êe]ncia\s+informada\.?",
            plain_text,
            re.IGNORECASE,
        )
        if conference_match:
            found_messages.add(conference_match.group().strip())

        for text in found_messages:
            if "já foi assinado" in text:
                result["already_signed"].append(text)
            elif "tipo de confer" in text.lower():
                result["errors"].append(text)
            elif "assinado com sucesso" in text.lower():
                result["signed"].append(doc_ids)
            elif "erro" in text.lower() or "incorret" in text.lower():
                result["errors"].append(text)

        returned_to_sign_form = (
            "acao=documento_assinar" in str(r.url)
            or 'id="frmAssinaturas"' in r.text
        )

        # Check if redirected to blocos list (success — signed and returned)
        if "Blocos de Assinatura" in r.text and not result["signed"] and not result["already_signed"]:
            result["signed"].append(doc_ids)

        # Check if redirected back to process page (success for individual doc sign/auth)
        if "procedimento_trabalhar" in str(r.url) and not result["signed"] and not result["already_signed"] and not result["errors"]:
            result["signed"].append(doc_ids)

        # Check if redirected to process tree (success — SEI returns to arvore after signing)
        if "arvore_visualizar" in str(r.url) and not result["signed"] and not result["already_signed"] and not result["errors"]:
            result["signed"].append(doc_ids)

        # Check for explicit success patterns in SEI response text
        if not result["signed"] and not result["already_signed"] and not result["errors"]:
            success_patterns = [
                "assinatura realizada",
                "documento assinado",
                "autenticação realizada",
            ]
            if any(p in r.text.lower() for p in success_patterns):
                result["signed"].append(doc_ids)

        # IMPORTANT: returning to frmAssinaturas is not conclusive by itself.
        # The server may still have applied the signature, and callers can
        # verify externally by re-reading the block/document state.
        if not result["signed"] and not result["already_signed"] and not result["errors"]:
            if "já foi assinado" in r.text:
                match = re.search(r'\bDocumento\s+\d+\s+já\s+foi\s+assinado[^<]*', r.text, re.IGNORECASE)
                msg = match.group().strip() if match else f"Documentos {doc_ids} já assinados"
                result["already_signed"].append(msg)
            elif not returned_to_sign_form:
                result["signed"].append(doc_ids)
        result["returned_to_sign_form"] = returned_to_sign_form

        self._control_html = None
        return result
