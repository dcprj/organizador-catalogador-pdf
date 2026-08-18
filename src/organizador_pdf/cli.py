"""Ponto de entrada da CLI."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

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
from .config import Config, ErroDeConfiguracao, Provedor
from .converter import ErroDeConversao, listar_pdfs
from .extractor import ErroFatalDeAPI
from .logging_utils import configurar_logs
from .pipeline import OpcoesDoPipeline, Pipeline, ResultadoDoArquivo, Situacao

logger = logging.getLogger(__name__)

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help=(
        "Converte PDFs em Markdown, extrai metadados bibliográficos com um LLM "
        "e organiza os arquivos em <DESTINO>/<AREA>/<SUBAREA>/<TIPO>/."
    ),
)

saida = Console()


def _versao(valor: bool) -> None:
    if valor:
        saida.print(f"organizador-pdf {__version__}")
        raise typer.Exit()


@app.command()
def processar(
    origem: Path = typer.Option(
        ...,
        "--origem",
        "-i",
        help="Diretório contendo os PDFs a processar.",
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
    ),
    destino: Path = typer.Option(
        ...,
        "--destino",
        "-o",
        help="Diretório raiz onde os arquivos organizados serão salvos.",
        file_okay=False,
        dir_okay=True,
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Analisa e exibe a estrutura planejada sem copiar/mover nada.",
    ),
    recursive: bool = typer.Option(
        True,
        "--recursive/--no-recursive",
        "-r/-R",
        help="Buscar PDFs também nas subpastas da origem.",
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
    modelo: Optional[str] = typer.Option(
        None,
        "--modelo",
        "-m",
        help=(
            "Modelo do Ollama a usar; tem precedência sobre ORGPDF_MODELO. "
            "Precisa já estar baixado (`ollama pull <modelo>`)."
        ),
    ),
    ollama_url: Optional[str] = typer.Option(
        None,
        "--ollama-url",
        help="Endereço do servidor Ollama. Padrão: http://localhost:11434.",
    ),
    provedor: Optional[Provedor] = typer.Option(
        None,
        "--provedor",
        "-p",
        help=(
            "Provedor do LLM. Padrão: ollama (local, grátis, privado). Os "
            "demais são pagos, opcionais, e exigem --apikey."
        ),
    ),
    api_key: Optional[str] = typer.Option(
        None,
        "--apikey",
        "-k",
        help=(
            "Chave de API do provedor pago escolhido (ignorada com --provedor "
            "ollama). Em máquina compartilhada, prefira ORGPDF_<PROVEDOR>_API_KEY "
            "no .env em vez desta flag — ela fica visível no histórico do shell "
            "e na lista de processos."
        ),
    ),
    provedor_fallback: Optional[Provedor] = typer.Option(
        None,
        "--provedor-fallback",
        help=(
            "Provedor pago acionado só quando a extração do --provedor principal "
            "falhar ou sair com aviso de divergência. Desligado por padrão — "
            "nenhuma chamada extra é feita sem isto. Exige --modelo-fallback e "
            "uma chave de API (mesmas variáveis ORGPDF_<PROVEDOR>_API_KEY)."
        ),
    ),
    modelo_fallback: Optional[str] = typer.Option(
        None,
        "--modelo-fallback",
        help="Modelo do provedor de fallback. Obrigatório se --provedor-fallback for usado.",
    ),
    api_key_fallback: Optional[str] = typer.Option(
        None,
        "--apikey-fallback",
        help="Chave de API do provedor de fallback (mesmas ressalvas de --apikey).",
    ),
    ocr: Optional[bool] = typer.Option(
        None,
        "--ocr/--no-ocr",
        help=(
            "Recorre a OCR (Tesseract) para PDFs sem texto extraível "
            "(digitalizados). Padrão: ligado, mas só tem efeito se o "
            "Tesseract estiver instalado — veja o README."
        ),
    ),
    ocr_idioma: Optional[str] = typer.Option(
        None,
        "--ocr-idioma",
        help="Idioma do OCR, no formato de 3 letras do Tesseract. Padrão: por.",
    ),
    limite: Optional[int] = typer.Option(
        None,
        "--limite",
        "-n",
        min=1,
        help="Processa no máximo N arquivos (útil para testar o custo do lote).",
    ),
    arquivo_log: Path = typer.Option(
        Path("erros.log"),
        "--log",
        help="Arquivo onde os erros são registrados.",
    ),
    env_file: Optional[Path] = typer.Option(
        None,
        "--env",
        help="Caminho de um arquivo .env alternativo.",
    ),
    verboso: bool = typer.Option(False, "--verbose", "-v", help="Log detalhado."),
    _: bool = typer.Option(
        False, "--version", callback=_versao, is_eager=True, help="Mostra a versão."
    ),
) -> None:
    """Processa em lote os PDFs de --origem e organiza em --destino."""
    console_log = configurar_logs(arquivo_log, verboso=verboso)

    try:
        config = Config.do_ambiente(
            env_file,
            modelo=modelo,
            ollama_url=ollama_url,
            provedor=provedor.value if provedor else None,
            api_key=api_key,
            provedor_fallback=provedor_fallback.value if provedor_fallback else None,
            modelo_fallback=modelo_fallback,
            api_key_fallback=api_key_fallback,
            ocr=ocr,
            ocr_idioma=ocr_idioma,
        )
    except ErroDeConfiguracao as exc:
        saida.print(f"[bold red]Erro de configuração:[/] {exc}")
        raise typer.Exit(code=2) from exc

    try:
        pdfs = listar_pdfs(origem, recursivo=recursive)
    except ErroDeConversao as exc:
        saida.print(f"[bold red]Erro:[/] {exc}")
        raise typer.Exit(code=2) from exc

    if not pdfs:
        saida.print(f"[yellow]Nenhum PDF encontrado em {origem}.[/]")
        raise typer.Exit(code=1)

    if limite:
        pdfs = pdfs[:limite]

    _cabecalho(config, origem, destino, pdfs, dry_run=dry_run, mover=mover)

    opcoes = OpcoesDoPipeline(
        destino=destino,
        dry_run=dry_run,
        mover=mover,
        subpasta_markdown=subpasta_markdown,
    )

    pipeline = Pipeline(config, opcoes)

    resultados: list[ResultadoDoArquivo] = []
    interrompido: Optional[str] = None

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console_log,
        transient=True,
    ) as progresso:
        tarefa = progresso.add_task("Processando PDFs", total=len(pdfs))
        for pdf in pdfs:
            progresso.update(tarefa, description=f"Processando {pdf.name[:45]}")
            try:
                resultados.append(pipeline.processar_arquivo(pdf))
            except ErroFatalDeAPI as exc:
                # Vale para todos os arquivos; insistir só gastaria tempo.
                interrompido = str(exc)
                logger.error("Lote interrompido: %s", exc)
                break
            except KeyboardInterrupt:
                interrompido = "interrompido pelo usuário"
                break
            progresso.advance(tarefa)

    _relatorio(resultados, destino, dry_run=dry_run, arquivo_log=arquivo_log)

    if interrompido:
        restantes = len(pdfs) - len(resultados)
        saida.print(
            f"\n[bold red]Lote interrompido:[/] {interrompido}\n"
            f"{restantes} arquivo(s) não chegaram a ser processados."
        )
        raise typer.Exit(code=2)

    houve_falha = any(not r.ok for r in resultados)
    raise typer.Exit(code=1 if houve_falha else 0)


def _cabecalho(
    config: Config,
    origem: Path,
    destino: Path,
    pdfs: list[Path],
    *,
    dry_run: bool,
    mover: bool,
) -> None:
    if config.provedor is Provedor.OLLAMA:
        linha_modelo = f"[bold]Modelo:[/]  {config.modelo}  [dim](Ollama em {config.ollama_url})[/]"
    else:
        linha_modelo = (
            f"[bold]Modelo:[/]  {config.modelo}  "
            f"[dim](provedor pago: {config.provedor.value})[/]"
        )
    linhas = [
        f"[bold]Origem:[/]  {origem.resolve()}",
        f"[bold]Destino:[/] {destino.resolve()}",
        f"[bold]PDFs:[/]    {len(pdfs)}",
        linha_modelo,
    ]
    if config.provedor_fallback is not None:
        linhas.append(
            f"[bold]Fallback:[/] {config.modelo_fallback}  "
            f"[dim](provedor pago: {config.provedor_fallback.value}, só se houver "
            "falha/aviso)[/]"
        )
    linhas += [
        f"[bold]Análise:[/] {config.max_paginas} primeiras páginas "
        f"(até {config.max_caracteres} caracteres)",
        f"[bold]Modo:[/]    " + ("mover" if mover else "copiar"),
    ]
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
        titulo = (
            "Estrutura planejada (dry-run)" if dry_run else "Arquivos organizados"
        )
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
            assert m is not None  # garantido quando a situação não é FALHA
            tabela.add_row(
                "[bold yellow]![/]" if resultado.aviso else "",
                resultado.origem.name,
                m.tipo_publicacao.value,
                f"{m.area_principal} / {m.subarea}",
                m.titulo,
                str(m.ano) if m.ano else "—",
            )
        saida.print()
        saida.print(tabela)

    avisos = [r for r in sucessos if r.aviso]
    if avisos:
        tabela_avisos = Table(
            title="! Possíveis divergências — confira manualmente",
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
    if avisos:
        resumo += f"\n[yellow]{len(avisos)} com possível divergência — revise antes de confiar cegamente[/]"
    if falhas or avisos:
        resumo += f"\nDetalhes em: {arquivo_log.resolve()}"
    saida.print()
    saida.print(Panel(resumo, title="Resumo", expand=False))


def _arvore(sucessos: list[ResultadoDoArquivo], destino: Path, titulo: str) -> Tree:
    """Monta a visualização em árvore dos caminhos de destino."""
    raiz = Tree(f"[bold]{titulo}[/] — {destino.resolve()}")
    nos: dict[Path, Tree] = {}

    for resultado in sorted(sucessos, key=lambda r: str(r.pdf_destino)):
        assert resultado.pdf_destino is not None
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
