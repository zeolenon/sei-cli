from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class Credentials:
    usuario: str
    senha: str
    orgao: str
    login_url: str
    cargo: str = ""
    id_usuario: str = ""


@dataclass(slots=True)
class LoginForm:
    action: str


@dataclass(slots=True)
class LoginStatus:
    success: bool
    message: str
    current_url: str | None = None


@dataclass(slots=True)
class Process:
    numero: str
    tipo: str
    especificacao: str
    id_procedimento: str | None
    link: str
    novo: bool
    atribuido: str | None = None
    marcador: str | None = None
    caixa: str = "recebidos"


@dataclass(slots=True)
class ProcessList:
    recebidos: list[Process] = field(default_factory=list)
    gerados: list[Process] = field(default_factory=list)


@dataclass(slots=True)
class SystemStatus:
    valid: bool
    unidade_sigla: str | None = None
    unidade_descricao: str | None = None
    usuario: str | None = None
    ultimo_acesso: str | None = None


@dataclass(slots=True)
class Unit:
    sigla: str
    descricao: str
    link: str | None = None


@dataclass(slots=True)
class Document:
    numero: str
    nome: str
    tipo: str  # "interno", "externo", "processo"
    id_documento: str | None = None
    link: str | None = None
    assinado: bool = False


@dataclass(slots=True)
class BlockDocument:
    seq: str
    processo: str
    documento_id: str
    tipo_documento: str
    assinante: str
    assinado: bool = False
    link_processo: str | None = None
    link_documento: str | None = None


@dataclass(slots=True)
class Block:
    numero: str
    estado: str
    unidade_origem: str
    unidade_destino: str
    descricao: str
    link: str | None = None
    documentos: list[BlockDocument] = field(default_factory=list)


@dataclass(slots=True)
class ProcessDetails:
    processo_numero: str
    processo_link: str
    documentos: list[Document] = field(default_factory=list)


@dataclass(slots=True)
class DocumentType:
    """A type of document available for creation (e.g., Despacho, Ofício)."""
    id_serie: str
    nome: str


@dataclass(slots=True)
class EditorSection:
    """A section of a document in the CKEditor form."""
    name: str         # e.g. txaEditor_406
    content: str      # HTML content
    section_id: str   # e.g. 406


@dataclass(slots=True)
class DocumentCreated:
    """Result of creating a new document."""
    id_documento: str
    id_procedimento: str
    tipo: str
    editor_url: str | None = None


@dataclass(slots=True)
class TreeFolder:
    """A lazy-loaded folder in the SEI document tree."""
    folder_id: str          # e.g. "PASTA1"
    index: int              # e.g. 1
    label: str              # e.g. "Pasta I (10)"
    link: str               # POST URL for expansion
    protocolos: str         # comma-separated protocol IDs
    carregado: bool         # whether already loaded


@dataclass(slots=True)
class TreeDocument:
    """A document node from the SEI tree (with download/view info)."""
    id_documento: str
    nome: str
    tipo: str                          # "interno", "externo", "pdf", "documento"
    parent_folder: str | None = None   # e.g. "PASTA1"
    arvore_url: str | None = None      # arvore_visualizar URL
    src_url: str | None = None         # documento_visualizar or documento_download_anexo
    html_content: str | None = None    # inline HTML (for docs with viewer info)
    sei_number: str | None = None      # SEI document number from title
    assinado: bool = False             # True if doc icon indicates signed status


@dataclass(slots=True)
class TramitarDestino:
    id_unidade: str
    nome: str


@dataclass(slots=True)
class TramitarForm:
    action: str
    hidden_fields: dict[str, str]
    select_fields: dict[str, str]
    destino_field: str
    manter_aberto_field: str | None
    destinos: list[TramitarDestino] = field(default_factory=list)


@dataclass(slots=True)
class Marcador:
    marcador_id: str
    nome: str
    descricao: str = ""
    cor: str | None = None
    link: str | None = None


@dataclass(slots=True)
class MarcadorForm:
    action: str
    hidden_fields: dict[str, str]
    select_fields: dict[str, str]
    marcador_field: str
    texto_field: str | None
    marcadores: list[Marcador] = field(default_factory=list)


def as_json(data: Any) -> Any:
    if hasattr(data, "__dataclass_fields__"):
        return asdict(data)
    if isinstance(data, list):
        return [as_json(item) for item in data]
    return data
