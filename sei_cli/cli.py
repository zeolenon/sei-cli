from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

import click
from rich.console import Console
from rich.table import Table

from sei_cli.client import SEIClient
from sei_cli.models import Block, Process, SystemStatus

console = Console()


def _emit(data: Any, as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(data, ensure_ascii=False, indent=2))


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
@click.argument("block_id")
@click.option("--json", "as_json", is_flag=True, help="Saída JSON")
def block_cmd(block_id: str, as_json: bool) -> None:
    with SEIClient() as client:
        details = client.get_block(block_id)
    if as_json:
        _emit(asdict(details), True)
        return

    table = Table(title=f"Bloco {details.block_id}")
    table.add_column("Documento")
    table.add_column("Nome")
    for doc in details.documentos:
        table.add_row(doc.numero, doc.nome)
    console.print(table)


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
        status = client.switch_unit(sigla)

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


if __name__ == "__main__":
    cli()
