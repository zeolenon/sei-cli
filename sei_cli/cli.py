from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.table import Table

from sei_cli import __version__
from sei_cli.client import SEIClient
from sei_cli.models import Block, Process, SystemStatus
from sei_cli.operations import (
    block_review as op_block_review,
    document_create_confirm as op_document_create_confirm,
    document_create_preview as op_document_create_preview,
    document_edit_confirm as op_document_edit_confirm,
    document_edit_preview as op_document_edit_preview,
    document_pdf_confirm as op_document_pdf_confirm,
    document_pdf_preview as op_document_pdf_preview,
    document_quality_check as op_document_quality_check,
    document_read as op_document_read,
    environment_triage_apply as op_environment_triage_apply,
    environment_triage_parallel as op_environment_triage_parallel,
    environment_triage_preview as op_environment_triage_preview,
    inbox_snapshot as op_inbox_snapshot,
    marker_catalog as op_marker_catalog,
    process_archive_confirm as op_process_archive_confirm,
    process_archive_preview as op_process_archive_preview,
    process_create_confirm as op_process_create_confirm,
    process_create_preview as op_process_create_preview,
    process_conclude_confirm as op_process_conclude_confirm,
    process_conclude_preview as op_process_conclude_preview,
    process_finalize_confirm as op_process_finalize_confirm,
    process_finalize_preview as op_process_finalize_preview,
    process_forward_confirm as op_process_forward_confirm,
    process_forward_preview as op_process_forward_preview,
    process_pdf_confirm as op_process_pdf_confirm,
    process_pdf_preview as op_process_pdf_preview,
    process_reopen_confirm as op_process_reopen_confirm,
    process_reopen_preview as op_process_reopen_preview,
    process_marker_history as op_process_marker_history,
    process_marker_preview as op_process_marker_preview,
    process_marker_read as op_process_marker_read,
    process_marker_remove_confirm as op_process_marker_remove_confirm,
    process_marker_remove_preview as op_process_marker_remove_preview,
    process_marker_set_confirm as op_process_marker_set_confirm,
    process_marker_set_preview as op_process_marker_set_preview,
    process_marker_update_confirm as op_process_marker_update_confirm,
    process_marker_update_preview as op_process_marker_update_preview,
    process_watch_confirm as op_process_watch_confirm,
    process_watch_preview as op_process_watch_preview,
    process_watch_read as op_process_watch_read,
    process_open as op_process_open,
    process_read as op_process_read,
    process_history as op_process_history,
    process_report as op_process_report,
    process_summary as op_process_summary,
    relatorio_read as op_relatorio_read,
    signature_block_add_document_confirm as op_signature_block_add_document_confirm,
    signature_block_add_document_preview as op_signature_block_add_document_preview,
    signature_block_list as op_signature_block_list,
    signature_block_recall_confirm as op_signature_block_recall_confirm,
    signature_block_recall_preview as op_signature_block_recall_preview,
    signature_block_refresh_confirm as op_signature_block_refresh_confirm,
    signature_block_refresh_preview as op_signature_block_refresh_preview,
    signature_block_read as op_signature_block_read,
    signature_block_sign_confirm as op_signature_block_sign_confirm,
    signature_block_sign_preview as op_signature_block_sign_preview,
    signature_block_review as op_signature_block_review,
    tracking_group_catalog as op_tracking_group_catalog,
    tracking_group_create_confirm as op_tracking_group_create_confirm,
    tracking_group_create_preview as op_tracking_group_create_preview,
    workflow_next as op_workflow_next,
    workflow_show as op_workflow_show,
)

console = Console()


def _emit(data: Any, as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(data, ensure_ascii=False, indent=2))


def _emit_operation_result(result: dict[str, Any], as_json: bool) -> None:
    if as_json:
        _emit(result, True)
    else:
        if result.get("ok"):
            console.print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            console.print(f"[red]{result.get('error', {}).get('message', 'Operação falhou')}[/red]")
    if not result.get("ok"):
        raise SystemExit(1)


def _print_status(status: SystemStatus) -> None:
    table = Table(title="Status da Sessão")
    table.add_column("Campo")
    table.add_column("Valor")
    table.add_row("Válida", "Sim" if status.valid else "Não")
    table.add_row("Unidade", status.unidade_sigla or "-")
    table.add_row("Descrição", status.unidade_descricao or "-")
    table.add_row("Usuário", status.usuario or "-")
    table.add_row("Último acesso", status.ultimo_acesso or "-")
    console.print(table)


def _normalize_switch_status(client: SEIClient, result: Any) -> SystemStatus:
    if isinstance(result, SystemStatus):
        return result
    if result is True:
        return client.status()
    if result is False:
        raise RuntimeError("Falha ao trocar unidade")
    raise RuntimeError(f"Retorno inesperado da troca de unidade: {type(result).__name__}")


def _table_processes(title: str, items: list[Process]) -> None:
    table = Table(title=title)
    table.add_column("Nº")
    table.add_column("Tipo")
    table.add_column("Especificação")
    table.add_column("Novo")
    table.add_column("Atribuído")

    for item in items:
        table.add_row(
            item.numero,
            item.tipo,
            item.especificacao,
            "Sim" if item.novo else "Não",
            item.atribuido or "-",
        )

    console.print(table)


def _table_blocks(items: list[Block]) -> None:
    table = Table(title="Blocos")
    table.add_column("Número")
    table.add_column("Estado")
    table.add_column("Descrição")
    table.add_column("Origem")
    table.add_column("Destino")
    for item in items:
        table.add_row(
            item.numero,
            item.estado or "-",
            item.descricao,
            item.unidade_origem,
            item.unidade_destino,
        )
    console.print(table)


@click.group()
@click.version_option(version=__version__, prog_name="sei")
def cli() -> None:
    """CLI read-only para o SEI-RN."""


@cli.command()
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def login(as_json: bool) -> None:
    with SEIClient() as client:
        status = client.login()
    if as_json:
        _emit(asdict(status), True)
        return
    _print_status(status)


@cli.command()
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def status(as_json: bool) -> None:
    with SEIClient() as client:
        data = client.status()
    if as_json:
        _emit(asdict(data), True)
        return
    _print_status(data)


@cli.command("processes")
@click.option("--unit", "unit", default=None, help="Filtra por unidade (troca unidade ativa)")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def processes_cmd(unit: str | None, as_json: bool) -> None:
    with SEIClient() as client:
        data = client.list_processes(unit=unit)
    if as_json:
        _emit({"recebidos": [asdict(x) for x in data.recebidos], "gerados": [asdict(x) for x in data.gerados]}, True)
        return
    _table_processes("Processos Recebidos", data.recebidos)
    _table_processes("Processos Gerados", data.gerados)


@cli.command("process")
@click.argument("numero")
@click.option("--unit", default=None, help="Unidade SEI (trocar antes)")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def process_cmd(numero: str, unit: str | None, as_json: bool) -> None:
    """Listar documentos de um processo pelo id_procedimento ou número SEI.

    Uses get_full_document_tree() which automatically expands lazy-loaded
    folders, returning ALL documents including those inside nested folders.
    """
    with SEIClient() as client:
        if unit:
            client.switch_unit(unit)
        # Se parece número de processo (tem ponto/barra), resolve via search
        if "." in numero or "/" in numero:
            import re as _re
            html = client.search(numero)
            if "ifrArvore" not in html:
                click.echo(f"❌ Processo '{numero}' não encontrado", err=True)
                raise SystemExit(1)
            id_proc_m = _re.search(r"id_procedimento=(\d+)", html)
            id_proc = id_proc_m.group(1) if id_proc_m else numero
        else:
            id_proc = numero
        docs = client.get_full_document_tree(id_proc)

    if as_json:
        _emit([asdict(d) for d in docs], True)
        return

    table = Table(title=f"Processo {numero} ({len(docs)} documentos)")
    table.add_column("ID")
    table.add_column("Nº SEI")
    table.add_column("Nome")
    table.add_column("Tipo")
    table.add_column("Pasta")
    for doc in docs:
        table.add_row(
            doc.id_documento or "-",
            doc.sei_number or "-",
            doc.nome,
            doc.tipo,
            doc.parent_folder or "-",
        )
    console.print(table)


@cli.command("doc")
@click.argument("numero")
@click.option("--unit", default=None, help="Unidade SEI (trocar antes)")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def doc_cmd(numero: str, unit: str | None, as_json: bool) -> None:
    """Buscar documento por número SEI e mostrar metadados."""
    with SEIClient() as client:
        if unit:
            client.switch_unit(unit)
        result = client.search_document(numero)
    if not result:
        click.echo(f"❌ Documento '{numero}' não encontrado", err=True)
        raise SystemExit(1)

    id_doc, id_proc = result
    if as_json:
        _emit({"id_documento": id_doc, "id_procedimento": id_proc, "numero_sei": numero}, True)
        return

    click.echo(f"✅ Documento SEI {numero}")
    click.echo(f"  id_documento: {id_doc}")
    click.echo(f"  id_procedimento: {id_proc}")


@cli.command("blocks")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def blocks_cmd(as_json: bool) -> None:
    with SEIClient() as client:
        blocks = client.list_blocks()
    if as_json:
        _emit([asdict(x) for x in blocks], True)
        return
    _table_blocks(blocks)


@cli.command("block")
@click.argument("block_numero")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def block_cmd(block_numero: str, as_json: bool) -> None:
    """Compatibilidade: lê um bloco de assinatura na unidade atual."""
    with SEIClient() as client:
        result = op_signature_block_read(client, block_numero)
    _emit_operation_result(result, as_json)


@cli.command("search")
@click.argument("query")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def search_cmd(query: str, as_json: bool) -> None:
    """Search processes by keyword in tipo, especificação, or marcador."""
    with SEIClient() as client:
        proc_list = client.list_processes()

    kw = query.lower()
    matches = [
        p for p in proc_list.recebidos + proc_list.gerados
        if kw in (p.tipo or "").lower()
        or kw in (p.especificacao or "").lower()
        or kw in (p.marcador or "").lower()
        or kw in (p.numero or "").lower()
    ]

    if as_json:
        _emit({"query": query, "processos": [asdict(x) for x in matches]}, True)
        return

    _table_processes(f"Pesquisa: {query} ({len(matches)} resultados)", matches)


@cli.command("goto")
@click.argument("numero")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
@click.option("--read", "do_read", is_flag=True, help="Ler conteúdo do documento (se for relatório)")
@click.option("--unit", default=None, help="Unidade SEI (sigla parcial)")
def goto_cmd(numero: str, as_json: bool, do_read: bool, unit: str | None) -> None:
    """Navegar direto para um documento ou processo pelo número SEI.

    Aceita número de documento SEI (ex: 39860248) ou número de processo
    (ex: 08810108.001215/2025-10). Usa a pesquisa rápida do SEI.
    """
    with SEIClient() as client:
        if unit:
            client.switch_unit(unit)

        # Detect if it's a process number (has dots/slashes) or document number
        is_process = "." in numero or "/" in numero

        if is_process:
            html = client.search(numero)
            has_tree = "ifrArvore" in html
            if has_tree:
                import re
                id_proc_m = re.search(r"id_procedimento=(\d+)", html)
                id_proc = id_proc_m.group(1) if id_proc_m else "?"
                if as_json:
                    docs = client.get_full_document_tree(id_proc) if id_proc != "?" else []
                    _emit({
                        "tipo": "processo",
                        "numero": numero,
                        "id_procedimento": id_proc,
                        "documentos": [{"id": d.id_documento, "nome": d.nome, "tipo": d.tipo, "sei_number": d.sei_number} for d in docs],
                    }, True)
                else:
                    console.print(f"[green]✅ Processo {numero}[/green] (id_procedimento={id_proc})")
                    if id_proc != "?":
                        docs = client.get_full_document_tree(id_proc)
                        table = Table(title=f"Documentos ({len(docs)})")
                        table.add_column("ID")
                        table.add_column("Nº SEI")
                        table.add_column("Nome")
                        table.add_column("Tipo")
                        for d in docs:
                            table.add_row(d.id_documento, d.sei_number or "-", d.nome, d.tipo)
                        console.print(table)
            else:
                console.print(f"[red]❌ Processo {numero} não encontrado[/red]")
        else:
            result = client.search_document(numero)
            if result:
                id_doc, id_proc = result
                if as_json:
                    out = {"tipo": "documento", "numero_sei": numero, "id_documento": id_doc, "id_procedimento": id_proc}
                    if do_read:
                        try:
                            relatorio = client.read_relatorio(id_doc, id_proc)
                            out["conteudo"] = str(relatorio) if relatorio else None
                        except Exception:
                            out["conteudo"] = None
                    _emit(out, True)
                else:
                    console.print(f"[green]✅ Documento SEI {numero}[/green]")
                    console.print(f"  id_documento: {id_doc}")
                    console.print(f"  id_procedimento: {id_proc}")
                    if do_read:
                        try:
                            relatorio = client.read_relatorio(id_doc, id_proc)
                            if relatorio:
                                console.print(f"\n[bold]Conteúdo:[/bold]")
                                console.print(str(relatorio))
                            else:
                                console.print("[yellow]Sem conteúdo legível[/yellow]")
                        except Exception as e:
                            console.print(f"[red]Erro ao ler: {e}[/red]")
            else:
                console.print(f"[red]❌ Documento {numero} não encontrado[/red]")


@cli.command("encaminhar")
@click.argument("processo")
@click.argument("destinos", nargs=-1, required=True)
@click.option("--unit", default=None, help="Unidade SEI atual (trocar antes de encaminhar)")
@click.option("--fechar", is_flag=True, help="Fechar processo na unidade atual após envio")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def encaminhar_cmd(processo: str, destinos: tuple[str, ...], unit: str | None, fechar: bool, as_json: bool) -> None:
    """Encaminhar processo para uma ou mais unidades.

    PROCESSO: id_procedimento ou número do processo (ex: 08810254.000081/2026-17)
    DESTINOS: sigla(s) ou nome(s) parcial(is) das unidades destino (aceita múltiplas)

    Exemplos:

      sei encaminhar 48145432 "DPSGP SECRETARIA"

      sei encaminhar 48145432 "DPSGP SECRETARIA" "AJUD SEC GERAL"
    """
    with SEIClient() as client:
        if unit:
            client.switch_unit(unit)

        # Se é número de processo, resolver pra id_procedimento via goto/search
        id_proc = processo
        if "." in processo or "/" in processo:
            import re
            html = client.search(processo)
            m = re.search(r"id_procedimento=(\d+)", html)
            if m:
                id_proc = m.group(1)
            else:
                console.print(f"[red]❌ Processo {processo} não encontrado[/red]")
                return

        manter_aberto = not fechar
        destinos_list = list(destinos)
        try:
            ok = client.enviar_processo(id_proc, destinos_list, manter_aberto=manter_aberto)
            if ok:
                destinos_str = ", ".join(destinos_list)
                if as_json:
                    _emit({
                        "status": "ok",
                        "processo": processo,
                        "destinos": destinos_list,
                        "mantido_aberto": manter_aberto,
                    }, True)
                else:
                    console.print(f"[green]✅ Processo {processo} encaminhado para: {destinos_str}[/green]")
                    if manter_aberto:
                        console.print("  (mantido aberto na unidade atual)")
            else:
                console.print(f"[red]❌ Falha ao encaminhar processo[/red]")
        except RuntimeError as e:
            console.print(f"[red]❌ {e}[/red]")


@cli.command("reabrir")
@click.argument("processo")
@click.option("--unit", default=None, help="Unidade SEI (trocar antes de reabrir)")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def reabrir_cmd(processo: str, unit: str | None, as_json: bool) -> None:
    """Reabrir um processo que foi fechado (enviado sem manter aberto) na unidade.

    PROCESSO: id_procedimento (ex: 48145432)
    """
    import re as _re
    with SEIClient() as client:
        if unit:
            client.switch_unit(unit)

        # Resolve formatted process numbers → id_procedimento
        id_proc = processo
        if "." in processo or "/" in processo:
            html = client.search(processo)
            m = _re.search(r"id_procedimento=(\d+)", html)
            if m:
                id_proc = m.group(1)
            else:
                console.print(f"[red]❌ Processo {processo} não encontrado[/red]")
                return

        try:
            ok = client.reabrir_processo(id_proc)
            if ok:
                if as_json:
                    _emit({"status": "ok", "processo": processo}, True)
                else:
                    console.print(f"[green]✅ Processo {processo} reaberto na unidade atual[/green]")
            else:
                console.print(f"[red]❌ Falha ao reabrir processo[/red]")
        except RuntimeError as e:
            console.print(f"[red]❌ {e}[/red]")


@cli.command("concluir")
@click.argument("processos", nargs=-1, required=True)
@click.option("--unit", default=None, help="Unidade SEI (trocar antes de concluir)")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def concluir_cmd(processos: tuple[str, ...], unit: str | None, as_json: bool) -> None:
    """Concluir um ou mais processos na unidade atual.

    PROCESSOS: ids ou números SEI (ex: 47162626 ou 08810198.000286/2024-52)
    """
    import re as _re
    with SEIClient() as client:
        if unit:
            client.switch_unit(unit)

        # Resolve formatted process numbers → id_procedimento
        ids = []
        for p in processos:
            if "." in p or "/" in p:
                html = client.search(p)
                m = _re.search(r"id_procedimento=(\d+)", html)
                if m:
                    ids.append(m.group(1))
                else:
                    console.print(f"[yellow]⚠️ Processo {p} não encontrado, pulando[/yellow]")
            else:
                ids.append(p)

        if not ids:
            console.print("[red]❌ Nenhum processo válido[/red]")
            return

        result = client.concluir_processos(ids)

        if as_json:
            _emit(result, True)
            return

        for pid in result["concluded"]:
            console.print(f"[green]✅ {pid} concluído[/green]")
        for pid in result["failed"]:
            err = result["errors"].get(pid, "?")
            console.print(f"[red]❌ {pid}: {err}[/red]")

        total = len(result["concluded"])
        console.print(f"\n[bold]{total}/{len(ids)} processos concluídos[/bold]")


@cli.command("units")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def units_cmd(as_json: bool) -> None:
    with SEIClient() as client:
        units = client.list_units()

    if as_json:
        _emit([asdict(x) for x in units], True)
        return

    table = Table(title="Unidades")
    table.add_column("Sigla")
    table.add_column("Descrição")
    for unit in units:
        table.add_row(unit.sigla, unit.descricao)
    console.print(table)


@cli.command("switch")
@click.argument("sigla")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def switch_cmd(sigla: str, as_json: bool) -> None:
    with SEIClient() as client:
        status = _normalize_switch_status(client, client.switch_unit(sigla))

    if as_json:
        _emit(asdict(status), True)
        return
    _print_status(status)


# ------------------------------------------------------------------
# Marcadores
# ------------------------------------------------------------------

@cli.command("marcadores")
@click.option("--unit", default=None, help="Unidade SEI")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def marcadores_cmd(unit: str | None, as_json: bool) -> None:
    """Listar marcadores da unidade atual."""
    with SEIClient() as client:
        if unit:
            client.switch_unit(unit)
        marcadores = client.listar_marcadores()

    if as_json:
        _emit(marcadores, True)
        return

    if not marcadores:
        console.print("[yellow]Nenhum marcador encontrado[/yellow]")
        return

    table = Table(title="Marcadores")
    table.add_column("ID")
    table.add_column("Nome")
    for m in sorted(marcadores, key=lambda x: x.get("nome", "")):
        table.add_row(str(m.get("id", "")), m.get("nome", ""))
    console.print(table)


@cli.command("marcador-criar")
@click.argument("nome")
@click.option("--cor", default="amarelo", show_default=True, help="Cor do marcador (nome ou ID numérico)")
@click.option("--unit", default=None, help="Unidade SEI")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def marcador_criar_cmd(nome: str, cor: str, unit: str | None, as_json: bool) -> None:
    """Criar um novo marcador."""
    with SEIClient() as client:
        if unit:
            client.switch_unit(unit)
        mid = client.criar_marcador(nome, cor=cor)

    if as_json:
        _emit({"id": mid, "nome": nome, "cor": cor}, True)
    else:
        console.print(f"[green]✅ Marcador criado: {nome} (ID {mid}, cor: {cor})[/green]")


@cli.command("marcador-editar")
@click.argument("marcador_id")
@click.option("--nome", default=None, help="Novo nome")
@click.option("--cor", default=None, help="Nova cor (nome ou ID numérico)")
@click.option("--unit", default=None, help="Unidade SEI")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def marcador_editar_cmd(marcador_id: str, nome: str | None, cor: str | None, unit: str | None, as_json: bool) -> None:
    """Editar nome e/ou cor de um marcador existente."""
    if nome is None and cor is None:
        console.print("[yellow]⚠️  Nenhuma alteração especificada (use --nome e/ou --cor)[/yellow]")
        return

    with SEIClient() as client:
        if unit:
            client.switch_unit(unit)
        ok = client.editar_marcador(marcador_id, nome=nome, cor=cor)

    if as_json:
        _emit({"id": marcador_id, "ok": ok}, True)
    else:
        if ok:
            console.print(f"[green]✅ Marcador {marcador_id} atualizado[/green]")
        else:
            console.print(f"[red]❌ Falha ao atualizar marcador {marcador_id}[/red]")


@cli.command("marcador-set")
@click.argument("processo")
@click.argument("marcador_id")
@click.option("--texto", "-t", default="", help="Texto descritivo do marcador")
@click.option("--unit", default=None, help="Unidade SEI")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def marcador_set_cmd(processo: str, marcador_id: str, texto: str, unit: str | None, as_json: bool) -> None:
    """Definir/atualizar marcador em um processo."""
    with SEIClient() as client:
        if unit:
            client.switch_unit(unit)
        ok = client.set_marcador(processo, marcador_id, texto)

    if as_json:
        _emit({"status": "ok" if ok else "failed", "processo": processo, "marcador_id": marcador_id}, True)
    elif ok:
        console.print(f"[green]✅ Marcador {marcador_id} definido no processo {processo}[/green]")
    else:
        console.print(f"[red]❌ Falha ao definir marcador[/red]")


@cli.command("marcador-remove")
@click.argument("processo")
@click.option("--unit", default=None, help="Unidade SEI")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def marcador_remove_cmd(processo: str, unit: str | None, as_json: bool) -> None:
    """Remover marcador de um processo."""
    with SEIClient() as client:
        if unit:
            client.switch_unit(unit)
        ok = client.remove_marcador(processo)

    if as_json:
        _emit({"status": "ok" if ok else "failed", "processo": processo}, True)
    elif ok:
        console.print(f"[green]✅ Marcador removido do processo {processo}[/green]")
    else:
        console.print(f"[red]❌ Falha ao remover marcador[/red]")


# ------------------------------------------------------------------
# Acompanhamento Especial / Grupos
# ------------------------------------------------------------------

@cli.command("grupos")
@click.option("--unit", default=None, help="Unidade SEI")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def grupos_cmd(unit: str | None, as_json: bool) -> None:
    """Listar grupos de acompanhamento especial."""
    with SEIClient() as client:
        if unit:
            client.switch_unit(unit)
        grupos = client.listar_grupos_acompanhamento()

    if as_json:
        _emit(grupos, True)
        return

    if not grupos:
        console.print("[yellow]Nenhum grupo encontrado[/yellow]")
        return

    table = Table(title="Grupos de Acompanhamento")
    table.add_column("ID")
    table.add_column("Nome")
    for g in sorted(grupos, key=lambda x: x.get("nome", "")):
        table.add_row(str(g.get("id", "")), g.get("nome", ""))
    console.print(table)


@cli.command("grupo-criar")
@click.argument("nome")
@click.option("--unit", default=None, help="Unidade SEI")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def grupo_criar_cmd(nome: str, unit: str | None, as_json: bool) -> None:
    """Criar um novo grupo de acompanhamento especial."""
    with SEIClient() as client:
        if unit:
            client.switch_unit(unit)
        gid = client.criar_grupo_acompanhamento(nome)

    if as_json:
        _emit({"id": gid, "nome": nome}, True)
    else:
        console.print(f"[green]✅ Grupo criado: {nome} (ID {gid})[/green]")


@cli.command("acompanhamento-add")
@click.argument("processo")
@click.argument("grupo_id")
@click.option("--obs", "-o", default="", help="Observação")
@click.option("--unit", default=None, help="Unidade SEI")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def acompanhamento_add_cmd(processo: str, grupo_id: str, obs: str, unit: str | None, as_json: bool) -> None:
    """Adicionar processo a grupo de acompanhamento especial."""
    with SEIClient() as client:
        if unit:
            client.switch_unit(unit)
        ok = client.add_acompanhamento_especial(processo, grupo_id, obs)

    if as_json:
        _emit({"status": "ok" if ok else "failed", "processo": processo, "grupo_id": grupo_id}, True)
    elif ok:
        console.print(f"[green]✅ Processo {processo} adicionado ao grupo {grupo_id}[/green]")
    else:
        console.print(f"[red]❌ Falha[/red]")


@cli.command("acompanhamento-alterar")
@click.argument("processo")
@click.argument("grupo_id")
@click.option("--obs", "-o", default="", help="Nova observação")
@click.option("--unit", default=None, help="Unidade SEI")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def acompanhamento_alterar_cmd(processo: str, grupo_id: str, obs: str, unit: str | None, as_json: bool) -> None:
    """Alterar acompanhamento especial de um processo."""
    with SEIClient() as client:
        if unit:
            client.switch_unit(unit)
        ok = client.alterar_acompanhamento_especial(processo, grupo_id, obs)

    if as_json:
        _emit({"status": "ok" if ok else "failed", "processo": processo, "grupo_id": grupo_id}, True)
    elif ok:
        console.print(f"[green]✅ Acompanhamento alterado no processo {processo}[/green]")
    else:
        console.print(f"[red]❌ Falha[/red]")


@cli.command("acompanhamentos")
@click.option("--unit", default=None, help="Unidade SEI")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def acompanhamentos_cmd(unit: str | None, as_json: bool) -> None:
    """Listar processos em acompanhamento especial."""
    with SEIClient() as client:
        if unit:
            client.switch_unit(unit)
        procs = client.list_acompanhamento_especial()

    if as_json:
        _emit([asdict(p) for p in procs], True)
        return

    if not procs:
        console.print("[yellow]Nenhum processo em acompanhamento especial[/yellow]")
        return

    table = Table(title="Processos em Acompanhamento Especial")
    table.add_column("Processo")
    table.add_column("ID")
    for p in procs:
        table.add_row(p.numero, p.id_procedimento)
    console.print(table)


@cli.command("acompanhamento-search")
@click.option("--palavras", "-p", default=None, help="Termos buscados na descrição/marcadores")
@click.option("--grupo", default=None, help="Nome ou ID do grupo de acompanhamento")
@click.option("--unit", default=None, help="Unidade SEI")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def acompanhamento_search_cmd(
    palavras: str | None,
    grupo: str | None,
    unit: str | None,
    as_json: bool,
) -> None:
    """Pesquisar acompanhamento especial em descrição e marcadores."""
    try:
        with SEIClient() as client:
            if unit:
                client.switch_unit(unit)
            records = client.search_acompanhamento_especial(palavras, grupo=grupo)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        _emit(
            {
                "total": len(records),
                "palavras": palavras or "",
                "grupo": grupo,
                "resultados": [asdict(record) for record in records],
            },
            True,
        )
        return

    if not records:
        console.print("[yellow]Nenhum acompanhamento especial corresponde à busca[/yellow]")
        return

    table = Table(title="Busca em Acompanhamento Especial")
    table.add_column("Processo")
    table.add_column("Tipo")
    table.add_column("Grupo")
    table.add_column("Descrição/observação")
    table.add_column("Marcadores")
    for record in records:
        table.add_row(
            record.numero,
            record.tipo,
            record.grupo,
            record.descricao,
            "\n".join(record.marcadores),
        )
    console.print(table)


# ------------------------------------------------------------------
# Blocos
# ------------------------------------------------------------------

@cli.command("block-create")
@click.argument("descricao")
@click.argument("unidade_destino")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def block_create_cmd(descricao: str, unidade_destino: str, as_json: bool) -> None:
    """Create a new bloco de assinatura.

    UNIDADE_DESTINO can be a known alias (e.g. 'CMDO 3GBM') or a numeric SEI unit ID.
    Known aliases: CMDO 3GBM, CMDO PABM APODI, SEC 1SGB/3GBM, SEC 2SGB/3GBM,
    SECRETARIA 3GBM, OP 3GBM, LOGISTICA 3GBM, PAD-PDF, DAT-1CAT.
    """
    # Resolve alias or use as-is
    unit_id = SEIClient.UNIT_IDS.get(unidade_destino.upper(), None)
    if not unit_id:
        # Try case-insensitive fuzzy match
        for alias, uid in SEIClient.UNIT_IDS.items():
            if unidade_destino.upper().replace("º", "").replace("°", "") in alias.replace("º", ""):
                unit_id = uid
                break
    if not unit_id:
        if unidade_destino.isdigit():
            unit_id = unidade_destino
        else:
            click.echo(f"❌ Unidade desconhecida: {unidade_destino}")
            click.echo(f"   Aliases válidos: {', '.join(SEIClient.UNIT_IDS.keys())}")
            raise SystemExit(1)

    with SEIClient() as client:
        client.login()
        numero = client.create_block(descricao, unit_id)

    result = {"ok": True, "numero": numero, "descricao": descricao, "unidade_id": unit_id}
    if as_json:
        _emit(result, True)
        return
    click.echo(f"✅ Bloco {numero} criado — {descricao}")


@cli.command("block-add")
@click.argument("id_procedimento")
@click.argument("id_documento")
@click.argument("block_numero")
@click.option("--disponibilizar", is_flag=True, help="Incluir E disponibilizar no mesmo passo")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def block_add_cmd(
    id_procedimento: str,
    id_documento: str,
    block_numero: str,
    disponibilizar: bool,
    as_json: bool,
) -> None:
    """Include a document in a bloco de assinatura."""
    with SEIClient() as client:
        client.login()
        result = client.add_document_to_block(
            id_procedimento, id_documento, block_numero,
            disponibilizar=disponibilizar,
        )
    if as_json:
        _emit(result, True)
        return
    icon = "✅" if result["ok"] else "❌"
    click.echo(f"{icon} {result['message']}")


@cli.command("block-disponibilizar")
@click.argument("block_numero")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def block_disponibilizar_cmd(block_numero: str, as_json: bool) -> None:
    """Disponibilizar (make available) a bloco de assinatura."""
    with SEIClient() as client:
        client.login()
        result = client.disponibilizar_block(block_numero)
    if as_json:
        _emit(result, True)
        return
    icon = "✅" if result["ok"] else "❌"
    click.echo(f"{icon} {result['message']}")


@cli.command("block-cancelar")
@click.argument("block_numero")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def block_cancelar_cmd(block_numero: str, as_json: bool) -> None:
    """Cancel disponibilização of a bloco de assinatura."""
    with SEIClient() as client:
        client.login()
        result = client.cancelar_disponibilizacao_block(block_numero)
    if as_json:
        _emit(result, True)
        return
    icon = "✅" if result["ok"] else "❌"
    click.echo(f"{icon} {result['message']}")


@cli.command("block-delete")
@click.argument("block_numero")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def block_delete_cmd(block_numero: str, as_json: bool) -> None:
    """Delete an empty bloco de assinatura."""
    with SEIClient() as client:
        client.login()
        try:
            client.delete_block(block_numero)
        except RuntimeError as e:
            if as_json:
                _emit({"ok": False, "message": str(e)}, True)
            else:
                click.echo(f"❌ {e}")
            raise SystemExit(1)
    if as_json:
        _emit({"ok": True, "message": f"Bloco {block_numero} excluído"}, True)
        return
    click.echo(f"✅ Bloco {block_numero} excluído")


@cli.command("block-devolver")
@click.argument("block_numero")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def block_devolver_cmd(block_numero: str, as_json: bool) -> None:
    """Devolver (return) a received bloco de assinatura to the sender."""
    with SEIClient() as client:
        client.login()
        result = client.devolver_block(block_numero)
    if as_json:
        _emit(result, True)
        return
    icon = "✅" if result["ok"] else "❌"
    click.echo(f"{icon} {result['message']}")


@cli.command("block-remove")
@click.argument("id_documento")
@click.argument("block_numero")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def block_remove_cmd(id_documento: str, block_numero: str, as_json: bool) -> None:
    """Remove a document from a bloco de assinatura."""
    with SEIClient() as client:
        client.login()
        result = client.remove_document_from_block(id_documento, block_numero)
    if as_json:
        _emit(result, True)
        return
    icon = "✅" if result["ok"] else "❌"
    click.echo(f"{icon} {result['message']}")


@cli.command("read-doc")
@click.argument("id_documento")
@click.argument("id_procedimento")
@click.option("--unit", default=None, help="Unidade SEI (trocar antes)")
def read_doc_cmd(id_documento: str, id_procedimento: str, unit: str | None) -> None:
    """Read a document's text content."""
    with SEIClient() as client:
        if unit:
            client.switch_unit(unit)
        text = client.read_document(id_documento, id_procedimento)
    click.echo(text)


@cli.command("read-relatorio")
@click.argument("id_documento")
@click.argument("id_procedimento")
@click.option("--unit", default=None, help="Switch to unit before reading (e.g. 'OP 3')")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
@click.option("--summary", is_flag=True, help="Print human-readable summary")
def read_relatorio_cmd(
    id_documento: str,
    id_procedimento: str,
    unit: str | None,
    as_json: bool,
    summary: bool,
) -> None:
    """Parse a Relatório de Serviço Operacional into structured data."""
    from sei_cli.relatorio_parser import summarize as _summarize, to_dict

    with SEIClient() as client:
        client.login()
        if unit:
            client.switch_unit(unit)
        r = client.read_relatorio(id_documento, id_procedimento)

    if as_json:
        click.echo(json.dumps(to_dict(r), ensure_ascii=False, indent=2))
        return

    if summary or not as_json:
        click.echo(_summarize(r))
        return


@cli.command("authenticate")
@click.argument("id_procedimento")
@click.argument("id_documentos", nargs=-1, required=True)
@click.option("--unit", default=None, help="Unidade SEI (trocar antes)")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def authenticate_cmd(
    id_procedimento: str,
    id_documentos: tuple[str, ...],
    unit: str | None,
    as_json: bool,
) -> None:
    """Authenticate (autenticar) external documents in a process.

    Usage: sei authenticate <id_procedimento> <doc1> <doc2> ...

    External documents (PDFs uploaded to SEI) need authentication
    instead of signing.  The mechanism is identical to signing but
    the SEI UI labels it "Autenticação de Documento".
    """
    with SEIClient() as client:
        client.login()
        if unit:
            client.switch_unit(unit)
        results = client.authenticate_documents(list(id_documentos), id_procedimento)

    if as_json:
        click.echo(json.dumps(results, ensure_ascii=False, indent=2))
        return

    for r in results:
        doc_id = r.get("id_documento", "?")
        if r.get("error"):
            console.print(f"❌ Doc {doc_id}: {r['error']}")
        elif r.get("already_signed"):
            console.print(f"✅ Doc {doc_id}: já autenticado")
        elif r.get("signed"):
            console.print(f"✅ Doc {doc_id}: autenticado com sucesso")
        else:
            console.print(f"⚠️  Doc {doc_id}: resultado indefinido — {r}")


@cli.command("sign")
@click.argument("id_procedimento")
@click.argument("id_documentos", nargs=-1, required=True)
@click.option("--unit", default=None, help="Unidade SEI (trocar antes)")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def sign_cmd(
    id_procedimento: str,
    id_documentos: tuple[str, ...],
    unit: str | None,
    as_json: bool,
) -> None:
    """Assinar documentos internos em um processo.

    Usage: sei sign <id_procedimento> <doc1> <doc2> ...

    Requer que o campo 'cargo' esteja configurado em ~/.config/sei/credentials.json
    (ex: "cargo": "Tenente-Coronel QOEM BM").
    """
    with SEIClient() as client:
        client.login()
        if unit:
            client.switch_unit(unit)
        results = []
        for doc_id in id_documentos:
            r = client.sign_document(doc_id, id_procedimento)
            r["id_documento"] = doc_id
            results.append(r)

    if as_json:
        click.echo(json.dumps(results, ensure_ascii=False, indent=2))
        return

    for r in results:
        doc_id = r.get("id_documento", "?")
        if r.get("error"):
            console.print(f"[red]❌ Doc {doc_id}: {r['error']}[/red]")
        elif r.get("already_signed"):
            console.print(f"[yellow]⚠️  Doc {doc_id}: já assinado[/yellow]")
        elif r.get("signed"):
            console.print(f"[green]✅ Doc {doc_id}: assinado com sucesso[/green]")
        else:
            console.print(f"[yellow]⚠️  Doc {doc_id}: resultado indefinido — {r}[/yellow]")


@cli.command("process-finalize-preview")
@click.argument("numero_ou_id_processo")
@click.argument("document_ids", nargs=-1, required=False)
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def process_finalize_preview_cmd(
    numero_ou_id_processo: str,
    document_ids: tuple[str, ...],
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_process_finalize_preview(
            client,
            numero_ou_id_processo,
            document_ids=list(document_ids),
        )
    _emit_operation_result(result, as_json)


@cli.command("process-finalize-confirm")
@click.argument("numero_ou_id_processo")
@click.argument("document_ids", nargs=-1, required=False)
@click.option("--confirm", is_flag=True, help="Confirma a autenticação/assinatura")
@click.option("--force-sign", "force_sign_document_ids", multiple=True, help="Força assinatura de documento interno após review")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def process_finalize_confirm_cmd(
    numero_ou_id_processo: str,
    document_ids: tuple[str, ...],
    confirm: bool,
    force_sign_document_ids: tuple[str, ...],
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_process_finalize_confirm(
            client,
            numero_ou_id_processo,
            document_ids=list(document_ids),
            force_sign_document_ids=list(force_sign_document_ids),
            confirm=confirm,
        )
    _emit_operation_result(result, as_json)


@cli.command("process-forward-preview")
@click.argument("numero_ou_id_processo")
@click.argument("destinos", nargs=-1, required=True)
@click.option("--fechar", is_flag=True, help="Fechar o processo na unidade atual apos encaminhar")
@click.option("--retorno-em", default=None, help="Data do retorno programado")
@click.option("--retorno-dias", default=None, help="Prazo em dias para retorno programado")
@click.option("--retorno-dias-uteis", is_flag=True, help="Usa dias uteis no retorno programado")
@click.option("--reabrir-em", default=None, help="Data de reabertura programada")
@click.option("--reabrir-dias", default=None, help="Prazo em dias para reabertura programada")
@click.option("--reabrir-dias-uteis", is_flag=True, help="Usa dias uteis na reabertura programada")
@click.option("--review-mode", type=click.Choice(["fast", "summary", "deep"]), default="deep", show_default=True)
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def process_forward_preview_cmd(
    numero_ou_id_processo: str,
    destinos: tuple[str, ...],
    fechar: bool,
    retorno_em: str | None,
    retorno_dias: str | None,
    retorno_dias_uteis: bool,
    reabrir_em: str | None,
    reabrir_dias: str | None,
    reabrir_dias_uteis: bool,
    review_mode: str,
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_process_forward_preview(
            client,
            numero_ou_id_processo,
            destinos=list(destinos),
            manter_aberto=not fechar,
            retorno_em=retorno_em,
            retorno_dias=retorno_dias,
            retorno_dias_uteis=retorno_dias_uteis,
            reabrir_em=reabrir_em,
            reabrir_dias=reabrir_dias,
            reabrir_dias_uteis=reabrir_dias_uteis,
            review_mode=review_mode,
        )
    _emit_operation_result(result, as_json)


@cli.command("process-forward-confirm")
@click.argument("numero_ou_id_processo")
@click.argument("destinos", nargs=-1, required=True)
@click.option("--fechar", is_flag=True, help="Fechar o processo na unidade atual apos encaminhar")
@click.option("--retorno-em", default=None, help="Data do retorno programado")
@click.option("--retorno-dias", default=None, help="Prazo em dias para retorno programado")
@click.option("--retorno-dias-uteis", is_flag=True, help="Usa dias uteis no retorno programado")
@click.option("--reabrir-em", default=None, help="Data de reabertura programada")
@click.option("--reabrir-dias", default=None, help="Prazo em dias para reabertura programada")
@click.option("--reabrir-dias-uteis", is_flag=True, help="Usa dias uteis na reabertura programada")
@click.option("--review-mode", type=click.Choice(["fast", "summary", "deep"]), default="deep", show_default=True)
@click.option("--confirm", is_flag=True, help="Confirma o encaminhamento")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def process_forward_confirm_cmd(
    numero_ou_id_processo: str,
    destinos: tuple[str, ...],
    fechar: bool,
    retorno_em: str | None,
    retorno_dias: str | None,
    retorno_dias_uteis: bool,
    reabrir_em: str | None,
    reabrir_dias: str | None,
    reabrir_dias_uteis: bool,
    review_mode: str,
    confirm: bool,
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_process_forward_confirm(
            client,
            numero_ou_id_processo,
            destinos=list(destinos),
            manter_aberto=not fechar,
            retorno_em=retorno_em,
            retorno_dias=retorno_dias,
            retorno_dias_uteis=retorno_dias_uteis,
            reabrir_em=reabrir_em,
            reabrir_dias=reabrir_dias,
            reabrir_dias_uteis=reabrir_dias_uteis,
            review_mode=review_mode,
            confirm=confirm,
        )
    _emit_operation_result(result, as_json)


@cli.command("process-conclude-preview")
@click.argument("numero_ou_id_processo")
@click.option("--reabrir-em", default=None, help="Data para reabrir automaticamente")
@click.option("--reabrir-dias", default=None, help="Prazo em dias para reabrir automaticamente")
@click.option("--reabrir-dias-uteis", is_flag=True, help="Usa dias uteis para reabertura programada")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def process_conclude_preview_cmd(
    numero_ou_id_processo: str,
    reabrir_em: str | None,
    reabrir_dias: str | None,
    reabrir_dias_uteis: bool,
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_process_conclude_preview(
            client,
            numero_ou_id_processo,
            reabrir_em=reabrir_em,
            reabrir_dias=reabrir_dias,
            reabrir_dias_uteis=reabrir_dias_uteis,
        )
    _emit_operation_result(result, as_json)


@cli.command("process-conclude-confirm")
@click.argument("numero_ou_id_processo")
@click.option("--reabrir-em", default=None, help="Data para reabrir automaticamente")
@click.option("--reabrir-dias", default=None, help="Prazo em dias para reabrir automaticamente")
@click.option("--reabrir-dias-uteis", is_flag=True, help="Usa dias uteis para reabertura programada")
@click.option("--confirm", is_flag=True, help="Confirma a conclusao")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def process_conclude_confirm_cmd(
    numero_ou_id_processo: str,
    reabrir_em: str | None,
    reabrir_dias: str | None,
    reabrir_dias_uteis: bool,
    confirm: bool,
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_process_conclude_confirm(
            client,
            numero_ou_id_processo,
            reabrir_em=reabrir_em,
            reabrir_dias=reabrir_dias,
            reabrir_dias_uteis=reabrir_dias_uteis,
            confirm=confirm,
        )
    _emit_operation_result(result, as_json)


@cli.command("process-reopen-preview")
@click.argument("numero_ou_id_processo")
@click.option("--unit", default=None, help="Unidade preferida para tentar a reabertura")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def process_reopen_preview_cmd(numero_ou_id_processo: str, unit: str | None, as_json: bool) -> None:
    with SEIClient() as client:
        result = op_process_reopen_preview(client, numero_ou_id_processo, unit=unit)
    _emit_operation_result(result, as_json)


@cli.command("process-reopen-confirm")
@click.argument("numero_ou_id_processo")
@click.option("--unit", default=None, help="Unidade preferida para tentar a reabertura")
@click.option("--confirm", is_flag=True, help="Confirma a reabertura")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def process_reopen_confirm_cmd(
    numero_ou_id_processo: str,
    unit: str | None,
    confirm: bool,
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_process_reopen_confirm(
            client,
            numero_ou_id_processo,
            unit=unit,
            confirm=confirm,
        )
    _emit_operation_result(result, as_json)


@cli.command("download-pdf")
@click.argument("id_procedimento")
@click.option("-o", "--output", default=None, help="Output PDF path")
@click.option("--unit", default=None, help="Switch unit before download")
@click.option("--json", "as_json", is_flag=True)
def download_pdf_cmd(id_procedimento: str, output: str | None, unit: str | None, as_json: bool) -> None:
    """Download process PDF via SEI's native PDF generation.

    ID_PROCEDIMENTO: Internal SEI process ID (numeric).

    Downloads the full process as a single PDF using SEI's
    'Gerar PDF do Processo' feature.
    """
    with SEIClient() as client:
        if unit:
            client.switch_unit(unit)
        try:
            path = client.download_pdf(id_procedimento, output_path=output)
        except RuntimeError as e:
            if as_json:
                _emit({"ok": False, "error": str(e)}, True)
            else:
                console.print(f"[red]❌ {e}[/red]")
            raise SystemExit(1)

    if as_json:
        _emit({"ok": True, "path": path, "id_procedimento": id_procedimento}, True)
        return
    console.print(f"[green]✅ PDF salvo em: {path}[/green]")


@cli.command("download-doc-pdf")
@click.argument("id_documento")
@click.argument("id_procedimento")
@click.option("-o", "--output", default=None, help="Output PDF path")
@click.option("--unit", default=None, help="Switch unit before download")
@click.option("--json", "as_json", is_flag=True)
def download_doc_pdf_cmd(
    id_documento: str,
    id_procedimento: str,
    output: str | None,
    unit: str | None,
    as_json: bool,
) -> None:
    """Download a single document as PDF via SEI's documento_gerar_pdf.

    ID_DOCUMENTO: Internal SEI document ID (numeric).
    ID_PROCEDIMENTO: Internal SEI process ID containing the document.
    """
    with SEIClient() as client:
        if unit:
            client.switch_unit(unit)
        try:
            path = client.download_document_pdf(
                id_documento, id_procedimento, output_path=output
            )
        except RuntimeError as e:
            if as_json:
                _emit({"ok": False, "error": str(e)}, True)
            else:
                console.print(f"[red]❌ {e}[/red]")
            raise SystemExit(1)

    if as_json:
        _emit({"ok": True, "path": path, "id_documento": id_documento}, True)
        return
    console.print(f"[green]✅ PDF salvo em: {path}[/green]")


@cli.command("ciencia-doc")
@click.argument("id_documento")
@click.argument("id_procedimento")
@click.option("--unit", default=None, help="Unidade SEI (trocar antes)")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def ciencia_doc_cmd(id_documento: str, id_procedimento: str, unit: str | None, as_json: bool) -> None:
    """Dar ciência em um documento específico (acknowledge a document).

    ID_DOCUMENTO: Internal SEI document ID (numeric).
    ID_PROCEDIMENTO: Internal SEI process ID containing the document.

    'Dar ciência' registra que você leu o documento. Diferente de assinar —
    não requer senha, é apenas uma confirmação de leitura.
    """
    with SEIClient() as client:
        if unit:
            client.switch_unit(unit)
        try:
            result = client.give_notice_document(id_documento, id_procedimento)
        except RuntimeError as e:
            if as_json:
                _emit({"ok": False, "error": str(e)}, True)
            else:
                console.print(f"[red]❌ {e}[/red]")
            raise SystemExit(1)

    if as_json:
        _emit(result, True)
        return
    icon = "✅" if result.get("ok") else "❌"
    console.print(f"{icon} {result.get('message', result)}")


@cli.command("ciencia")
@click.argument("id_procedimento")
@click.option("--unit", default=None, help="Unidade SEI (trocar antes)")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def ciencia_cmd(id_procedimento: str, unit: str | None, as_json: bool) -> None:
    """Dar ciência no processo inteiro (acknowledge entire process).

    ID_PROCEDIMENTO: Internal SEI process ID (numeric).

    'Dar ciência' registra que você leu o processo. Diferente de assinar —
    não requer senha, é apenas uma confirmação de leitura.
    """
    with SEIClient() as client:
        if unit:
            client.switch_unit(unit)
        try:
            result = client.give_notice_process(id_procedimento)
        except RuntimeError as e:
            if as_json:
                _emit({"ok": False, "error": str(e)}, True)
            else:
                console.print(f"[red]❌ {e}[/red]")
            raise SystemExit(1)

    if as_json:
        _emit(result, True)
        return
    icon = "✅" if result.get("ok") else "❌"
    console.print(f"{icon} {result.get('message', result)}")


@cli.command("upload")
@click.argument("id_procedimento")
@click.argument("file_path")
@click.option("--tipo", default="externo", show_default=True,
              help="Tipo de documento (ex: oficio, despacho, externo)")
@click.option("--descricao", default="", help="Descrição do documento")
@click.option("--data", "data_elaboracao", default=None,
              help="Data de elaboração (DD/MM/YYYY). Padrão: hoje")
@click.option("--conferencia", "tipo_conferencia", default="4", show_default=True,
              help="Tipo de conferência: 1=Cópia Simples, 2=Auth Adm, 3=Auth Cartório, 4=Original")
@click.option("--nivel", "nivel_acesso", default="0", show_default=True,
              help="Nível de acesso: 0=Público, 1=Restrito, 2=Sigiloso")
@click.option("--numero", default="", help="Número do documento (opcional)")
@click.option("--unit", default=None, help="Unidade SEI (trocar antes)")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def upload_cmd(
    id_procedimento: str,
    file_path: str,
    tipo: str,
    descricao: str,
    data_elaboracao: str | None,
    tipo_conferencia: str,
    nivel_acesso: str,
    numero: str,
    unit: str | None,
    as_json: bool,
) -> None:
    """Anexar um PDF como Documento Externo em um processo.

    ID_PROCEDIMENTO: Internal SEI process ID (numeric).
    FILE_PATH: Caminho para o arquivo PDF a ser anexado.

    Faz o upload do arquivo como documento externo no processo SEI.
    Diferente de 'criar documento' — anexa um arquivo existente.
    """
    import os
    if not os.path.exists(file_path):
        if as_json:
            _emit({"ok": False, "error": f"Arquivo não encontrado: {file_path}"}, True)
        else:
            console.print(f"[red]❌ Arquivo não encontrado: {file_path}[/red]")
        raise SystemExit(1)

    with SEIClient() as client:
        if unit:
            client.switch_unit(unit)
        try:
            id_doc = client.upload_external_document(
                id_procedimento,
                file_path,
                tipo,
                nivel_acesso=nivel_acesso,
                descricao=descricao,
                data_elaboracao=data_elaboracao,
                tipo_conferencia=tipo_conferencia,
                numero=numero,
            )
        except (RuntimeError, FileNotFoundError) as e:
            if as_json:
                _emit({"ok": False, "error": str(e)}, True)
            else:
                console.print(f"[red]❌ {e}[/red]")
            raise SystemExit(1)

    if as_json:
        _emit({
            "ok": True,
            "id_documento": id_doc,
            "id_procedimento": id_procedimento,
            "file_path": file_path,
        }, True)
        return
    console.print(f"[green]✅ Documento externo criado — id_documento: {id_doc}[/green]")


@cli.command("inbox-snapshot")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def inbox_snapshot_cmd(as_json: bool) -> None:
    with SEIClient() as client:
        result = op_inbox_snapshot(client)
    _emit_operation_result(result, as_json)


@cli.command("environment-triage-preview")
@click.option("--only-new", is_flag=True, help="Somente processos novos (vermelho)")
@click.option("--only-changed", is_flag=True, help="Somente processos com novidade (ícone amarelo)")
@click.option("--only-unmarked", is_flag=True, help="Somente processos sem marcador")
@click.option("--include-marked-review", is_flag=True, help="Incluir processos já marcados para revisão")
@click.option("--offset", default=0, show_default=True, type=int)
@click.option("--limit", default=5, show_default=True, type=int)
@click.option("--mode", default="contextual", show_default=True, type=click.Choice(["fast", "contextual", "deep"]))
@click.option("--sample-size", default=3, show_default=True, type=int, help="Amostra usada no modo deep")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def environment_triage_preview_cmd(
    only_new: bool,
    only_changed: bool,
    only_unmarked: bool,
    include_marked_review: bool,
    offset: int,
    limit: int,
    mode: str,
    sample_size: int,
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_environment_triage_preview(
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
    _emit_operation_result(result, as_json)


@cli.command("environment-triage-parallel")
@click.option("--only-new", is_flag=True, help="Somente processos novos (vermelho)")
@click.option("--only-changed", is_flag=True, help="Somente processos com novidade (ícone amarelo)")
@click.option("--only-unmarked", is_flag=True, help="Somente processos sem marcador")
@click.option("--include-marked-review", is_flag=True, help="Incluir processos já marcados para revisão")
@click.option("--offset", default=0, show_default=True, type=int)
@click.option("--limit", default=15, show_default=True, type=int)
@click.option("--mode", default="contextual", show_default=True, type=click.Choice(["fast", "contextual", "deep"]))
@click.option("--sample-size", default=3, show_default=True, type=int, help="Amostra usada no modo deep")
@click.option("--workers", default=4, show_default=True, type=int)
@click.option("--chunk-size", default=15, show_default=True, type=int)
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def environment_triage_parallel_cmd(
    only_new: bool,
    only_changed: bool,
    only_unmarked: bool,
    include_marked_review: bool,
    offset: int,
    limit: int,
    mode: str,
    sample_size: int,
    workers: int,
    chunk_size: int,
    as_json: bool,
) -> None:
    result = op_environment_triage_parallel(
        only_new=only_new,
        only_changed=only_changed,
        only_unmarked=only_unmarked,
        include_marked_review=include_marked_review,
        offset=offset,
        limit=limit,
        mode=mode,
        sample_size=sample_size,
        workers=workers,
        chunk_size=chunk_size,
    )
    _emit_operation_result(result, as_json)


@cli.command("environment-triage-apply")
@click.option("--only-new", is_flag=True, help="Somente processos novos (vermelho)")
@click.option("--only-changed", is_flag=True, help="Somente processos com novidade (ícone amarelo)")
@click.option("--only-unmarked", is_flag=True, help="Somente processos sem marcador")
@click.option("--include-marked-review", is_flag=True, help="Incluir processos já marcados para revisão")
@click.option("--offset", default=0, show_default=True, type=int)
@click.option("--limit", default=5, show_default=True, type=int)
@click.option("--mode", default="contextual", show_default=True, type=click.Choice(["fast", "contextual", "deep"]))
@click.option("--sample-size", default=3, show_default=True, type=int, help="Amostra usada no modo deep")
@click.option("--confirm", is_flag=True, help="Confirma a aplicação da triagem")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def environment_triage_apply_cmd(
    only_new: bool,
    only_changed: bool,
    only_unmarked: bool,
    include_marked_review: bool,
    offset: int,
    limit: int,
    mode: str,
    sample_size: int,
    confirm: bool,
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_environment_triage_apply(
            client,
            only_new=only_new,
            only_changed=only_changed,
            only_unmarked=only_unmarked,
            include_marked_review=include_marked_review,
            offset=offset,
            limit=limit,
            mode=mode,
            sample_size=sample_size,
            confirm=confirm,
        )
    _emit_operation_result(result, as_json)


@cli.command("process-open")
@click.argument("numero_ou_id")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def process_open_cmd(numero_ou_id: str, as_json: bool) -> None:
    with SEIClient() as client:
        result = op_process_open(client, numero_ou_id)
    _emit_operation_result(result, as_json)


@cli.command("document-read")
@click.argument("numero_ou_id")
@click.option("--process-id", default=None, help="ID do processo relacionado")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def document_read_cmd(numero_ou_id: str, process_id: str | None, as_json: bool) -> None:
    with SEIClient() as client:
        result = op_document_read(client, numero_ou_id, id_procedimento=process_id)
    _emit_operation_result(result, as_json)


@cli.command("process-read")
@click.argument("numero_ou_id")
@click.option("--mode", default="contextual", show_default=True, type=click.Choice(["fast", "contextual", "summary", "deep", "all"]))
@click.option("--date-from", default=None, help="Filtrar documentos a partir desta data")
@click.option("--date-to", default=None, help="Filtrar documentos até esta data")
@click.option("--sample-size", default=3, show_default=True, type=int)
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def process_read_cmd(
    numero_ou_id: str,
    mode: str,
    date_from: str | None,
    date_to: str | None,
    sample_size: int,
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_process_read(
            client,
            numero_ou_id,
            mode=mode,
            date_from=date_from,
            date_to=date_to,
            sample_size=sample_size,
        )
    _emit_operation_result(result, as_json)


@cli.command("process-history")
@click.argument("numero_ou_id")
@click.option("--full", is_flag=True, help="Solicita o histórico completo ao SEI")
@click.option("--limit", default=None, type=int, help="Limita os registros retornados")
@click.option("--date-from", default=None, help="Data inicial (DD/MM/AAAA ou DD/MM/AAAA HH:MM)")
@click.option("--date-to", default=None, help="Data final (DD/MM/AAAA ou DD/MM/AAAA HH:MM)")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def process_history_cmd(
    numero_ou_id: str,
    full: bool,
    limit: int | None,
    date_from: str | None,
    date_to: str | None,
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_process_history(
            client,
            numero_ou_id,
            full=full,
            limit=limit,
            date_from=date_from,
            date_to=date_to,
        )
    _emit_operation_result(result, as_json)


@cli.command("process-summary")
@click.argument("numero_ou_id")
@click.option("--mode", default="contextual", show_default=True, type=click.Choice(["fast", "contextual", "summary", "deep", "all"]))
@click.option("--date-from", default=None)
@click.option("--date-to", default=None)
@click.option("--sample-size", default=3, show_default=True, type=int)
@click.option("--include-history", is_flag=True, help="Inclui o histórico recente no contexto")
@click.option("--history-limit", default=20, show_default=True, type=int, help="Máximo de eventos no resumo")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def process_summary_cmd(
    numero_ou_id: str,
    mode: str,
    date_from: str | None,
    date_to: str | None,
    sample_size: int,
    include_history: bool,
    history_limit: int,
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_process_summary(
            client,
            numero_ou_id,
            mode=mode,
            date_from=date_from,
            date_to=date_to,
            sample_size=sample_size,
            include_history=include_history,
            history_limit=history_limit,
        )
    _emit_operation_result(result, as_json)


@cli.command("marker-catalog")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def marker_catalog_cmd(as_json: bool) -> None:
    with SEIClient() as client:
        result = op_marker_catalog(client)
    _emit_operation_result(result, as_json)


@cli.command("process-marker-preview")
@click.argument("numero_ou_id")
@click.option("--marker", default=None, help="ID ou nome do marcador")
@click.option("--texto", "--text", "texto", default=None, help="Texto sugerido explícito para o marcador")
@click.option("--mode", default="contextual", show_default=True, type=click.Choice(["fast", "contextual", "summary", "deep", "all"]))
@click.option("--date-from", default=None)
@click.option("--date-to", default=None)
@click.option("--sample-size", default=3, show_default=True, type=int)
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def process_marker_preview_cmd(
    numero_ou_id: str,
    marker: str | None,
    texto: str | None,
    mode: str,
    date_from: str | None,
    date_to: str | None,
    sample_size: int,
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_process_marker_preview(
            client,
            numero_ou_id,
            marker=marker,
            mode=mode,
            date_from=date_from,
            date_to=date_to,
            sample_size=sample_size,
            suggested_text=texto,
        )
    _emit_operation_result(result, as_json)


@cli.command("process-marker-read")
@click.argument("numero_ou_id")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def process_marker_read_cmd(numero_ou_id: str, as_json: bool) -> None:
    with SEIClient() as client:
        result = op_process_marker_read(client, numero_ou_id)
    _emit_operation_result(result, as_json)


@cli.command("process-marker-history")
@click.argument("numero_ou_id")
@click.option("--marker", default=None, help="ID ou nome do marcador")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def process_marker_history_cmd(numero_ou_id: str, marker: str | None, as_json: bool) -> None:
    with SEIClient() as client:
        result = op_process_marker_history(client, numero_ou_id, marker=marker)
    _emit_operation_result(result, as_json)


@cli.command("process-marker-set-preview")
@click.argument("numero_ou_id")
@click.option("--marker", required=True, help="ID ou nome do marcador")
@click.option("--texto", "--text", "texto", default=None, help="Texto do marcador")
@click.option("--mode", default="contextual", show_default=True, type=click.Choice(["fast", "contextual", "summary", "deep", "all"]))
@click.option("--date-from", default=None)
@click.option("--date-to", default=None)
@click.option("--sample-size", default=3, show_default=True, type=int)
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def process_marker_set_preview_cmd(
    numero_ou_id: str,
    marker: str,
    texto: str | None,
    mode: str,
    date_from: str | None,
    date_to: str | None,
    sample_size: int,
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_process_marker_set_preview(
            client,
            numero_ou_id,
            marker=marker,
            texto=texto,
            mode=mode,
            date_from=date_from,
            date_to=date_to,
            sample_size=sample_size,
        )
    _emit_operation_result(result, as_json)


@cli.command("process-marker-set-confirm")
@click.argument("numero_ou_id")
@click.option("--marker", required=True, help="ID ou nome do marcador")
@click.option("--texto", "--text", "texto", default=None, help="Texto do marcador")
@click.option("--mode", default="contextual", show_default=True, type=click.Choice(["fast", "contextual", "summary", "deep", "all"]))
@click.option("--date-from", default=None)
@click.option("--date-to", default=None)
@click.option("--sample-size", default=3, show_default=True, type=int)
@click.option("--confirm", is_flag=True, help="Confirma a aplicação do marcador")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def process_marker_set_confirm_cmd(
    numero_ou_id: str,
    marker: str,
    texto: str | None,
    mode: str,
    date_from: str | None,
    date_to: str | None,
    sample_size: int,
    confirm: bool,
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_process_marker_set_confirm(
            client,
            numero_ou_id,
            marker=marker,
            texto=texto,
            mode=mode,
            date_from=date_from,
            date_to=date_to,
            sample_size=sample_size,
            confirm=confirm,
        )
    _emit_operation_result(result, as_json)


@cli.command("process-marker-remove-preview")
@click.argument("numero_ou_id")
@click.option("--marker", default=None, help="ID ou nome do marcador a remover")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def process_marker_remove_preview_cmd(numero_ou_id: str, marker: str | None, as_json: bool) -> None:
    with SEIClient() as client:
        result = op_process_marker_remove_preview(client, numero_ou_id, marker=marker)
    _emit_operation_result(result, as_json)


@cli.command("process-marker-remove-confirm")
@click.argument("numero_ou_id")
@click.option("--marker", default=None, help="ID ou nome do marcador a remover")
@click.option("--confirm", is_flag=True, help="Confirma a remoção do marcador")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def process_marker_remove_confirm_cmd(numero_ou_id: str, marker: str | None, confirm: bool, as_json: bool) -> None:
    with SEIClient() as client:
        result = op_process_marker_remove_confirm(client, numero_ou_id, marker=marker, confirm=confirm)
    _emit_operation_result(result, as_json)


@cli.command("process-marker-update-preview")
@click.argument("numero_ou_id")
@click.option("--marker", default=None, help="ID ou nome do marcador a alterar")
@click.option("--texto", "--text", "texto", default=None, help="Novo texto do marcador")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def process_marker_update_preview_cmd(
    numero_ou_id: str,
    marker: str | None,
    texto: str | None,
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_process_marker_update_preview(client, numero_ou_id, marker=marker, texto=texto)
    _emit_operation_result(result, as_json)


@cli.command("process-marker-update-confirm")
@click.argument("numero_ou_id")
@click.option("--marker", default=None, help="ID ou nome do marcador a alterar")
@click.option("--texto", "--text", "texto", required=True, help="Novo texto do marcador")
@click.option("--confirm", is_flag=True, help="Confirma a alteração do marcador")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def process_marker_update_confirm_cmd(
    numero_ou_id: str,
    marker: str | None,
    texto: str,
    confirm: bool,
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_process_marker_update_confirm(
            client,
            numero_ou_id,
            marker=marker,
            texto=texto,
            confirm=confirm,
        )
    _emit_operation_result(result, as_json)


@cli.command("tracking-group-catalog")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def tracking_group_catalog_cmd(as_json: bool) -> None:
    with SEIClient() as client:
        result = op_tracking_group_catalog(client)
    _emit_operation_result(result, as_json)


@cli.command("tracking-group-create-preview")
@click.argument("nome")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def tracking_group_create_preview_cmd(nome: str, as_json: bool) -> None:
    with SEIClient() as client:
        result = op_tracking_group_create_preview(client, nome)
    _emit_operation_result(result, as_json)


@cli.command("tracking-group-create-confirm")
@click.argument("nome")
@click.option("--confirm", is_flag=True, help="Confirma a criação do grupo")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def tracking_group_create_confirm_cmd(nome: str, confirm: bool, as_json: bool) -> None:
    with SEIClient() as client:
        result = op_tracking_group_create_confirm(client, nome, confirm=confirm)
    _emit_operation_result(result, as_json)


@cli.command("process-watch-read")
@click.argument("numero_ou_id")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def process_watch_read_cmd(numero_ou_id: str, as_json: bool) -> None:
    with SEIClient() as client:
        result = op_process_watch_read(client, numero_ou_id)
    _emit_operation_result(result, as_json)


@cli.command("process-watch-preview")
@click.argument("numero_ou_id")
@click.option("--group", "--grupo", "group", required=True, help="ID ou nome do grupo de acompanhamento")
@click.option("--obs", "--observacao", "observacao", default=None, help="Observação do acompanhamento")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def process_watch_preview_cmd(
    numero_ou_id: str,
    group: str,
    observacao: str | None,
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_process_watch_preview(client, numero_ou_id, group=group, observacao=observacao)
    _emit_operation_result(result, as_json)


@cli.command("process-watch-confirm")
@click.argument("numero_ou_id")
@click.option("--group", "--grupo", "group", required=True, help="ID ou nome do grupo de acompanhamento")
@click.option("--obs", "--observacao", "observacao", default=None, help="Observação do acompanhamento")
@click.option("--confirm", is_flag=True, help="Confirma o acompanhamento especial")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def process_watch_confirm_cmd(
    numero_ou_id: str,
    group: str,
    observacao: str | None,
    confirm: bool,
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_process_watch_confirm(
            client,
            numero_ou_id,
            group=group,
            observacao=observacao,
            confirm=confirm,
        )
    _emit_operation_result(result, as_json)


@cli.command("process-archive-preview")
@click.argument("numero_ou_id")
@click.option("--group", "--grupo", "group", default=None, help="ID ou nome do grupo de acompanhamento")
@click.option("--obs", "--observacao", "observacao", default=None, help="Observação do acompanhamento")
@click.option("--reabrir-em", default=None, help="Data para reabrir automaticamente")
@click.option("--reabrir-dias", default=None, help="Prazo em dias para reabrir automaticamente")
@click.option("--reabrir-dias-uteis", is_flag=True, help="Usa dias uteis para reabertura programada")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def process_archive_preview_cmd(
    numero_ou_id: str,
    group: str | None,
    observacao: str | None,
    reabrir_em: str | None,
    reabrir_dias: str | None,
    reabrir_dias_uteis: bool,
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_process_archive_preview(
            client,
            numero_ou_id,
            group=group,
            observacao=observacao,
            reabrir_em=reabrir_em,
            reabrir_dias=reabrir_dias,
            reabrir_dias_uteis=reabrir_dias_uteis,
        )
    _emit_operation_result(result, as_json)


@cli.command("process-archive-confirm")
@click.argument("numero_ou_id")
@click.option("--group", "--grupo", "group", default=None, help="ID ou nome do grupo de acompanhamento")
@click.option("--obs", "--observacao", "observacao", default=None, help="Observação do acompanhamento")
@click.option("--reabrir-em", default=None, help="Data para reabrir automaticamente")
@click.option("--reabrir-dias", default=None, help="Prazo em dias para reabrir automaticamente")
@click.option("--reabrir-dias-uteis", is_flag=True, help="Usa dias uteis para reabertura programada")
@click.option("--confirm", is_flag=True, help="Confirma o acompanhamento e a conclusão")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def process_archive_confirm_cmd(
    numero_ou_id: str,
    group: str | None,
    observacao: str | None,
    reabrir_em: str | None,
    reabrir_dias: str | None,
    reabrir_dias_uteis: bool,
    confirm: bool,
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_process_archive_confirm(
            client,
            numero_ou_id,
            group=group,
            observacao=observacao,
            reabrir_em=reabrir_em,
            reabrir_dias=reabrir_dias,
            reabrir_dias_uteis=reabrir_dias_uteis,
            confirm=confirm,
        )
    _emit_operation_result(result, as_json)


@cli.command("process-report")
@click.argument("numero_ou_id")
@click.option("--mode", default="contextual", show_default=True, type=click.Choice(["fast", "contextual", "summary", "deep", "all"]))
@click.option("--date-from", default=None)
@click.option("--date-to", default=None)
@click.option("--sample-size", default=3, show_default=True, type=int)
@click.option("--include-relatorios/--no-include-relatorios", default=True, show_default=True)
@click.option("--relatorio-limit", default=1, show_default=True, type=int)
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def process_report_cmd(
    numero_ou_id: str,
    mode: str,
    date_from: str | None,
    date_to: str | None,
    sample_size: int,
    include_relatorios: bool,
    relatorio_limit: int,
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_process_report(
            client,
            numero_ou_id,
            mode=mode,
            date_from=date_from,
            date_to=date_to,
            sample_size=sample_size,
            include_relatorios=include_relatorios,
            relatorio_limit=relatorio_limit,
        )
    _emit_operation_result(result, as_json)


@cli.command("process-create-preview")
@click.argument("tipo_processo")
@click.option("--especificacao", required=True)
@click.option("--interessados", default="")
@click.option("--observacoes", default="")
@click.option("--nivel", "nivel_acesso", default="0")
@click.option("--motivo-acesso", default="")
@click.option("--hipotese-acesso", default="")
@click.option("--hipotese-campo", default="")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def process_create_preview_cmd(
    tipo_processo: str,
    especificacao: str,
    interessados: str,
    observacoes: str,
    nivel_acesso: str,
    motivo_acesso: str,
    hipotese_acesso: str,
    hipotese_campo: str,
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_process_create_preview(
            client,
            tipo_processo,
            especificacao=especificacao,
            interessados=interessados,
            observacoes=observacoes,
            nivel_acesso=nivel_acesso,
            motivo_acesso=motivo_acesso,
            hipotese_acesso=hipotese_acesso,
            hipotese_campo=hipotese_campo,
        )
    _emit_operation_result(result, as_json)


@cli.command("process-create-confirm")
@click.argument("tipo_processo")
@click.option("--especificacao", required=True)
@click.option("--interessados", default="")
@click.option("--observacoes", default="")
@click.option("--nivel", "nivel_acesso", default="0")
@click.option("--motivo-acesso", default="")
@click.option("--hipotese-acesso", default="")
@click.option("--hipotese-campo", default="")
@click.option("--confirm", is_flag=True)
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def process_create_confirm_cmd(
    tipo_processo: str,
    especificacao: str,
    interessados: str,
    observacoes: str,
    nivel_acesso: str,
    motivo_acesso: str,
    hipotese_acesso: str,
    hipotese_campo: str,
    confirm: bool,
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_process_create_confirm(
            client,
            tipo_processo,
            especificacao=especificacao,
            interessados=interessados,
            observacoes=observacoes,
            nivel_acesso=nivel_acesso,
            motivo_acesso=motivo_acesso,
            hipotese_acesso=hipotese_acesso,
            hipotese_campo=hipotese_campo,
            confirm=confirm,
        )
    _emit_operation_result(result, as_json)


@cli.command("document-create-preview")
@click.argument("numero_ou_id_processo")
@click.argument("tipo_documento")
@click.option("--descricao", default="")
@click.option("--interessados", default="")
@click.option("--texto-inicial", default="N", help="Modo do texto inicial: N=Nenhum, T=Texto Padrão cadastrado no SEI, D=Documento Modelo. Não é o corpo do documento.")
@click.option("--documento-modelo", default="", help="Número SEI de documento existente para copiar como Documento Modelo; força texto inicial D.")
@click.option("--nivel", "nivel_acesso", default="inherit")
@click.option("--motivo-acesso", default="")
@click.option("--hipotese-acesso", default="")
@click.option("--hipotese-campo", default="")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def document_create_preview_cmd(
    numero_ou_id_processo: str,
    tipo_documento: str,
    descricao: str,
    interessados: str,
    texto_inicial: str,
    documento_modelo: str,
    nivel_acesso: str,
    motivo_acesso: str,
    hipotese_acesso: str,
    hipotese_campo: str,
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_document_create_preview(
            client,
            numero_ou_id_processo,
            tipo_documento,
            descricao=descricao,
            interessados=interessados,
            texto_inicial=texto_inicial,
            documento_modelo=documento_modelo,
            nivel_acesso=nivel_acesso,
            motivo_acesso=motivo_acesso,
            hipotese_acesso=hipotese_acesso,
            hipotese_campo=hipotese_campo,
        )
    _emit_operation_result(result, as_json)


@cli.command("document-create-confirm")
@click.argument("numero_ou_id_processo")
@click.argument("tipo_documento")
@click.option("--descricao", default="")
@click.option("--interessados", default="")
@click.option("--texto-inicial", default="N", help="Modo do texto inicial: N=Nenhum, T=Texto Padrão cadastrado no SEI, D=Documento Modelo. Não é o corpo do documento.")
@click.option("--documento-modelo", default="", help="Número SEI de documento existente para copiar como Documento Modelo; força texto inicial D.")
@click.option("--nivel", "nivel_acesso", default="inherit")
@click.option("--motivo-acesso", default="")
@click.option("--hipotese-acesso", default="")
@click.option("--hipotese-campo", default="")
@click.option("--confirm", is_flag=True)
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def document_create_confirm_cmd(
    numero_ou_id_processo: str,
    tipo_documento: str,
    descricao: str,
    interessados: str,
    texto_inicial: str,
    documento_modelo: str,
    nivel_acesso: str,
    motivo_acesso: str,
    hipotese_acesso: str,
    hipotese_campo: str,
    confirm: bool,
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_document_create_confirm(
            client,
            numero_ou_id_processo,
            tipo_documento,
            descricao=descricao,
            interessados=interessados,
            texto_inicial=texto_inicial,
            documento_modelo=documento_modelo,
            nivel_acesso=nivel_acesso,
            motivo_acesso=motivo_acesso,
            hipotese_acesso=hipotese_acesso,
            hipotese_campo=hipotese_campo,
            confirm=confirm,
        )
    _emit_operation_result(result, as_json)


@cli.command("document-edit-preview")
@click.argument("numero_ou_id")
@click.option("--process-id", default=None)
@click.option("--section-id", default=None)
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def document_edit_preview_cmd(
    numero_ou_id: str,
    process_id: str | None,
    section_id: str | None,
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_document_edit_preview(
            client,
            numero_ou_id,
            process_id=process_id,
            section_id=section_id,
        )
    _emit_operation_result(result, as_json)


@cli.command("document-edit-confirm")
@click.argument("numero_ou_id")
@click.option("--process-id", default=None)
@click.option("--section-id", default=None)
@click.option("--content", default=None, help="HTML bruto a salvar")
@click.option("--content-file", default=None, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--confirm", is_flag=True)
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def document_edit_confirm_cmd(
    numero_ou_id: str,
    process_id: str | None,
    section_id: str | None,
    content: str | None,
    content_file: Path | None,
    confirm: bool,
    as_json: bool,
) -> None:
    payload = content
    if content_file is not None:
        payload = content_file.read_text(encoding="utf-8")
    if payload is None:
        raise click.UsageError("Informe --content ou --content-file.")
    with SEIClient() as client:
        result = op_document_edit_confirm(
            client,
            numero_ou_id,
            content=payload,
            process_id=process_id,
            section_id=section_id,
            confirm=confirm,
        )
    _emit_operation_result(result, as_json)


@cli.command("document-quality-check")
@click.argument("numero_ou_id")
@click.option("--process-id", default=None)
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def document_quality_check_cmd(numero_ou_id: str, process_id: str | None, as_json: bool) -> None:
    with SEIClient() as client:
        result = op_document_quality_check(client, numero_ou_id, process_id=process_id)
    _emit_operation_result(result, as_json)


@cli.command("process-pdf-preview")
@click.argument("numero_ou_id")
@click.option("-o", "--output", default=None, help="Caminho local do PDF")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def process_pdf_preview_cmd(numero_ou_id: str, output: str | None, as_json: bool) -> None:
    with SEIClient() as client:
        result = op_process_pdf_preview(client, numero_ou_id, output_path=output)
    _emit_operation_result(result, as_json)


@cli.command("process-pdf-confirm")
@click.argument("numero_ou_id")
@click.option("-o", "--output", default=None, help="Caminho local do PDF")
@click.option("--confirm", is_flag=True, help="Confirma a geração do PDF")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def process_pdf_confirm_cmd(
    numero_ou_id: str,
    output: str | None,
    confirm: bool,
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_process_pdf_confirm(client, numero_ou_id, output_path=output, confirm=confirm)
    _emit_operation_result(result, as_json)


@cli.command("document-pdf-preview")
@click.argument("numero_ou_id")
@click.option("--process-id", default=None, help="ID interno do processo")
@click.option("-o", "--output", default=None, help="Caminho local do PDF")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def document_pdf_preview_cmd(
    numero_ou_id: str,
    process_id: str | None,
    output: str | None,
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_document_pdf_preview(
            client,
            numero_ou_id,
            process_id=process_id,
            output_path=output,
        )
    _emit_operation_result(result, as_json)


@cli.command("document-pdf-confirm")
@click.argument("numero_ou_id")
@click.option("--process-id", default=None, help="ID interno do processo")
@click.option("-o", "--output", default=None, help="Caminho local do PDF")
@click.option("--confirm", is_flag=True, help="Confirma a geração do PDF")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def document_pdf_confirm_cmd(
    numero_ou_id: str,
    process_id: str | None,
    output: str | None,
    confirm: bool,
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_document_pdf_confirm(
            client,
            numero_ou_id,
            process_id=process_id,
            output_path=output,
            confirm=confirm,
        )
    _emit_operation_result(result, as_json)


@cli.command("relatorio-read")
@click.argument("numero_ou_id")
@click.option("--process-id", default=None)
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def relatorio_read_cmd(numero_ou_id: str, process_id: str | None, as_json: bool) -> None:
    with SEIClient() as client:
        result = op_relatorio_read(client, numero_ou_id, id_procedimento=process_id)
    _emit_operation_result(result, as_json)


@cli.command("block-review")
@click.argument("block_numero")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def block_review_cmd(block_numero: str, as_json: bool) -> None:
    with SEIClient() as client:
        result = op_block_review(client, block_numero)
    _emit_operation_result(result, as_json)


@cli.command("signature-block-list")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def signature_block_list_cmd(as_json: bool) -> None:
    with SEIClient() as client:
        result = op_signature_block_list(client)
    _emit_operation_result(result, as_json)


@cli.command("signature-block-read")
@click.argument("block_numero")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def signature_block_read_cmd(block_numero: str, as_json: bool) -> None:
    with SEIClient() as client:
        result = op_signature_block_read(client, block_numero)
    _emit_operation_result(result, as_json)


@cli.command("signature-block-review")
@click.argument("block_numero")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def signature_block_review_cmd(block_numero: str, as_json: bool) -> None:
    with SEIClient() as client:
        result = op_signature_block_review(client, block_numero)
    _emit_operation_result(result, as_json)


@cli.command("signature-block-add-document-preview")
@click.argument("block_numero")
@click.argument("numero_ou_id_documento")
@click.option("--process-id", default=None)
@click.option("--disponibilizar", is_flag=True)
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def signature_block_add_document_preview_cmd(
    block_numero: str,
    numero_ou_id_documento: str,
    process_id: str | None,
    disponibilizar: bool,
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_signature_block_add_document_preview(
            client,
            block_numero,
            numero_ou_id_documento,
            process_id=process_id,
            disponibilizar=disponibilizar,
        )
    _emit_operation_result(result, as_json)


@cli.command("signature-block-add-document-confirm")
@click.argument("block_numero")
@click.argument("numero_ou_id_documento")
@click.option("--process-id", default=None)
@click.option("--disponibilizar", is_flag=True)
@click.option("--confirm", is_flag=True)
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def signature_block_add_document_confirm_cmd(
    block_numero: str,
    numero_ou_id_documento: str,
    process_id: str | None,
    disponibilizar: bool,
    confirm: bool,
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_signature_block_add_document_confirm(
            client,
            block_numero,
            numero_ou_id_documento,
            process_id=process_id,
            disponibilizar=disponibilizar,
            confirm=confirm,
        )
    _emit_operation_result(result, as_json)


@cli.command("signature-block-recall-preview")
@click.argument("block_numero")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def signature_block_recall_preview_cmd(block_numero: str, as_json: bool) -> None:
    with SEIClient() as client:
        result = op_signature_block_recall_preview(client, block_numero)
    _emit_operation_result(result, as_json)


@cli.command("signature-block-recall-confirm")
@click.argument("block_numero")
@click.option("--confirm", is_flag=True)
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def signature_block_recall_confirm_cmd(
    block_numero: str,
    confirm: bool,
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_signature_block_recall_confirm(
            client,
            block_numero,
            confirm=confirm,
        )
    _emit_operation_result(result, as_json)


@cli.command("signature-block-refresh-preview")
@click.argument("block_numero")
@click.option("--add-document-id", "add_document_ids", multiple=True)
@click.option("--remove-document-id", "remove_document_ids", multiple=True)
@click.option("--no-redisponibilizar", is_flag=True)
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def signature_block_refresh_preview_cmd(
    block_numero: str,
    add_document_ids: tuple[str, ...],
    remove_document_ids: tuple[str, ...],
    no_redisponibilizar: bool,
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_signature_block_refresh_preview(
            client,
            block_numero,
            add_document_ids=list(add_document_ids),
            remove_document_ids=list(remove_document_ids),
            redisponibilizar=not no_redisponibilizar,
        )
    _emit_operation_result(result, as_json)


@cli.command("signature-block-refresh-confirm")
@click.argument("block_numero")
@click.option("--add-document-id", "add_document_ids", multiple=True)
@click.option("--remove-document-id", "remove_document_ids", multiple=True)
@click.option("--no-redisponibilizar", is_flag=True)
@click.option("--confirm", is_flag=True)
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def signature_block_refresh_confirm_cmd(
    block_numero: str,
    add_document_ids: tuple[str, ...],
    remove_document_ids: tuple[str, ...],
    no_redisponibilizar: bool,
    confirm: bool,
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_signature_block_refresh_confirm(
            client,
            block_numero,
            add_document_ids=list(add_document_ids),
            remove_document_ids=list(remove_document_ids),
            redisponibilizar=not no_redisponibilizar,
            confirm=confirm,
        )
    _emit_operation_result(result, as_json)


@cli.command("signature-block-sign-preview")
@click.argument("block_numero")
@click.option("--document-id", "document_ids", multiple=True)
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def signature_block_sign_preview_cmd(
    block_numero: str,
    document_ids: tuple[str, ...],
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_signature_block_sign_preview(
            client,
            block_numero,
            document_ids=list(document_ids),
        )
    _emit_operation_result(result, as_json)


@cli.command("signature-block-sign-confirm")
@click.argument("block_numero")
@click.option("--document-id", "document_ids", multiple=True)
@click.option("--confirm", is_flag=True)
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def signature_block_sign_confirm_cmd(
    block_numero: str,
    document_ids: tuple[str, ...],
    confirm: bool,
    as_json: bool,
) -> None:
    with SEIClient() as client:
        result = op_signature_block_sign_confirm(
            client,
            block_numero,
            document_ids=list(document_ids),
            confirm=confirm,
        )
    _emit_operation_result(result, as_json)


@cli.command("workflow-show")
@click.argument("ref")
@click.option("--orgao", default=None)
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def workflow_show_cmd(ref: str, orgao: str | None, as_json: bool) -> None:
    result = op_workflow_show(ref, orgao=orgao)
    _emit_operation_result(result, as_json)


@cli.command("workflow-next")
@click.argument("ref")
@click.option("--current-step", default=None)
@click.option("--decision", default=None)
@click.option("--orgao", default=None)
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def workflow_next_cmd(
    ref: str,
    current_step: str | None,
    decision: str | None,
    orgao: str | None,
    as_json: bool,
) -> None:
    result = op_workflow_next(ref, current_step=current_step, decision=decision, orgao=orgao)
    _emit_operation_result(result, as_json)


if __name__ == "__main__":
    cli()
