from __future__ import annotations

import httpx
import pytest

from organizador_pdf.models import Identificadores, Metadados, TipoPublicacao
from organizador_pdf.verificacao import verificar_identificadores


def _meta(titulo: str, autor_principal: str, *, isbn=None, issn=None, doi=None) -> Metadados:
    return Metadados(
        tipo_publicacao=TipoPublicacao.LIVRO,
        area_principal="Geral",
        subarea="Geral",
        titulo=titulo,
        autores=[autor_principal],
        autor_principal=autor_principal,
        identificadores=Identificadores(isbn=isbn, issn=issn, doi=doi),
        referencia_abnt=titulo,
    )


def cliente_com(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


class TestVerificarDoi:
    def test_titulo_e_autor_batem_nao_gera_aviso(self):
        metadados = _meta(
            "Aprendizado de máquina aplicado a diagnóstico",
            "Souza, Ana",
            doi="10.1000/xyz123",
        )

        def handler(request: httpx.Request) -> httpx.Response:
            assert "10.1000/xyz123" in str(request.url)
            return httpx.Response(
                200,
                json={
                    "message": {
                        "title": ["Aprendizado de máquina aplicado a diagnóstico"],
                        "author": [{"given": "Ana", "family": "Souza"}],
                    }
                },
            )

        aviso = verificar_identificadores(metadados, cliente=cliente_com(handler))
        assert aviso is None

    def test_titulo_diferente_gera_aviso(self):
        # Reproduz o caso real: DOI fabricado que, se existisse, apontaria
        # para uma obra diferente da extraída.
        metadados = _meta(
            "A Experiência Contemporânea da Loucura",
            "Foucault, Michel",
            doi="10.1000/outra-obra",
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "message": {
                        "title": ["Deep Learning for Computer Vision"],
                        "author": [{"given": "John", "family": "Smith"}],
                    }
                },
            )

        aviso = verificar_identificadores(metadados, cliente=cliente_com(handler))
        assert aviso is not None
        assert "10.1000/outra-obra" in aviso
        assert "Deep Learning for Computer Vision" in aviso

    def test_doi_nao_indexado_nao_gera_aviso(self):
        metadados = _meta("Obra Qualquer", "Autor, Nome", doi="10.1000/inexistente")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        aviso = verificar_identificadores(metadados, cliente=cliente_com(handler))
        assert aviso is None

    def test_prefixo_de_url_e_normalizado(self):
        metadados = _meta("Obra", "Autor, Nome", doi="https://doi.org/10.1000/xyz")

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("10.1000/xyz")
            return httpx.Response(200, json={"message": {"title": ["Obra"], "author": []}})

        aviso = verificar_identificadores(metadados, cliente=cliente_com(handler))
        assert aviso is None


class TestVerificarIsbn:
    def test_titulo_e_autor_batem_nao_gera_aviso(self):
        metadados = _meta(
            "A clínica, a relação psicoterapêutica",
            "Frazão, Lilian Meyer",
            isbn="978-85-323-1005-7",
        )

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params.get("bibkeys") == "ISBN:9788532310057"
            return httpx.Response(
                200,
                json={
                    "ISBN:9788532310057": {
                        "title": "A clínica, a relação psicoterapêutica e o manejo",
                        "authors": [{"name": "Lilian Meyer Frazão"}],
                    }
                },
            )

        aviso = verificar_identificadores(metadados, cliente=cliente_com(handler))
        assert aviso is None

    def test_titulo_diferente_gera_aviso(self):
        metadados = _meta(
            "A Experiência Contemporânea da Loucura",
            "Foucault, Michel",
            isbn="978-85-241-0001-2",
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "ISBN:9788524100012": {
                        "title": "Manual de Enfermagem Pediátrica",
                        "authors": [{"name": "Maria Santos"}],
                    }
                },
            )

        aviso = verificar_identificadores(metadados, cliente=cliente_com(handler))
        assert aviso is not None
        assert "978-85-241-0001-2" in aviso
        assert "Manual de Enfermagem Pediátrica" in aviso

    def test_isbn_nao_catalogado_nao_gera_aviso(self):
        # Cobertura incompleta da Open Library não é evidência de erro.
        metadados = _meta("Obra Rara", "Autor, Nome", isbn="978-00-000-0000-0")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={})

        aviso = verificar_identificadores(metadados, cliente=cliente_com(handler))
        assert aviso is None

    def test_hifens_sao_removidos_antes_da_consulta(self):
        metadados = _meta("Obra", "Autor, Nome", isbn="978-85-323-1005-7")

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params.get("bibkeys") == "ISBN:9788532310057"
            return httpx.Response(200, json={})

        verificar_identificadores(metadados, cliente=cliente_com(handler))


class TestResiliencia:
    def test_sem_identificadores_nao_faz_chamada(self):
        chamou = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal chamou
            chamou = True
            return httpx.Response(200, json={})

        metadados = _meta("Obra", "Autor, Nome")
        aviso = verificar_identificadores(metadados, cliente=cliente_com(handler))

        assert aviso is None
        assert chamou is False

    def test_falha_de_rede_nao_propaga_excecao(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("sem internet", request=request)

        metadados = _meta("Obra", "Autor, Nome", doi="10.1000/x", isbn="978-85-323-1005-7")

        aviso = verificar_identificadores(metadados, cliente=cliente_com(handler))
        assert aviso is None

    def test_erro_500_da_api_nao_gera_aviso(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        metadados = _meta("Obra", "Autor, Nome", doi="10.1000/x")
        aviso = verificar_identificadores(metadados, cliente=cliente_com(handler))
        assert aviso is None

    def test_doi_com_aviso_evita_chamada_extra_de_isbn(self):
        chamadas = []

        def handler(request: httpx.Request) -> httpx.Response:
            chamadas.append(str(request.url))
            if "crossref" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "message": {
                            "title": ["Deep Learning for Computer Vision"],
                            "author": [{"given": "John", "family": "Smith"}],
                        }
                    },
                )
            return httpx.Response(200, json={})

        metadados = _meta(
            "Aprendizado de Máquina Aplicado a Diagnóstico",
            "Souza, Ana",
            doi="10.1000/x",
            isbn="978-85-323-1005-7",
        )
        aviso = verificar_identificadores(metadados, cliente=cliente_com(handler))

        assert aviso is not None
        assert len(chamadas) == 1  # parou no DOI, não chegou a consultar o ISBN
