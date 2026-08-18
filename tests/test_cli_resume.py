from __future__ import annotations

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
    """Três PDFs mínimos (conteúdo não importa: processar_arquivo é mockado)."""
    pymupdf = pytest.importorskip("pymupdf")
    origem = tmp_path / "origem"
    origem.mkdir()
    destino = tmp_path / "destino"

    pdfs = []
    for nome in ("a.pdf", "b.pdf", "c.pdf"):
        caminho = origem / nome
        documento = pymupdf.open()
        documento.new_page()
        documento.save(caminho)
        documento.close()
        pdfs.append(caminho)

    return origem, destino, sorted(pdfs)


def _resultado_sucesso(
    caminho: Path, *, provedor_usado: str = "ollama", usou_fallback: bool = False
) -> ResultadoDoArquivo:
    return ResultadoDoArquivo(
        origem=caminho,
        situacao=Situacao.SUCESSO,
        metadados=_METADADOS,
        pdf_destino=caminho,
        provedor_usado=provedor_usado,
        usou_fallback=usou_fallback,
    )


def _resultado_falha(caminho: Path) -> ResultadoDoArquivo:
    return ResultadoDoArquivo(origem=caminho, situacao=Situacao.FALHA, erro="deu ruim", etapa="extração")


class TestSemResume:
    def test_lote_completo_limpa_o_estado_ao_final(self, lote, monkeypatch):
        origem, destino, pdfs = lote
        monkeypatch.setattr(Pipeline, "processar_arquivo", lambda self, c: _resultado_sucesso(c))

        resultado = runner.invoke(app, ["--origem", str(origem), "--destino", str(destino)])

        assert resultado.exit_code == 0, resultado.output
        assert not estado_mod.CAMINHO_ESTADO.exists()

    def test_origem_e_destino_sao_obrigatorios_sem_resume(self, tmp_path: Path):
        resultado = runner.invoke(app, [])
        assert resultado.exit_code == 2
        assert "obrigatórios" in resultado.output

    def test_dry_run_nao_grava_estado(self, lote, monkeypatch):
        origem, destino, pdfs = lote
        monkeypatch.setattr(Pipeline, "processar_arquivo", lambda self, c: _resultado_sucesso(c))

        resultado = runner.invoke(
            app, ["--origem", str(origem), "--destino", str(destino), "--dry-run"]
        )

        assert resultado.exit_code == 0, resultado.output
        assert not estado_mod.CAMINHO_ESTADO.exists()


class TestInterrupcaoEResume:
    def test_erro_fatal_no_meio_do_lote_preserva_progresso(self, lote, monkeypatch):
        origem, destino, pdfs = lote
        processados: list[Path] = []

        def falso_processar(self, caminho: Path) -> ResultadoDoArquivo:
            processados.append(caminho)
            if caminho == pdfs[1]:
                raise ErroFatalDeAPI("chave de API inválida")
            return _resultado_sucesso(caminho)

        monkeypatch.setattr(Pipeline, "processar_arquivo", falso_processar)

        resultado = runner.invoke(app, ["--origem", str(origem), "--destino", str(destino)])

        assert resultado.exit_code == 2
        assert "interrompido" in resultado.output.lower()
        # primeiro arquivo concluído antes da interrupção; o que interrompeu
        # (pdfs[1]) e o restante (pdfs[2]) ficam de fora, para retomar depois.
        estado = estado_mod.EstadoDeExecucao.carregar()
        assert estado is not None
        assert estado.concluidos == {str(pdfs[0].resolve())}

    def test_resume_pula_concluidos_e_reaplica_parametros(self, lote, monkeypatch):
        origem, destino, pdfs = lote

        def falso_processar_com_falha_no_meio(self, caminho: Path) -> ResultadoDoArquivo:
            if caminho == pdfs[1]:
                raise ErroFatalDeAPI("indisponível")
            return _resultado_sucesso(caminho)

        monkeypatch.setattr(Pipeline, "processar_arquivo", falso_processar_com_falha_no_meio)
        primeira = runner.invoke(app, ["--origem", str(origem), "--destino", str(destino), "--mover"])
        assert primeira.exit_code == 2

        vistos: list[Path] = []
        monkeypatch.setattr(
            Pipeline,
            "processar_arquivo",
            lambda self, c: (vistos.append(c), _resultado_sucesso(c))[1],
        )

        segunda = runner.invoke(app, ["--resume"])

        assert segunda.exit_code == 0, segunda.output
        assert sorted(vistos) == [pdfs[1], pdfs[2]]
        # --mover da execução original foi reaplicado sem ser passado de novo
        assert "mover" in segunda.output.lower()
        # lote fechado por completo -> estado limpo
        assert not estado_mod.CAMINHO_ESTADO.exists()

    def test_resume_sem_estado_pendente_da_erro_claro(self):
        resultado = runner.invoke(app, ["--resume"])
        assert resultado.exit_code == 2
        assert "nenhuma execução pendente" in resultado.output.lower()

    def test_falhas_definitivas_nao_sao_retentadas_no_resume(self, lote, monkeypatch):
        origem, destino, pdfs = lote

        def falso_processar(self, caminho: Path) -> ResultadoDoArquivo:
            if caminho == pdfs[0]:
                return _resultado_falha(caminho)
            raise ErroFatalDeAPI("indisponível")

        monkeypatch.setattr(Pipeline, "processar_arquivo", falso_processar)
        primeira = runner.invoke(app, ["--origem", str(origem), "--destino", str(destino)])
        assert primeira.exit_code == 2

        vistos: list[Path] = []
        monkeypatch.setattr(
            Pipeline,
            "processar_arquivo",
            lambda self, c: (vistos.append(c), _resultado_sucesso(c))[1],
        )
        segunda = runner.invoke(app, ["--resume"])

        assert segunda.exit_code == 0, segunda.output
        # pdfs[0] falhou definitivamente na 1a execução -> não é retentado
        assert pdfs[0] not in vistos
        assert sorted(vistos) == [pdfs[1], pdfs[2]]


class TestEstatisticaLocalVsPago:
    def test_resumo_mostra_contagem_local_e_pago(self, lote, monkeypatch):
        origem, destino, pdfs = lote

        def falso_processar(self, caminho: Path) -> ResultadoDoArquivo:
            if caminho == pdfs[2]:
                return _resultado_sucesso(
                    caminho, provedor_usado="anthropic", usou_fallback=True
                )
            return _resultado_sucesso(caminho)

        monkeypatch.setattr(Pipeline, "processar_arquivo", falso_processar)

        resultado = runner.invoke(app, ["--origem", str(origem), "--destino", str(destino)])

        assert resultado.exit_code == 0, resultado.output
        assert "2 extraído(s) localmente" in resultado.output
        assert "1 via provedor pago" in resultado.output
        # marcador informativo na linha do arquivo que usou fallback
        assert "$ extraído pelo provedor de fallback" in resultado.output

    def test_sem_fallback_nenhum_marcador_de_fallback_aparece(self, lote, monkeypatch):
        origem, destino, pdfs = lote
        monkeypatch.setattr(Pipeline, "processar_arquivo", lambda self, c: _resultado_sucesso(c))

        resultado = runner.invoke(app, ["--origem", str(origem), "--destino", str(destino)])

        assert resultado.exit_code == 0, resultado.output
        assert "3 extraído(s) localmente" in resultado.output
        assert "0 via provedor pago" in resultado.output
        assert "extraído pelo provedor de fallback" not in resultado.output
