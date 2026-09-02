"""SEI HTML parsers.

The SEI interface uses server-rendered HTML with JS-enhanced navigation.
Key patterns:
- Process tree: ifrArvore iframe with JS array `Nos[]` = new infraArvoreNo(...)
- Blocks: standard HTML tables with infraTrClara/infraTrEscura rows
- Unit switch: table with infraArvoreNo-like rows
"""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import parse_qs, urljoin, urlparse

from lxml import html

from sei_cli.models import (
    Block, BlockDocument, Document, LoginForm, Process,
    Marcador, MarcadorForm, ProcessDetails, ProcessHistoryEntry, ProcessList, SystemStatus,
    SignatureInfo, TramitarDestino, TramitarForm, TreeDocument, TreeFolder, Unit,
)


def _tree(content: str) -> html.HtmlElement:
    if not content or not content.strip():
        raise ValueError("Empty HTML content")
    return html.fromstring(content)


def _norm(text: str | None) -> str:
    return (text or "").replace("\xa0", " ").strip()


def _extract_id(link: str, param: str = "id_procedimento") -> str | None:
    parsed = urlparse(link)
    values = parse_qs(parsed.query).get(param)
    return values[0] if values else None


def _split_js_args(raw: str) -> list[str]:
    args: list[str] = []
    buf: list[str] = []
    in_string = False
    quote = ""
    escape = False

    for ch in raw:
        if in_string:
            buf.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                in_string = False
        else:
            if ch in ("'", '"'):
                in_string = True
                quote = ch
                buf.append(ch)
            elif ch == ",":
                args.append("".join(buf).strip())
                buf = []
            else:
                buf.append(ch)

    if buf:
        args.append("".join(buf).strip())
    return args


def _decode_js_string(token: str) -> str:
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"'):
        token = token[1:-1]
    token = token.replace(r"\'", "'").replace(r"\"", '"').replace(r"\\", "\\")
    token = token.replace(r"\n", "\n").replace(r"\r", "\r").replace(r"\t", "\t")
    return unescape(token)


def parse_tree_signatures(content: str) -> dict[str, list[SignatureInfo]]:
    signatures: dict[str, list[SignatureInfo]] = {}

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if "NosAcoes[" not in line or "new infraArvoreAcao(" not in line:
            continue
        match = re.match(r"NosAcoes\[\d+\]\s*=\s*new\s+infraArvoreAcao\((.*)\);\s*$", line)
        if not match:
            continue
        args = _split_js_args(match.group(1))
        if len(args) < 7:
            continue
        action_type = _decode_js_string(args[0])
        if action_type != "ASSINATURA":
            continue

        id_documento = _decode_js_string(args[2]).strip()
        if not id_documento:
            continue

        tooltip = _decode_js_string(args[5]).strip()
        icon = _decode_js_string(args[6]).strip()
        kind = "autenticacao" if "autenticacao" in icon.lower() else "assinatura"

        tooltip = re.sub(r"^(Assinado por:|Autenticado por:)\n?", "", tooltip, flags=re.I).strip()
        signer_blocks = [block.strip() for block in tooltip.split("\n\n") if block.strip()]
        parsed_signers: list[SignatureInfo] = []
        for block in signer_blocks:
            lines = [line.strip() for line in block.splitlines() if line.strip()]
            if len(lines) < 3:
                continue
            parsed_signers.append(
                SignatureInfo(
                    signer=lines[0],
                    role=lines[1],
                    unit=lines[2],
                    kind=kind,
                    icon=icon,
                )
            )
        if parsed_signers:
            signatures[id_documento] = parsed_signers

    return signatures


# --- Login ---

def parse_login_form(content: str, current_url: str) -> LoginForm:
    page = _tree(content)
    form = page.xpath("//form[@id='frmLogin']")
    if not form:
        form = page.xpath("//form")
    if not form:
        raise ValueError("Formulário de login não encontrado")
    action = form[0].attrib.get("action", "")
    return LoginForm(action=urljoin(current_url, action))


# --- System status ---

def parse_system_status(content: str) -> SystemStatus:
    page = _tree(content)
    valid = bool(
        page.xpath("//title[contains(., 'Controle de Processos')]")
        or "Controle de Processos" in (page.text_content() or "")[:5000]
    )

    unit_el = page.xpath("//a[@id='lnkInfraUnidade']")
    unidade_sigla = _norm(unit_el[0].text_content()) if unit_el else None
    unidade_desc = _norm(unit_el[0].attrib.get("title")) if unit_el else None

    user_el = page.xpath("//a[@id='lnkUsuarioSistema']")
    usuario = _norm(user_el[0].attrib.get("title")) if user_el else None

    ultimo = page.xpath("//div[@id='divInfraBarraAcesso']//a/text()")
    ultimo_acesso = _norm(ultimo[0]) if ultimo else None

    return SystemStatus(
        valid=valid,
        unidade_sigla=unidade_sigla,
        unidade_descricao=unidade_desc,
        usuario=usuario,
        ultimo_acesso=ultimo_acesso,
    )


# --- Processes ---

def _parse_process_row(row: html.HtmlElement, caixa: str, base_url: str) -> Process | None:
    anchors = row.xpath(
        "./td//a[contains(@class, 'processoVisualizado') "
        "or contains(@class, 'processoNaoVisualizado')]"
    )
    if not anchors:
        return None

    el = anchors[0]
    link = urljoin(base_url, el.attrib.get("href", ""))
    numero = _norm(el.text_content()).replace(" ", "")
    aria = _norm(el.attrib.get("aria-label"))

    tipo, especificacao = aria, ""
    if " / " in aria:
        tipo, especificacao = aria.split(" / ", 1)

    novo = "processoNaoVisualizado" in (el.attrib.get("class") or "")
    recente = bool(
        row.xpath(
            "./td//a[contains(@aria-label,'Um documento foi incluído ou assinado neste processo')]"
        )
    )

    atrib = row.xpath("./td/a[contains(@class, 'ancoraSigla')]/text()")
    atribuido = _norm(atrib[0]) if atrib else None

    marcador = row.xpath("./td/a[contains(@aria-label,'Marcador')]/@aria-label")
    marcador_val = _norm(unescape(marcador[0])) if marcador else None

    return Process(
        numero=numero,
        tipo=tipo,
        especificacao=especificacao,
        id_procedimento=_extract_id(link),
        link=link,
        novo=novo,
        recente=recente,
        atribuido=atribuido,
        marcador=marcador_val,
        caixa=caixa,
    )


def parse_processes(content: str, base_url: str) -> ProcessList:
    page = _tree(content)

    # Validate we're actually on the control page
    tables_found = 0
    for table_id in ("tblProcessosRecebidos", "tblProcessosGerados"):
        if page.xpath(f"//table[@id='{table_id}']"):
            tables_found += 1

    if tables_found == 0:
        # Not on the control page — likely a login page or error page
        if "login" in content.lower() or "txtUsuario" in content:
            raise RuntimeError(
                "Sessão expirada — página de login retornada em vez do controle de processos"
            )
        raise RuntimeError(
            "Página de controle de processos não encontrada (tabelas ausentes). "
            "Verifique se a sessão está ativa e na unidade correta."
        )

    recebidos, gerados = [], []
    for table_id, caixa, dest in [
        ("tblProcessosRecebidos", "recebidos", recebidos),
        ("tblProcessosGerados", "gerados", gerados),
    ]:
        for row in page.xpath(f"//table[@id='{table_id}']//tr[starts-with(@id, 'P')]"):
            p = _parse_process_row(row, caixa, base_url)
            if p:
                dest.append(p)
    return ProcessList(recebidos=recebidos, gerados=gerados)


# --- Document tree (from iframe JS) ---

# Pattern: new infraArvoreNo("TYPE","id_doc","id_proc","url","iframe","title1","title2",...)
_ARVORE_RE = re.compile(
    r'new\s+infraArvoreNo\('
    r'"(\w+)",'              # tipo: PROCESSO, DOCUMENTO, PASTA, AGUARDE
    r'"([^"]+)",'            # id (numeric or string like PASTA7)
    r'(?:"([^"]+)"|null),'   # parent id (numeric, string, or null)
    r'"([^"]+)",'            # url
    r'"([^"]*)",'            # iframe target
    r'"([^"]*)",'            # title/label
    r'"([^"]*)"'             # title2
)


def parse_document_tree(content: str, base_url: str) -> list[Document]:
    """Parse the ifrArvore iframe content to extract document list."""
    docs = []
    for m in _ARVORE_RE.finditer(content):
        tipo_raw, doc_id, _parent_id, url, _iframe, title, _title2 = m.groups()
        if tipo_raw in ("PROCESSO", "PASTA", "AGUARDE"):
            continue
        # Determine document type from icon in the full match context
        full_line = content[m.start():m.start() + 500]
        if "documento_externo" in full_line:
            tipo = "externo"
        elif "documento_interno" in full_line:
            tipo = "interno"
        else:
            tipo = "documento"
        
        # Detect signed status from icon/src in context around this node
        # SEI uses icon filenames like "documento_assinado" or "protocolo_documento_assinado"
        assinado = "assinado" in full_line.lower()

        docs.append(Document(
            numero=doc_id,
            nome=_norm(title),
            tipo=tipo,
            id_documento=doc_id,
            link=urljoin(base_url, url),
            assinado=assinado,
        ))
    return docs


def parse_tree_folders(content: str) -> list[TreeFolder]:
    """Parse folder definitions (Pastas[N]) and carregado status from arvore JS.

    Returns list of TreeFolder with link, protocolos, and loaded status.
    """
    folders: list[TreeFolder] = []

    # Extract Pastas[N]['link'] and Pastas[N]['protocolos']
    links: dict[int, str] = {}
    protos: dict[int, str] = {}
    for m in re.finditer(r"Pastas\[(\d+)\]\['link'\]\s*=\s*'([^']+)'", content):
        links[int(m.group(1))] = m.group(2)
    for m in re.finditer(r"Pastas\[(\d+)\]\['protocolos'\]\s*=\s*'([^']+)'", content):
        protos[int(m.group(1))] = m.group(2)

    # Extract carregado status per Nos[] index
    carregado_map: dict[int, bool] = {}
    for m in re.finditer(r'Nos\[(\d+)\]\.carregado\s*=\s*(true|false)', content):
        carregado_map[int(m.group(1))] = m.group(2) == 'true'

    # Match Nos[N] PASTA nodes to get folder_id and label
    pasta_idx = 0
    for m in re.finditer(r'Nos\[(\d+)\]\s*=\s*new\s+infraArvoreNo\(([^;]+)\)', content):
        nos_idx = int(m.group(1))
        params_raw = m.group(2)
        params = re.findall(r'"([^"]*)"', params_raw)
        if len(params) >= 2 and params[0] == 'PASTA':
            folder_id = params[1]  # e.g. "PASTA1"
            idx_num = int(folder_id.replace('PASTA', ''))
            label = params[5] if len(params) > 5 else folder_id
            loaded = carregado_map.get(nos_idx, True)

            if idx_num in links:
                folders.append(TreeFolder(
                    folder_id=folder_id,
                    index=idx_num,
                    label=label,
                    link=links[idx_num],
                    protocolos=protos.get(idx_num, ''),
                    carregado=loaded,
                ))

    return folders


def parse_expanded_folder(content: str, base_url: str = '') -> list[TreeDocument]:
    """Parse the AJAX response from expanding a lazy-loaded folder.

    The response starts with 'OK\\n' followed by JS statements defining
    Nos[] nodes with .src and .html properties.

    Returns list of TreeDocument with download/view URLs.
    """
    if content.startswith('OK'):
        content = content[2:].lstrip('\n')

    docs: list[TreeDocument] = []
    signature_map = parse_tree_signatures(content)
    origin_map: dict[str, tuple[str | None, str | None]] = {}
    for match in re.finditer(
        r'new\s+infraArvoreAcao\("UNIDADE_GERADORA",'
        r'"UG(\d+)","(\d+)","[^"]*",[^,]*,"([^"]*)",[^,]*,[^,]*,"([^"]*)"\)',
        content,
    ):
        _ug_id, doc_id, description, unit = match.groups()
        origin_map[doc_id] = (unit or None, description or None)
    lines = content.split('\n')

    # Parse all Nos[N] definitions and their .src/.html assignments
    nodes: dict[int, dict] = {}

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Match node creation: Nos[N] = new infraArvoreNo(...)
        m = re.match(r'Nos\[(\d+)\]\s*=\s*new\s+infraArvoreNo\((.+)\);?$', line)
        if m:
            idx = int(m.group(1))
            params = re.findall(r'"([^"]*)"', m.group(2))
            if len(params) >= 6:
                nodes.setdefault(idx, {})
                nodes[idx]['tipo_raw'] = params[0]
                nodes[idx]['id'] = params[1]
                nodes[idx]['parent'] = params[2]
                nodes[idx]['arvore_url'] = params[3]
                nodes[idx]['target'] = params[4]
                nodes[idx]['nome'] = params[5]
                nodes[idx]['label'] = params[6] if len(params) > 6 else params[5]
                # Detect type from icon (also store for signed detection)
                icon = params[7] if len(params) > 7 else ''
                nodes[idx]['icon'] = icon
                if 'documento_pdf' in icon:
                    nodes[idx]['tipo'] = 'pdf'
                elif 'documento_externo' in icon:
                    nodes[idx]['tipo'] = 'externo'
                elif 'documento_interno' in icon:
                    nodes[idx]['tipo'] = 'interno'
                else:
                    nodes[idx]['tipo'] = 'documento'
                # Extract SEI number from name (e.g. "Despacho 35516263")
                sei_m = re.search(r'\((\d+)\)$', params[5])
                if sei_m:
                    nodes[idx]['sei_number'] = sei_m.group(1)
                else:
                    sei_m2 = re.search(r'\s(\d{8,})$', params[5])
                    if sei_m2:
                        nodes[idx]['sei_number'] = sei_m2.group(1)
            continue

        # Match .src assignment
        m = re.match(r"Nos\[(\d+)\]\.src\s*=\s*'([^']+)';?$", line)
        if m:
            idx = int(m.group(1))
            nodes.setdefault(idx, {})
            nodes[idx]['src_url'] = m.group(2)
            continue

        # Match .html assignment (can be multi-line, but usually single)
        m = re.match(r"Nos\[(\d+)\]\.html\s*=\s*'(.*?)';?$", line)
        if m:
            idx = int(m.group(1))
            nodes.setdefault(idx, {})
            html_val = m.group(2)
            if html_val:
                nodes[idx]['html_content'] = html_val
            continue

    for idx in sorted(nodes.keys()):
        node = nodes[idx]
        if node.get('tipo_raw') not in ('DOCUMENTO',):
            continue

        # Clean HTML tags from name
        nome_clean = re.sub(r'<[^>]+>', '', node.get('nome', '')).strip()

        # Ensure sei_number is populated: try multiple patterns
        sei_number = node.get('sei_number')
        if not sei_number:
            # Try extracting from id field (often the numeric doc id IS the sei_number)
            doc_id = node.get('id', '')
            if doc_id and doc_id.isdigit() and len(doc_id) >= 7:
                sei_number = doc_id
            # Try extracting from label
            if not sei_number:
                label = node.get('label', '')
                m = re.search(r'(\d{7,})', label)
                if m:
                    sei_number = m.group(1)

        assinaturas = signature_map.get(node.get('id', ''), [])
        assinado = any(sig.kind == 'assinatura' for sig in assinaturas)
        autenticado = any(sig.kind == 'autenticacao' for sig in assinaturas)
        origin_unit, origin_description = origin_map.get(node.get('id', ''), (None, None))

        docs.append(TreeDocument(
            id_documento=node.get('id', ''),
            nome=nome_clean,
            tipo=node.get('tipo', 'documento'),
            parent_folder=node.get('parent', ''),
            arvore_url=urljoin(base_url, node['arvore_url']) if node.get('arvore_url') else None,
            src_url=urljoin(base_url, node['src_url']) if node.get('src_url') else None,
            html_content=node.get('html_content'),
            sei_number=sei_number,
            origin_unit=origin_unit,
            origin_description=origin_description,
            assinado=assinado,
            autenticado=autenticado,
            assinaturas=assinaturas,
        ))

    return docs


def parse_process_history(content: str) -> list[ProcessHistoryEntry]:
    """Parse the full process-history table rendered by SEI.

    SEI has changed the table id and CSS classes between installations, so the
    parser intentionally uses the stable four-column layout: date/time, unit,
    user and description. Header/pager rows are ignored.
    """
    page = _tree(content)
    entries: list[ProcessHistoryEntry] = []
    date_pattern = re.compile(r"^\d{2}/\d{2}/\d{4}(?:\s+\d{2}:\d{2}(?::\d{2})?)?$")
    for row in page.xpath("//tr"):
        cells = row.xpath("./td")
        if len(cells) < 4:
            continue
        values = [_norm(cell.text_content()) for cell in cells[:4]]
        if not values[0] or not date_pattern.match(values[0]):
            continue
        if not any(values[1:]):
            continue
        entries.append(
            ProcessHistoryEntry(
                date_time=values[0],
                unit=values[1],
                user=values[2],
                description=values[3],
            )
        )
    return entries


# --- Blocks ---

def parse_blocks(content: str, base_url: str) -> list[Block]:
    """Parse blocos de assinatura list page.
    
    Table columns: checkbox | numero | icons | icons | estado | unidade_origem | unidade_destino | icons | descricao | actions
    """
    page = _tree(content)
    blocks = []
    rows = page.xpath(
        "//tr[contains(@class,'infraTrClara') or contains(@class,'infraTrEscura') or contains(@class,'trVermelha')]"
    )
    
    for row in rows:
        tds = row.xpath("./td")
        if len(tds) < 8:
            continue
        
        numero = _norm(tds[1].text_content())
        if not numero or not numero.isdigit():
            continue
        
        estado = _norm(tds[4].text_content())
        unidade_origem = _norm(tds[5].text_content())
        dest_units = [
            label
            for div in tds[6].xpath('.//div[contains(@class,"divUnidadeRotulo")]')
            if (label := _norm(div.text_content()))
        ]
        if not dest_units:
            # When no divUnidadeRotulo divs exist, units may be in separate
            # child elements (spans, divs, anchors) or separated by <br>.
            # First try to extract from individual child elements.
            child_texts = []
            for child in tds[6]:
                if child.tag == 'br':
                    continue
                t = _norm(child.text_content()).replace("Aguardando Devolução", "").strip()
                if t:
                    child_texts.append(t)
            if child_texts:
                dest_units = child_texts
            else:
                # Fallback: insert separator before <br> tags, then split
                from lxml.html import tostring as _html_tostring
                raw_html = _html_tostring(tds[6], encoding='unicode')
                raw_html = re.sub(r'<br\s*/?>', '\n', raw_html, flags=re.IGNORECASE)
                parts = [
                    _norm(p).replace("Aguardando Devolução", "").strip()
                    for p in raw_html.split('\n')
                ]
                dest_units = [p for p in parts if p]
            if not dest_units:
                raw_dest = _norm(tds[6].text_content()).replace("Aguardando Devolução", "").strip()
                dest_units = [raw_dest] if raw_dest else []
        unidade_destino = "; ".join(dest_units)
        descricao = _norm(tds[8].text_content()) if len(tds) > 8 else ""
        
        # Link to bloco detail
        link_el = tds[1].xpath(".//a[@href]")
        link = urljoin(base_url, link_el[0].attrib.get("href", "")) if link_el else None
        
        blocks.append(Block(
            numero=numero,
            estado=estado,
            unidade_origem=unidade_origem,
            unidade_destino=unidade_destino,
            unidades_destino=dest_units,
            descricao=descricao,
            link=link,
        ))
    
    return blocks


def parse_block_documents(content: str, base_url: str) -> list[BlockDocument]:
    """Parse documents inside a bloco de assinatura.
    
    Table: seq | processo | documento_id | tipo | assinante | status icons
    """
    page = _tree(content)
    docs = []
    rows = page.xpath("//tr[contains(@class,'infraTrClara') or contains(@class,'infraTrEscura')]")
    
    for row in rows:
        tds = row.xpath("./td")
        if len(tds) < 5:
            continue

        def _cell_lines(td: object) -> list[str]:
            labels = [
                _norm(div.text_content())
                for div in td.xpath('.//div[contains(@class,"divRotuloItemCelula") or contains(@class,"divUnidadeRotulo")]')
            ]
            labels = [item for item in labels if item]
            if labels:
                return labels

            values: list[str] = []
            for raw in td.xpath(".//text()"):
                text = _norm(str(raw))
                if not text:
                    continue
                if text in values:
                    continue
                values.append(text)
            return values

        def _cell_metadata_candidates(td: object) -> list[str]:
            candidates: list[str] = []
            for attr_name in ("title", "aria-label", "onclick", "href"):
                for raw in td.xpath(f".//*[@{attr_name}]/@{attr_name}"):
                    text = _norm(unescape(str(raw)))
                    if not text or text in candidates:
                        continue
                    candidates.append(text)
            for text in _cell_lines(td):
                if text not in candidates:
                    candidates.append(text)
            return candidates

        def _extract_document_number(
            *,
            doc_id: str,
            visible_lines: list[str],
            metadata_candidates: list[str],
        ) -> str | None:
            contextual_patterns = (
                r"\bdocumento\s+sei\s+(\d{6,})\b",
                r"\bsei\s*[#: -]?\s*(\d{6,})\b",
                r"\bn[úu]mero\s+sei\s*[#: -]?\s*(\d{6,})\b",
            )
            for item in metadata_candidates:
                lowered = item.casefold()
                for pattern in contextual_patterns:
                    match = re.search(pattern, lowered, flags=re.IGNORECASE)
                    if match and match.group(1) != doc_id:
                        return match.group(1)

            for item in visible_lines[1:]:
                if re.fullmatch(r"\d{8,}", item) and item != doc_id:
                    return item

            fallback_candidates: list[str] = []
            ignored_query_params = {"infra_sistema", "infra_unidade_atual", "id_procedimento", "id_bloco"}
            for item in metadata_candidates:
                parsed = urlparse(item)
                if parsed.query:
                    query = parse_qs(parsed.query)
                    for key, values in query.items():
                        if key in ignored_query_params or key == "id_documento":
                            continue
                        for value in values:
                            if re.fullmatch(r"\d{8,}", value) and value != doc_id and value not in fallback_candidates:
                                fallback_candidates.append(value)
                    continue
                for match in re.findall(r"\b\d{8,}\b", item):
                    if match != doc_id and match not in fallback_candidates:
                        fallback_candidates.append(match)
            return fallback_candidates[0] if fallback_candidates else None
        
        seq = _norm(tds[1].text_content()) if len(tds) > 1 else ""
        processo = _norm(tds[2].text_content()) if len(tds) > 2 else ""
        document_lines = _cell_lines(tds[3]) if len(tds) > 3 else []
        document_candidates = _cell_metadata_candidates(tds[3]) if len(tds) > 3 else []
        visible_doc_id = document_lines[0] if document_lines else ""

        # Extract real id_documento from href #ID-{id}-{bloco} or ?id_documento=
        # When the real internal ID differs from the visible text, the visible
        # text is the numero_sei.
        real_doc_id = visible_doc_id
        numero_sei_value: str | None = None
        for anchor in row.xpath(".//a[@href]"):
            href = anchor.attrib.get("href", "")
            id_match = re.search(r"#ID-(\d+)-\d+", href)
            if id_match:
                real_doc_id = id_match.group(1)
                if real_doc_id != visible_doc_id and re.fullmatch(r"\d+", visible_doc_id):
                    numero_sei_value = visible_doc_id
                break
            id_doc_match = re.search(r"[?&]id_documento=(\d+)", href)
            if id_doc_match:
                real_doc_id = id_doc_match.group(1)
                break

        doc_id = real_doc_id
        numero_documento = _extract_document_number(
            doc_id=doc_id,
            visible_lines=document_lines,
            metadata_candidates=document_candidates,
        )
        data_documento = None

        for item in document_candidates:
            if data_documento is None:
                date_match = re.search(r"\b\d{2}/\d{2}/\d{4}\b", item)
                if date_match:
                    data_documento = date_match.group(0)
                    break
        tipo_doc = _norm(tds[4].text_content()) if len(tds) > 4 else ""
        assinantes = _cell_lines(tds[5]) if len(tds) > 5 else []
        assinante = "; ".join(assinantes)

        sign_action_present = False
        for anchor in row.xpath(".//a[@onclick] | .//a[@href]"):
            onclick = anchor.attrib.get("onclick", "")
            href = anchor.attrib.get("href", "")
            if "acaoAssinar" in onclick or "documento_assinar" in onclick or "documento_assinar" in href:
                sign_action_present = True
                break

        # Full signed state for the current unit should only be true when
        # there is a signature marker and no remaining sign action on the row.
        imgs = row.xpath(".//img[@title]")
        has_signature_marker = any("Assinatura" in (i.attrib.get("title", "")) for i in imgs)
        assinado = has_signature_marker and not sign_action_present
        
        bd_kwargs: dict[str, Any] = dict(
            seq=seq,
            processo=processo,
            documento_id=doc_id,
            tipo_documento=tipo_doc,
            assinante=assinante,
            assinantes=assinantes,
            numero_documento=numero_documento,
            data_documento=data_documento,
            assinado=assinado,
            can_sign=sign_action_present,
        )
        if numero_sei_value:
            bd_kwargs["numero_sei"] = numero_sei_value
        docs.append(BlockDocument(**bd_kwargs))
    
    return docs


# --- Units ---

def parse_unit_switch_link(content: str, base_url: str) -> str | None:
    """Extract the 'trocar unidade' URL from the control page."""
    page = _tree(content)
    el = page.xpath("//a[@id='lnkInfraUnidade']")
    if not el:
        return None
    onclick = el[0].attrib.get("onclick", "")
    match = re.search(r"href='([^']+)'", onclick)
    if not match:
        return None
    return urljoin(base_url, match.group(1))


def parse_units_switch_page(content: str, base_url: str) -> list[Unit]:
    """Parse the unit switch page.
    
    Structure: form with radio buttons (chkInfraItem=<unit_id>) and table rows.
    Switching uses JS: creates hidden 'selInfraUnidades' field and submits form.
    We return the form action URL and unit IDs so the client can POST directly.
    """
    page = _tree(content)
    units = []
    
    # Get radio buttons (unit IDs)
    radios = page.xpath("//input[@type='radio' and @name='chkInfraItem']")
    radio_values = [r.attrib.get("value", "") for r in radios]
    
    # Get table rows (sigla, descricao)
    rows = page.xpath("//tr[contains(@class,'infraTrClara') or contains(@class,'infraTrEscura')]")
    
    for i, row in enumerate(rows):
        tds = row.xpath("./td")
        if len(tds) < 3:
            continue
        
        sigla = _norm(tds[1].text_content())
        descricao = _norm(tds[2].text_content())
        unit_id = radio_values[i] if i < len(radio_values) else None
        
        if not sigla:
            continue
        
        units.append(Unit(sigla=sigla, descricao=descricao, link=unit_id))
    
    return units


def parse_unit_switch_form(content: str) -> tuple[str, dict[str, str]]:
    """Extract form action URL and hidden fields from the switch page.
    
    Returns (form_action, hidden_fields_dict).
    """
    page = _tree(content)
    form = page.xpath("//form[@id='frmInfraSelecaoUnidade']")
    if not form:
        # Might be on a different page (e.g. already redirected to control)
        form = page.xpath("//form")
    if not form:
        raise ValueError("Form de troca de unidade não encontrado")
    
    action = form[0].attrib.get("action", "")
    hiddens = {}
    for inp in form[0].xpath(".//input[@type='hidden']"):
        name = inp.attrib.get("name", "")
        if name:
            hiddens[name] = inp.attrib.get("value", "")
    
    return action, hiddens


# --- Menu links ---

def parse_menu_links(content: str, base_url: str) -> dict[str, str]:
    """Extract menu links from the control page (blocos, pesquisa, etc.)."""
    page = _tree(content)
    links = {}
    for el in page.xpath("//a[@href]"):
        href = el.attrib.get("href", "")
        text = _norm(el.text_content())
        # Key navigation links
        if "bloco_assinatura_listar" in href:
            links["blocos_assinatura"] = urljoin(base_url, href)
        elif "bloco_interno_listar" in href:
            links["blocos_internos"] = urljoin(base_url, href)
        elif "protocolo_pesquisa_rapida" in href:
            links["pesquisa_rapida"] = urljoin(base_url, href)
        elif "marcador_listar" in href:
            links["marcadores"] = urljoin(base_url, href)
    return links


# --- Tramitação ---

def parse_tramitar_form(content: str, base_url: str, current_url: str) -> TramitarForm:
    """Parse the Enviar Processo page form."""
    page = _tree(content)
    form_nodes = page.xpath("//form[@id='frmProcedimentoEnviar'] | //form")
    if not form_nodes:
        raise ValueError("Formulário de tramitação não encontrado")
    form = form_nodes[0]

    action = urljoin(current_url, form.attrib.get("action", ""))
    if not action:
        action = current_url

    hidden_fields: dict[str, str] = {}
    for inp in form.xpath(".//input[@type='hidden']"):
        name = inp.attrib.get("name", "")
        if name:
            hidden_fields[name] = inp.attrib.get("value", "")

    select_fields: dict[str, str] = {}
    destino_field = ""
    destinos: list[TramitarDestino] = []
    for sel in form.xpath(".//select"):
        name = sel.attrib.get("name", "")
        if not name:
            continue
        selected = sel.xpath(".//option[@selected]")
        if selected:
            select_fields[name] = selected[0].attrib.get("value", "")
        else:
            first = sel.xpath(".//option")
            if first:
                select_fields[name] = first[0].attrib.get("value", "")

        opts = []
        for opt in sel.xpath(".//option[@value]"):
            value = _norm(opt.attrib.get("value"))
            label = _norm(opt.text_content())
            if not value or not label:
                continue
            opts.append(TramitarDestino(id_unidade=value, nome=label))

        if opts:
            lname = name.lower()
            if (
                "unidade" in lname
                or "destino" in lname
                or "infraitem" in lname
                or not destino_field
            ):
                destino_field = name
                destinos = opts

    manter_aberto_field = None
    retorno_programado_fields: dict[str, str] = {}
    reabertura_programada_fields: dict[str, str] = {}
    for chk in form.xpath(".//input[@type='checkbox']"):
        name = chk.attrib.get("name", "")
        lname = name.lower()
        if "manter" in lname or "aberto" in lname:
            manter_aberto_field = name
        if "retorno" in lname and "uteis" in lname:
            retorno_programado_fields["uteis"] = name
        if "reabertura" in lname and "uteis" in lname:
            reabertura_programada_fields["uteis"] = name

    for inp in form.xpath(".//input"):
        name = inp.attrib.get("name", "")
        if not name:
            continue
        lname = name.lower()
        itype = inp.attrib.get("type", "").lower()
        if "retorno" in lname:
            if lname.startswith("rdoprazoretornoprogramado"):
                retorno_programado_fields["radio"] = name
            elif "diasretornoprogramado" in lname:
                retorno_programado_fields["dias"] = name
            elif "prazoretornoprogramado" in lname:
                retorno_programado_fields["data"] = name
        if "reabertura" in lname:
            if lname.startswith("rdoprazoreaberturaprogramada"):
                reabertura_programada_fields["radio"] = name
            elif "diasreaberturaprogramada" in lname:
                reabertura_programada_fields["dias"] = name
            elif "prazoreaberturaprogramada" in lname:
                reabertura_programada_fields["data"] = name

    if not destino_field or not destinos:
        raise ValueError("Campo de unidade destino não encontrado na tramitação")

    # Extract AJAX auto-complete URL for unit resolution
    ajax_url: str | None = None
    ajax_match = re.search(
        r"(controlador_ajax\.php\?acao_ajax="
        r"unidade_auto_completar_envio_processo[^']+)",
        content,
    )
    if ajax_match:
        ajax_url = base_url.rstrip("/") + "/" + ajax_match.group(1)

    return TramitarForm(
        action=urljoin(base_url, action),
        hidden_fields=hidden_fields,
        select_fields=select_fields,
        destino_field=destino_field,
        manter_aberto_field=manter_aberto_field,
        retorno_programado_fields=retorno_programado_fields,
        reabertura_programada_fields=reabertura_programada_fields,
        destinos=destinos,
        ajax_url=ajax_url,
    )


# --- Marcadores ---

def parse_marcadores_list(content: str, base_url: str) -> list[Marcador]:
    """Parse marcador list page."""
    page = _tree(content)
    marcadores: list[Marcador] = []
    seen: set[str] = set()

    rows = page.xpath("//tr[contains(@class,'infraTrClara') or contains(@class,'infraTrEscura')]")
    for row in rows:
        tds = row.xpath("./td")
        if len(tds) < 2:
            continue

        marcador_id = ""
        for inp in row.xpath(".//input[@type='checkbox' or @type='radio']"):
            value = _norm(inp.attrib.get("value"))
            if value:
                marcador_id = value
                break

        link = None
        hrefs = row.xpath(".//a[@href]/@href")
        if hrefs:
            link = urljoin(base_url, hrefs[0])
            mid = _extract_id(link, "id_marcador")
            if mid:
                marcador_id = marcador_id or mid

        # Column layout may vary. In the real SEI layout the numeric marker ID
        # may appear as a visible column, so we should avoid treating a pure
        # numeric cell as descrição.
        nome = ""
        descricao = ""
        for idx, td in enumerate(tds):
            text = _norm(td.text_content())
            if text and idx > 0:
                if not nome and not text.isdigit():
                    nome = text
                elif not descricao and not text.isdigit():
                    descricao = text
                    break
        cor = None
        img = row.xpath(".//img[@src]/@src")
        if img:
            cor = img[0].split("/")[-1].replace(".svg", "").split("?")[0]

        if not marcador_id or not nome or marcador_id in seen:
            continue
        seen.add(marcador_id)
        marcadores.append(
            Marcador(
                marcador_id=marcador_id,
                nome=nome,
                descricao=descricao,
                cor=cor,
                link=link,
            )
        )

    return marcadores


def parse_marcador_form(content: str, base_url: str, current_url: str) -> MarcadorForm:
    """Parse marcador management form for a process."""
    page = _tree(content)
    form_nodes = (
        page.xpath("//form[@id='frmAndamentoMarcadorCadastro']")
        or page.xpath("//form[@id='frmAndamentoMarcador']")
        or page.xpath("//form")
    )
    if not form_nodes:
        raise ValueError("Formulário de marcador não encontrado")
    form = form_nodes[0]

    action = urljoin(current_url, form.attrib.get("action", ""))
    if not action:
        action = current_url

    hidden_fields: dict[str, str] = {}
    for inp in form.xpath(".//input[@type='hidden']"):
        name = inp.attrib.get("name", "")
        if name:
            hidden_fields[name] = inp.attrib.get("value", "")

    select_fields: dict[str, str] = {}
    marcador_field = ""
    marcadores: list[Marcador] = []
    for sel in form.xpath(".//select"):
        name = sel.attrib.get("name", "")
        if not name:
            continue
        selected = sel.xpath(".//option[@selected]")
        if selected:
            select_fields[name] = selected[0].attrib.get("value", "")
        else:
            first = sel.xpath(".//option")
            if first:
                select_fields[name] = first[0].attrib.get("value", "")

        opts: list[Marcador] = []
        for opt in sel.xpath(".//option[@value]"):
            value = _norm(opt.attrib.get("value"))
            nome = _norm(opt.text_content())
            if value and nome:
                opts.append(Marcador(marcador_id=value, nome=nome))
        if opts:
            marcador_field = name
            marcadores = opts
            break

    texto_field = None
    for txt in form.xpath(".//textarea[@name]"):
        texto_field = txt.attrib.get("name")
        if texto_field:
            break
    if not texto_field:
        for inp in form.xpath(".//input[@type='text' and @name]"):
            nome = inp.attrib.get("name", "")
            if "txt" in nome.lower():
                texto_field = nome
                break

    if not marcador_field:
        raise ValueError("Campo de seleção de marcador não encontrado")

    return MarcadorForm(
        action=urljoin(base_url, action),
        hidden_fields=hidden_fields,
        select_fields=select_fields,
        marcador_field=marcador_field,
        texto_field=texto_field,
        marcadores=marcadores,
    )
