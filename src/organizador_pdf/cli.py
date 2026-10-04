"""Ponto de entrada da CLI (organizador-pdf)."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.tree import Tree

from . import __version__
from .config import Config, ErroDeConfiguracao
from .converter import ErroDeConversao, listar_pdfs
from .estado import EstadoDeExecucao, ParametrosSalvos
from .extractor import ErroFatalDeAPI
from .logging_utils import configurar_logs
from .pipeline import OpcoesDoPipeline, Pipeline, ResultadoDoArquivo, Situacao

logger = logging.getLogger(__name__)

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help=(
        "Organizador e Catalogador de PDFs determinístico com ABNT NBR 6023, "
        "extração de Ficha Catalográfica (CIP), enriquecimento em APIs públicas e Markdown companion."
    ),
)

saida = Console()


def _versao(valor: bool) -> None:
    if valor:
        saida.print(f"organizador-pdf {__version__}")
        raise typer.Exit()


@app.command()
def processar(
    origem: Optional[Path] = typer.Option(
        None,
        "--origem",
        "--input",
        "-i",
        help="Diretório contendo os PDFs a processar. Obrigatório, exceto com --resume.",
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
    ),
    destino: Optional[Path] = typer.Option(
        None,
        "--destino",
        "--output",
        "-o",
        help="Diretório raiz onde os arquivos organizados serão salvos. Obrigatório, exceto com --resume.",
        file_okay=False,
        dir_okay=True,
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Analisa e exibe a estrutura planejada sem copiar/mover nada.",
    ),
    resume: bool = typer.Option(
        False,
        "--resume",
        help=(
            "Retoma o último lote interrompido: reaplica os parâmetros da "
            "execução anterior e pula os arquivos já concluídos."
        ),
    ),
    recursive: bool = typer.Option(
        True,
        "--recursive/--no-recursive",
        "-r/-R",
        help="Buscar PDFs também nas subpastas da origem (padrão: ligado).",
    ),
    mover: bool = typer.Option(
        False,
        "--mover",
        help="Mover o PDF original em vez de copiá-lo (padrão: copiar).",
    ),
    subpasta_markdown: Optional[str] = typer.Option(
        None,
        "--subpasta-md",
        help="Grava os .md em uma subpasta espelho (ex.: 'Markdown').",
    ),
    quarantine: bool = typer.Option(
        True,
        "--quarantine/--no-quarantine",
        help="Direciona arquivos com avisos de divergência ou baixa confiança para revisao_manual/ (padrão: ligado).",
    ),
    online: bool = typer.Option(
        True,
        "--online/--offline",
        help="Permite ou desativa consultas a APIs públicas (Crossref, Google Books, Brasil API) para enriquecimento bibliográfico (padrão: online).",
    ),
    paralelo: int = typer.Option(
        1,
        "--paralelo",
        "-j",
        min=1,
        max=32,
        help="Número de arquivos processados simultaneamente (padrão: 1).",
    ),
    limite: Optional[int] = typer.Option(
        None,
        "--limite",
        "-n",
        min=1,
        help="Processa no máximo N arquivos da fila (útil para testes rápidos).",
    ),
    arquivo_log: Path = typer.Option(
        Path("erros.log"),
        "--log",
        help="Arquivo onde falhas e avisos detalhados serão registrados.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Exibe mensagens detalhadas no terminal.",
    ),
    interactive: bool = typer.Option(
        False,
        "--interactive",
        "--validate",
        help="Executa em modo interativo de validação passo a passo com confirmação de metadados em tempo real.",
    ),
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        callback=_versao,
        is_eager=True,
        help="Exibe a versão do organizador-pdf e sai.",
    ),
) -> None:
    """Executa a catalogação e organização do lote de PDFs."""
    configurar_logs(arquivo_log=arquivo_log, verbose=verbose)

    if interactive:
        if origem is None or destino is None:
            saida.print("[bold red]--origem e --destino são obrigatórios para o modo interativo.[/]")
            raise typer.Exit(code=2)
        from scripts.interactive_validator import run_interactive_validator
        cod = run_interactive_validator(
            input_dir=str(origem.resolve()),
            output_dir=str(destino.resolve()),
            interactive=True,
            move_original=mover,
        )
        raise typer.Exit(code=cod)

    # 1. Trata o --resume
    estado: Optional[EstadoDeExecucao] = None
    if resume:
        estado = EstadoDeExecucao.carregar()
        if estado is None:
            saida.print("[bold red]Nenhuma execução pendente para retomar com --resume.[/]")
            raise typer.Exit(code=2)
        origem = Path(estado.parametros.origem)
        destino = Path(estado.parametros.destino)
        dry_run = estado.parametros.dry_run
        recursive = estado.parametros.recursive
        mover = estado.parametros.mover
        subpasta_markdown = estado.parametros.subpasta_markdown
        paralelo = estado.parametros.paralelo or paralelo
    else:
        if origem is None or destino is None:
            saida.print("[bold red]--origem e --destino são obrigatórios (ou use --resume).[/]")
            raise typer.Exit(code=2)
        if not dry_run:
            estado = EstadoDeExecucao(
                parametros=ParametrosSalvos(
                    origem=str(origem.resolve()),
                    destino=str(destino.resolve()),
                    dry_run=dry_run,
                    recursive=recursive,
                    mover=mover,
                    subpasta_markdown=subpasta_markdown,
                    paralelo=paralelo,
                )
            )

    # 2. Lista os PDFs
    try:
        todos_pdfs = listar_pdfs(origem, recursivo=recursive)
    except ErroDeConversao as exc:
        saida.print(f"[bold red]{exc}[/]")
        raise typer.Exit(code=2)

    if not todos_pdfs:
        saida.print(f"[yellow]Nenhum arquivo PDF encontrado em {origem.resolve()}[/]")
        raise typer.Exit(code=0)

    # Filtra os já concluídos com --resume
    if estado is not None and resume:
        pdfs = [p for p in todos_pdfs if str(p.resolve()) not in estado.concluidos]
        pulados = len(todos_pdfs) - len(pdfs)
        if pulados:
            saida.print(f"[dim]{pulados} arquivo(s) pulado(s) pois já foram concluídos.[/]")
    else:
        pdfs = todos_pdfs

    if limite:
        pdfs = pdfs[:limite]

    if not pdfs:
        saida.print("[green]Todos os arquivos já foram concluídos com sucesso.[/]")
        if estado is not None:
            EstadoDeExecucao.limpar()
        raise typer.Exit(code=0)

    # 3. Cabeçalho de Execução
    _cabecalho(
        origem=origem,
        destino=destino,
        pdfs=pdfs,
        dry_run=dry_run,
        mover=mover,
        paralelo=paralelo,
        online=online,
    )

    opcoes = OpcoesDoPipeline(
        destino=destino,
        dry_run=dry_run,
        mover=mover,
        subpasta_markdown=subpasta_markdown,
        quarantine=quarantine,
        online=online,
    )
    pipeline = Pipeline(opcoes=opcoes)

    # 4. Processamento com barra de progresso
    resultados_dict, interrompido = _executar_lote(
        pdfs,
        pipeline=pipeline,
        estado=estado if not dry_run else None,
        paralelo=paralelo,
    )

    # Mantém a ordem original da lista
    resultados = [resultados_dict[p] for p in pdfs if p in resultados_dict]

    # 5. Relatório e Resumo
    _relatorio(resultados, destino, dry_run=dry_run, arquivo_log=arquivo_log)

    if interrompido:
        saida.print(f"\n[bold yellow]Lote interrompido ({interrompido}). Use --resume para continuar.[/]")
        raise typer.Exit(code=2)

    if estado is not None and not dry_run and all(r.ok for r in resultados):
        EstadoDeExecucao.limpar()

    tem_falhas = any(not r.ok for r in resultados)
    if tem_falhas:
        raise typer.Exit(code=1)


def _executar_lote(
    pdfs: list[Path],
    *,
    pipeline: Pipeline,
    estado: Optional[EstadoDeExecucao],
    paralelo: int,
) -> tuple[dict[Path, ResultadoDoArquivo], Optional[str]]:
    resultados: dict[Path, ResultadoDoArquivo] = {}
    interrompido: Optional[str] = None
    lock_estado = threading.Lock()
    lock_progresso = threading.Lock()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=saida,
    ) as progresso:
        tarefa = progresso.add_task("Processando PDFs...", total=len(pdfs))

        with ThreadPoolExecutor(max_workers=paralelo) as executor:
            fila = list(pdfs)
            em_andamento = {}

            def submeter_proximo():
                if fila and interrompido is None:
                    pdf = fila.pop(0)
                    em_andamento[executor.submit(pipeline.processar_arquivo, pdf)] = pdf

            for _ in range(min(paralelo, len(fila))):
                submeter_proximo()

            try:
                while em_andamento:
                    concluidos = wait(list(em_andamento.keys()), return_when=FIRST_COMPLETED).done
                    for futuro in concluidos:
                        pdf = em_andamento.pop(futuro)
                        try:
                            resultado = futuro.result()
                        except ErroFatalDeAPI as exc:
                            if interrompido is None:
                                interrompido = str(exc)
                                logger.error("Lote interrompido: %s", exc)
                            continue
                        except Exception as exc:
                            logger.error("Erro fatal no arquivo %s: %s", pdf.name, exc)
                            resultado = ResultadoDoArquivo(
                                origem=pdf,
                                situacao=Situacao.FALHA,
                                erro=str(exc),
                                etapa="execução de thread",
                            )
                        resultados[pdf] = resultado
                        if estado is not None:
                            with lock_estado:
                                estado.marcar_concluido(pdf)
                        with lock_progresso:
                            progresso.advance(tarefa)
                        if interrompido is None:
                            submeter_proximo()
            except KeyboardInterrupt:
                if interrompido is None:
                    interrompido = "interrompido pelo usuário"

    return resultados, interrompido


def _cabecalho(
    origem: Path,
    destino: Path,
    pdfs: list[Path],
    *,
    dry_run: bool,
    mover: bool,
    paralelo: int = 1,
    online: bool = True,
) -> None:
    linhas = [
        f"[bold]Origem:[/]  {origem.resolve()}",
        f"[bold]Destino:[/] {destino.resolve()}",
        f"[bold]PDFs:[/]    {len(pdfs)}",
        "[bold]Motor:[/]   Determinístico Local (CIP, ABNT NBR 6023)",
        f"[bold]Rede:[/]    " + ("[green]Online[/] (consultas a APIs públicas ativas)" if online else "[yellow]100% Offline[/] (consultas externas desativadas)"),
        "[bold]Análise:[/] 10 primeiras + 10 últimas páginas (sem converter miolo)",
        f"[bold]Modo:[/]    " + ("mover" if mover else "copiar"),
    ]
    if paralelo > 1:
        linhas.append(f"[bold]Paralelo:[/] até {paralelo} arquivo(s) simultâneos")
    if dry_run:
        linhas.append("[bold yellow]DRY-RUN — nenhum arquivo será gravado.[/]")
    saida.print(Panel("\n".join(linhas), title="Organizador de PDF", expand=False))


def _relatorio(
    resultados: list[ResultadoDoArquivo],
    destino: Path,
    *,
    dry_run: bool,
    arquivo_log: Path,
) -> None:
    sucessos = [r for r in resultados if r.ok]
    falhas = [r for r in resultados if not r.ok]

    if sucessos:
        titulo = "Estrutura planejada (dry-run)" if dry_run else "Arquivos organizados"
        saida.print()
        saida.print(_arvore(sucessos, destino, titulo))

        tabela = Table(title="Metadados extraídos", show_lines=False, expand=True)
        tabela.add_column("", max_width=2)
        tabela.add_column("Arquivo de origem", overflow="ellipsis", max_width=32)
        tabela.add_column("Tipo", max_width=16)
        tabela.add_column("Área / Subárea", overflow="ellipsis", max_width=30)
        tabela.add_column("Título", overflow="ellipsis")
        tabela.add_column("Ano", justify="right", max_width=5)
        for resultado in sucessos:
            m = resultado.metadados
            assert m is not None
            marcador = "[bold yellow]![/]" if resultado.aviso else ""
            if resultado.usou_fallback:
                marcador += "[bold cyan]$[/]"
            tabela.add_row(
                marcador,
                resultado.origem.name,
                m.tipo_publicacao.value,
                f"{m.area_principal} / {m.subarea}",
                m.titulo,
                str(m.ano) if m.ano else "—",
            )
        saida.print()
        saida.print(tabela)
        if any(r.usou_fallback for r in sucessos):
            saida.print("[dim]$ extraído pelo provedor de fallback (pago)[/]")

    avisos = [r for r in sucessos if r.aviso]
    if avisos:
        tabela_avisos = Table(
            title="! Alertas e Revisão Manual",
            show_lines=False,
            expand=True,
            border_style="yellow",
        )
        tabela_avisos.add_column("Arquivo", overflow="ellipsis", max_width=34)
        tabela_avisos.add_column("Aviso", overflow="fold")
        for resultado in avisos:
            tabela_avisos.add_row(resultado.origem.name, resultado.aviso or "—")
        saida.print()
        saida.print(tabela_avisos)

    if falhas:
        tabela_erros = Table(title="Falhas", show_lines=False, expand=True)
        tabela_erros.add_column("Arquivo", overflow="ellipsis", max_width=34)
        tabela_erros.add_column("Etapa", max_width=22)
        tabela_erros.add_column("Erro", overflow="fold")
        for resultado in falhas:
            tabela_erros.add_row(
                resultado.origem.name, resultado.etapa or "—", resultado.erro or "—"
            )
        saida.print()
        saida.print(tabela_erros)

    simulados = sum(1 for r in resultados if r.situacao is Situacao.SIMULADO)
    gravados = sum(1 for r in resultados if r.situacao is Situacao.SUCESSO)
    resumo = (
        f"[green]{gravados} gravado(s)[/] · "
        f"[cyan]{simulados} simulado(s)[/] · "
        f"[red]{len(falhas)} falha(s)[/] · {len(resultados)} total"
    )
    if sucessos:
        locais = sum(1 for r in sucessos if r.provedor_usado in ("ollama", "deterministico_local"))
        pagos = len(sucessos) - locais
        resumo += f"\n[dim]{locais} extraído(s) localmente (Ollama) · {pagos} via provedor pago[/]"
    if avisos:
        resumo += f"\n[yellow]{len(avisos)} com possível divergência/revisão manual[/]"
    if falhas or avisos:
        resumo += f"\nDetalhes em: {arquivo_log.resolve()}"
    saida.print()
    saida.print(Panel(resumo, title="Resumo", expand=False))


def _arvore(sucessos: list[ResultadoDoArquivo], destino: Path, titulo: str) -> Tree:
    raiz = Tree(f"[bold]{titulo}[/] — {destino.resolve()}")
    nos: dict[Path, Tree] = {}

    for resultado in sorted(sucessos, key=lambda r: str(r.pdf_destino)):
        if resultado.pdf_destino is None:
            continue
        try:
            relativo = resultado.pdf_destino.parent.relative_to(destino)
        except ValueError:
            relativo = resultado.pdf_destino.parent

        atual = raiz
        acumulado = Path()
        for parte in relativo.parts:
            acumulado = acumulado / parte
            if acumulado not in nos:
                nos[acumulado] = atual.add(f"[blue]{parte}/[/]")
            atual = nos[acumulado]

        atual.add(f"{resultado.pdf_destino.name}")
        if md := resultado.markdown_destino:
            try:
                rotulo = md.relative_to(resultado.pdf_destino.parent).as_posix()
            except ValueError:
                rotulo = md.name
            atual.add(f"[dim]{rotulo}[/]")

    return raiz


def main() -> None:
    """Entrada do console script `organizador-pdf`."""
    app()


if __name__ == "__main__":
    main()
