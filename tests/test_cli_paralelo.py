from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from organizador_pdf import estado as estado_mod
from organizador_pdf.cli import app
from organizador_pdf.extractor import ErroFatalDeAPI
from organizador_pdf.models import Identificadores, Metadados, TipoPublicacao
from organizador_pdf.pipeline import Pipeline, ResultadoDoArquivo, Situacao

runner = CliRunner()

_METADADOS = Metadados(
    tipo_publicacao=TipoPublicacao.LIVRO,
    area_principal="Psicologia",
    subarea="Logoterapia",
    titulo="Em Busca de Sentido",
    subtitulo=None,
    autores=["Frankl, Viktor E."],
    autor_principal="Frankl, Viktor E.",
    editora_ou_periodico="Vozes",
    ano=2019,
    local="Petrópolis",
    identificadores=Identificadores(),
    referencia_abnt="FRANKL, Viktor E. Em busca de sentido. Petrópolis: Vozes, 2019.",
)


@pytest.fixture(autouse=True)
def isolar_caminho_estado(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(estado_mod, "CAMINHO_ESTADO", tmp_path / "estado.json")


@pytest.fixture
def lote(tmp_path: Path) -> tuple[Path, Path, list[Path]]:
    pymupdf = pytest.importorskip("pymupdf")
    origem = tmp_path / "origem"
    origem.mkdir()
    destino = tmp_path / "destino"

    pdfs = []
    for nome in ("a.pdf", "b.pdf", "c.pdf", "d.pdf", "e.pdf", "f.pdf"):
        caminho = origem / nome
        documento = pymupdf.open()
        documento.new_page()
        documento.save(caminho)
        documento.close()
        pdfs.append(caminho)

    return origem, destino, sorted(pdfs)


def _resultado_sucesso(caminho: Path) -> ResultadoDoArquivo:
    return ResultadoDoArquivo(
        origem=caminho,
        situacao=Situacao.SUCESSO,
        metadados=_METADADOS,
        pdf_destino=caminho,
        provedor_usado="ollama",
    )


class TestProcessamentoParalelo:
    def test_processa_todos_os_arquivos_e_mantem_ordem_no_relatorio(self, lote, monkeypatch):
        origem, destino, pdfs = lote
        vistos: list[Path] = []
        lock = threading.Lock()

        def falso_processar(self, caminho: Path) -> ResultadoDoArquivo:
            time.sleep(0.01)
            with lock:
                vistos.append(caminho)
            return _resultado_sucesso(caminho)

        monkeypatch.setattr(Pipeline, "processar_arquivo", falso_processar)

        resultado = runner.invoke(
            app, ["--origem", str(origem), "--destino", str(destino), "--paralelo", "3"]
        )

        assert resultado.exit_code == 0, resultado.output
        assert sorted(vistos) == pdfs
        # relatório final aparece na ordem original da listagem, não na
        # ordem (não-determinística) de conclusão em paralelo.
        posicoes = [resultado.output.index(pdf.name) for pdf in pdfs]
        assert posicoes == sorted(posicoes)

    def test_roda_de_fato_em_paralelo_nao_so_com_a_flag_ligada(self, lote, monkeypatch):
        origem, destino, pdfs = lote
        simultaneos_max = 0
        simultaneos_agora = 0
        lock = threading.Lock()

        def falso_processar(self, caminho: Path) -> ResultadoDoArquivo:
            nonlocal simultaneos_max, simultaneos_agora
            with lock:
                simultaneos_agora += 1
                simultaneos_max = max(simultaneos_max, simultaneos_agora)
            time.sleep(0.05)
            with lock:
                simultaneos_agora -= 1
            return _resultado_sucesso(caminho)

        monkeypatch.setattr(Pipeline, "processar_arquivo", falso_processar)

        resultado = runner.invoke(
            app, ["--origem", str(origem), "--destino", str(destino), "--paralelo", "3"]
        )

        assert resultado.exit_code == 0, resultado.output
        assert simultaneos_max > 1

    def test_paralelo_1_e_o_padrao_e_processa_sequencial(self, lote, monkeypatch):
        origem, destino, pdfs = lote
        simultaneos_max = 0
        simultaneos_agora = 0
        lock = threading.Lock()

        def falso_processar(self, caminho: Path) -> ResultadoDoArquivo:
            nonlocal simultaneos_max, simultaneos_agora
            with lock:
                simultaneos_agora += 1
                simultaneos_max = max(simultaneos_max, simultaneos_agora)
            time.sleep(0.01)
            with lock:
                simultaneos_agora -= 1
            return _resultado_sucesso(caminho)

        monkeypatch.setattr(Pipeline, "processar_arquivo", falso_processar)

        resultado = runner.invoke(app, ["--origem", str(origem), "--destino", str(destino)])

        assert resultado.exit_code == 0, resultado.output
        assert simultaneos_max == 1

    def test_erro_fatal_para_de_puxar_trabalho_novo(self, lote, monkeypatch):
        origem, destino, pdfs = lote
        processados: list[Path] = []
        lock = threading.Lock()

        def falso_processar(self, caminho: Path) -> ResultadoDoArquivo:
            if caminho == pdfs[2]:
                raise ErroFatalDeAPI("chave inválida")
            time.sleep(0.02)
            with lock:
                processados.append(caminho)
            return _resultado_sucesso(caminho)

        monkeypatch.setattr(Pipeline, "processar_arquivo", falso_processar)

        resultado = runner.invoke(
            app, ["--origem", str(origem), "--destino", str(destino), "--paralelo", "2"]
        )

        assert resultado.exit_code == 2
        assert "interrompido" in resultado.output.lower()
        # com paralelo=2, no máximo 1 arquivo extra (além do que falhou)
        # deveria já estar em voo quando a falha é detectada — nem todos os
        # 6 arquivos do lote chegam a ser processados.
        assert len(processados) < len(pdfs)

    def test_resume_funciona_apos_interrupcao_em_modo_paralelo(self, lote, monkeypatch):
        origem, destino, pdfs = lote

        def falso_processar_com_falha(self, caminho: Path) -> ResultadoDoArquivo:
            if caminho == pdfs[3]:
                raise ErroFatalDeAPI("indisponível")
            return _resultado_sucesso(caminho)

        monkeypatch.setattr(Pipeline, "processar_arquivo", falso_processar_com_falha)
        primeira = runner.invoke(
            app, ["--origem", str(origem), "--destino", str(destino), "--paralelo", "2"]
        )
        assert primeira.exit_code == 2

        estado = estado_mod.EstadoDeExecucao.carregar()
        assert estado is not None
        assert len(estado.concluidos) < len(pdfs)  # progresso parcial preservado

        vistos: list[Path] = []
        lock = threading.Lock()

        def falso_processar_sucesso(self, caminho: Path) -> ResultadoDoArquivo:
            with lock:
                vistos.append(caminho)
            return _resultado_sucesso(caminho)

        monkeypatch.setattr(Pipeline, "processar_arquivo", falso_processar_sucesso)
        segunda = runner.invoke(app, ["--resume"])

        assert segunda.exit_code == 0, segunda.output
        assert not estado_mod.CAMINHO_ESTADO.exists()  # lote fechado por completo
