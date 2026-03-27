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
    Marcador, MarcadorForm, ProcessDetails, ProcessList, SystemStatus,
    TramitarDestino, TramitarForm, TreeDocument, TreeFolder, Unit,
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
        
        docs.append(Document(
            numero=doc_id,
            nome=_norm(title),
            tipo=tipo,
            id_documento=doc_id,
            link=urljoin(base_url, url),
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
                # Detect type from icon
                icon = params[7] if len(params) > 7 else ''
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

        docs.append(TreeDocument(
            id_documento=node.get('id', ''),
            nome=nome_clean,
            tipo=node.get('tipo', 'documento'),
            parent_folder=node.get('parent', ''),
            arvore_url=urljoin(base_url, node['arvore_url']) if node.get('arvore_url') else None,
            src_url=urljoin(base_url, node['src_url']) if node.get('src_url') else None,
            html_content=node.get('html_content'),
            sei_number=sei_number,
        ))

    return docs


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
        # Column 6 may contain "Aguardando Devolução <unit>" for disponibilizado blocks
        raw_dest = _norm(tds[6].text_content())
        unidade_destino = raw_dest.replace("Aguardando Devolução", "").strip() if raw_dest else ""
        descricao = _norm(tds[8].text_content()) if len(tds) > 8 else ""
        
        # Link to bloco detail
        link_el = tds[1].xpath(".//a[@href]")
        link = urljoin(base_url, link_el[0].attrib.get("href", "")) if link_el else None
        
        blocks.append(Block(
            numero=numero,
            estado=estado,
            unidade_origem=unidade_origem,
            unidade_destino=unidade_destino,
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
        
        seq = _norm(tds[1].text_content()) if len(tds) > 1 else ""
        processo = _norm(tds[2].text_content()) if len(tds) > 2 else ""
        doc_id = _norm(tds[3].text_content()) if len(tds) > 3 else ""
        tipo_doc = _norm(tds[4].text_content()) if len(tds) > 4 else ""
        assinante = _norm(tds[5].text_content()) if len(tds) > 5 else ""
        
        # Check if signed (look for 'Assinatura' img with specific title)
        imgs = row.xpath(".//img[@title]")
        assinado = any("Assinatura" in (i.attrib.get("title", "")) for i in imgs)
        
        docs.append(BlockDocument(
            seq=seq,
            processo=processo,
            documento_id=doc_id,
            tipo_documento=tipo_doc,
            assinante=assinante,
            assinado=assinado,
        ))
    
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
    for chk in form.xpath(".//input[@type='checkbox']"):
        name = chk.attrib.get("name", "")
        lname = name.lower()
        if "manter" in lname or "aberto" in lname:
            manter_aberto_field = name
            break

    if not destino_field or not destinos:
        raise ValueError("Campo de unidade destino não encontrado na tramitação")

    return TramitarForm(
        action=urljoin(base_url, action),
        hidden_fields=hidden_fields,
        select_fields=select_fields,
        destino_field=destino_field,
        manter_aberto_field=manter_aberto_field,
        destinos=destinos,
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

        nome = _norm(tds[1].text_content())
        descricao = _norm(tds[2].text_content()) if len(tds) > 2 else ""
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
