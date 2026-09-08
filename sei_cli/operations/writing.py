from __future__ import annotations

import html
from pathlib import Path
import re
import time
from typing import Any

from sei_cli.config import (
    ConfigError,
    append_created_document_log,
    append_created_process_log,
    load_created_documents,
    load_credentials,
)

from .contracts import NextAction
from .errors import TrackingVerificationInconclusiveError, UnsupportedStateError, WorkflowViolationError
from .reading import (
    _context,
    _error_result,
    _list_process_markers,
    _normalize_marker_item,
    _normalize_tracking_group_item,
    _process_in_tracking_list,
    _process_unit_preflight,
    _process_preview,
    _resolve_document_id_with_process,
    _resolve_document_ids,
    _resolve_marker_reference,
    _resolve_process_id,
    _resolve_tracking_group_reference,
    _resolve_visible_process_for_marker,
    _result,
    document_read,
    environment_triage_preview,
    process_marker_read,
    process_marker_preview,
    process_summary,
)


def _best_effort_switch_to_unit(client: Any, unit_sigla: str | None) -> None:
    if not unit_sigla:
        return
    try:
        client.switch_unit(unit_sigla)
    except Exception:
        pass


def _block_document_matches(
    item: dict[str, Any],
    *,
    id_documento: str | None = None,
    numero_documento: str | None = None,
) -> bool:
    expected = {
        str(value).strip()
        for value in (id_documento, numero_documento)
        if value is not None and str(value).strip()
    }
    actual = {
        str(value).strip()
        for value in (
            item.get("documento_id"),
            item.get("numero_sei"),
            item.get("numero_documento"),
        )
        if value is not None and str(value).strip()
    }
    return bool(expected and actual and expected & actual)


def _normalize_process_type_key(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace("-", "_")


def _normalize_person_text(value: str) -> str:
    import unicodedata

    normalized = unicodedata.normalize("NFKD", value or "")
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = re.sub(r"[^a-z0-9\s]", " ", normalized.casefold())
    return re.sub(r"\s+", " ", normalized).strip()


def _strip_institutional_prefixes(value: str) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    if not text:
        return ""
    prefixes = [
        "SEAD",
        "SEI",
        "GOVERNO DO ESTADO",
        "GOVERNO DO ESTADO DO RIO GRANDE DO NORTE",
        "CORPO DE BOMBEIROS MILITAR",
        "CBMRN",
    ]
    changed = True
    while changed and text:
        changed = False
        for prefix in prefixes:
            pattern = re.compile(rf"^\s*{re.escape(prefix)}\s+", re.IGNORECASE)
            updated = pattern.sub("", text).strip()
            if updated != text:
                text = updated
                changed = True
    return text


def _current_signer_profile(client: Any) -> dict[str, str]:
    status = client.status()
    try:
        credentials = load_credentials()
    except Exception:
        credentials = None
    return {
        "usuario": status.usuario or "",
        "usuario_normalized": _normalize_person_text(status.usuario or ""),
        "cargo": getattr(credentials, "cargo", "") or "",
        "cargo_normalized": _normalize_person_text(getattr(credentials, "cargo", "") or ""),
    }


def _extract_expected_signer(text: str) -> dict[str, Any] | None:
    non_empty_lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    patterns = [
        re.compile(
            r"(?P<name>[A-ZÁ-Ú][A-Za-zÁ-Úá-ú'`-]+(?:\s+[A-ZÁ-Ú][A-Za-zÁ-Úá-ú'`-]+){0,5})\s*[-–—,]\s*"
            r"(?P<rank>SD(?:\s+BM)?|CB(?:\s+BM)?|[123][°º]?\s*SGT(?:\s+(?:QP)?BM|\s+BM)?|ST\s+BM|"
            r"[12][°º]?\s*TEN(?:\s+QOEM)?(?:\s+BM)?|CAP(?:\s+BM)?|MAJ(?:\s+BM)?|TC(?:\s+BM)?|CEL(?:\s+QOEM)?(?:\s+BM)?)",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?P<rank>SD(?:\s+BM)?|CB(?:\s+BM)?|[123][°º]?\s*SGT(?:\s+(?:QP)?BM|\s+BM)?|ST\s+BM|"
            r"[12][°º]?\s*TEN(?:\s+QOEM)?(?:\s+BM)?|CAP(?:\s+BM)?|MAJ(?:\s+BM)?|TC(?:\s+BM)?|CEL(?:\s+QOEM)?(?:\s+BM)?)\s+"
            r"(?P<name>[A-ZÁ-Ú][A-Za-zÁ-Úá-ú'`-]+(?:\s+[A-ZÁ-Ú][A-Za-zÁ-Úá-ú'`-]+){0,5})",
            re.IGNORECASE,
        ),
    ]
    candidate_windows: list[list[str]] = []
    ref_idx: int | None = None
    for i in range(len(non_empty_lines) - 1, -1, -1):
        if non_empty_lines[i].startswith("Referência"):
            ref_idx = i
            break
    if ref_idx is not None and ref_idx > 0:
        start = max(0, ref_idx - 12)
        candidate_windows.append(non_empty_lines[start:ref_idx])
    candidate_windows.extend([non_empty_lines[-5:], non_empty_lines[-12:]])
    seen_windows: set[str] = set()
    best_match: dict[str, Any] | None = None
    for window_lines in candidate_windows:
        tail_text = re.sub(r"\s+", " ", " ".join(window_lines)).strip()
        if not tail_text or tail_text in seen_windows:
            continue
        seen_windows.add(tail_text)
        for pattern in patterns:
            match = pattern.search(tail_text)
            if match:
                clean_name = _strip_institutional_prefixes(match.group("name").strip())
                candidate = {
                    "line": tail_text,
                    "name": clean_name,
                    "cargo": match.group("rank").strip(),
                    "normalized_name": _normalize_person_text(clean_name),
                    "normalized_cargo": _normalize_person_text(match.group("rank")),
                }
                if best_match is None:
                    best_match = candidate
                else:
                    candidate_tokens = len(candidate["name"].split())
                    best_tokens = len(best_match["name"].split())
                    if candidate_tokens > best_tokens:
                        best_match = candidate
                break
    return best_match


def _matches_current_signer(expected_signer: dict[str, Any] | None, current_signer: dict[str, str]) -> bool:
    if not expected_signer:
        return False
    expected_name = expected_signer.get("normalized_name", "")
    user_name = current_signer.get("usuario_normalized", "")
    expected_tokens = {token for token in expected_name.split() if len(token) > 2}
    user_tokens = {token for token in user_name.split() if len(token) > 2}
    if expected_tokens and user_tokens:
        overlap = expected_tokens & user_tokens
        if len(overlap) >= 2:
            return True
        if overlap and expected_name.split()[-1] == user_name.split()[-1]:
            return True

    expected_cargo = expected_signer.get("normalized_cargo", "")
    user_cargo = current_signer.get("cargo_normalized", "")
    if expected_cargo and user_cargo and expected_cargo in user_cargo:
        return True
    return False


def _looks_like_role_not_person(expected_signer: dict[str, Any] | None) -> bool:
    if not expected_signer:
        return False
    normalized_name = expected_signer.get("normalized_name", "")
    if not normalized_name:
        return False
    tokens = normalized_name.split()
    role_keywords = {
        "chefe",
        "diretor",
        "diretora",
        "secretario",
        "secretaria",
        "coordenador",
        "coordenadora",
        "comandante",
        "subcomandante",
        "gerente",
        "assessor",
        "assessora",
        "presidente",
    }
    function_connectors = {"da", "de", "do", "das", "dos"}
    if tokens and tokens[0] in role_keywords:
        return True
    if len(tokens) <= 3 and any(token in function_connectors for token in tokens):
        return True
    return False


def _evaluate_signer_compatibility(
    *,
    form_signer: dict[str, Any] | None,
    expected_signer: dict[str, Any] | None,
    current_signer: dict[str, str],
) -> dict[str, Any]:
    form_user = _normalize_person_text((form_signer or {}).get("txtUsuario", ""))
    current_user = current_signer.get("usuario_normalized", "")
    form_cargo = _normalize_person_text((form_signer or {}).get("selCargoFuncao", ""))
    current_cargo = current_signer.get("cargo_normalized", "")

    form_matches = False
    if form_user and current_user:
        form_matches = form_user == current_user or (
            len(set(form_user.split()) & set(current_user.split())) >= 2
        )
    elif form_cargo and current_cargo:
        form_matches = form_cargo == current_cargo or form_cargo in current_cargo or current_cargo in form_cargo

    tail_matches = _matches_current_signer(expected_signer, current_signer)
    tail_state = "ambiguous"
    if expected_signer:
        if tail_matches:
            tail_state = "match"
        elif _looks_like_role_not_person(expected_signer):
            tail_state = "ambiguous"
        else:
            tail_state = "mismatch"

    if form_matches and tail_state == "match":
        return {
            "decision": "sign",
            "reason": "sign_form_and_tail_match_current_user",
            "override_allowed": True,
            "form_matches": True,
            "tail_state": tail_state,
        }
    if form_matches and tail_state == "ambiguous":
        return {
            "decision": "skip",
            "reason": "tail_ambiguous_review_required",
            "override_allowed": True,
            "form_matches": True,
            "tail_state": tail_state,
        }
    if form_matches and tail_state == "mismatch":
        return {
            "decision": "skip",
            "reason": "tail_indicates_other_signer",
            "override_allowed": True,
            "form_matches": True,
            "tail_state": tail_state,
        }
    return {
        "decision": "skip",
        "reason": "sign_form_does_not_match_current_user",
        "override_allowed": False,
        "form_matches": False,
        "tail_state": tail_state,
    }


def _resolve_process_type(client: Any, tipo_processo: str) -> tuple[str, str]:
    if tipo_processo.isdigit():
        return tipo_processo, tipo_processo

    aliases = getattr(client, "PROC_TYPES", {})
    key = _normalize_process_type_key(tipo_processo)
    resolved = aliases.get(key)
    if resolved:
        return resolved, key

    if hasattr(client, "list_process_types"):
        available = client.list_process_types()
        by_name = next((item for item in available if _normalize_process_type_key(item.nome) == key), None)
        if by_name:
            return by_name.id_serie, key
        raise WorkflowViolationError(
            f"Tipo de processo '{tipo_processo}' nao reconhecido.",
            details={
                "tipo_processo_informado": tipo_processo,
                "known_aliases": sorted(aliases.keys()),
                "available_types": [item.nome for item in available],
            },
        )

    raise WorkflowViolationError(
        f"Tipo de processo '{tipo_processo}' nao reconhecido.",
        details={
            "tipo_processo_informado": tipo_processo,
            "known_aliases": sorted(aliases.keys()),
        },
    )


def _resolve_access_level(nivel_acesso: str) -> tuple[str, str, list[str]]:
    key = nivel_acesso.strip().lower()
    warnings: list[str] = []
    mapping = {
        "0": ("0", "publico"),
        "publico": ("0", "publico"),
        "público": ("0", "publico"),
        "1": ("1", "restrito"),
        "restrito": ("1", "restrito"),
        "privado": ("1", "restrito"),
        "private": ("1", "restrito"),
        "2": ("2", "sigiloso"),
        "sigiloso": ("2", "sigiloso"),
    }
    resolved = mapping.get(key)
    if not resolved:
        raise WorkflowViolationError(
            f"Nivel de acesso '{nivel_acesso}' nao reconhecido.",
            details={"nivel_acesso_informado": nivel_acesso},
        )
    if key == "privado":
        warnings.append("SEI usa 'restrito' no formulario. 'privado' foi mapeado para nivel 1.")
    return resolved[0], resolved[1], warnings


def _build_access_policy(
    *,
    nivel_acesso: str,
    motivo_acesso: str,
) -> tuple[dict[str, Any], list[str]]:
    nivel_codigo, nivel_label, warnings = _resolve_access_level(nivel_acesso)
    policy = {
        "nivel_codigo": nivel_codigo,
        "nivel_label": nivel_label,
        "motivo_acesso": motivo_acesso.strip() or None,
        "requires_hypothesis": nivel_codigo != "0",
    }
    return policy, warnings


def _default_process_pdf_path(id_procedimento: str, output_path: str | None) -> str:
    return output_path or f"/tmp/sei_{id_procedimento}.pdf"


def _default_document_pdf_path(id_documento: str, output_path: str | None) -> str:
    return output_path or f"/tmp/sei_doc_{id_documento}.pdf"


def _resolve_process_marker(
    client: Any,
    id_procedimento: str,
    marker: str | None,
) -> dict[str, Any]:
    current_markers = _list_process_markers(client, id_procedimento)
    selected_marker = _resolve_marker_reference(current_markers, marker)
    if marker and not selected_marker:
        raise WorkflowViolationError(
            f"Marcador '{marker}' não encontrado no processo.",
            details={"id_procedimento": id_procedimento, "marker": marker},
        )
    if not selected_marker and current_markers:
        selected_marker = current_markers[0]
    if not selected_marker:
        raise WorkflowViolationError(
            "Processo não possui marcador gerenciável no momento.",
            details={"id_procedimento": id_procedimento},
        )
    return selected_marker


def _resolve_requested_access_level(value: str) -> str | None:
    normalized = value.strip().lower()
    if normalized in {"", "inherit", "herdar", "processo"}:
        return None
    return value


def _resolve_texto_inicial(texto_inicial: str, *, documento_modelo: str = "") -> str:
    normalized = (texto_inicial or "N").strip().upper()
    if normalized in {"", "NENHUM", "NONE"}:
        normalized = "N"
    elif normalized in {"TEXTO_PADRAO", "TEXTO-PADRAO", "PADRAO", "PADRÃO"}:
        normalized = "T"
    elif normalized in {"DOCUMENTO_MODELO", "DOCUMENTO-MODELO", "MODELO"}:
        normalized = "D"
    elif normalized not in {"N", "T", "D"}:
        raise WorkflowViolationError(
            f"Texto inicial '{texto_inicial}' nao reconhecido.",
            details={"texto_inicial": texto_inicial, "allowed_values": ["N", "T", "D"]},
        )

    if documento_modelo.strip():
        if normalized not in {"N", "D"}:
            raise WorkflowViolationError(
                "Documento modelo exige texto_inicial 'D' ou omissao do texto inicial.",
                details={"texto_inicial": texto_inicial, "documento_modelo": documento_modelo},
            )
        return "D"
    return normalized


def _resolve_created_document_context(document_ref: str) -> tuple[str | None, str | None]:
    normalized = str(document_ref).strip()
    for item in reversed(load_created_documents()):
        if normalized in {
            str(item.get("id_documento") or "").strip(),
            str(item.get("numero_documento") or "").strip(),
        }:
            return (
                str(item.get("id_documento") or "").strip() or None,
                str(item.get("id_procedimento") or "").strip() or None,
            )
    return None, None


def _prune_access_warnings(
    warnings: list[str],
    *,
    selected_hypothesis: dict[str, Any] | list[dict[str, Any]] | None,
) -> list[str]:
    if not selected_hypothesis:
        return warnings
    return [
        warning
        for warning in warnings
        if "Hipóteses legais de acesso parecem ser carregadas via AJAX" not in warning
    ]


def _select_access_hypothesis(
    metadata: dict[str, Any],
    *,
    nivel_codigo: str,
    hipotese_acesso: str,
    hipotese_campo: str,
) -> tuple[dict[str, Any] | list[dict[str, Any]] | None, dict[str, str]]:
    if nivel_codigo == "0":
        return None, {}

    hypotheses = metadata.get("access_hypotheses", [])
    if not hypotheses:
        raise WorkflowViolationError(
            "Formulario nao expôs hipoteses de acesso para este tipo de processo.",
            details={"expected_field": "hipotese_acesso"},
        )
    if not hipotese_acesso.strip():
        raise WorkflowViolationError(
            "Nivel de acesso nao publico exige hipotese de acesso valida do formulario.",
            details={"expected_field": "hipotese_acesso"},
        )

    fields_by_name = {item["field_name"]: item for item in hypotheses}

    if nivel_codigo == "2":
        selected_values = {}
        for chunk in [part.strip() for part in hipotese_acesso.split(",") if part.strip()]:
            if "=" not in chunk:
                raise WorkflowViolationError(
                    "Para nivel sigiloso, informe hipoteses no formato campo=valor,campo=valor.",
                    details={"expected_format": "selGrauSigilo=R,selHipoteseLegal=23"},
                )
            field_name, value = chunk.split("=", 1)
            selected_values[field_name.strip()] = value.strip()

        required = {"selGrauSigilo", "selHipoteseLegal"}
        if not required.issubset(selected_values):
            raise WorkflowViolationError(
                "Processo sigiloso exige selGrauSigilo e selHipoteseLegal.",
                details={"required_fields": sorted(required)},
            )

        resolved_items: list[dict[str, Any]] = []
        extra_fields: dict[str, str] = {}
        for field_name in sorted(required):
            field = fields_by_name.get(field_name)
            if field is None:
                raise WorkflowViolationError(
                    f"Campo obrigatório '{field_name}' nao disponivel no formulario.",
                    details={"available_fields": list(fields_by_name)},
                )
            value = selected_values[field_name]
            option = next((opt for opt in field["options"] if opt["value"] == value), None)
            if option is None:
                raise WorkflowViolationError(
                    f"Valor '{value}' nao disponivel no campo '{field_name}'.",
                    details={
                        "field_name": field_name,
                        "available_values": [opt["value"] for opt in field["options"]],
                    },
                )
            extra_fields[field_name] = option["value"]
            hidden_field = field.get("hidden_field_name")
            if hidden_field:
                extra_fields[hidden_field] = option["value"]
            resolved_items.append(
                {
                    "field_name": field_name,
                    "field_label": field.get("field_label"),
                    "hidden_field_name": hidden_field,
                    "value": option["value"],
                    "label": option["label"],
                }
            )
        return resolved_items, extra_fields

    selected_field = hipotese_campo.strip() or hypotheses[0]["field_name"]
    if len(hypotheses) > 1 and not hipotese_campo.strip():
        raise WorkflowViolationError(
            "Mais de um campo de hipotese encontrado. Informe explicitamente o campo.",
            details={
                "expected_field": "hipotese_campo",
                "available_fields": [item["field_name"] for item in hypotheses],
            },
        )

    field = fields_by_name.get(selected_field)
    if field is None:
        raise WorkflowViolationError(
            f"Campo de hipotese '{selected_field}' nao disponivel no formulario.",
            details={"available_fields": [item["field_name"] for item in hypotheses]},
        )

    option = next((opt for opt in field["options"] if opt["value"] == hipotese_acesso), None)
    if option is None:
        raise WorkflowViolationError(
            f"Hipotese '{hipotese_acesso}' nao disponivel no campo '{selected_field}'.",
            details={
                "field_name": selected_field,
                "available_values": [opt["value"] for opt in field["options"]],
            },
        )

    extra_fields = {field["field_name"]: option["value"]}
    hidden_field = field.get("hidden_field_name")
    if hidden_field:
        extra_fields[hidden_field] = option["value"]

    return (
        {
            "field_name": field["field_name"],
            "field_label": field.get("field_label"),
            "hidden_field_name": hidden_field,
            "value": option["value"],
            "label": option["label"],
        },
        extra_fields,
    )


def process_create_preview(
    client: Any,
    tipo_processo: str,
    *,
    especificacao: str,
    interessados: str = "",
    observacoes: str = "",
    nivel_acesso: str = "0",
    motivo_acesso: str = "",
    hipotese_acesso: str = "",
    hipotese_campo: str = "",
) -> dict[str, Any]:
    operation = "process-create-preview"
    resolved_ids: dict[str, Any] = {}
    try:
        context = _context(client)
        tipo_id, tipo_alias = _resolve_process_type(client, tipo_processo)
        access_policy, warnings = _build_access_policy(
            nivel_acesso=nivel_acesso,
            motivo_acesso=motivo_acesso,
        )
        form_metadata = client.get_process_creation_metadata(
            tipo_id,
            nivel_acesso=access_policy["nivel_codigo"],
        )
        selected_hypothesis, _extra_fields = _select_access_hypothesis(
            form_metadata,
            nivel_codigo=access_policy["nivel_codigo"],
            hipotese_acesso=hipotese_acesso,
            hipotese_campo=hipotese_campo,
        ) if access_policy["nivel_codigo"] != "0" and hipotese_acesso.strip() else (None, {})
        resolved_ids = {
            "tipo_processo_id": tipo_id,
            "tipo_processo_alias": tipo_alias,
        }
        return _result(
            operation=operation,
            context=context,
            resolved_ids=resolved_ids,
            data={
                "preflight": {
                    "current_unit": context.get("unidade_sigla"),
                    "current_user": context.get("usuario"),
                    "will_create_in_current_unit": True,
                },
                "process_type": {
                    "id": tipo_id,
                    "alias": tipo_alias,
                },
                "access_policy": {
                    **access_policy,
                    "available_hypotheses": form_metadata.get("access_hypotheses", []),
                    "selected_hypothesis": selected_hypothesis,
                },
                "payload_preview": {
                    "especificacao": especificacao,
                    "interessados": interessados,
                    "observacoes": observacoes,
                },
                "confirmation_required": True,
            },
            next_actions=[
                NextAction(
                    action="process-create-confirm",
                    label="Criar processo com este payload",
                    params={
                        "tipo_processo": tipo_processo,
                        "especificacao": especificacao,
                        "interessados": interessados,
                        "observacoes": observacoes,
                        "nivel_acesso": access_policy["nivel_codigo"],
                        "motivo_acesso": access_policy["motivo_acesso"],
                        "hipotese_acesso": (
                            ",".join(f"{item['field_name']}={item['value']}" for item in selected_hypothesis)
                            if isinstance(selected_hypothesis, list)
                            else (selected_hypothesis["value"] if selected_hypothesis else None)
                        ),
                        "hipotese_campo": (
                            None
                            if isinstance(selected_hypothesis, list)
                            else (selected_hypothesis["field_name"] if selected_hypothesis else None)
                        ),
                    },
                )
            ],
            warnings=[*warnings, *form_metadata.get("warnings", [])],
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("context"),
            resolved_ids=resolved_ids,
            exc=exc,
        )


def process_create_confirm(
    client: Any,
    tipo_processo: str,
    *,
    especificacao: str,
    interessados: str = "",
    observacoes: str = "",
    nivel_acesso: str = "0",
    motivo_acesso: str = "",
    hipotese_acesso: str = "",
    hipotese_campo: str = "",
    confirm: bool = False,
) -> dict[str, Any]:
    operation = "process-create-confirm"
    resolved_ids: dict[str, Any] = {}
    try:
        if not confirm:
            raise WorkflowViolationError(
                "Confirmacao explicita obrigatoria para criar processo.",
                details={"expected_flag": "--confirm"},
            )
        context = _context(client)
        tipo_id, tipo_alias = _resolve_process_type(client, tipo_processo)
        access_policy, warnings = _build_access_policy(
            nivel_acesso=nivel_acesso,
            motivo_acesso=motivo_acesso,
        )
        form_metadata = client.get_process_creation_metadata(
            tipo_id,
            nivel_acesso=access_policy["nivel_codigo"],
        )
        selected_hypothesis, extra_fields = _select_access_hypothesis(
            form_metadata,
            nivel_codigo=access_policy["nivel_codigo"],
            hipotese_acesso=hipotese_acesso,
            hipotese_campo=hipotese_campo,
        )
        if access_policy["nivel_codigo"] == "2" and selected_hypothesis is None:
            raise UnsupportedStateError("Processo sigiloso exige hipotese de acesso valida.")
        created = client.create_process(
            tipo_id,
            especificacao=especificacao,
            interessados=interessados,
            observacoes=observacoes,
            nivel_acesso=access_policy["nivel_codigo"],
            extra_fields=extra_fields,
        )
        try:
            append_created_process_log(
                {
                    "numero_processo": created.get("numero"),
                    "id_procedimento": created.get("id_procedimento"),
                    "tipo_processo_id": tipo_id,
                    "tipo_processo_alias": tipo_alias,
                    "unidade_sigla": context.get("unidade_sigla"),
                    "usuario": context.get("usuario"),
                    "access_policy": {**access_policy, "selected_hypothesis": selected_hypothesis},
                    "especificacao": especificacao,
                }
            )
        except Exception as exc:
            warnings.append(f"Nao foi possivel registrar o processo criado localmente: {exc}")
        resolved_ids = {
            "tipo_processo_id": tipo_id,
            "tipo_processo_alias": tipo_alias,
            "id_procedimento": created.get("id_procedimento"),
            "numero_processo": created.get("numero"),
        }
        return _result(
            operation=operation,
            context=context,
            resolved_ids=resolved_ids,
            data={
                "preflight": {
                    "current_unit": context.get("unidade_sigla"),
                    "current_user": context.get("usuario"),
                    "created_in_current_unit": True,
                },
                "process_type": {
                    "id": tipo_id,
                    "alias": tipo_alias,
                },
                "access_policy": {**access_policy, "selected_hypothesis": selected_hypothesis},
                "created_process": created,
            },
            next_actions=[
                NextAction(
                    action="process-open",
                    label="Abrir processo criado",
                    params={"numero_ou_id": created.get("id_procedimento") or created.get("numero")},
                )
            ],
            warnings=[*warnings, *form_metadata.get("warnings", [])],
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("context"),
            resolved_ids=resolved_ids,
            exc=exc,
        )


def _resolve_document_type(client: Any, id_procedimento: str, tipo_documento: str) -> tuple[str, str, str | None]:
    normalized = _normalize_process_type_key(tipo_documento)
    alias_map = getattr(client, "DOC_TYPES", {})
    tipo_id = alias_map.get(normalized, tipo_documento)
    available = client.list_document_types(id_procedimento)

    by_id = next((item for item in available if item.id_serie == tipo_id), None)
    if by_id:
        return by_id.id_serie, normalized, by_id.nome

    by_name = next((item for item in available if _normalize_process_type_key(item.nome) == normalized), None)
    if by_name:
        return by_name.id_serie, normalized, by_name.nome

    if tipo_documento.isdigit():
        return tipo_documento, tipo_documento, None

    raise WorkflowViolationError(
        f"Tipo de documento '{tipo_documento}' nao disponivel neste processo.",
        details={
            "tipo_documento_informado": tipo_documento,
            "available_types": [item.nome for item in available],
        },
    )


def _reverse_unit_name(client: Any, unit_id: str) -> str:
    for name, uid in getattr(client, "UNIT_IDS", {}).items():
        if uid == unit_id:
            return name
    return unit_id


def _resolve_forward_destinations(
    client: Any,
    destinos: list[str],
    ajax_url: str | None = None,
) -> list[dict[str, str]]:
    resolved: list[dict[str, str]] = []
    seen: set[str] = set()
    unit_descriptions: dict[str, str] = {}
    for destino in destinos:
        if ajax_url:
            unit_id = client._resolve_unit_id_ajax(
                destino, ajax_url, unit_descriptions,
            )
        else:
            unit_id = client._resolve_unit_id(destino)
        if unit_id in seen:
            continue
        seen.add(unit_id)
        # Use AJAX description if available, else static reverse lookup
        nome = unit_descriptions.get(unit_id) or _reverse_unit_name(client, unit_id)
        resolved.append(
            {
                "requested": destino,
                "id_unidade": unit_id,
                "nome": nome,
            }
        )
    return resolved


def _find_reopen_candidate_unit(
    client: Any,
    id_procedimento: str,
    *,
    preferred_unit: str | None = None,
) -> tuple[str | None, list[str]]:
    tried: list[str] = []
    current_unit = client.status().unidade_sigla
    if preferred_unit:
        tried.append(preferred_unit)
        client.switch_unit(preferred_unit)
        if client.check_reopen_available(id_procedimento):
            return preferred_unit, tried

    current_after = client.status().unidade_sigla
    if current_after not in tried:
        tried.append(current_after)
        if client.check_reopen_available(id_procedimento):
            return current_after, tried

    available_units = {u.sigla for u in client.list_units()}
    for unit in client.list_process_history_units(id_procedimento):
        if unit in tried or unit not in available_units:
            continue
        tried.append(unit)
        client.switch_unit(unit)
        if client.check_reopen_available(id_procedimento):
            return unit, tried

    return None, tried


def process_forward_preview(
    client: Any,
    numero_ou_id_processo: str,
    *,
    destinos: list[str],
    manter_aberto: bool = True,
    retorno_em: str | None = None,
    retorno_dias: str | None = None,
    retorno_dias_uteis: bool = False,
    reabrir_em: str | None = None,
    reabrir_dias: str | None = None,
    reabrir_dias_uteis: bool = False,
    review_mode: str = "deep",
) -> dict[str, Any]:
    operation = "process-forward-preview"
    resolved_ids: dict[str, Any] = {}
    try:
        if not destinos:
            raise WorkflowViolationError("Pelo menos uma unidade destino deve ser informada.")
        if retorno_em and retorno_dias:
            raise WorkflowViolationError("Retorno programado aceita data ou dias, não ambos.")
        if reabrir_em and reabrir_dias:
            raise WorkflowViolationError("Reabertura programada aceita data ou dias, não ambos.")
        if manter_aberto and (reabrir_em or reabrir_dias):
            raise WorkflowViolationError("Reabertura programada só faz sentido quando o processo for fechado na unidade atual.")

        context = _context(client)
        id_procedimento, numero_processo = _resolve_process_id(client, numero_ou_id_processo)
        preflight, unit_guard = _process_unit_preflight(client, id_procedimento)
        warnings: list[str] = []

        with unit_guard as switched_to:
            if switched_to:
                preflight["switched"] = True
                preflight["switched_to"] = switched_to
                context = _context(client)

            tramitar_form = client.get_tramitar_form(id_procedimento)
            resolved_destinos = _resolve_forward_destinations(
                client, destinos, ajax_url=tramitar_form.ajax_url,
            )
            review_data = None
            if review_mode != "fast":
                summary_mode = "all" if review_mode == "deep" else "summary"
                summary_sample = 5 if review_mode == "deep" else 3
                summary_result = process_summary(
                    client,
                    id_procedimento,
                    mode=summary_mode,
                    sample_size=summary_sample,
                )
                if summary_result.get("ok"):
                    review_data = summary_result["data"]
                else:
                    warnings.append("Nao foi possivel contextualizar o processo antes do encaminhamento.")

            scheduled_reopen_supported = bool(tramitar_form.reabertura_programada_fields)
            scheduled_return_supported = bool(tramitar_form.retorno_programado_fields)
            if (reabrir_em or reabrir_dias) and not scheduled_reopen_supported:
                warnings.append("Formulario atual nao expôs campo de reabertura programada.")
            if (retorno_em or retorno_dias) and not scheduled_return_supported:
                warnings.append("Formulario atual nao expôs campo de retorno programado.")

        resolved_ids = {
            "id_procedimento": id_procedimento,
            "numero_processo": numero_processo or numero_ou_id_processo,
            "destination_unit_ids": [item["id_unidade"] for item in resolved_destinos],
        }
        return _result(
            operation=operation,
            context=context,
            resolved_ids=resolved_ids,
            data={
                "preflight": {
                    **preflight,
                    "current_unit": context.get("unidade_sigla"),
                },
                "processo": {
                    "id_procedimento": id_procedimento,
                    "numero": numero_processo or numero_ou_id_processo,
                },
                "review_mode": review_mode,
                "review": review_data,
                "forward_policy": {
                    "manter_aberto": manter_aberto,
                    "fechar_na_unidade_atual": not manter_aberto,
                    "retorno_em": retorno_em,
                    "retorno_dias": retorno_dias,
                    "retorno_dias_uteis": retorno_dias_uteis,
                    "reabrir_em": reabrir_em,
                    "reabrir_dias": reabrir_dias,
                    "reabrir_dias_uteis": reabrir_dias_uteis,
                    "scheduled_return_supported": scheduled_return_supported,
                    "scheduled_reopen_supported": scheduled_reopen_supported,
                },
                "available_form_fields": {
                    "destino_field": tramitar_form.destino_field,
                    "manter_aberto_field": tramitar_form.manter_aberto_field,
                    "retorno_programado_fields": tramitar_form.retorno_programado_fields,
                    "reabertura_programada_fields": tramitar_form.reabertura_programada_fields,
                },
                "destinations": resolved_destinos,
                "confirmation_required": True,
            },
            next_actions=[
                NextAction(
                    action="process-forward-confirm",
                    label="Encaminhar o processo para as unidades destino",
                    params={
                        "numero_ou_id_processo": id_procedimento,
                        "destinos": [item["id_unidade"] for item in resolved_destinos],
                        "manter_aberto": manter_aberto,
                        "retorno_em": retorno_em,
                        "retorno_dias": retorno_dias,
                        "retorno_dias_uteis": retorno_dias_uteis,
                        "reabrir_em": reabrir_em,
                        "reabrir_dias": reabrir_dias,
                        "reabrir_dias_uteis": reabrir_dias_uteis,
                    },
                )
            ],
            warnings=warnings,
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("context"),
            resolved_ids=resolved_ids,
            exc=exc,
        )


def process_forward_confirm(
    client: Any,
    numero_ou_id_processo: str,
    *,
    destinos: list[str],
    manter_aberto: bool = True,
    retorno_em: str | None = None,
    retorno_dias: str | None = None,
    retorno_dias_uteis: bool = False,
    reabrir_em: str | None = None,
    reabrir_dias: str | None = None,
    reabrir_dias_uteis: bool = False,
    review_mode: str = "deep",
    confirm: bool = False,
) -> dict[str, Any]:
    operation = "process-forward-confirm"
    resolved_ids: dict[str, Any] = {}
    try:
        if not confirm:
            raise WorkflowViolationError(
                "Confirmacao explicita obrigatoria para encaminhar processo.",
                details={"expected_flag": "--confirm"},
            )

        preview = process_forward_preview(
            client,
            numero_ou_id_processo,
            destinos=destinos,
            manter_aberto=manter_aberto,
            retorno_em=retorno_em,
            retorno_dias=retorno_dias,
            retorno_dias_uteis=retorno_dias_uteis,
            reabrir_em=reabrir_em,
            reabrir_dias=reabrir_dias,
            reabrir_dias_uteis=reabrir_dias_uteis,
            review_mode=review_mode,
        )
        if not preview.get("ok"):
            preview["operation"] = operation
            return preview

        id_procedimento = preview["resolved_ids"]["id_procedimento"]
        context = preview["context"]
        target_destinos = [item["id_unidade"] for item in preview["data"]["destinations"]]
        result = client.enviar_processo(
            id_procedimento,
            target_destinos,
            manter_aberto=manter_aberto,
            retorno_em=retorno_em,
            retorno_dias=retorno_dias,
            retorno_dias_uteis=retorno_dias_uteis,
            reabrir_em=reabrir_em,
            reabrir_dias=reabrir_dias,
            reabrir_dias_uteis=reabrir_dias_uteis,
        )
        if not result:
            raise WorkflowViolationError("SEI nao confirmou o encaminhamento do processo.")

        open_units = client.list_process_open_units(id_procedimento)
        current_unit = context.get("unidade_sigla")
        resolved_ids = dict(preview["resolved_ids"])
        return _result(
            operation=operation,
            context=context,
            resolved_ids=resolved_ids,
            data={
                "preflight": preview["data"]["preflight"],
                "processo": preview["data"]["processo"],
                "forward_policy": preview["data"]["forward_policy"],
                "destinations": preview["data"]["destinations"],
                "status_after": {
                    "open_units": open_units,
                    "current_unit_still_open": bool(current_unit and current_unit in open_units),
                },
            },
            next_actions=[
                NextAction(
                    action="process-open",
                    label="Reabrir processo para verificar estado apos encaminhamento",
                    params={"numero_ou_id": id_procedimento},
                )
            ],
            warnings=preview.get("warnings", []),
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("preview", {}).get("context") if "preview" in locals() else locals().get("context"),
            resolved_ids=resolved_ids or locals().get("preview", {}).get("resolved_ids"),
            exc=exc,
        )


def process_conclude_preview(
    client: Any,
    numero_ou_id_processo: str,
    *,
    reabrir_em: str | None = None,
    reabrir_dias: str | None = None,
    reabrir_dias_uteis: bool = False,
) -> dict[str, Any]:
    operation = "process-conclude-preview"
    resolved_ids: dict[str, Any] = {}
    try:
        if reabrir_em and reabrir_dias:
            raise WorkflowViolationError("Conclusão com reabertura aceita data ou dias, não ambos.")
        context = _context(client)
        id_procedimento, numero_processo = _resolve_process_id(client, numero_ou_id_processo)
        preflight, unit_guard = _process_unit_preflight(client, id_procedimento)
        with unit_guard as switched_to:
            if switched_to:
                preflight["switched"] = True
                preflight["switched_to"] = switched_to
                context = _context(client)
            form_info = client.get_concluir_form(id_procedimento)

        resolved_ids = {
            "id_procedimento": id_procedimento,
            "numero_processo": numero_processo or numero_ou_id_processo,
        }
        return _result(
            operation=operation,
            context=context,
            resolved_ids=resolved_ids,
            data={
                "preflight": {**preflight, "current_unit": context.get("unidade_sigla")},
                "processo": {"id_procedimento": id_procedimento, "numero": numero_processo or numero_ou_id_processo},
                "conclude_policy": {
                    "mode": "reopen_on_date" if reabrir_em else "reopen_in_days" if reabrir_dias else "definitive",
                    "reabrir_em": reabrir_em,
                    "reabrir_dias": reabrir_dias,
                    "reabrir_dias_uteis": reabrir_dias_uteis,
                    "scheduled_reopen_supported": form_info.get("supports_reopen_schedule", False),
                },
                "available_form_fields": {
                    "reabertura_programada_fields": form_info.get("reabertura_programada_fields", {}),
                    "rdoConcluir": "rdoConcluir",
                },
                "confirmation_required": True,
            },
            next_actions=[
                NextAction(
                    action="process-conclude-confirm",
                    label="Concluir processo nesta unidade",
                    params={
                        "numero_ou_id_processo": id_procedimento,
                        "reabrir_em": reabrir_em,
                        "reabrir_dias": reabrir_dias,
                        "reabrir_dias_uteis": reabrir_dias_uteis,
                    },
                )
            ],
            warnings=[] if not (reabrir_em or reabrir_dias) or form_info.get("supports_reopen_schedule") else [
                "Formulário atual não expôs campos de reabertura programada."
            ],
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("context"),
            resolved_ids=resolved_ids,
            exc=exc,
        )


def process_conclude_confirm(
    client: Any,
    numero_ou_id_processo: str,
    *,
    reabrir_em: str | None = None,
    reabrir_dias: str | None = None,
    reabrir_dias_uteis: bool = False,
    confirm: bool = False,
) -> dict[str, Any]:
    operation = "process-conclude-confirm"
    resolved_ids: dict[str, Any] = {}
    try:
        if not confirm:
            raise WorkflowViolationError(
                "Confirmacao explicita obrigatoria para concluir processo.",
                details={"expected_flag": "--confirm"},
            )
        preview = process_conclude_preview(
            client,
            numero_ou_id_processo,
            reabrir_em=reabrir_em,
            reabrir_dias=reabrir_dias,
            reabrir_dias_uteis=reabrir_dias_uteis,
        )
        if not preview.get("ok"):
            preview["operation"] = operation
            return preview
        id_procedimento = preview["resolved_ids"]["id_procedimento"]
        ok = client.concluir_processo(
            id_procedimento,
            reabrir_em=reabrir_em,
            reabrir_dias=reabrir_dias,
            reabrir_dias_uteis=reabrir_dias_uteis,
        )
        if not ok:
            raise WorkflowViolationError("SEI nao confirmou a conclusão do processo.")
        resolved_ids = dict(preview["resolved_ids"])
        return _result(
            operation=operation,
            context=preview["context"],
            resolved_ids=resolved_ids,
            data={
                "preflight": preview["data"]["preflight"],
                "processo": preview["data"]["processo"],
                "conclude_policy": preview["data"]["conclude_policy"],
            },
            next_actions=[
                NextAction(
                    action="process-reopen-preview",
                    label="Verificar se a reabertura está disponível nesta unidade",
                    params={"numero_ou_id_processo": id_procedimento},
                )
            ],
            warnings=preview.get("warnings", []),
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("preview", {}).get("context") if "preview" in locals() else locals().get("context"),
            resolved_ids=resolved_ids or locals().get("preview", {}).get("resolved_ids"),
            exc=exc,
        )


def process_reopen_preview(
    client: Any,
    numero_ou_id_processo: str,
    *,
    unit: str | None = None,
) -> dict[str, Any]:
    operation = "process-reopen-preview"
    resolved_ids: dict[str, Any] = {}
    try:
        context = _context(client)
        id_procedimento, numero_processo = _resolve_process_id(client, numero_ou_id_processo)
        preflight, unit_guard = _process_unit_preflight(client, id_procedimento)
        with unit_guard as switched_to:
            if switched_to:
                preflight["switched"] = True
                preflight["switched_to"] = switched_to
                context = _context(client)
            available = False
            reason = "reopen_action_not_available_in_accessible_units"
            candidate_unit = None
            tried_units: list[str] = []
            try:
                candidate_unit, tried_units = _find_reopen_candidate_unit(
                    client,
                    id_procedimento,
                    preferred_unit=unit,
                )
                if candidate_unit:
                    available = True
                    reason = "reopen_action_available"
                    context = _context(client)
                    preflight["switched"] = preflight.get("switched") or (candidate_unit != preflight.get("current_unit"))
                    preflight["switched_to"] = candidate_unit
            except Exception:
                available = False
                tried_units = [unit] if unit else []
        resolved_ids = {
            "id_procedimento": id_procedimento,
            "numero_processo": numero_processo or numero_ou_id_processo,
        }
        return _result(
            operation=operation,
            context=context,
            resolved_ids=resolved_ids,
            data={
                "preflight": {**preflight, "current_unit": context.get("unidade_sigla")},
                "processo": {"id_procedimento": id_procedimento, "numero": numero_processo or numero_ou_id_processo},
                "reopen_available": available,
                "reason": reason,
                "requested_unit": unit,
                "candidate_unit": candidate_unit,
                "tried_units": tried_units,
                "confirmation_required": True,
            },
            next_actions=[
                NextAction(
                    action="process-reopen-confirm",
                    label="Reabrir processo nesta unidade",
                    params={"numero_ou_id_processo": id_procedimento, "unit": candidate_unit or unit},
                )
            ] if available else [],
            warnings=[] if available else [
                "A reabertura só fica disponível na unidade onde o processo foi concluído."
            ],
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("context"),
            resolved_ids=resolved_ids,
            exc=exc,
        )


def process_reopen_confirm(
    client: Any,
    numero_ou_id_processo: str,
    *,
    unit: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    operation = "process-reopen-confirm"
    resolved_ids: dict[str, Any] = {}
    try:
        if not confirm:
            raise WorkflowViolationError(
                "Confirmacao explicita obrigatoria para reabrir processo.",
                details={"expected_flag": "--confirm"},
            )
        preview = process_reopen_preview(client, numero_ou_id_processo, unit=unit)
        if not preview.get("ok"):
            preview["operation"] = operation
            return preview
        if not preview["data"].get("reopen_available"):
            raise WorkflowViolationError("Ação de reabrir não está disponível nesta unidade.")
        id_procedimento = preview["resolved_ids"]["id_procedimento"]
        ok = client.reabrir_processo(id_procedimento)
        if not ok:
            raise WorkflowViolationError("SEI não confirmou a reabertura do processo.")
        resolved_ids = dict(preview["resolved_ids"])
        return _result(
            operation=operation,
            context=preview["context"],
            resolved_ids=resolved_ids,
            data={
                "preflight": preview["data"]["preflight"],
                "processo": preview["data"]["processo"],
                "reopened": True,
            },
            next_actions=[
                NextAction(
                    action="process-open",
                    label="Abrir o processo reaberto",
                    params={"numero_ou_id": id_procedimento},
                )
            ],
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("preview", {}).get("context") if "preview" in locals() else locals().get("context"),
            resolved_ids=resolved_ids or locals().get("preview", {}).get("resolved_ids"),
            exc=exc,
        )


def document_create_preview(
    client: Any,
    numero_ou_id_processo: str,
    tipo_documento: str,
    *,
    descricao: str = "",
    interessados: str = "",
    texto_inicial: str = "N",
    documento_modelo: str = "",
    nivel_acesso: str = "inherit",
    motivo_acesso: str = "",
    hipotese_acesso: str = "",
    hipotese_campo: str = "",
) -> dict[str, Any]:
    operation = "document-create-preview"
    resolved_ids: dict[str, Any] = {}
    try:
        context = _context(client)
        original_unit = context.get("unidade_sigla")
        result_payload: dict[str, Any] | None = None
        id_procedimento, numero_processo = _resolve_process_id(client, numero_ou_id_processo)
        preflight, unit_guard = _process_unit_preflight(client, id_procedimento)
        with unit_guard as switched_to:
            if switched_to:
                preflight["switched"] = True
                preflight["switched_to"] = switched_to
                context = _context(client)
            effective_texto_inicial = _resolve_texto_inicial(
                texto_inicial,
                documento_modelo=documento_modelo,
            )
            tipo_id, tipo_alias, tipo_nome = _resolve_document_type(client, id_procedimento, tipo_documento)
            requested_access = _resolve_requested_access_level(nivel_acesso)
            inherited_access = client.get_process_access_metadata(id_procedimento) if requested_access is None else None
            metadata = client.get_document_creation_metadata(
                id_procedimento,
                tipo_documento,
                nivel_acesso=requested_access or (inherited_access or {}).get("nivel_acesso"),
            )
            effective_access = requested_access or (inherited_access or {}).get("nivel_acesso") or metadata.get("default_nivel_acesso", "0")
            access_policy, warnings = _build_access_policy(
                nivel_acesso=effective_access,
                motivo_acesso=motivo_acesso,
            )
            if access_policy["nivel_codigo"] != "0":
                if hipotese_acesso.strip():
                    selected_hypothesis, _extra_fields = _select_access_hypothesis(
                        metadata,
                        nivel_codigo=access_policy["nivel_codigo"],
                        hipotese_acesso=hipotese_acesso,
                        hipotese_campo=hipotese_campo,
                    )
                else:
                    selected_hypothesis = (inherited_access or {}).get("selected_hypothesis")
            else:
                selected_hypothesis = None
            metadata_warnings = _prune_access_warnings(
                metadata.get("warnings", []),
                selected_hypothesis=selected_hypothesis,
            )
            resolved_ids = {
                "id_procedimento": id_procedimento,
                "numero_processo": numero_processo or numero_ou_id_processo,
                "tipo_documento_id": tipo_id,
                "tipo_documento_alias": tipo_alias,
            }
            result_payload = _result(
                operation=operation,
                context=context,
                resolved_ids=resolved_ids,
                data={
                    "preflight": {
                        **preflight,
                        "current_unit": context.get("unidade_sigla"),
                        "will_create_in_current_unit": True,
                    },
                    "document_type": {
                        "id": tipo_id,
                        "alias": tipo_alias,
                        "nome": tipo_nome or tipo_documento,
                    },
                    "access_policy": {
                        **access_policy,
                        "inherits_from_process": requested_access is None,
                        "requested_nivel_acesso": nivel_acesso,
                        "process_nivel_acesso": (inherited_access or {}).get("nivel_acesso"),
                        "available_hypotheses": metadata.get("access_hypotheses", []),
                        "selected_hypothesis": selected_hypothesis,
                    },
                    "texto_inicial_options": metadata.get("texto_inicial_options", []),
                    "payload_preview": {
                        "descricao": descricao,
                        "interessados": interessados,
                        "texto_inicial": effective_texto_inicial,
                        "documento_modelo": documento_modelo,
                    },
                    "confirmation_required": True,
                },
                next_actions=[
                    NextAction(
                        action="document-create-confirm",
                        label="Criar documento com este payload",
                        params={
                            "numero_ou_id_processo": id_procedimento,
                            "tipo_documento": tipo_documento,
                            "descricao": descricao,
                            "interessados": interessados,
                            "texto_inicial": effective_texto_inicial,
                            "documento_modelo": documento_modelo,
                            "nivel_acesso": (
                                "inherit" if requested_access is None else access_policy["nivel_codigo"]
                            ),
                            "motivo_acesso": access_policy["motivo_acesso"],
                            "hipotese_acesso": (
                                ",".join(f"{item['field_name']}={item['value']}" for item in selected_hypothesis)
                                if isinstance(selected_hypothesis, list)
                                else (selected_hypothesis["value"] if selected_hypothesis else None)
                            ),
                            "hipotese_campo": (
                                None if isinstance(selected_hypothesis, list)
                                else (selected_hypothesis["field_name"] if selected_hypothesis else None)
                            ),
                        },
                    )
                ],
                warnings=[*warnings, *metadata_warnings],
            )
        _best_effort_switch_to_unit(client, original_unit)
        if result_payload is not None:
            result_payload["context"] = _context(client)
            return result_payload
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("context"),
            resolved_ids=resolved_ids,
            exc=exc,
        )


def document_create_confirm(
    client: Any,
    numero_ou_id_processo: str,
    tipo_documento: str,
    *,
    descricao: str = "",
    interessados: str = "",
    texto_inicial: str = "N",
    documento_modelo: str = "",
    nivel_acesso: str = "inherit",
    motivo_acesso: str = "",
    hipotese_acesso: str = "",
    hipotese_campo: str = "",
    confirm: bool = False,
) -> dict[str, Any]:
    operation = "document-create-confirm"
    resolved_ids: dict[str, Any] = {}
    try:
        if not confirm:
            raise WorkflowViolationError(
                "Confirmacao explicita obrigatoria para criar documento.",
                details={"expected_flag": "--confirm"},
            )
        context = _context(client)
        original_unit = context.get("unidade_sigla")
        result_payload: dict[str, Any] | None = None
        id_procedimento, numero_processo = _resolve_process_id(client, numero_ou_id_processo)
        preflight, unit_guard = _process_unit_preflight(client, id_procedimento)
        with unit_guard as switched_to:
            if switched_to:
                preflight["switched"] = True
                preflight["switched_to"] = switched_to
                context = _context(client)
            effective_texto_inicial = _resolve_texto_inicial(
                texto_inicial,
                documento_modelo=documento_modelo,
            )
            tipo_id, tipo_alias, tipo_nome = _resolve_document_type(client, id_procedimento, tipo_documento)
            requested_access = _resolve_requested_access_level(nivel_acesso)
            inherited_access = client.get_process_access_metadata(id_procedimento) if requested_access is None else None
            metadata = client.get_document_creation_metadata(
                id_procedimento,
                tipo_documento,
                nivel_acesso=requested_access or (inherited_access or {}).get("nivel_acesso"),
            )
            effective_access = requested_access or (inherited_access or {}).get("nivel_acesso") or metadata.get("default_nivel_acesso", "0")
            access_policy, warnings = _build_access_policy(
                nivel_acesso=effective_access,
                motivo_acesso=motivo_acesso,
            )
            if access_policy["nivel_codigo"] != "0" and hipotese_acesso.strip():
                selected_hypothesis, extra_fields = _select_access_hypothesis(
                    metadata,
                    nivel_codigo=access_policy["nivel_codigo"],
                    hipotese_acesso=hipotese_acesso,
                    hipotese_campo=hipotese_campo,
                )
            else:
                selected_hypothesis = (inherited_access or {}).get("selected_hypothesis")
                extra_fields = dict((inherited_access or {}).get("selected_extra_fields", {}))
            if access_policy["nivel_codigo"] == "2" and selected_hypothesis is None:
                raise UnsupportedStateError("Documento sigiloso exige hipotese de acesso valida.")
            created = client.create_document(
                id_procedimento,
                tipo_documento,
                nivel_acesso=effective_access,
                texto_inicial=effective_texto_inicial,
                documento_modelo=documento_modelo,
                descricao=descricao,
                interessados=interessados,
                extra_fields=extra_fields,
            )
            if not getattr(created, "id_documento", ""):
                raise WorkflowViolationError(
                    "SEI não retornou id_documento após a criação do documento.",
                    details={"id_procedimento": id_procedimento, "tipo_documento": tipo_documento},
                )
            metadata_warnings = _prune_access_warnings(
                metadata.get("warnings", []),
                selected_hypothesis=selected_hypothesis,
            )
            try:
                append_created_document_log(
                    {
                        "id_procedimento": id_procedimento,
                        "numero_processo": numero_processo or numero_ou_id_processo,
                        "id_documento": created.id_documento,
                        "tipo_documento_id": tipo_id,
                        "tipo_documento_alias": tipo_alias,
                        "tipo_documento_nome": tipo_nome or tipo_documento,
                        "unidade_sigla": context.get("unidade_sigla"),
                        "usuario": context.get("usuario"),
                        "access_policy": {
                            **access_policy,
                            "inherits_from_process": requested_access is None,
                            "process_nivel_acesso": (inherited_access or {}).get("nivel_acesso"),
                            "selected_hypothesis": selected_hypothesis,
                        },
                        "descricao": descricao,
                        "texto_inicial": effective_texto_inicial,
                        "documento_modelo": documento_modelo,
                    }
                )
            except Exception as exc:
                warnings.append(f"Nao foi possivel registrar o documento criado localmente: {exc}")
            resolved_ids = {
                "id_procedimento": id_procedimento,
                "numero_processo": numero_processo or numero_ou_id_processo,
                "id_documento": created.id_documento,
                "tipo_documento_id": tipo_id,
                "tipo_documento_alias": tipo_alias,
            }
            result_payload = _result(
                operation=operation,
                context=context,
                resolved_ids=resolved_ids,
                data={
                    "preflight": {
                        **preflight,
                        "current_unit": context.get("unidade_sigla"),
                        "created_in_current_unit": True,
                    },
                    "document_type": {
                        "id": tipo_id,
                        "alias": tipo_alias,
                        "nome": tipo_nome or tipo_documento,
                    },
                    "access_policy": {
                        **access_policy,
                        "inherits_from_process": requested_access is None,
                        "process_nivel_acesso": (inherited_access or {}).get("nivel_acesso"),
                        "selected_hypothesis": selected_hypothesis,
                    },
                    "created_document": {
                        "id_documento": created.id_documento,
                        "id_procedimento": created.id_procedimento,
                        "tipo": created.tipo,
                        "editor_url": created.editor_url,
                        "texto_inicial": effective_texto_inicial,
                        "documento_modelo": documento_modelo,
                    },
                },
                next_actions=[
                    NextAction(
                        action="document-edit-preview",
                        label="Inspecionar secoes do documento criado",
                        params={"numero_ou_id": created.id_documento, "process_id": id_procedimento},
                    )
                ],
                warnings=[*warnings, *metadata_warnings],
            )
        _best_effort_switch_to_unit(client, original_unit)
        if result_payload is not None:
            result_payload["context"] = _context(client)
            return result_payload
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("context"),
            resolved_ids=resolved_ids,
            exc=exc,
        )


def document_cancel_preview(
    client: Any,
    numero_ou_id: str,
    *,
    motivo: str = "",
    process_id: str | None = None,
) -> dict[str, Any]:
    """Preview native cancellation of a signed document."""
    operation = "document-cancel-preview"
    resolved_ids: dict[str, Any] = {}
    try:
        if not motivo or not motivo.strip():
            raise WorkflowViolationError(
                "Motivo do cancelamento é obrigatório.",
                details={"expected_option": "--motivo"},
            )

        context = _context(client)
        id_documento, id_procedimento, numero_documento = _resolve_document_ids(
            client,
            numero_ou_id,
            id_procedimento=process_id,
        )
        preflight, unit_guard = _process_unit_preflight(client, id_procedimento)
        with unit_guard as switched_to:
            if switched_to:
                preflight["switched"] = True
                preflight["switched_to"] = switched_to
                context = _context(client)

            docs = client.get_full_document_tree(id_procedimento, expand_all=True)
            id_documento, docs = _resolve_document_id_with_process(
                client,
                id_documento,
                id_procedimento=id_procedimento,
                docs=docs,
            )
            target = next((item for item in docs if item.id_documento == id_documento), None)
            if target is None:
                raise WorkflowViolationError(
                    f"Documento {numero_ou_id} não encontrado no processo informado."
                )
            if not (target.assinado or target.autenticado):
                raise WorkflowViolationError(
                    f"Documento {id_documento} ainda não está assinado/autenticado; "
                    "cancelamento não pode substituir exclusão de rascunho."
                )

            form_info = client.get_document_cancel_form_info(id_documento, id_procedimento)
            if not form_info.get("ok"):
                raise WorkflowViolationError(str(form_info.get("error", "Cancelamento indisponível")))

            resolved_ids = {
                "id_documento": id_documento,
                "id_procedimento": id_procedimento,
                "numero_documento": target.sei_number or numero_documento or numero_ou_id,
            }
            return _result(
                operation=operation,
                context=context,
                resolved_ids=resolved_ids,
                data={
                    "document": {
                        "id_documento": target.id_documento,
                        "numero_documento": target.sei_number,
                        "nome": target.nome,
                        "assinado": bool(target.assinado),
                        "autenticado": bool(target.autenticado),
                        "origin_unit": target.origin_unit,
                    },
                    "cancel_policy": {
                        "irreversible": True,
                        "reason_required": True,
                        "reason_length": len(motivo.strip()),
                        "document_remains_in_tree": True,
                        "delete_is_not_fallback": True,
                    },
                    "native_form": {
                        "form_id": form_info.get("form_id"),
                        "method": form_info.get("method"),
                        "reason_field": form_info.get("reason_field"),
                    },
                    "confirmation_required": 2,
                    "confirmation_flags": ["--confirm", "--confirm-impact"],
                },
                next_actions=[
                    NextAction(
                        action="document-cancel-confirm",
                        label="Cancelar o documento com duas confirmações explícitas",
                        params={
                            "numero_ou_id": id_documento,
                            "process_id": id_procedimento,
                            "motivo": motivo,
                            "confirm": False,
                            "confirm_impact": False,
                        },
                    )
                ],
                warnings=[],
            )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("context"),
            resolved_ids=resolved_ids,
            exc=exc,
        )


def document_cancel_confirm(
    client: Any,
    numero_ou_id: str,
    *,
    motivo: str,
    process_id: str | None = None,
    confirm: bool = False,
    confirm_impact: bool = False,
) -> dict[str, Any]:
    """Execute native document cancellation after two confirmations."""
    operation = "document-cancel-confirm"
    resolved_ids: dict[str, Any] = {}
    try:
        if not confirm or not confirm_impact:
            raise WorkflowViolationError(
                "Cancelamento destrutivo exige duas confirmações independentes: "
                "--confirm e --confirm-impact.",
                details={
                    "expected_flags": ["--confirm", "--confirm-impact"],
                    "confirm": confirm,
                    "confirm_impact": confirm_impact,
                },
            )
        if not motivo or not motivo.strip():
            raise WorkflowViolationError(
                "Motivo do cancelamento é obrigatório.",
                details={"expected_option": "--motivo"},
            )

        preview = document_cancel_preview(
            client,
            numero_ou_id,
            motivo=motivo,
            process_id=process_id,
        )
        if not preview.get("ok"):
            preview["operation"] = operation
            return preview

        id_documento = str(preview["resolved_ids"]["id_documento"])
        id_procedimento = str(preview["resolved_ids"]["id_procedimento"])
        result = client.cancel_document(id_documento, id_procedimento, motivo)
        if not result.get("verified"):
            raise WorkflowViolationError(
                "SEI não confirmou o cancelamento nativo do documento.",
                details=result,
            )
        return _result(
            operation=operation,
            context=preview["context"],
            resolved_ids=preview["resolved_ids"],
            data={
                "document": preview["data"]["document"],
                "cancel_policy": preview["data"]["cancel_policy"],
                "native_form": preview["data"]["native_form"],
                "result": result,
            },
            next_actions=[
                NextAction(
                    action="process-open",
                    label="Reler a árvore para auditoria do documento cancelado",
                    params={"numero_ou_id": id_procedimento},
                ),
                NextAction(
                    action="process-history",
                    label="Consultar o andamento do cancelamento",
                    params={"numero_ou_id": id_procedimento},
                ),
            ],
            warnings=[],
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("preview", {}).get("context") if "preview" in locals() else locals().get("context"),
            resolved_ids=resolved_ids or locals().get("preview", {}).get("resolved_ids"),
            exc=exc,
        )


def _section_preview(section: Any) -> dict[str, Any]:
    content = section.content or ""
    return {
        "section_id": section.section_id,
        "name": section.name,
        "editable": getattr(section, "editable", True),
        "content_length": len(content),
        "escaped_structural_html": _has_escaped_structural_html(content),
        "looks_empty": _is_effectively_empty_editor_content(content),
        "preview": content[:240],
    }


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


def _section_sort_key(section: Any) -> tuple[int, int, int, int]:
    section_id = str(getattr(section, "section_id", "") or "")
    try:
        numeric_id = int(section_id)
    except ValueError:
        numeric_id = 0
    content = getattr(section, "content", "") or ""
    decoded_content = content
    for _ in range(5):
        updated = html.unescape(decoded_content)
        if updated == decoded_content:
            break
        decoded_content = updated
    decoded_lower = decoded_content.lower()
    normalized = _normalize_editor_text(content)
    is_template_lock_candidate = section_id == "217"
    is_standard_body_candidate = section_id == "220"
    is_after_template_lock = numeric_id > 217
    has_body_class = "Texto_Justificado_Recuo_Primeira_Linha" in content or "Texto_Justificado" in content
    has_real_content = bool(normalized)
    is_header_or_timbre = "data:image" in decoded_lower or (
        "texto_centralizado_maiusculas_negrito" in decoded_lower
        and not has_body_class
    )
    is_footer = "<hr" in decoded_lower or "documento assinado eletronicamente" in decoded_lower
    is_process_metadata = (
        "processo n" in normalized.lower()
        and ("interessado:" in normalized.lower() or "referência:" in normalized.lower() or "referencia:" in normalized.lower())
    )
    is_reference_metadata = (
        ("referência:" in normalized.lower() or "referencia:" in normalized.lower())
        and "processo n" in normalized.lower()
    )

    if is_footer or is_header_or_timbre or is_reference_metadata:
        body_score = 0
    elif is_standard_body_candidate:
        body_score = 100
    elif has_body_class and not is_process_metadata:
        body_score = 80
    elif has_body_class:
        body_score = 30
    elif is_after_template_lock:
        body_score = 40
    else:
        body_score = 10

    return (
        1 if not is_template_lock_candidate else 0,
        body_score,
        numeric_id,
        1 if has_real_content else 0,
    )


def _pick_default_section(sections: list[Any], requested: str | None) -> Any:
    if requested:
        for section in sections:
            if section.section_id == requested:
                if not getattr(section, "editable", True):
                    raise WorkflowViolationError(
                        f"Secao '{requested}' e somente leitura.",
                        details={"available_editable_sections": [s.section_id for s in sections if getattr(s, 'editable', True)]},
                    )
                return section
        raise WorkflowViolationError(
            f"Secao '{requested}' nao encontrada no editor.",
            details={"available_sections": [section.section_id for section in sections]},
        )
    if not sections:
        raise WorkflowViolationError("Documento nao expôs secoes editaveis.")
    editable_sections = [section for section in sections if getattr(section, "editable", True)]
    if editable_sections:
        return max(
            editable_sections,
            key=lambda section: (*_section_sort_key(section), len(section.content or "")),
        )
    return max(sections, key=lambda section: len(section.content or ""))


def _normalize_editor_text(value: str) -> str:
    normalized = value or ""
    for _ in range(5):
        updated = html.unescape(normalized)
        if updated == normalized:
            break
        normalized = updated
    normalized = re.sub(r"<!--.*?-->", " ", normalized, flags=re.DOTALL)
    normalized = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", normalized)
    normalized = re.sub(r"(?i)<br\s*/?>", " ", normalized)
    normalized = re.sub(r"<[^>]+>", " ", normalized)
    normalized = (
        normalized
        .replace("\xa0", " ")
        .replace("\u200b", " ")
        .replace("\u200c", " ")
        .replace("\u200d", " ")
        .replace("\ufeff", " ")
    )
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _is_effectively_empty_editor_content(value: str) -> bool:
    normalized = _normalize_editor_text(value)
    normalized = re.sub(r"@[a-z0-9_]+@", " ", normalized, flags=re.IGNORECASE)
    normalized = re.sub(
        r"\b[\wÀ-ÿ.-]+/[A-Z]{2}\s*,?\s*data\s+da\s+assinatura\s+eletr[oô]nica\.?",
        " ",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(r"[^\wÀ-ÿ]+", "", normalized, flags=re.UNICODE)
    return not bool(normalized)


def _document_quality_check(
    text: str,
    signature_status: dict[str, Any],
    *,
    semantic_context: dict[str, Any] | None = None,
    editable_sections_count: int = 0,
    edited_sections: list[str] | None = None,
    editable_section_contents: list[str] | None = None,
) -> dict[str, Any]:
    normalized = text or ""
    semantic_context = semantic_context or {}
    editable_section_contents = editable_section_contents or []
    suspicious_patterns = [
        r"\bsgt\b",
        r"\bsd\b",
        r"\bcb\b",
        r"\bmaj\b",
        r"\bcap\b",
        r"\bten\b",
    ]
    suspicious_terms: list[str] = []
    lower = normalized.lower()
    for pattern in suspicious_patterns:
        for match in re.finditer(pattern, lower, re.IGNORECASE):
            suspicious_terms.append(match.group(0))
    all_template_variables = sorted(set(re.findall(r"@[a-z0-9_]+@", normalized, re.IGNORECASE)))
    standard_template_variables = {
        "@interessados_virgula_espaco@",
        "@processo@",
        "@procedimento@",
        "@unidade@",
        "@usuario_nome@",
        "@usuario@",
        "@descricao@",
    }
    template_variables_remaining = [
        item for item in all_template_variables if item.lower() not in standard_template_variables
    ]
    plain_text = _normalize_editor_text(normalized)
    editable_plain_text = ""
    editable_body_empty = False
    external_sei_hrefs: list[str] = []
    if editable_section_contents:
        normalized_editable_sections = [
            _normalize_editor_text(content or "")
            for content in editable_section_contents
        ]
        raw_editable_html = "\n".join(editable_section_contents)
        external_sei_hrefs = sorted(
            set(
                match.group(1)
                for match in re.finditer(
                    r"""href=["'](https?://[^"']*sei[^"']*)["']""",
                    raw_editable_html,
                    re.IGNORECASE,
                )
            )
        )
        editable_plain_text = " ".join(normalized_editable_sections).strip()
        editable_body_empty = all(
            _is_effectively_empty_editor_content(content or "")
            for content in editable_section_contents
        )
    document_kind = semantic_context.get("document_kind_guess")
    subject = semantic_context.get("subject") or ""
    is_despacho = document_kind == "despacho" or "despacho" in subject.lower()
    dispatch_action_verbs = [
        "encaminh",
        "autoriz",
        "determino",
        "determinar",
        "remet",
        "seguimento",
        "prosseguimento",
        "providenc",
    ]
    has_dispatch_action = any(token in lower for token in dispatch_action_verbs)
    destination_markers = semantic_context.get("involved_units") or []
    has_destination_signal = bool(destination_markers) or bool(
        re.search(r"\b(ao|aos|à|às|para)\b", lower)
    )
    rank_mentions = sorted(
        set(
            match.group(0)
            for match in re.finditer(
                r"\b(?:maj|cap|ten|asp|sgt|cb|sd)\s*(?:bm)?\b",
                lower,
                re.IGNORECASE,
            )
        )
    )
    rank_mentions_without_bm = sorted(
        set(
            match.group(0)
            for match in re.finditer(
                r"\b(?:maj|cap|ten|asp|sgt|cb|sd)\b(?!\s*bm)",
                lower,
                re.IGNORECASE,
            )
        )
    )
    return {
        "char_count": len(normalized),
        "line_count": len([line for line in normalized.splitlines() if line.strip()]),
        "editable_sections_count": editable_sections_count,
        "edited_sections": edited_sections or [],
        "suspicious_rank_terms": sorted(set(suspicious_terms)),
        "rank_mentions": rank_mentions,
        "rank_mentions_without_bm": rank_mentions_without_bm,
        "template_variables_remaining": template_variables_remaining,
        "standard_template_variables_remaining": [
            item for item in all_template_variables if item.lower() in standard_template_variables
        ],
        "external_sei_hrefs": external_sei_hrefs,
        "empty_body_check": (
            editable_body_empty
            if editable_section_contents
            else not bool(plain_text)
        ),
        "signature_status": signature_status,
        "has_signature_marker": "assinado eletronicamente" in lower,
        "needs_signature_review": bool(signature_status.get("signature_pending")),
        "document_profile": {
            "kind": document_kind,
            "subject": subject,
            "dispatch_checks": {
                "applies": is_despacho,
                "has_action_verb": has_dispatch_action if is_despacho else None,
                "has_destination_signal": has_destination_signal if is_despacho else None,
                "signature_expected": is_despacho,
                "signature_pending": bool(signature_status.get("signature_pending")) if is_despacho else None,
            },
        },
    }


def document_edit_preview(
    client: Any,
    numero_ou_id: str,
    *,
    process_id: str | None = None,
    section_id: str | None = None,
) -> dict[str, Any]:
    operation = "document-edit-preview"
    resolved_ids: dict[str, Any] = {}
    try:
        context = _context(client)
        id_documento, id_procedimento, numero_documento = _resolve_document_ids(
            client,
            numero_ou_id,
            id_procedimento=process_id,
        )
        preflight, unit_guard = _process_unit_preflight(client, id_procedimento)
        with unit_guard as switched_to:
            if switched_to:
                preflight["switched"] = True
                preflight["switched_to"] = switched_to
                context = _context(client)
            save_url, sections = client.get_editor_sections(id_documento, id_procedimento)
            target = _pick_default_section(sections, section_id)
            resolved_ids = {
                "id_documento": id_documento,
                "id_procedimento": id_procedimento,
                "numero_documento": numero_documento or numero_ou_id,
            }
            return _result(
                operation=operation,
                context=context,
                resolved_ids=resolved_ids,
                data={
                    "preflight": preflight,
                    "editor": {
                        "save_url_present": bool(save_url),
                        "sections_total": len(sections),
                        "editable_sections_count": len([section for section in sections if getattr(section, "editable", True)]),
                        "selected_section": _section_preview(target),
                        "selected_editable_section": _section_preview(target),
                        "sections": [_section_preview(section) for section in sections],
                    },
                    "confirmation_required": True,
                },
                next_actions=[
                    NextAction(
                        action="document-edit-confirm",
                        label="Salvar conteudo na secao selecionada",
                        params={
                            "numero_ou_id": id_documento,
                            "process_id": id_procedimento,
                            "section_id": target.section_id,
                        },
                    )
                ],
            )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("context"),
            resolved_ids=resolved_ids,
            exc=exc,
        )


def document_quality_check(
    client: Any,
    numero_ou_id: str,
    *,
    process_id: str | None = None,
) -> dict[str, Any]:
    operation = "document-quality-check"
    resolved_ids: dict[str, Any] = {}
    try:
        result = __import__("sei_cli.operations.reading", fromlist=["document_read"]).document_read(
            client,
            numero_ou_id,
            id_procedimento=process_id,
        )
        if not result.get("ok"):
            return result
        resolved_ids = result["resolved_ids"]
        data = result["data"]
        editable_sections_count = 0
        editable_section_contents: list[str] = []
        try:
            _save_url, sections = client.get_editor_sections(
                resolved_ids["id_documento"],
                resolved_ids["id_procedimento"],
            )
            editable_sections = [
                section for section in sections if getattr(section, "editable", True)
            ]
            editable_sections_count = len(editable_sections)
            editable_section_contents = [section.content or "" for section in editable_sections]
        except Exception:
            editable_sections_count = 0
            editable_section_contents = []
        quality = _document_quality_check(
            data.get("text", ""),
            data.get("signature_status") or {},
            semantic_context=data.get("semantic_context") or {},
            editable_sections_count=editable_sections_count,
            edited_sections=[],
            editable_section_contents=editable_section_contents,
        )
        warnings: list[str] = []
        if quality["suspicious_rank_terms"] or quality["rank_mentions_without_bm"]:
            warnings.append("Revisar padronizacao de postos/graduações antes de assinatura ou geração de PDF.")
        if quality["template_variables_remaining"]:
            warnings.append("Documento ainda contém variáveis de template não substituídas.")
        if quality["external_sei_hrefs"]:
            warnings.append("Referência SEI usa href externo; use âncora nativa ancora_sei para evitar relogin.")
        if quality["empty_body_check"]:
            warnings.append("Corpo editável parece vazio.")
        dispatch_checks = quality["document_profile"]["dispatch_checks"]
        if dispatch_checks.get("applies") and not dispatch_checks.get("has_action_verb"):
            warnings.append("Despacho sem verbo de ação claro.")
        if dispatch_checks.get("applies") and not dispatch_checks.get("has_destination_signal"):
            warnings.append("Despacho sem indicação clara de destinatário ou unidade.")
        return _result(
            operation=operation,
            context=result["context"],
            resolved_ids=resolved_ids,
            data={
                "quality_check": quality,
                "document_read": {
                    "document_kind_guess": data.get("semantic_context", {}).get("document_kind_guess"),
                    "subject": data.get("semantic_context", {}).get("subject"),
                },
            },
            next_actions=[
                NextAction(
                    action="document-read",
                    label="Reler documento completo",
                    params={"numero_ou_id": resolved_ids.get("id_documento"), "process_id": resolved_ids.get("id_procedimento")},
                )
            ],
            warnings=warnings,
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("context"),
            resolved_ids=resolved_ids,
            exc=exc,
        )


def document_edit_confirm(
    client: Any,
    numero_ou_id: str,
    *,
    content: str,
    process_id: str | None = None,
    section_id: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    operation = "document-edit-confirm"
    resolved_ids: dict[str, Any] = {}
    try:
        if not confirm:
            raise WorkflowViolationError(
                "Confirmacao explicita obrigatoria para editar documento.",
                details={"expected_flag": "--confirm"},
            )
        context = _context(client)
        id_documento, id_procedimento, numero_documento = _resolve_document_ids(
            client,
            numero_ou_id,
            id_procedimento=process_id,
        )
        preflight, unit_guard = _process_unit_preflight(client, id_procedimento)
        with unit_guard as switched_to:
            if switched_to:
                preflight["switched"] = True
                preflight["switched_to"] = switched_to
                context = _context(client)
            save_url, sections = client.get_editor_sections(id_documento, id_procedimento)
            target = _pick_default_section(sections, section_id)
            ok = client.edit_document_section(
                id_documento,
                id_procedimento,
                target.section_id,
                content,
            )
            if not ok:
                raise WorkflowViolationError("SEI nao confirmou a gravacao do documento.")
            prepare_for_save = getattr(client, "_prepare_editor_content_for_save", None)
            normalized_saved_content = prepare_for_save(content) if callable(prepare_for_save) else content
            target.content = normalized_saved_content
            resolved_ids = {
                "id_documento": id_documento,
                "id_procedimento": id_procedimento,
                "numero_documento": numero_documento or numero_ou_id,
                "section_id": target.section_id,
            }
            quality_result = document_quality_check(
                client,
                id_documento,
                process_id=id_procedimento,
            )
            quality_data = quality_result.get("data", {})
            quality_payload = quality_data.get("quality_check", {})
            quality_payload["edited_sections"] = [target.section_id]
            quality_payload["editable_sections_count"] = len(
                [section for section in sections if getattr(section, "editable", True)]
            )
            quality_warnings = quality_result.get("warnings", [])
            return _result(
                operation=operation,
                context=context,
                resolved_ids=resolved_ids,
                data={
                    "preflight": preflight,
                    "edited_section": _section_preview(target),
                    "save_url_present": bool(save_url),
                    "save_validation": {
                        "target_section_id": target.section_id,
                        "target_has_escaped_structural_html": _has_escaped_structural_html(normalized_saved_content),
                        "preserved_sections_total": max(len(sections) - 1, 0),
                        "preserved_sections_with_escaped_structural_html_before_normalization": len(
                            [
                                section
                                for section in sections
                                if section.section_id != target.section_id
                                and _has_escaped_structural_html(section.content or "")
                            ]
                        ),
                    },
                    "quality_check": quality_payload,
                },
                next_actions=[
                    NextAction(
                        action="document-quality-check",
                        label="Revalidar conteudo salvo",
                        params={"numero_ou_id": id_documento, "process_id": id_procedimento},
                    ),
                    NextAction(
                        action="document-read",
                        label="Ler documento salvo",
                        params={"numero_ou_id": id_documento, "process_id": id_procedimento},
                    ),
                ],
                warnings=quality_warnings,
            )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("context"),
            resolved_ids=resolved_ids,
            exc=exc,
        )


def process_finalize_preview(
    client: Any,
    numero_ou_id_processo: str,
    *,
    document_ids: list[str] | None = None,
) -> dict[str, Any]:
    operation = "process-finalize-preview"
    resolved_ids: dict[str, Any] = {}
    try:
        context = _context(client)
        id_procedimento, numero_processo = _resolve_process_id(client, numero_ou_id_processo)
        preflight, unit_guard = _process_unit_preflight(client, id_procedimento)
        current_signer = _current_signer_profile(client)
        requested_ids = {str(item).strip() for item in (document_ids or []) if str(item).strip()}

        with unit_guard as switched_to:
            if switched_to:
                preflight["switched"] = True
                preflight["switched_to"] = switched_to
                context = _context(client)
            docs = client.get_full_document_tree(id_procedimento)
            process = next(
                (
                    item for item in client.list_processes().recebidos + client.list_processes().gerados
                    if item.id_procedimento == id_procedimento
                ),
                None,
            )
            selected_docs = []
            for doc in docs:
                if requested_ids and doc.id_documento not in requested_ids and (doc.sei_number or "") not in requested_ids:
                    continue
                selected_docs.append(doc)

            documents: list[dict[str, Any]] = []
            authenticate_ids: list[str] = []
            sign_ids: list[str] = []
            warnings: list[str] = []
            for doc in selected_docs:
                doc_type = (doc.tipo or "").lower()
                doc_entry = {
                    "id_documento": doc.id_documento,
                    "numero_documento": doc.sei_number,
                    "nome": doc.nome,
                    "tipo": doc.tipo,
                    "assinado": bool(doc.assinado),
                    "autenticado": bool(doc.autenticado),
                }
                if doc.assinado or doc.autenticado:
                    doc_entry["recommended_action"] = "already_finalized"
                    documents.append(doc_entry)
                    continue

                if doc_type in {"pdf", "documento", "externo"}:
                    doc_entry["recommended_action"] = "authenticate"
                    doc_entry["reason"] = "documento_externo"
                    authenticate_ids.append(doc.id_documento)
                    documents.append(doc_entry)
                    continue

                read_result = document_read(
                    client,
                    doc.id_documento,
                    id_procedimento=id_procedimento,
                )
                if not read_result.get("ok"):
                    doc_entry["recommended_action"] = "skip"
                    doc_entry["reason"] = "read_failed"
                    doc_entry["error"] = read_result.get("error")
                    documents.append(doc_entry)
                    continue

                text = read_result["data"]["text"]
                expected_signer = _extract_expected_signer(text)
                form_signer = client.get_document_sign_form_info(doc.id_documento, id_procedimento)
                signer_validation = _evaluate_signer_compatibility(
                    form_signer=form_signer if form_signer.get("ok") else None,
                    expected_signer=expected_signer,
                    current_signer=current_signer,
                )
                semantic = read_result["data"].get("semantic_context", {})
                doc_entry["expected_signer"] = expected_signer
                doc_entry["form_signer"] = form_signer if form_signer.get("ok") else None
                doc_entry["signer_validation"] = signer_validation
                doc_entry["override_allowed"] = bool(signer_validation.get("override_allowed"))
                doc_entry["summary"] = semantic.get("summary")
                if not form_signer.get("ok"):
                    doc_entry["recommended_action"] = "skip"
                    doc_entry["reason"] = "sign_form_unavailable"
                    doc_entry["form_error"] = form_signer.get("error")
                    warnings.append(
                        f"Documento {doc.sei_number or doc.id_documento} sem formulario de assinatura acessivel; assinatura bloqueada."
                    )
                elif signer_validation["decision"] == "sign":
                    doc_entry["recommended_action"] = "sign"
                    doc_entry["reason"] = signer_validation["reason"]
                    sign_ids.append(doc.id_documento)
                else:
                    doc_entry["recommended_action"] = "skip"
                    doc_entry["reason"] = signer_validation["reason"]
                    if signer_validation["reason"] == "sign_form_does_not_match_current_user":
                        warnings.append(
                            f"Documento {doc.sei_number or doc.id_documento} traz formulario de assinatura para outro usuario/cargo; assinatura bloqueada."
                        )
                    elif expected_signer and signer_validation["reason"] == "tail_indicates_other_signer":
                        warnings.append(
                            f"Documento {doc.sei_number or doc.id_documento} indica outro signatário: {expected_signer.get('name')}."
                        )
                    else:
                        warnings.append(
                            f"Documento {doc.sei_number or doc.id_documento} sem signatário claro no rodapé; assinatura bloqueada."
                        )
                documents.append(doc_entry)

        resolved_ids = {
            "id_procedimento": id_procedimento,
            "numero_processo": numero_processo or numero_ou_id_processo,
            "document_ids": [item["id_documento"] for item in documents],
        }
        return _result(
            operation=operation,
            context=context,
            resolved_ids=resolved_ids,
            data={
                "preflight": {
                    **preflight,
                    "current_unit": context.get("unidade_sigla"),
                },
                "processo": _process_preview(process) if process else {"id_procedimento": id_procedimento, "numero": numero_processo},
                "current_signer": current_signer,
                "documents_total": len(documents),
                "documents": documents,
                "authenticate_document_ids": authenticate_ids,
                "sign_document_ids": sign_ids,
            },
            next_actions=[
                NextAction(
                    action="process-finalize-confirm",
                    label="Autenticar externos e assinar apenas internos compatíveis com o signatário atual",
                    params={
                        "numero_ou_id_processo": id_procedimento,
                        "document_ids": authenticate_ids + sign_ids,
                    },
                )
            ],
            warnings=warnings,
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("context"),
            resolved_ids=resolved_ids,
            exc=exc,
        )


def process_finalize_confirm(
    client: Any,
    numero_ou_id_processo: str,
    *,
    document_ids: list[str] | None = None,
    force_sign_document_ids: list[str] | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    operation = "process-finalize-confirm"
    resolved_ids: dict[str, Any] = {}
    try:
        if not confirm:
            raise WorkflowViolationError(
                "Confirmacao explicita obrigatoria para assinar/autenticar documentos.",
                details={"expected_flag": "--confirm"},
            )
        preview = process_finalize_preview(client, numero_ou_id_processo, document_ids=document_ids)
        if not preview.get("ok"):
            preview["operation"] = operation
            return preview

        id_procedimento = preview["resolved_ids"]["id_procedimento"]
        force_sign_set = {str(item).strip() for item in (force_sign_document_ids or []) if str(item).strip()}
        auth_results: list[dict[str, Any]] = []
        sign_results: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []

        for item in preview["data"]["documents"]:
            if item.get("recommended_action") != "skip":
                continue
            item_ids = {item["id_documento"], item.get("numero_documento")}
            if force_sign_set & {str(value) for value in item_ids if value}:
                validation = item.get("signer_validation") or {}
                if validation.get("override_allowed") and validation.get("form_matches"):
                    item["recommended_action"] = "sign"
                    item["reason"] = "forced_after_signer_review"
                    continue
            skipped.append(
                {
                    "id_documento": item["id_documento"],
                    "numero_documento": item.get("numero_documento"),
                    "nome": item.get("nome"),
                    "reason": item.get("reason"),
                }
            )

        for doc_id in preview["data"].get("authenticate_document_ids", []):
            result = client.authenticate_document(doc_id, id_procedimento)
            result["id_documento"] = doc_id
            auth_results.append(result)
        sign_doc_ids = [
            item["id_documento"]
            for item in preview["data"]["documents"]
            if item.get("recommended_action") == "sign"
        ]
        for doc_id in sign_doc_ids:
            result = client.sign_document(doc_id, id_procedimento)
            result["id_documento"] = doc_id
            sign_results.append(result)

        errors = [item for item in auth_results + sign_results if item.get("error") or item.get("errors")]
        if errors:
            first = errors[0]
            message = first.get("error") or (first.get("errors") or ["Falha ao finalizar documentos."])[0]
            raise WorkflowViolationError(
                message,
                details={
                    "id_procedimento": id_procedimento,
                    "auth_results": auth_results,
                    "sign_results": sign_results,
                    "skipped": skipped,
                },
            )

        resolved_ids = dict(preview["resolved_ids"])
        return _result(
            operation=operation,
            context=preview["context"],
            resolved_ids=resolved_ids,
            data={
                "preflight": preview["data"]["preflight"],
                "processo": preview["data"]["processo"],
                "authenticated_total": len(auth_results),
                "signed_total": len(sign_results),
                "skipped_total": len(skipped),
                "force_sign_document_ids": sorted(force_sign_set),
                "authenticated": auth_results,
                "signed": sign_results,
                "skipped": skipped,
            },
            warnings=preview.get("warnings", []),
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("preview", {}).get("context") if "preview" in locals() else locals().get("context"),
            resolved_ids=resolved_ids or locals().get("preview", {}).get("resolved_ids"),
            exc=exc,
        )


def process_marker_update_preview(
    client: Any,
    numero_ou_id: str,
    *,
    marker: str | None = None,
    texto: str | None = None,
) -> dict[str, Any]:
    operation = "process-marker-update-preview"
    resolved_ids: dict[str, Any] = {}
    try:
        context = _context(client)
        read_result = process_marker_read(client, numero_ou_id)
        if not read_result.get("ok"):
            read_result["operation"] = operation
            return read_result
        id_procedimento = read_result["resolved_ids"]["id_procedimento"]
        resolved_ids = dict(read_result["resolved_ids"])
        selected_marker = _resolve_process_marker(client, id_procedimento, marker)
        suggested_text = (texto or "").strip() or selected_marker.get("texto") or ""
        return _result(
            operation=operation,
            context=context,
            resolved_ids=resolved_ids,
            data={
                "processo": read_result["data"].get("processo"),
                "marker_authority": read_result["data"].get("marker_authority"),
                "selected_marker": selected_marker,
                "current_markers_total": read_result["data"].get("current_markers_total"),
                "current_markers": read_result["data"].get("current_markers"),
                "mutation_preview": {
                    "marcador_id": selected_marker.get("marcador_id"),
                    "current_text": selected_marker.get("texto") or "",
                    "new_text": suggested_text,
                },
                "confirmation_required": True,
            },
            next_actions=[
                NextAction(
                    action="process-marker-update-confirm",
                    label="Alterar o texto do marcador do processo",
                    params={
                        "numero_ou_id": id_procedimento,
                        "marker": selected_marker.get("marcador_id"),
                        "texto": suggested_text,
                    },
                )
            ],
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("context"),
            resolved_ids=resolved_ids,
            exc=exc,
        )


def process_marker_update_confirm(
    client: Any,
    numero_ou_id: str,
    *,
    marker: str | None = None,
    texto: str = "",
    confirm: bool = False,
) -> dict[str, Any]:
    operation = "process-marker-update-confirm"
    resolved_ids: dict[str, Any] = {}
    try:
        if not confirm:
            raise WorkflowViolationError(
                "Confirmacao explicita obrigatoria para alterar o texto do marcador.",
                details={"expected_flag": "--confirm"},
            )
        preview = process_marker_update_preview(client, numero_ou_id, marker=marker, texto=texto)
        if not preview.get("ok"):
            preview["operation"] = operation
            return preview
        selected_marker = preview["data"]["selected_marker"]
        new_text = preview["data"]["mutation_preview"]["new_text"]
        id_procedimento = preview["resolved_ids"]["id_procedimento"]
        resolved_ids = dict(preview["resolved_ids"])
        applied = client.update_marcador(
            id_procedimento,
            selected_marker["marcador_id"],
            new_text,
        )
        if not applied:
            raise WorkflowViolationError(
                "SEI não confirmou a alteração do texto do marcador.",
                details={
                    "id_procedimento": id_procedimento,
                    "marcador_id": selected_marker["marcador_id"],
                },
            )
        return _result(
            operation=operation,
            context=preview["context"],
            resolved_ids=resolved_ids,
            data={
                "marker_authority": preview["data"].get("marker_authority"),
                "selected_marker": selected_marker,
                "mutation": {
                    "applied": True,
                    "marcador_id": selected_marker["marcador_id"],
                    "new_text": new_text,
                },
            },
            next_actions=[
                NextAction(
                    action="process-marker-read",
                    label="Rever marcadores do processo",
                    params={"numero_ou_id": id_procedimento},
                )
            ],
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("context"),
            resolved_ids=resolved_ids,
            exc=exc,
        )


def tracking_group_create_preview(
    client: Any,
    nome: str,
) -> dict[str, Any]:
    operation = "tracking-group-create-preview"
    try:
        context = _context(client)
        group_name = nome.strip()
        if not group_name:
            raise WorkflowViolationError("Nome do grupo de acompanhamento especial é obrigatório.")
        groups = [
            _normalize_tracking_group_item(item)
            for item in client.listar_grupos_acompanhamento()
        ]
        groups = [item for item in groups if item.get("id") and item.get("nome")]
        existing = _resolve_tracking_group_reference(groups, group_name)
        warnings: list[str] = []
        if existing:
            warnings.append("Já existe grupo de acompanhamento especial com nome igual ou compatível na unidade atual.")
        return _result(
            operation=operation,
            context=context,
            data={
                "tracking_scope": {
                    "unit_specific": True,
                    "current_unit": context.get("unidade_sigla"),
                    "rule": "Grupo de acompanhamento especial é específico da unidade atual.",
                },
                "group_preview": {
                    "nome": group_name,
                    "already_exists": existing is not None,
                    "existing_group": existing,
                },
                "available_groups_total": len(groups),
                "available_groups": groups,
                "confirmation_required": True,
            },
            next_actions=[
                NextAction(
                    action="tracking-group-create-confirm",
                    label="Criar grupo de acompanhamento especial",
                    params={"nome": group_name},
                )
            ],
            warnings=warnings,
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("context"),
            exc=exc,
        )


def tracking_group_create_confirm(
    client: Any,
    nome: str,
    *,
    confirm: bool = False,
) -> dict[str, Any]:
    operation = "tracking-group-create-confirm"
    try:
        if not confirm:
            raise WorkflowViolationError(
                "Confirmação explícita obrigatória para criar grupo de acompanhamento especial.",
                details={"expected_flag": "--confirm"},
            )
        preview = tracking_group_create_preview(client, nome)
        if not preview.get("ok"):
            preview["operation"] = operation
            return preview
        existing = preview["data"]["group_preview"].get("existing_group")
        if existing:
            return _result(
                operation=operation,
                context=preview["context"],
                resolved_ids={"grupo_id": existing.get("id")},
                data={
                    "tracking_scope": preview["data"].get("tracking_scope"),
                    "group": existing,
                    "mutation": {
                        "created": False,
                        "already_exists": True,
                        "message": f"Grupo {existing.get('nome')} já existia na unidade atual.",
                    },
                },
                warnings=preview.get("warnings", []),
            )
        group_id = client.criar_grupo_acompanhamento(preview["data"]["group_preview"]["nome"])
        groups = [
            _normalize_tracking_group_item(item)
            for item in client.listar_grupos_acompanhamento()
        ]
        created = _resolve_tracking_group_reference(groups, str(group_id)) or _resolve_tracking_group_reference(
            groups,
            preview["data"]["group_preview"]["nome"],
        )
        if not created:
            raise WorkflowViolationError(
                "SEI retornou sucesso, mas o grupo não apareceu na lista após a criação.",
                details={"grupo_id": group_id, "nome": preview["data"]["group_preview"]["nome"]},
            )
        return _result(
            operation=operation,
            context=preview["context"],
            resolved_ids={"grupo_id": created.get("id")},
            data={
                "tracking_scope": preview["data"].get("tracking_scope"),
                "group": created,
                "mutation": {
                    "created": True,
                    "already_exists": False,
                    "message": f"Grupo {created.get('nome')} criado na unidade atual.",
                },
            },
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("preview", {}).get("context"),
            resolved_ids=locals().get("resolved_ids"),
            exc=exc,
        )


def process_watch_preview(
    client: Any,
    numero_ou_id: str,
    *,
    group: str,
    observacao: str | None = None,
) -> dict[str, Any]:
    operation = "process-watch-preview"
    resolved_ids: dict[str, Any] = {}
    try:
        context = _context(client)
        id_procedimento, numero_processo, visible_process = _resolve_visible_process_for_marker(
            client,
            numero_ou_id,
        )
        resolved_ids = {
            "id_procedimento": id_procedimento,
            "numero_processo": numero_processo or numero_ou_id,
        }
        groups = [
            _normalize_tracking_group_item(item)
            for item in client.listar_grupos_acompanhamento()
        ]
        groups = [item for item in groups if item.get("id") and item.get("nome")]
        selected_group = _resolve_tracking_group_reference(groups, group)
        if not selected_group:
            raise WorkflowViolationError(
                f"Grupo de acompanhamento especial '{group}' não encontrado na unidade atual.",
                details={"group": group, "available_groups": groups},
            )
        current_tracking = _process_in_tracking_list(client, id_procedimento, numero_processo)
        mutation_action = "update" if current_tracking else "add"
        note = (observacao or "").strip()
        return _result(
            operation=operation,
            context=context,
            resolved_ids={
                **resolved_ids,
                "grupo_id": selected_group.get("id"),
            },
            data={
                "processo": _process_preview(visible_process),
                "tracking_authority": {
                    "source": "current_unit_process_list",
                    "visible_in_current_unit": True,
                    "caixa": visible_process.caixa,
                    "unit_specific": True,
                    "rule": "Processo visível em recebidos/gerados da unidade atual pode ser colocado em acompanhamento especial nesta unidade, independentemente da unidade de origem.",
                },
                "current_tracking": {
                    "tracked": current_tracking is not None,
                    "processo": current_tracking,
                },
                "selected_group": selected_group,
                "observacao": note,
                "mutation_preview": {
                    "action": mutation_action,
                    "grupo_id": selected_group.get("id"),
                    "observacao": note,
                },
                "available_groups_total": len(groups),
                "available_groups": groups,
                "confirmation_required": True,
            },
            next_actions=[
                NextAction(
                    action="process-watch-confirm",
                    label="Colocar processo em acompanhamento especial",
                    params={
                        "numero_ou_id": id_procedimento,
                        "group": selected_group.get("id"),
                        "observacao": note,
                    },
                )
            ],
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("context"),
            resolved_ids=resolved_ids,
            exc=exc,
        )


def process_watch_confirm(
    client: Any,
    numero_ou_id: str,
    *,
    group: str,
    observacao: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    operation = "process-watch-confirm"
    try:
        if not confirm:
            raise WorkflowViolationError(
                "Confirmação explícita obrigatória para colocar processo em acompanhamento especial.",
                details={"expected_flag": "--confirm"},
            )
        preview = process_watch_preview(client, numero_ou_id, group=group, observacao=observacao)
        if not preview.get("ok"):
            preview["operation"] = operation
            return preview
        process_id = preview["resolved_ids"]["id_procedimento"]
        selected_group = preview["data"]["selected_group"]
        group_id = str(selected_group["id"])
        note = preview["data"].get("observacao") or ""
        action = preview["data"]["mutation_preview"]["action"]
        if action == "update":
            ok = client.alterar_acompanhamento_especial(process_id, group_id, note)
        else:
            ok = client.add_acompanhamento_especial(process_id, group_id, note)
        if not ok:
            raise WorkflowViolationError(
                "SEI não confirmou o acompanhamento especial do processo.",
                details={"id_procedimento": process_id, "grupo_id": group_id, "action": action},
            )
        tracked_process = _process_in_tracking_list(
            client,
            process_id,
            preview["resolved_ids"].get("numero_processo"),
        )
        if not tracked_process:
            raise TrackingVerificationInconclusiveError(
                "SEI retornou sucesso, mas a verificação do acompanhamento especial foi inconclusiva.",
                details={
                    "id_procedimento": process_id,
                    "grupo_id": group_id,
                    "action": action,
                    "mutation_applied": True,
                    "verification_attempts": [
                        "acompanhamento_listar",
                        "acompanhamento_gerenciar",
                    ],
                    "hint": "A listagem geral pode estar paginada/filtrada; confira process-watch-read ou a tela local do processo.",
                },
            )
        return _result(
            operation=operation,
            context=preview["context"],
            resolved_ids=preview["resolved_ids"],
            data={
                "processo": preview["data"].get("processo"),
                "tracking_authority": preview["data"].get("tracking_authority"),
                "selected_group": selected_group,
                "observacao": note,
                "mutation": {
                    "applied": True,
                    "action": action,
                    "message": f"Processo {process_id} em acompanhamento especial no grupo {group_id}.",
                },
                "verification": {
                    "tracked": True,
                    "processo": tracked_process,
                },
            },
            next_actions=[
                NextAction(
                    action="process-watch-read",
                    label="Rever acompanhamento especial do processo",
                    params={"numero_ou_id": process_id},
                ),
                NextAction(
                    action="process-conclude-preview",
                    label="Preparar conclusão do processo na unidade atual",
                    params={"numero_ou_id": process_id},
                ),
            ],
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("preview", {}).get("context"),
            resolved_ids=locals().get("preview", {}).get("resolved_ids"),
            exc=exc,
        )


def _default_archive_observation(process_data: dict[str, Any]) -> str:
    parts = [
        "Concluído/arquivado na unidade atual",
        str(process_data.get("tipo") or "").strip(),
        str(process_data.get("especificacao") or "").strip(),
    ]
    return " - ".join(item for item in parts if item)


def process_archive_preview(
    client: Any,
    numero_ou_id: str,
    *,
    group: str | None = None,
    observacao: str | None = None,
    reabrir_em: str | None = None,
    reabrir_dias: str | None = None,
    reabrir_dias_uteis: bool = False,
) -> dict[str, Any]:
    operation = "process-archive-preview"
    resolved_ids: dict[str, Any] = {}
    try:
        context = _context(client)
        id_procedimento, numero_processo, visible_process = _resolve_visible_process_for_marker(
            client,
            numero_ou_id,
        )
        resolved_ids = {
            "id_procedimento": id_procedimento,
            "numero_processo": numero_processo or numero_ou_id,
        }
        process_data = _process_preview(visible_process)
        tracked_process = _process_in_tracking_list(client, id_procedimento, numero_processo)
        groups = [
            _normalize_tracking_group_item(item)
            for item in client.listar_grupos_acompanhamento()
        ]
        groups = [item for item in groups if item.get("id") and item.get("nome")]
        selected_group = _resolve_tracking_group_reference(groups, group)
        if not tracked_process and not selected_group:
            raise WorkflowViolationError(
                "Para arquivar processo ainda não acompanhado, informe --group/--grupo com ID ou nome de grupo de acompanhamento especial.",
                details={
                    "group": group,
                    "available_groups": groups,
                    "hint": "Use tracking-group-catalog para listar grupos ou tracking-group-create-* para criar um grupo.",
                },
            )
        note = (observacao or "").strip() or _default_archive_observation(process_data)
        tracking_action = "skip_already_tracked" if tracked_process else "add"
        conclude_preview = process_conclude_preview(
            client,
            id_procedimento,
            reabrir_em=reabrir_em,
            reabrir_dias=reabrir_dias,
            reabrir_dias_uteis=reabrir_dias_uteis,
        )
        if not conclude_preview.get("ok"):
            conclude_preview["operation"] = operation
            return conclude_preview
        resolved_ids = {
            **(conclude_preview.get("resolved_ids") or {}),
            **resolved_ids,
        }
        if selected_group:
            resolved_ids["grupo_id"] = selected_group.get("id")
        return _result(
            operation=operation,
            context=context,
            resolved_ids=resolved_ids,
            data={
                "processo": process_data,
                "tracking_authority": {
                    "source": "current_unit_process_list",
                    "visible_in_current_unit": True,
                    "caixa": visible_process.caixa,
                    "unit_specific": True,
                    "rule": "Arquivamento seguro exige acompanhamento especial na unidade atual antes da conclusão.",
                },
                "current_tracking": {
                    "tracked": tracked_process is not None,
                    "processo": tracked_process,
                },
                "selected_group": selected_group,
                "observacao": note,
                "archive_plan": {
                    "tracking_action": tracking_action,
                    "will_apply_tracking": tracking_action != "skip_already_tracked",
                    "will_conclude": True,
                    "order": ["ensure_tracking", "conclude_process"],
                },
                "conclude_preview": conclude_preview["data"],
                "available_groups_total": len(groups),
                "available_groups": groups,
                "confirmation_required": True,
            },
            next_actions=[
                NextAction(
                    action="process-archive-confirm",
                    label="Colocar em acompanhamento especial se necessário e concluir processo",
                    params={
                        "numero_ou_id": id_procedimento,
                        "group": (selected_group or {}).get("id") if selected_group else group,
                        "observacao": note,
                        "reabrir_em": reabrir_em,
                        "reabrir_dias": reabrir_dias,
                        "reabrir_dias_uteis": reabrir_dias_uteis,
                    },
                )
            ],
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("context"),
            resolved_ids=resolved_ids,
            exc=exc,
        )


def process_archive_confirm(
    client: Any,
    numero_ou_id: str,
    *,
    group: str | None = None,
    observacao: str | None = None,
    reabrir_em: str | None = None,
    reabrir_dias: str | None = None,
    reabrir_dias_uteis: bool = False,
    confirm: bool = False,
) -> dict[str, Any]:
    operation = "process-archive-confirm"
    try:
        if not confirm:
            raise WorkflowViolationError(
                "Confirmação explícita obrigatória para arquivar/concluir processo.",
                details={"expected_flag": "--confirm"},
            )
        preview = process_archive_preview(
            client,
            numero_ou_id,
            group=group,
            observacao=observacao,
            reabrir_em=reabrir_em,
            reabrir_dias=reabrir_dias,
            reabrir_dias_uteis=reabrir_dias_uteis,
        )
        if not preview.get("ok"):
            preview["operation"] = operation
            return preview
        process_id = preview["resolved_ids"]["id_procedimento"]
        tracking_result: dict[str, Any] | None = None
        tracking_warnings: list[str] = []
        if preview["data"]["archive_plan"]["will_apply_tracking"]:
            selected_group = preview["data"].get("selected_group") or {}
            tracking_result = process_watch_confirm(
                client,
                process_id,
                group=str(selected_group.get("id") or group or ""),
                observacao=preview["data"].get("observacao") or "",
                confirm=True,
            )
            if not tracking_result.get("ok"):
                error = tracking_result.get("error") or {}
                details = error.get("details") or {}
                if error.get("code") != "tracking_verification_inconclusive" or not details.get("mutation_applied"):
                    tracking_result["operation"] = operation
                    return tracking_result
                tracking_warnings.append(
                    "Acompanhamento especial: SEI aceitou a mutação, mas a verificação ficou inconclusiva; conclusão executada por autorização explícita do arquivamento."
                )
        else:
            tracking_result = {
                "ok": True,
                "operation": "process-watch-read",
                "data": {
                    "current_tracking": preview["data"].get("current_tracking"),
                    "mutation": {"applied": False, "action": "skip_already_tracked"},
                },
            }
        conclude_result = process_conclude_confirm(
            client,
            process_id,
            reabrir_em=reabrir_em,
            reabrir_dias=reabrir_dias,
            reabrir_dias_uteis=reabrir_dias_uteis,
            confirm=True,
        )
        if not conclude_result.get("ok"):
            conclude_result["operation"] = operation
            return conclude_result
        return _result(
            operation=operation,
            context=conclude_result.get("context") or preview["context"],
            resolved_ids=preview["resolved_ids"],
            data={
                "processo": preview["data"].get("processo"),
                "tracking_authority": preview["data"].get("tracking_authority"),
                "selected_group": preview["data"].get("selected_group"),
                "observacao": preview["data"].get("observacao"),
                "archive": {
                    "tracked_before_conclude": True,
                    "concluded": True,
                    "order": ["ensure_tracking", "conclude_process"],
                },
                "tracking_result": {
                    "ok": tracking_result.get("ok"),
                    "operation": tracking_result.get("operation"),
                    "mutation": (tracking_result.get("data") or {}).get("mutation"),
                    "verification": (tracking_result.get("data") or {}).get("verification"),
                    "current_tracking": (tracking_result.get("data") or {}).get("current_tracking"),
                    "error": tracking_result.get("error"),
                },
                "conclude_result": conclude_result.get("data"),
            },
            next_actions=[
                NextAction(
                    action="process-reopen-preview",
                    label="Verificar se a reabertura está disponível nesta unidade",
                    params={"numero_ou_id_processo": process_id},
                )
            ],
            warnings=preview.get("warnings", []) + tracking_warnings + conclude_result.get("warnings", []),
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("preview", {}).get("context"),
            resolved_ids=locals().get("preview", {}).get("resolved_ids"),
            exc=exc,
        )


def process_pdf_preview(
    client: Any,
    numero_ou_id: str,
    *,
    output_path: str | None = None,
) -> dict[str, Any]:
    operation = "process-pdf-preview"
    resolved_ids: dict[str, Any] = {}
    try:
        context = _context(client)
        id_procedimento, numero_processo = _resolve_process_id(client, numero_ou_id)
        preflight, unit_guard = _process_unit_preflight(client, id_procedimento)
        process_data = None
        with unit_guard as switched_to:
            if switched_to:
                preflight["switched"] = True
                preflight["switched_to"] = switched_to
                context = _context(client)
            process_result = process_marker_read(client, id_procedimento)
            if process_result.get("ok"):
                process_data = process_result["data"].get("processo")
                numero_processo = numero_processo or process_result["resolved_ids"].get("numero_processo")
        target_path = _default_process_pdf_path(id_procedimento, output_path)
        resolved_ids = {
            "id_procedimento": id_procedimento,
            "numero_processo": numero_processo or numero_ou_id,
        }
        return _result(
            operation=operation,
            context=context,
            resolved_ids=resolved_ids,
            data={
                "processo": process_data,
                "preflight": preflight,
                "download_preview": {
                    "kind": "process_pdf",
                    "output_path": target_path,
                    "will_overwrite": Path(target_path).exists(),
                },
                "confirmation_required": True,
            },
            next_actions=[
                NextAction(
                    action="process-pdf-confirm",
                    label="Gerar PDF do processo",
                    params={"numero_ou_id": id_procedimento, "output_path": target_path},
                )
            ],
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("context"),
            resolved_ids=resolved_ids,
            exc=exc,
        )


def process_pdf_confirm(
    client: Any,
    numero_ou_id: str,
    *,
    output_path: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    operation = "process-pdf-confirm"
    resolved_ids: dict[str, Any] = {}
    try:
        if not confirm:
            raise WorkflowViolationError(
                "Confirmacao explicita obrigatoria para gerar PDF do processo.",
                details={"expected_flag": "--confirm"},
            )
        preview = process_pdf_preview(client, numero_ou_id, output_path=output_path)
        if not preview.get("ok"):
            preview["operation"] = operation
            return preview
        id_procedimento = preview["resolved_ids"]["id_procedimento"]
        target_path = preview["data"]["download_preview"]["output_path"]
        path = client.download_pdf(id_procedimento, output_path=target_path)
        file_path = Path(path)
        resolved_ids = dict(preview["resolved_ids"])
        return _result(
            operation=operation,
            context=_context(client),
            resolved_ids=resolved_ids,
            data={
                "preflight": preview["data"]["preflight"],
                "download": {
                    "kind": "process_pdf",
                    "path": path,
                    "size_bytes": file_path.stat().st_size if file_path.exists() else None,
                },
            },
            next_actions=[],
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("context"),
            resolved_ids=resolved_ids,
            exc=exc,
        )


def document_pdf_preview(
    client: Any,
    numero_ou_id: str,
    *,
    process_id: str | None = None,
    output_path: str | None = None,
) -> dict[str, Any]:
    operation = "document-pdf-preview"
    resolved_ids: dict[str, Any] = {}
    try:
        context = _context(client)
        if process_id:
            id_documento, _docs = _resolve_document_id_with_process(
                client,
                numero_ou_id,
                id_procedimento=process_id,
            )
            id_procedimento = process_id
            numero_documento = numero_ou_id
        else:
            id_documento, id_procedimento, numero_documento = _resolve_document_ids(
                client,
                numero_ou_id,
                id_procedimento=None,
            )
        preflight, unit_guard = _process_unit_preflight(client, id_procedimento)
        document_data = None
        with unit_guard as switched_to:
            if switched_to:
                preflight["switched"] = True
                preflight["switched_to"] = switched_to
                context = _context(client)
            read_result = __import__("sei_cli.operations.reading", fromlist=["document_read"]).document_read(
                client,
                id_documento,
                id_procedimento=id_procedimento,
            )
            if read_result.get("ok"):
                document_data = read_result["data"].get("documento")
                numero_documento = numero_documento or read_result["resolved_ids"].get("numero_documento")
        target_path = _default_document_pdf_path(id_documento, output_path)
        resolved_ids = {
            "id_documento": id_documento,
            "id_procedimento": id_procedimento,
            "numero_documento": numero_documento or numero_ou_id,
        }
        return _result(
            operation=operation,
            context=context,
            resolved_ids=resolved_ids,
            data={
                "documento": document_data,
                "preflight": preflight,
                "download_preview": {
                    "kind": "document_pdf",
                    "output_path": target_path,
                    "will_overwrite": Path(target_path).exists(),
                },
                "confirmation_required": True,
            },
            next_actions=[
                NextAction(
                    action="document-pdf-confirm",
                    label="Gerar PDF do documento",
                    params={
                        "numero_ou_id": id_documento,
                        "process_id": id_procedimento,
                        "output_path": target_path,
                    },
                )
            ],
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("context"),
            resolved_ids=resolved_ids,
            exc=exc,
        )


def document_pdf_confirm(
    client: Any,
    numero_ou_id: str,
    *,
    process_id: str | None = None,
    output_path: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    operation = "document-pdf-confirm"
    resolved_ids: dict[str, Any] = {}
    try:
        if not confirm:
            raise WorkflowViolationError(
                "Confirmacao explicita obrigatoria para gerar PDF do documento.",
                details={"expected_flag": "--confirm"},
            )
        preview = document_pdf_preview(
            client,
            numero_ou_id,
            process_id=process_id,
            output_path=output_path,
        )
        if not preview.get("ok"):
            preview["operation"] = operation
            return preview
        id_documento = preview["resolved_ids"]["id_documento"]
        id_procedimento = preview["resolved_ids"]["id_procedimento"]
        target_path = preview["data"]["download_preview"]["output_path"]
        path = client.download_document_pdf(id_documento, id_procedimento, output_path=target_path)
        file_path = Path(path)
        resolved_ids = dict(preview["resolved_ids"])
        return _result(
            operation=operation,
            context=_context(client),
            resolved_ids=resolved_ids,
            data={
                "preflight": preview["data"]["preflight"],
                "download": {
                    "kind": "document_pdf",
                    "path": path,
                    "size_bytes": file_path.stat().st_size if file_path.exists() else None,
                },
            },
            next_actions=[],
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("context"),
            resolved_ids=resolved_ids,
            exc=exc,
        )


def environment_triage_apply(
    client: Any,
    *,
    only_new: bool = False,
    only_changed: bool = False,
    only_unmarked: bool = False,
    include_marked_review: bool = False,
    offset: int = 0,
    limit: int = 5,
    mode: str = "contextual",
    sample_size: int = 3,
    confirm: bool = False,
) -> dict[str, Any]:
    operation = "environment-triage-apply"
    try:
        if not confirm:
            raise WorkflowViolationError(
                "Confirmacao explicita obrigatoria para aplicar a triagem de ambiente.",
                details={"expected_flag": "--confirm"},
            )
        preview = environment_triage_preview(
            client,
            only_new=only_new,
            only_changed=only_changed,
            only_unmarked=only_unmarked,
            include_marked_review=include_marked_review,
            offset=offset,
            limit=limit,
            mode=mode,
            sample_size=sample_size,
        )
        if not preview.get("ok"):
            preview["operation"] = operation
            return preview

        applied: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for item in preview["data"].get("candidates", []):
            processo = item.get("processo") or {}
            id_procedimento = processo.get("id_procedimento")
            selected_marker = item.get("selected_marker")
            marker_action = item.get("marker_action")
            suggested_text = item.get("suggested_marker_text") or ""
            if not id_procedimento or not selected_marker or marker_action not in {"create", "update"}:
                skipped.append(
                    {
                        "processo": processo,
                        "marker_action": marker_action,
                        "reason": "no_mutation_needed_or_no_marker_suggestion",
                    }
                )
                continue
            marker_id = selected_marker.get("marcador_id")
            if marker_action == "create":
                ok = client.set_marcador(id_procedimento, marker_id, suggested_text)
            else:
                current_markers = item.get("current_markers") or []
                current_marker_id = (current_markers[0].get("marcador_id") if current_markers else marker_id)
                ok = client.update_marcador(id_procedimento, current_marker_id, suggested_text)
            if ok:
                applied.append(
                    {
                        "processo": processo,
                        "marker_action": marker_action,
                        "selected_marker": selected_marker,
                        "text": suggested_text,
                    }
                )
            else:
                skipped.append(
                    {
                        "processo": processo,
                        "marker_action": marker_action,
                        "reason": "mutation_not_confirmed",
                    }
                )

        return _result(
            operation=operation,
            context=preview["context"],
            data={
                "applied_total": len(applied),
                "skipped_total": len(skipped),
                "applied": applied,
                "skipped": skipped,
            },
        )
    except Exception as exc:
        return _error_result(operation=operation, exc=exc)


def signature_block_add_document_preview(
    client: Any,
    block_numero: str,
    numero_ou_id_documento: str,
    *,
    process_id: str | None = None,
    disponibilizar: bool = False,
) -> dict[str, Any]:
    operation = "signature-block-add-document-preview"
    resolved_ids: dict[str, Any] = {"block_numero": block_numero}
    try:
        context = _context(client)
        block_result = __import__("sei_cli.operations.reading", fromlist=["signature_block_read"]).signature_block_read(
            client,
            block_numero,
        )
        if not block_result.get("ok"):
            block_result["operation"] = operation
            return block_result
        block_unit = block_result.get("context", {}).get("unidade_sigla") or context.get("unidade_sigla")

        id_documento, id_procedimento, numero_documento = _resolve_document_ids(
            client,
            numero_ou_id_documento,
            id_procedimento=process_id,
        )
        preflight, unit_guard = _process_unit_preflight(client, id_procedimento)
        with unit_guard as switched_to:
            if switched_to:
                preflight["switched"] = True
                preflight["switched_to"] = switched_to
                context = _context(client)
            doc_result = __import__("sei_cli.operations.reading", fromlist=["document_read"]).document_read(
                client,
                id_documento,
                id_procedimento=id_procedimento,
            )
            if not doc_result.get("ok"):
                doc_result["operation"] = operation
                return doc_result
        _best_effort_switch_to_unit(client, block_unit)
        context = _context(client)

        numero_documento_resolvido = numero_documento or doc_result["resolved_ids"].get("numero_documento")
        resolved_ids.update(
            {
                "id_documento": id_documento,
                "id_procedimento": id_procedimento,
                "numero_documento": numero_documento_resolvido,
            }
        )
        warnings: list[str] = []
        block_state = block_result["data"]["bloco"].get("estado")
        already_in_block = any(
            _block_document_matches(
                item,
                id_documento=id_documento,
                numero_documento=numero_documento_resolvido,
            )
            for item in block_result["data"].get("documents", [])
        )
        normalized_state = str(block_state or "").casefold()
        if normalized_state == "disponibilizado":
            warnings.append("Bloco está disponibilizado; pode ser necessário cancelar a disponibilização antes de alterá-lo.")
        if normalized_state == "recebido":
            warnings.append("Bloco em estado 'Recebido' normalmente não aceita inclusão de documentos.")
        if already_in_block:
            warnings.append("Documento já consta neste bloco.")
        if doc_result["data"].get("signature_status", {}).get("signed"):
            warnings.append("Documento já consta como assinado.")

        return _result(
            operation=operation,
            context=context,
            resolved_ids=resolved_ids,
            data={
                "preflight": preflight,
                "bloco": block_result["data"]["bloco"],
                "documento": doc_result["data"]["documento"],
                "document_access": {
                    "signature_status": doc_result["data"].get("signature_status"),
                    "action_context": doc_result["data"].get("action_context"),
                },
                "mutation_preview": {
                    "block_numero": block_numero,
                    "id_documento": id_documento,
                    "disponibilizar": disponibilizar,
                    "already_in_block": already_in_block,
                },
                "confirmation_required": True,
            },
            next_actions=[
                NextAction(
                    action="signature-block-add-document-confirm",
                    label="Incluir documento no bloco",
                    params={
                        "block_numero": block_numero,
                        "numero_ou_id_documento": id_documento,
                        "process_id": id_procedimento,
                        "disponibilizar": disponibilizar,
                    },
                )
            ],
            warnings=warnings,
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("context"),
            resolved_ids=resolved_ids,
            exc=exc,
        )


def signature_block_recall_preview(
    client: Any,
    block_numero: str,
) -> dict[str, Any]:
    operation = "signature-block-recall-preview"
    resolved_ids: dict[str, Any] = {"block_numero": block_numero}
    try:
        context = _context(client)
        block_result = __import__("sei_cli.operations.reading", fromlist=["signature_block_read"]).signature_block_read(
            client,
            block_numero,
        )
        if not block_result.get("ok"):
            block_result["operation"] = operation
            return block_result

        bloco = block_result["data"]["bloco"]
        estado = str(bloco.get("estado") or "")
        normalized_state = estado.casefold()
        action_name: str | None = None
        action_label: str | None = None
        warnings: list[str] = []

        if normalized_state == "disponibilizado":
            action_name = "cancelar_disponibilizacao"
            action_label = "Cancelar disponibilização do bloco"
        elif normalized_state == "recebido":
            action_name = "devolver"
            action_label = "Devolver bloco para a unidade de origem"
            warnings.append("Bloco em estado 'Recebido' será devolvido para a unidade de origem.")
        else:
            raise UnsupportedStateError(
                f"Bloco {block_numero} está em estado '{estado}' e não pode ser recolhido por esta canônica.",
                details={"block_numero": block_numero, "estado": estado},
            )

        return _result(
            operation=operation,
            context=block_result["context"],
            resolved_ids=resolved_ids,
            data={
                "bloco": bloco,
                "mutation_preview": {
                    "block_numero": block_numero,
                    "current_state": estado,
                    "action": action_name,
                    "action_label": action_label,
                },
                "confirmation_required": True,
            },
            next_actions=[
                NextAction(
                    action="signature-block-recall-confirm",
                    label=action_label or "Recolher bloco",
                    params={"block_numero": block_numero},
                )
            ],
            warnings=warnings,
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("context"),
            resolved_ids=resolved_ids,
            exc=exc,
        )


def signature_block_recall_confirm(
    client: Any,
    block_numero: str,
    *,
    confirm: bool = False,
) -> dict[str, Any]:
    operation = "signature-block-recall-confirm"
    resolved_ids: dict[str, Any] = {"block_numero": block_numero}
    try:
        if not confirm:
            raise WorkflowViolationError(
                "Confirmacao explicita obrigatoria para recolher o bloco.",
                details={"expected_flag": "--confirm"},
            )

        preview = signature_block_recall_preview(client, block_numero)
        if not preview.get("ok"):
            preview["operation"] = operation
            return preview

        context = preview["context"]
        mutation_preview = preview["data"]["mutation_preview"]
        action_name = mutation_preview["action"]
        if action_name == "cancelar_disponibilizacao":
            mutation = client.cancelar_disponibilizacao_block(block_numero)
        elif action_name == "devolver":
            mutation = client.devolver_block(block_numero)
        else:
            raise UnsupportedStateError(
                f"Ação de recolhimento '{action_name}' não suportada.",
                details={"block_numero": block_numero, "action": action_name},
            )

        if not mutation.get("ok"):
            raise WorkflowViolationError(
                mutation.get("message", "SEI não confirmou o recolhimento do bloco."),
                details={"block_numero": block_numero, "action": action_name},
            )

        verification = __import__("sei_cli.operations.reading", fromlist=["signature_block_read"]).signature_block_read(
            client,
            block_numero,
        )

        verification_state = None
        if verification.get("ok"):
            verification_state = verification["data"]["bloco"].get("estado")
            invalid_after = (
                action_name == "cancelar_disponibilizacao" and str(verification_state or "").casefold() == "disponibilizado"
            ) or (
                action_name == "devolver" and str(verification_state or "").casefold() == "recebido"
            )
            if invalid_after:
                raise WorkflowViolationError(
                    "SEI retornou sucesso, mas o estado do bloco não mudou após releitura.",
                    details={
                        "block_numero": block_numero,
                        "action": action_name,
                        "state_after": verification_state,
                    },
                )

        return _result(
            operation=operation,
            context=context,
            resolved_ids=resolved_ids,
            data={
                "mutation": {
                    "action": action_name,
                    "message": mutation.get("message"),
                },
                "verification": {
                    "available": verification.get("ok", False),
                    "state_after": verification_state,
                },
            },
            next_actions=[
                NextAction(
                    action="signature-block-read",
                    label="Ler bloco após recolhimento",
                    params={"block_numero": block_numero},
                )
            ],
            warnings=[],
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("context"),
            resolved_ids=resolved_ids,
            exc=exc,
        )


def signature_block_refresh_preview(
    client: Any,
    block_numero: str,
    *,
    add_document_ids: list[str] | None = None,
    remove_document_ids: list[str] | None = None,
    redisponibilizar: bool = True,
) -> dict[str, Any]:
    operation = "signature-block-refresh-preview"
    resolved_ids: dict[str, Any] = {"block_numero": block_numero}
    try:
        context = _context(client)
        block_result = __import__("sei_cli.operations.reading", fromlist=["signature_block_read"]).signature_block_read(
            client,
            block_numero,
        )
        if not block_result.get("ok"):
            block_result["operation"] = operation
            return block_result

        bloco = block_result["data"]["bloco"]
        estado = str(bloco.get("estado") or "")
        normalized_state = estado.casefold()
        current_documents = block_result["data"].get("documents", [])
        current_ids = {
            str(value).strip()
            for item in current_documents
            for value in (item.get("documento_id"), item.get("numero_sei"), item.get("numero_documento"))
            if value is not None and str(value).strip()
        }
        add_list = [item.strip() for item in (add_document_ids or []) if item and item.strip()]
        remove_list = [item.strip() for item in (remove_document_ids or []) if item and item.strip()]
        already_present = [item for item in add_list if item in current_ids]
        missing_for_removal = [item for item in remove_list if item not in current_ids]

        warnings: list[str] = []
        recall_required = normalized_state == "disponibilizado"
        recall_action = "cancelar_disponibilizacao" if recall_required else None
        if normalized_state == "recebido":
            raise UnsupportedStateError(
                f"Bloco {block_numero} está em estado '{estado}' e não pode ser administrado pela unidade atual.",
                details={"block_numero": block_numero, "estado": estado},
            )
        if normalized_state not in {"gerado", "disponibilizado"}:
            raise UnsupportedStateError(
                f"Bloco {block_numero} está em estado '{estado}' e não pode ser atualizado por esta canônica.",
                details={"block_numero": block_numero, "estado": estado},
            )
        if recall_required:
            warnings.append("Bloco disponibilizado será recolhido antes da atualização.")
        if already_present:
            warnings.append("Alguns documentos solicitados para inclusão já constam no bloco.")
        if missing_for_removal:
            warnings.append("Alguns documentos solicitados para remoção não constam no bloco.")
        if not add_list and not remove_list:
            warnings.append("Nenhuma alteração de documentos foi informada; a canônica só executará recolhimento/redisponibilização se necessário.")

        resolved_ids["add_document_ids"] = add_list
        resolved_ids["remove_document_ids"] = remove_list
        return _result(
            operation=operation,
            context=context,
            resolved_ids=resolved_ids,
            data={
                "bloco": bloco,
                "mutation_preview": {
                    "block_numero": block_numero,
                    "current_state": estado,
                    "recall_required": recall_required,
                    "recall_action": recall_action,
                    "documents_to_add": add_list,
                    "documents_to_remove": remove_list,
                    "already_present": already_present,
                    "missing_for_removal": missing_for_removal,
                    "redisponibilizar": redisponibilizar,
                },
                "current_documents_total": len(current_documents),
                "confirmation_required": True,
            },
            next_actions=[
                NextAction(
                    action="signature-block-refresh-confirm",
                    label="Atualizar bloco e redisponibilizar",
                    params={
                        "block_numero": block_numero,
                        "add_document_ids": add_list,
                        "remove_document_ids": remove_list,
                        "redisponibilizar": redisponibilizar,
                    },
                )
            ],
            warnings=warnings,
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("context"),
            resolved_ids=resolved_ids,
            exc=exc,
        )


def signature_block_refresh_confirm(
    client: Any,
    block_numero: str,
    *,
    add_document_ids: list[str] | None = None,
    remove_document_ids: list[str] | None = None,
    redisponibilizar: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    operation = "signature-block-refresh-confirm"
    resolved_ids: dict[str, Any] = {"block_numero": block_numero}
    try:
        if not confirm:
            raise WorkflowViolationError(
                "Confirmacao explicita obrigatoria para atualizar o bloco.",
                details={"expected_flag": "--confirm"},
            )

        preview = signature_block_refresh_preview(
            client,
            block_numero,
            add_document_ids=add_document_ids,
            remove_document_ids=remove_document_ids,
            redisponibilizar=redisponibilizar,
        )
        if not preview.get("ok"):
            preview["operation"] = operation
            return preview

        context = preview["context"]
        mutation_preview = preview["data"]["mutation_preview"]
        add_list = mutation_preview["documents_to_add"]
        remove_list = mutation_preview["documents_to_remove"]
        results: dict[str, Any] = {"recall": None, "removed": [], "added": [], "redisponibilizacao": None}

        if mutation_preview["recall_required"]:
            recall = signature_block_recall_confirm(client, block_numero, confirm=True)
            if not recall.get("ok"):
                recall["operation"] = operation
                return recall
            results["recall"] = recall["data"]["mutation"]

        current_block = __import__("sei_cli.operations.reading", fromlist=["signature_block_read"]).signature_block_read(
            client,
            block_numero,
        )
        if not current_block.get("ok"):
            current_block["operation"] = operation
            return current_block
        block_unit = current_block.get("context", {}).get("unidade_sigla") or context.get("unidade_sigla")

        for doc_ref in remove_list:
            current_docs = current_block["data"].get("documents", [])
            match = next(
                (
                    item for item in current_docs
                    if doc_ref in {
                        str(item.get("documento_id") or "").strip(),
                        str(item.get("numero_sei") or "").strip(),
                        str(item.get("numero_documento") or "").strip(),
                    }
                ),
                None,
            )
            if not match:
                continue
            _best_effort_switch_to_unit(client, block_unit)
            mutation = client.remove_document_from_block(str(match.get("documento_id")), block_numero)
            if not mutation.get("ok"):
                raise WorkflowViolationError(
                    mutation.get("message", "SEI não confirmou a remoção do documento do bloco."),
                    details={"block_numero": block_numero, "documento_ref": doc_ref},
                )
            results["removed"].append({"documento_ref": doc_ref, "message": mutation.get("message")})
            current_block = __import__("sei_cli.operations.reading", fromlist=["signature_block_read"]).signature_block_read(
                client,
                block_numero,
            )
            if not current_block.get("ok"):
                current_block["operation"] = operation
                return current_block

        for doc_ref in add_list:
            _best_effort_switch_to_unit(client, block_unit)
            logged_doc_id, logged_process_id = _resolve_created_document_context(doc_ref)
            add_result = signature_block_add_document_confirm(
                client,
                block_numero,
                logged_doc_id or doc_ref,
                process_id=logged_process_id,
                disponibilizar=False,
                confirm=True,
            )
            if not add_result.get("ok"):
                add_result["operation"] = operation
                return add_result
            results["added"].append(
                {
                    "documento_ref": doc_ref,
                    "resolved_id_documento": add_result["resolved_ids"].get("id_documento"),
                }
            )

        if redisponibilizar:
            _best_effort_switch_to_unit(client, block_unit)
            mutation = client.disponibilizar_block(block_numero)
            if not mutation.get("ok"):
                raise WorkflowViolationError(
                    mutation.get("message", "SEI não confirmou a disponibilização do bloco."),
                    details={"block_numero": block_numero},
                )
            results["redisponibilizacao"] = {"message": mutation.get("message")}

        verification = __import__("sei_cli.operations.reading", fromlist=["signature_block_read"]).signature_block_read(
            client,
            block_numero,
        )
        if not verification.get("ok"):
            verification["operation"] = operation
            return verification

        resolved_ids["add_document_ids"] = add_list
        resolved_ids["remove_document_ids"] = remove_list
        return _result(
            operation=operation,
            context=context,
            resolved_ids=resolved_ids,
            data={
                "mutation": results,
                "verification": {
                    "state_after": verification["data"]["bloco"].get("estado"),
                    "documents_total": verification["data"].get("documents_total"),
                },
            },
            next_actions=[
                NextAction(
                    action="signature-block-read",
                    label="Ler bloco atualizado",
                    params={"block_numero": block_numero},
                )
            ],
            warnings=[],
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("context"),
            resolved_ids=resolved_ids,
            exc=exc,
        )


def signature_block_add_document_confirm(
    client: Any,
    block_numero: str,
    numero_ou_id_documento: str,
    *,
    process_id: str | None = None,
    disponibilizar: bool = False,
    confirm: bool = False,
) -> dict[str, Any]:
    operation = "signature-block-add-document-confirm"
    resolved_ids: dict[str, Any] = {"block_numero": block_numero}
    try:
        if not confirm:
            raise WorkflowViolationError(
                "Confirmacao explicita obrigatoria para incluir documento em bloco.",
                details={"expected_flag": "--confirm"},
            )

        context = _context(client)
        current_block = __import__("sei_cli.operations.reading", fromlist=["signature_block_read"]).signature_block_read(
            client,
            block_numero,
        )
        if not current_block.get("ok"):
            current_block["operation"] = operation
            return current_block
        block_unit = current_block.get("context", {}).get("unidade_sigla") or context.get("unidade_sigla")
        id_documento, id_procedimento, numero_documento = _resolve_document_ids(
            client,
            numero_ou_id_documento,
            id_procedimento=process_id,
        )
        numero_documento_resolvido = numero_documento or numero_ou_id_documento
        preflight, unit_guard = _process_unit_preflight(client, id_procedimento)
        with unit_guard as switched_to:
            if switched_to:
                preflight["switched"] = True
                preflight["switched_to"] = switched_to
                context = _context(client)
            _best_effort_switch_to_unit(client, block_unit)
            context = _context(client)
            already_in_block = any(
                _block_document_matches(
                    item,
                    id_documento=id_documento,
                    numero_documento=numero_documento_resolvido,
                )
                for item in current_block["data"].get("documents", [])
            )
            if already_in_block:
                raise WorkflowViolationError(
                    "Documento já consta no bloco informado.",
                    details={"block_numero": block_numero, "id_documento": id_documento},
                )
            mutation = client.add_document_to_block(
                id_procedimento,
                id_documento,
                block_numero,
                disponibilizar=disponibilizar,
            )

        if not mutation.get("ok"):
            raise WorkflowViolationError(
                mutation.get("message", "SEI não confirmou a inclusão do documento no bloco."),
                details={"block_numero": block_numero, "id_documento": id_documento},
            )
        verification = __import__("sei_cli.operations.reading", fromlist=["signature_block_read"]).signature_block_read(
            client,
            block_numero,
        )
        if not verification.get("ok"):
            verification["operation"] = operation
            return verification
        included = any(
            _block_document_matches(
                item,
                id_documento=id_documento,
                numero_documento=numero_documento_resolvido,
            )
            for item in verification["data"].get("documents", [])
        )
        if not included:
            raise WorkflowViolationError(
                "SEI retornou sucesso, mas o documento não apareceu no bloco após leitura de conferência.",
                details={"block_numero": block_numero, "id_documento": id_documento},
            )

        resolved_ids.update(
            {
                "id_documento": id_documento,
                "id_procedimento": id_procedimento,
                "numero_documento": numero_documento_resolvido,
            }
        )
        return _result(
            operation=operation,
            context=context,
            resolved_ids=resolved_ids,
            data={
                "preflight": preflight,
                "mutation": {
                    "block_numero": block_numero,
                    "id_documento": id_documento,
                    "disponibilizar": disponibilizar,
                    "message": mutation.get("message"),
                },
                "verification": {
                    "included_in_block": True,
                    "documents_total": verification["data"].get("documents_total"),
                },
            },
            next_actions=[
                NextAction(
                    action="signature-block-read",
                    label="Ler bloco atualizado",
                    params={"block_numero": block_numero},
                )
            ],
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("context"),
            resolved_ids=resolved_ids,
            exc=exc,
        )


def signature_block_sign_preview(
    client: Any,
    block_numero: str,
    *,
    document_ids: list[str] | None = None,
) -> dict[str, Any]:
    operation = "signature-block-sign-preview"
    resolved_ids: dict[str, Any] = {"block_numero": block_numero}
    try:
        context = _context(client)
        block_result = __import__("sei_cli.operations.reading", fromlist=["signature_block_review"]).signature_block_review(
            client,
            block_numero,
        )
        if not block_result.get("ok"):
            block_result["operation"] = operation
            return block_result

        pending_documents = block_result["data"].get("pending_documents", [])
        signable_documents = block_result["data"].get("signable_documents", pending_documents)
        requested_ids = {item.strip() for item in (document_ids or []) if item and item.strip()}
        selected_documents = [
            item for item in signable_documents
            if not requested_ids or item.get("documento_id") in requested_ids or item.get("numero_sei") in requested_ids
        ]
        if requested_ids and not selected_documents:
            raise WorkflowViolationError(
                "Nenhum documento pendente correspondente foi encontrado no bloco.",
                details={"block_numero": block_numero, "requested_ids": sorted(requested_ids)},
            )

        warnings: list[str] = []
        block_state = str(block_result["data"]["bloco"].get("estado") or "").casefold()
        if block_state == "recebido":
            warnings.append("Bloco em estado 'Recebido' normalmente não aceita assinatura pela unidade atual.")
        if not signable_documents:
            warnings.append("Bloco sem documentos pendentes de assinatura.")

        normalized_documents: list[dict[str, Any]] = []
        signable_document_ids: list[str] = []
        reading_module = __import__("sei_cli.operations.reading", fromlist=["_resolve_document_id_with_process"])
        for item in selected_documents:
            process_ref = item.get("processo")
            doc_ref = item.get("numero_sei") or item.get("numero_documento") or item.get("documento_id")
            normalized = dict(item)
            if process_ref and doc_ref:
                try:
                    process_id, _ = _resolve_process_id(client, process_ref)
                    resolved_doc_id, docs = reading_module._resolve_document_id_with_process(
                        client,
                        str(doc_ref),
                        id_procedimento=process_id,
                    )
                    matched_doc = next((doc for doc in docs if doc.id_documento == resolved_doc_id), None)
                    normalized["resolved_id_documento"] = resolved_doc_id
                    normalized["resolved_id_procedimento"] = process_id
                    normalized["resolved_numero_documento"] = (
                        (matched_doc.sei_number if matched_doc else None)
                        or normalized.get("numero_sei")
                        or normalized.get("numero_documento")
                    )
                    signable_document_ids.append(resolved_doc_id)
                except Exception:
                    normalized["resolved_id_documento"] = None
                    normalized["resolved_id_procedimento"] = None
            normalized_documents.append(normalized)
        signable_process_ids = sorted({item.get("processo") for item in selected_documents if item.get("processo")})
        resolved_ids["document_ids"] = signable_document_ids

        return _result(
            operation=operation,
            context=context,
            resolved_ids=resolved_ids,
            data={
                "bloco": block_result["data"]["bloco"],
                "pending_documents_total": len(pending_documents),
                "signable_documents_total": len(signable_documents),
                "selected_documents_total": len(selected_documents),
                "selected_documents": normalized_documents,
                "signable_document_ids": signable_document_ids,
                "processos": signable_process_ids,
                "confirmation_required": True,
            },
            next_actions=[
                NextAction(
                    action="signature-block-sign-confirm",
                    label="Assinar documentos selecionados do bloco",
                    params={"block_numero": block_numero, "document_ids": signable_document_ids},
                )
            ],
            warnings=warnings,
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("context"),
            resolved_ids=resolved_ids,
            exc=exc,
        )


def _is_block_post_verification_error(message: str | None) -> bool:
    if not message:
        return False
    normalized = message.casefold()
    return (
        "documentos permanecem pendentes" in normalized
        or "documentos permanecem assináveis" in normalized
        or "documentos permanecem assinaveis" in normalized
        or "após releitura do bloco" in normalized
        or "apos releitura do bloco" in normalized
    )


def _sanitize_ignored_block_postcheck_errors(results: list[dict[str, Any]]) -> None:
    for result in results:
        ignored: list[str] = []
        if _is_block_post_verification_error(result.get("error")):
            ignored.append(str(result.pop("error")))

        kept_errors: list[str] = []
        for item in result.get("errors", []) or []:
            if _is_block_post_verification_error(str(item)):
                ignored.append(str(item))
            else:
                kept_errors.append(str(item))

        if ignored:
            result["errors"] = kept_errors
            result["ignored_postcheck_errors"] = ignored


def signature_block_sign_confirm(
    client: Any,
    block_numero: str,
    *,
    document_ids: list[str] | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    operation = "signature-block-sign-confirm"
    resolved_ids: dict[str, Any] = {"block_numero": block_numero}
    try:
        if not confirm:
            raise WorkflowViolationError(
                "Confirmacao explicita obrigatoria para assinar documentos do bloco.",
                details={"expected_flag": "--confirm"},
            )

        preview = signature_block_sign_preview(client, block_numero, document_ids=document_ids)
        if not preview.get("ok"):
            preview["operation"] = operation
            return preview

        selected_documents = preview["data"].get("selected_documents", [])
        if not selected_documents:
            raise WorkflowViolationError(
                "Nenhum documento pendente selecionado para assinatura.",
                details={"block_numero": block_numero},
            )

        context = preview["context"]
        results: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        sign_block = getattr(client, "sign_block", None)
        selected_indices = [
            int(item["seq"])
            for item in selected_documents
            if item.get("seq") and str(item.get("seq")).isdigit()
        ]
        if callable(sign_block) and selected_indices:
            result = sign_block(block_numero, doc_indices=selected_indices)
            result["block_numero"] = block_numero
            result["document_indices"] = selected_indices
            results.append(result)
            result_messages = [result.get("error")] + list(result.get("errors") or [])
            non_verification_errors = [
                item for item in result_messages
                if item and not _is_block_post_verification_error(str(item))
            ]
            if not result.get("already_signed") and non_verification_errors:
                errors.append(result)
        else:
            for item in selected_documents:
                doc_id = item.get("resolved_id_documento") or item.get("documento_id")
                process_ref = item.get("processo")
                process_id = item.get("resolved_id_procedimento")
                if not doc_id or not process_ref:
                    errors.append(
                        {
                            "documento_id": doc_id,
                            "processo": process_ref,
                            "error": "Documento pendente sem identificação suficiente para assinatura.",
                        }
                    )
                    continue
                if not process_id:
                    process_id, _numero_processo = _resolve_process_id(client, process_ref)
                result = client.sign_document(doc_id, process_id)
                result["documento_id"] = doc_id
                result["processo"] = process_ref
                results.append(result)
                if result.get("already_signed"):
                    pass
                elif result.get("error") or result.get("errors"):
                    errors.append(result)

        if errors:
            first_error = next(
                (
                    error
                    for error in errors
                    if error.get("error") or error.get("errors")
                ),
                errors[0],
            )
            message = first_error.get("error")
            if not message and first_error.get("errors"):
                message = first_error["errors"][0]
            raise WorkflowViolationError(
                message or "Falha ao assinar um ou mais documentos do bloco.",
                details={
                    "block_numero": block_numero,
                    "sign_results": results,
                },
            )

        verification = None
        still_signable_after_verification: list[str] = []
        for attempt in range(3):
            verification = __import__("sei_cli.operations.reading", fromlist=["signature_block_review"]).signature_block_review(
                client,
                block_numero,
            )
            if not verification.get("ok"):
                verification["operation"] = operation
                return verification

            signable_items = verification["data"].get("signable_documents", [])
            still_signable_after_verification = [
                (item.get("resolved_id_documento") or item.get("documento_id"))
                for item in selected_documents
                if any(
                    _block_document_matches(
                        signable_item,
                        id_documento=item.get("resolved_id_documento") or item.get("documento_id"),
                        numero_documento=item.get("resolved_numero_documento") or item.get("numero_sei") or item.get("numero_documento"),
                    )
                    for signable_item in signable_items
                )
            ]
            if not still_signable_after_verification:
                break
            if attempt < 2:
                time.sleep(0.6 * (attempt + 1))

        if verification is None:
            raise WorkflowViolationError(
                "Falha ao reler o bloco após assinatura.",
                details={"block_numero": block_numero},
            )

        requested_ids = [
            item.get("resolved_id_documento") or item.get("documento_id")
            for item in selected_documents
            if item.get("resolved_id_documento") or item.get("documento_id")
        ]
        if still_signable_after_verification:
            raise WorkflowViolationError(
                "Assinatura submetida, mas alguns documentos continuam assináveis após releitura do bloco.",
                details={
                    "block_numero": block_numero,
                    "remaining_signable_document_ids": still_signable_after_verification,
                },
            )

        _sanitize_ignored_block_postcheck_errors(results)
        resolved_ids["document_ids"] = requested_ids
        return _result(
            operation=operation,
            context=context,
            resolved_ids=resolved_ids,
            data={
                "sign_results": results,
                "verification": {
                    "remaining_pending_total": verification["data"].get("pending_total"),
                    "remaining_signable_total": len(verification["data"].get("signable_documents", [])),
                    "signed_total": verification["data"].get("signed_total"),
                },
            },
            next_actions=[
                NextAction(
                    action="signature-block-review",
                    label="Revisar bloco após assinatura",
                    params={"block_numero": block_numero},
                )
            ],
            warnings=[item.get("error") for item in errors if item.get("error")],
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("context"),
            resolved_ids=resolved_ids,
            exc=exc,
        )


def process_marker_set_preview(
    client: Any,
    numero_ou_id: str,
    *,
    marker: str,
    texto: str | None = None,
    mode: str = "contextual",
    date_from: str | None = None,
    date_to: str | None = None,
    sample_size: int = 3,
) -> dict[str, Any]:
    operation = "process-marker-set-preview"
    try:
        base = process_marker_preview(
            client,
            numero_ou_id,
            marker=marker,
            mode=mode,
            date_from=date_from,
            date_to=date_to,
            sample_size=sample_size,
            suggested_text=texto,
        )
        if not base.get("ok"):
            base["operation"] = operation
            return base
        selected_marker = base["data"].get("selected_marker")
        if not selected_marker:
            raise WorkflowViolationError(
                f"Marcador '{marker}' não encontrado no catálogo atual da unidade.",
                details={"marker": marker},
            )

        marker_text = (texto or "").strip() or base["data"].get("suggested_marker_text") or ""
        warnings = list(base.get("warnings", []))
        warnings.append("Usar apenas processos de teste nesta fase de desenvolvimento.")
        if base["data"].get("current_marker"):
            warnings.append(
                "A visão atual já exibe marcador no processo; gestão granular de múltiplos marcadores ainda será tratada em outra canônica."
            )

        return _result(
            operation=operation,
            context=base["context"],
            resolved_ids=base["resolved_ids"],
            data={
                **base["data"],
                "selected_marker": selected_marker,
                "marker_text": marker_text,
                "confirmation_required": True,
            },
            next_actions=[
                NextAction(
                    action="process-marker-set-confirm",
                    label="Aplicar marcador ao processo",
                    params={
                        "numero_ou_id": base["resolved_ids"].get("id_procedimento") or numero_ou_id,
                        "marker": selected_marker.get("marcador_id"),
                        "texto": marker_text,
                    },
                )
            ],
            warnings=warnings,
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("base", {}).get("context"),
            resolved_ids=locals().get("base", {}).get("resolved_ids"),
            exc=exc,
        )


def process_marker_set_confirm(
    client: Any,
    numero_ou_id: str,
    *,
    marker: str,
    texto: str | None = None,
    confirm: bool = False,
    mode: str = "contextual",
    date_from: str | None = None,
    date_to: str | None = None,
    sample_size: int = 3,
) -> dict[str, Any]:
    operation = "process-marker-set-confirm"
    try:
        if not confirm:
            raise WorkflowViolationError(
                "Confirmação explícita obrigatória para aplicar marcador.",
                details={"expected_flag": "--confirm"},
            )

        preview = process_marker_set_preview(
            client,
            numero_ou_id,
            marker=marker,
            texto=texto,
            mode=mode,
            date_from=date_from,
            date_to=date_to,
            sample_size=sample_size,
        )
        if not preview.get("ok"):
            preview["operation"] = operation
            return preview

        process_id = preview["resolved_ids"]["id_procedimento"]
        selected_marker = preview["data"]["selected_marker"]
        marker_text = preview["data"].get("marker_text") or ""
        ok = client.set_marcador(process_id, str(selected_marker["marcador_id"]), marker_text)
        if not ok:
            raise WorkflowViolationError(
                "SEI não confirmou a aplicação do marcador no processo.",
                details={"id_procedimento": process_id, "marcador_id": selected_marker["marcador_id"]},
            )

        return _result(
            operation=operation,
            context=preview["context"],
            resolved_ids=preview["resolved_ids"],
            data={
                "processo": preview["data"].get("processo"),
                "marker_authority": preview["data"].get("marker_authority"),
                "selected_marker": selected_marker,
                "marker_text": marker_text,
                "mutation": {
                    "applied": True,
                    "message": f"Marcador {selected_marker['marcador_id']} aplicado ao processo {process_id}.",
                },
            },
            next_actions=[
                NextAction(
                    action="process-marker-preview",
                    label="Revisar marcador e contexto do processo",
                    params={"numero_ou_id": process_id},
                ),
                NextAction(
                    action="process-read",
                    label="Reler processo",
                    params={"numero_ou_id": process_id},
                ),
            ],
            warnings=preview.get("warnings", []),
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("preview", {}).get("context"),
            resolved_ids=locals().get("preview", {}).get("resolved_ids"),
            exc=exc,
        )


def process_marker_remove_preview(
    client: Any,
    numero_ou_id: str,
    *,
    marker: str | None = None,
) -> dict[str, Any]:
    operation = "process-marker-remove-preview"
    try:
        base = process_marker_preview(client, numero_ou_id, marker=marker)
        if not base.get("ok"):
            base["operation"] = operation
            return base
        warnings = list(base.get("warnings", []))
        selected_marker = base["data"].get("selected_marker")
        current_marker = base["data"].get("current_marker")
        warnings.append(
            "A remoção seletiva por marcador foi priorizada; histórico e múltiplos marcadores continuam como próxima fase."
        )
        if marker and not selected_marker:
            raise WorkflowViolationError(
                f"Marcador '{marker}' não encontrado para remoção.",
                details={"marker": marker},
            )
        if not current_marker and not selected_marker:
            warnings.append("A visão atual do processo não exibe marcador associado.")
        return _result(
            operation=operation,
            context=base["context"],
            resolved_ids=base["resolved_ids"],
            data={
                "processo": base["data"].get("processo"),
                "current_marker": current_marker,
                "selected_marker": selected_marker,
                "confirmation_required": True,
            },
            next_actions=[
                NextAction(
                    action="process-marker-remove-confirm",
                    label="Remover marcador do processo",
                    params={
                        "numero_ou_id": base["resolved_ids"].get("id_procedimento") or numero_ou_id,
                        "marker": (selected_marker or {}).get("marcador_id") if selected_marker else marker,
                    },
                )
            ],
            warnings=warnings,
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("base", {}).get("context"),
            resolved_ids=locals().get("base", {}).get("resolved_ids"),
            exc=exc,
        )


def process_marker_remove_confirm(
    client: Any,
    numero_ou_id: str,
    *,
    marker: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    operation = "process-marker-remove-confirm"
    try:
        if not confirm:
            raise WorkflowViolationError(
                "Confirmação explícita obrigatória para remover marcador.",
                details={"expected_flag": "--confirm"},
            )

        preview = process_marker_remove_preview(client, numero_ou_id, marker=marker)
        if not preview.get("ok"):
            preview["operation"] = operation
            return preview
        process_id = preview["resolved_ids"]["id_procedimento"]
        selected_marker = preview["data"].get("selected_marker") or {}
        marcador_id = selected_marker.get("marcador_id")
        ok = client.remove_marcador(process_id, marcador_id=marcador_id)
        if not ok:
            raise WorkflowViolationError(
                "SEI não confirmou a remoção do marcador do processo.",
                details={"id_procedimento": process_id, "marcador_id": marcador_id},
            )

        return _result(
            operation=operation,
            context=preview["context"],
            resolved_ids=preview["resolved_ids"],
            data={
                "processo": preview["data"].get("processo"),
                "marker_authority": preview["data"].get("marker_authority"),
                "selected_marker": selected_marker or None,
                "mutation": {
                    "removed": True,
                    "message": (
                        f"Marcador {marcador_id} removido do processo {process_id}."
                        if marcador_id
                        else f"Marcador removido do processo {process_id}."
                    ),
                },
            },
            next_actions=[
                NextAction(
                    action="process-marker-preview",
                    label="Revisar processo após remoção",
                    params={"numero_ou_id": process_id},
                )
            ],
            warnings=preview.get("warnings", []),
        )
    except Exception as exc:
        return _error_result(
            operation=operation,
            context=locals().get("preview", {}).get("context"),
            resolved_ids=locals().get("preview", {}).get("resolved_ids"),
            exc=exc,
        )
