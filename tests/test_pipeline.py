from __future__ import annotations

from pathlib import Path

import pytest

from organizador_pdf.config import Config
from organizador_pdf.converter import converter_pdf, listar_pdfs
from organizador_pdf.extractor import ErroDeExtracao, ErroFatalDeAPI
from organizador_pdf.models import Identificadores, Metadados, TipoPublicacao
from organizador_pdf.pipeline import (
    OpcoesDoPipeline,
    Pipeline,
    Situacao,
    _titulo_diverge_do_arquivo,
)

# Os testes deste arquivo passam `verificar_online=False` para evitar que a
# suíte dependa de rede real (Crossref/Open Library) — a integração com esse
# módulo é testada à parte, abaixo, com a função trocada por um dublê.


class ExtratorFalso:
    def __init__(self, metadados: Metadados | None = None, erro: Exception | None = None):
        self.metadados = metadados
        self.erro = erro
        self.chamadas = 0

    def extrair(self, documento):
        self.chamadas += 1
        if self.erro:
            raise self.erro
        return self.metadados


def _meta(
    titulo: str,
    *,
    autor_principal: str | None = None,
    editora_ou_periodico: str | None = None,
    subtitulo: str | None = None,
) -> Metadados:
    return Metadados(
        tipo_publicacao=TipoPublicacao.ARTIGO,
        area_principal="Geral",
        subarea="Geral",
        titulo=titulo,
        subtitulo=subtitulo,
        autor_principal=autor_principal,
        autores=[autor_principal] if autor_principal else [],
        editora_ou_periodico=editora_ou_periodico,
        identificadores=Identificadores(),
        referencia_abnt=titulo,
    )


class TestTituloDivergeDoArquivo:
    def test_alucinacao_real_e_detectada_sartre(self):
        # Caso real encontrado em produção: PDF é sobre Sartre/existencialismo,
        # mas o modelo catalogou um artigo de direito totalmente diferente.
        metadados = _meta(
            "A mulher na sociedade medieval: uma análise da tutela e do poder",
            autor_principal="Autor2022",
            editora_ou_periodico="Revista Jurídica Cesumar",
        )
        aviso = _titulo_diverge_do_arquivo(
            "O (IN)EXISTENCIALISMO FEMININO EM SARTRE - UMA ANÁLISE LITERÁRIA.pdf",
            metadados,
        )
        assert aviso is not None

    def test_alucinacao_real_e_detectada_foucault(self):
        metadados = _meta(
            "O Internamento e a Constituição da Loucura na História da Psiquiatria",
            autor_principal="Silva, Ana",
            editora_ou_periodico="Revista de Psiquiatria",
        )
        aviso = _titulo_diverge_do_arquivo(
            "foucault-michel-doenca-mental-e-psicologia.pdf", metadados
        )
        assert aviso is not None

    def test_extracao_correta_nao_gera_aviso(self):
        metadados = _meta(
            "Em Busca de Sentido",
            subtitulo="Um psicólogo no campo de concentração",
            autor_principal="Frankl, Viktor E.",
            editora_ou_periodico="Vozes",
        )
        aviso = _titulo_diverge_do_arquivo(
            "Frankl - Em Busca de Sentido (Vozes, 2019).pdf", metadados
        )
        assert aviso is None

    def test_nome_de_arquivo_generico_nao_gera_aviso(self):
        # Nome sem conteúdo real (poucas palavras significativas) — não há
        # sinal suficiente para comparar, então não deve soar falso alarme.
        metadados = _meta("Qualquer Título", autor_principal="Qualquer Autor")
        assert _titulo_diverge_do_arquivo("scan001.pdf", metadados) is None
        assert _titulo_diverge_do_arquivo("documento.pdf", metadados) is None

    def test_uma_palavra_em_comum_nao_basta(self):
        # Uma única palavra batendo pode ser coincidência (ex.: "análise"
        # aparece em quase qualquer título acadêmico) — exige pelo menos 2.
        metadados = _meta(
            "A mulher na sociedade medieval: uma análise da tutela e do poder",
        )
        aviso = _titulo_diverge_do_arquivo(
            "UMA ANÁLISE LITERÁRIA SOBRE SARTRE.pdf", metadados
        )
        assert aviso is not None


class TestListarPdfs:
    def test_recursivo_e_nao_recursivo(self, tmp_path: Path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "a.pdf").touch()
        (tmp_path / "sub" / "b.PDF").touch()
        (tmp_path / "nao-pdf.txt").touch()
        (tmp_path / ".oculto.pdf").touch()

        assert [c.name for c in listar_pdfs(tmp_path, recursivo=False)] == ["a.pdf"]
        assert [c.name for c in listar_pdfs(tmp_path)] == ["a.pdf", "b.PDF"]


class TestConverter:
    def test_extrai_texto_e_limita_o_trecho_de_analise(self, pdf_de_teste: Path):
        documento = converter_pdf(
            pdf_de_teste, paginas_para_analise=1, max_caracteres_analise=50
        )

        assert documento.total_paginas == 2
        assert "Em Busca de Sentido" in documento.markdown_completo
        assert "Vozes" in documento.markdown_completo  # veio da 2ª página
        assert len(documento.markdown_inicial) <= 50
        assert "Vozes" not in documento.markdown_inicial  # análise só da 1ª página


class TestPipeline:
    def test_fluxo_completo_grava_pdf_e_markdown(
        self, pdf_de_teste: Path, metadados: Metadados, tmp_path: Path
    ):
        destino = tmp_path / "biblioteca"
        pipeline = Pipeline(
            Config(verificar_online=False),
            OpcoesDoPipeline(destino=destino),
            extrator=ExtratorFalso(metadados),
        )

        resultado = pipeline.processar_arquivo(pdf_de_teste)

        assert resultado.situacao is Situacao.SUCESSO
        assert resultado.pdf_destino.exists()
        assert resultado.markdown_destino.exists()

        conteudo = resultado.markdown_destino.read_text(encoding="utf-8")
        assert conteudo.startswith("---\n")
        assert "## Referência Bibliográfica (ABNT)" in conteudo
        assert "Em Busca de Sentido" in conteudo  # texto original do PDF
        assert "total_paginas: 2" in conteudo

    def test_dry_run_planeja_sem_gravar(
        self, pdf_de_teste: Path, metadados: Metadados, tmp_path: Path
    ):
        destino = tmp_path / "biblioteca"
        pipeline = Pipeline(
            Config(verificar_online=False),
            OpcoesDoPipeline(destino=destino, dry_run=True),
            extrator=ExtratorFalso(metadados),
        )

        resultado = pipeline.processar_arquivo(pdf_de_teste)

        assert resultado.situacao is Situacao.SIMULADO
        assert not destino.exists()
        assert resultado.pdf_destino.parent == (
            destino / "Psicologia" / "Logoterapia" / "Livros"
        )

    def test_falha_isolada_nao_interrompe_o_lote(
        self, pdf_de_teste: Path, metadados: Metadados, tmp_path: Path
    ):
        corrompido = tmp_path / "origem" / "corrompido.pdf"
        corrompido.write_bytes(b"isto nao e um pdf")

        pipeline = Pipeline(
            Config(verificar_online=False),
            OpcoesDoPipeline(destino=tmp_path / "biblioteca"),
            extrator=ExtratorFalso(metadados),
        )

        resultados = pipeline.processar_lote([corrompido, pdf_de_teste])

        assert [r.situacao for r in resultados] == [Situacao.FALHA, Situacao.SUCESSO]
        assert resultados[0].etapa == "conversão"
        assert resultados[0].erro

    def test_aviso_de_divergencia_e_propagado_no_resultado(
        self, pdf_de_teste: Path, tmp_path: Path
    ):
        # `pdf_de_teste` chama-se "documento.pdf" (nome genérico, sem sinal),
        # então renomeamos para simular um nome de arquivo com conteúdo real
        # que diverge da extração fabricada.
        renomeado = pdf_de_teste.with_name("HISTORIA-DA-FILOSOFIA-ANTIGA.pdf")
        pdf_de_teste.rename(renomeado)

        metadados_divergente = _meta(
            "Receita de Bolo de Cenoura", autor_principal="Chef Anônimo"
        )
        pipeline = Pipeline(
            Config(verificar_online=False),
            OpcoesDoPipeline(destino=tmp_path / "biblioteca"),
            extrator=ExtratorFalso(metadados_divergente),
        )

        resultado = pipeline.processar_arquivo(renomeado)

        assert resultado.situacao is Situacao.SUCESSO  # não bloqueia, só avisa
        assert resultado.aviso is not None
        assert "outra obra" in resultado.aviso

    def test_erro_do_llm_e_reportado_na_etapa_certa(
        self, pdf_de_teste: Path, tmp_path: Path
    ):
        pipeline = Pipeline(
            Config(verificar_online=False),
            OpcoesDoPipeline(destino=tmp_path / "biblioteca"),
            extrator=ExtratorFalso(erro=ErroDeExtracao("cota excedida")),
        )

        resultado = pipeline.processar_arquivo(pdf_de_teste)

        assert resultado.situacao is Situacao.FALHA
        assert resultado.etapa == "extração de metadados"
        assert "cota excedida" in resultado.erro

    def test_erro_fatal_interrompe_o_lote(self, pdf_de_teste: Path, tmp_path: Path):
        extrator = ExtratorFalso(erro=ErroFatalDeAPI("credencial inválida"))
        pipeline = Pipeline(
            Config(verificar_online=False), OpcoesDoPipeline(destino=tmp_path / "biblioteca"), extrator=extrator
        )

        # Diferente das demais falhas, esta propaga para a CLI encerrar o lote.
        with pytest.raises(ErroFatalDeAPI):
            pipeline.processar_arquivo(pdf_de_teste)

    def test_modo_mover_esvazia_a_origem(
        self, pdf_de_teste: Path, metadados: Metadados, tmp_path: Path
    ):
        pipeline = Pipeline(
            Config(verificar_online=False),
            OpcoesDoPipeline(destino=tmp_path / "biblioteca", mover=True),
            extrator=ExtratorFalso(metadados),
        )

        pipeline.processar_arquivo(pdf_de_teste)

        assert not pdf_de_teste.exists()


class TestVerificacaoOnlineNoPipeline:
    """Testa só a integração (chamar ou não, e como o aviso se combina) —
    a lógica de comparação em si já é testada em test_verificacao.py."""

    def test_desligada_por_padrao_de_config_nao_chama(
        self, pdf_de_teste: Path, metadados: Metadados, tmp_path: Path, monkeypatch
    ):
        chamou = False

        def falso(*args, **kwargs):
            nonlocal chamou
            chamou = True
            return None

        monkeypatch.setattr("organizador_pdf.pipeline.verificar_identificadores", falso)

        pipeline = Pipeline(
            Config(verificar_online=False),
            OpcoesDoPipeline(destino=tmp_path / "biblioteca"),
            extrator=ExtratorFalso(metadados),
        )
        pipeline.processar_arquivo(pdf_de_teste)

        assert chamou is False

    def test_ligada_chama_e_aviso_e_propagado(
        self, pdf_de_teste: Path, metadados: Metadados, tmp_path: Path, monkeypatch
    ):
        monkeypatch.setattr(
            "organizador_pdf.pipeline.verificar_identificadores",
            lambda metadados: "o ISBN pertence a outra obra segundo a Open Library",
        )

        pipeline = Pipeline(
            Config(verificar_online=True),
            OpcoesDoPipeline(destino=tmp_path / "biblioteca"),
            extrator=ExtratorFalso(metadados),
        )
        resultado = pipeline.processar_arquivo(pdf_de_teste)

        assert resultado.aviso is not None
        assert "Open Library" in resultado.aviso

    def test_combina_com_o_aviso_de_divergencia_de_nome_de_arquivo(
        self, pdf_de_teste: Path, tmp_path: Path, monkeypatch
    ):
        monkeypatch.setattr(
            "organizador_pdf.pipeline.verificar_identificadores",
            lambda metadados: "o DOI pertence a outra obra segundo o Crossref",
        )

        renomeado = pdf_de_teste.with_name("HISTORIA-DA-FILOSOFIA-ANTIGA.pdf")
        pdf_de_teste.rename(renomeado)
        metadados_divergente = _meta(
            "Receita de Bolo de Cenoura", autor_principal="Chef Anônimo"
        )

        pipeline = Pipeline(
            Config(verificar_online=True),
            OpcoesDoPipeline(destino=tmp_path / "biblioteca"),
            extrator=ExtratorFalso(metadados_divergente),
        )
        resultado = pipeline.processar_arquivo(renomeado)

        # Os dois avisos aparecem — nome de arquivo diverge E identificador
        # aponta para outra obra segundo o Crossref.
        assert "outra obra" in resultado.aviso
        assert "Crossref" in resultado.aviso
