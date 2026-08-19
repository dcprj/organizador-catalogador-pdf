from __future__ import annotations

import httpx
import pytest

from organizador_pdf.models import Identificadores, Metadados, TipoPublicacao
from organizador_pdf.verificacao import verificar_identificadores


def _meta(
    titulo: str,
    autor_principal: str,
    *,
    isbn=None,
    issn=None,
    doi=None,
    editora=None,
    ano=None,
    local=None,
) -> Metadados:
    return Metadados(
        tipo_publicacao=TipoPublicacao.LIVRO,
        area_principal="Geral",
        subarea="Geral",
        titulo=titulo,
        autores=[autor_principal],
        autor_principal=autor_principal,
        editora_ou_periodico=editora,
        ano=ano,
        local=local,
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

        _, aviso = verificar_identificadores(metadados, cliente=cliente_com(handler))
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

        _, aviso = verificar_identificadores(metadados, cliente=cliente_com(handler))
        assert aviso is not None
        assert "10.1000/outra-obra" in aviso
        assert "Deep Learning for Computer Vision" in aviso

    def test_doi_nao_indexado_nao_gera_aviso(self):
        metadados = _meta("Obra Qualquer", "Autor, Nome", doi="10.1000/inexistente")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        _, aviso = verificar_identificadores(metadados, cliente=cliente_com(handler))
        assert aviso is None

    def test_prefixo_de_url_e_normalizado(self):
        metadados = _meta("Obra", "Autor, Nome", doi="https://doi.org/10.1000/xyz")

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("10.1000/xyz")
            return httpx.Response(200, json={"message": {"title": ["Obra"], "author": []}})

        _, aviso = verificar_identificadores(metadados, cliente=cliente_com(handler))
        assert aviso is None

    def test_diverge_nao_enriquece_metadados(self):
        # Título diverge -> não é a mesma obra -> não deve herdar dados
        # da API, mesmo que ela tenha editora/ano.
        metadados = _meta("Fenomenologia Existencial", "Silva, João", doi="10.1000/outra-obra")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "message": {
                        "title": ["Culinária Regional Brasileira"],
                        "author": [{"given": "John", "family": "Smith"}],
                        "publisher": "Editora Que Não Deveria Aparecer",
                        "published": {"date-parts": [[1999]]},
                    }
                },
            )

        resultado, aviso = verificar_identificadores(metadados, cliente=cliente_com(handler))
        assert aviso is not None
        assert resultado.editora_ou_periodico is None
        assert resultado.ano is None


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

        _, aviso = verificar_identificadores(metadados, cliente=cliente_com(handler))
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

        _, aviso = verificar_identificadores(metadados, cliente=cliente_com(handler))
        assert aviso is not None
        assert "978-85-241-0001-2" in aviso
        assert "Manual de Enfermagem Pediátrica" in aviso

    def test_isbn_nao_catalogado_nao_gera_aviso(self):
        # Cobertura incompleta da Open Library não é evidência de erro.
        metadados = _meta("Obra Rara", "Autor, Nome", isbn="978-00-000-0000-0")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={})

        _, aviso = verificar_identificadores(metadados, cliente=cliente_com(handler))
        assert aviso is None

    def test_hifens_sao_removidos_antes_da_consulta(self):
        metadados = _meta("Obra", "Autor, Nome", isbn="978-85-323-1005-7")

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params.get("bibkeys") == "ISBN:9788532310057"
            return httpx.Response(200, json={})

        verificar_identificadores(metadados, cliente=cliente_com(handler))


class TestEnriquecimento:
    """Preencher lacunas (editora/ano/local) só quando os dados batem —
    nunca sobrescrever o que já foi extraído."""

    def test_doi_preenche_editora_e_ano_ausentes(self):
        metadados = _meta("Obra", "Autor, Nome", doi="10.1000/xyz")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "message": {
                        "title": ["Obra"],
                        "author": [{"given": "Nome", "family": "Autor"}],
                        "publisher": "Editora Confirmada",
                        "published": {"date-parts": [[2021, 3, 1]]},
                    }
                },
            )

        resultado, aviso = verificar_identificadores(metadados, cliente=cliente_com(handler))
        assert aviso is None
        assert resultado.editora_ou_periodico == "Editora Confirmada"
        assert resultado.ano == 2021

    def test_doi_nao_sobrescreve_editora_ja_extraida(self):
        metadados = _meta("Obra", "Autor, Nome", doi="10.1000/xyz", editora="Editora Original")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "message": {
                        "title": ["Obra"],
                        "author": [{"given": "Nome", "family": "Autor"}],
                        "publisher": "Editora Da API",
                    }
                },
            )

        resultado, _ = verificar_identificadores(metadados, cliente=cliente_com(handler))
        assert resultado.editora_ou_periodico == "Editora Original"

    def test_isbn_preenche_editora_ano_e_local_ausentes(self):
        metadados = _meta("Obra", "Autor, Nome", isbn="978-85-323-1005-7")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "ISBN:9788532310057": {
                        "title": "Obra",
                        "authors": [{"name": "Nome Autor"}],
                        "publishers": [{"name": "Vozes"}],
                        "publish_date": "May 2019",
                        "publish_places": [{"name": "Petrópolis"}],
                    }
                },
            )

        resultado, aviso = verificar_identificadores(metadados, cliente=cliente_com(handler))
        assert aviso is None
        assert resultado.editora_ou_periodico == "Vozes"
        assert resultado.ano == 2019
        assert resultado.local == "Petrópolis"

    def test_isbn_nao_sobrescreve_ano_ja_extraido(self):
        metadados = _meta("Obra", "Autor, Nome", isbn="978-85-323-1005-7", ano=2015)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "ISBN:9788532310057": {
                        "title": "Obra",
                        "authors": [{"name": "Nome Autor"}],
                        "publish_date": "1999",
                    }
                },
            )

        resultado, _ = verificar_identificadores(metadados, cliente=cliente_com(handler))
        assert resultado.ano == 2015

    def test_sem_dado_extra_na_api_nao_altera_nada(self):
        metadados = _meta("Obra", "Autor, Nome", doi="10.1000/xyz")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"message": {"title": ["Obra"], "author": []}},
            )

        resultado, aviso = verificar_identificadores(metadados, cliente=cliente_com(handler))
        assert aviso is None
        assert resultado.editora_ou_periodico is None
        assert resultado.ano is None


class TestResiliencia:
    def test_sem_identificadores_nao_faz_chamada(self):
        chamou = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal chamou
            chamou = True
            return httpx.Response(200, json={})

        metadados = _meta("Obra", "Autor, Nome")
        _, aviso = verificar_identificadores(metadados, cliente=cliente_com(handler))

        assert aviso is None
        assert chamou is False

    def test_falha_de_rede_nao_propaga_excecao(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("sem internet", request=request)

        metadados = _meta("Obra", "Autor, Nome", doi="10.1000/x", isbn="978-85-323-1005-7")

        _, aviso = verificar_identificadores(metadados, cliente=cliente_com(handler))
        assert aviso is None

    def test_erro_500_da_api_nao_gera_aviso(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        metadados = _meta("Obra", "Autor, Nome", doi="10.1000/x")
        _, aviso = verificar_identificadores(metadados, cliente=cliente_com(handler))
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
        _, aviso = verificar_identificadores(metadados, cliente=cliente_com(handler))

        assert aviso is not None
        assert len(chamadas) == 1  # parou no DOI, não chegou a consultar o ISBN
