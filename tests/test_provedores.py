from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from anthropic import DefaultHttpxClient

from organizador_pdf.config import Config, Provedor
from organizador_pdf.converter import DocumentoConvertido
from organizador_pdf.extractor import ErroDeExtracao, ErroFatalDeAPI, ExtratorOllama
from organizador_pdf.models import TipoPublicacao
from organizador_pdf.provedores import (
    ExtratorAnthropic,
    ExtratorOpenAICompativel,
    criar_extrator,
    criar_extrator_fallback,
)


def documento(texto: str = "Em Busca de Sentido — Viktor Frankl") -> DocumentoConvertido:
    return DocumentoConvertido(
        caminho=Path("/tmp/livro.pdf"),
        markdown_completo=texto,
        markdown_inicial=texto,
        total_paginas=3,
        metadados_embutidos={},
    )


PAYLOAD_VALIDO = {
    "tipo_publicacao": "Livro",
    "area_principal": "Psicologia",
    "subarea": "Logoterapia",
    "titulo": "Em Busca de Sentido",
    "subtitulo": None,
    "autores": ["Frankl, Viktor E."],
    "autor_principal": "Frankl, Viktor E.",
    "editora_ou_periodico": "Vozes",
    "ano": 2019,
    "local": "Petrópolis",
    "identificadores": {"isbn": None, "issn": None, "doi": None},
    "referencia_abnt": "FRANKL, Viktor E. Em busca de sentido. Petrópolis: Vozes, 2019.",
}


def config_anthropic(**extra) -> Config:
    return Config(
        provedor=Provedor.ANTHROPIC, modelo="claude-haiku-4-5", api_key="sk-ant-teste", **extra
    )


def config_openai_compat(provedor: Provedor, *, modelo: str = "algum-modelo", **extra) -> Config:
    return Config(provedor=provedor, modelo=modelo, api_key="sk-teste", **extra)


class TestCriarExtrator:
    def test_ollama_e_o_padrao(self):
        assert isinstance(criar_extrator(Config()), ExtratorOllama)

    def test_anthropic(self):
        assert isinstance(criar_extrator(config_anthropic()), ExtratorAnthropic)

    @pytest.mark.parametrize(
        "provedor", [Provedor.OPENAI, Provedor.DEEPSEEK, Provedor.GEMINI, Provedor.GROK]
    )
    def test_compativeis_com_openai(self, provedor):
        extrator = criar_extrator(config_openai_compat(provedor))
        assert isinstance(extrator, ExtratorOpenAICompativel)


class TestCriarExtratorFallback:
    def test_desligado_por_padrao_devolve_none(self):
        assert criar_extrator_fallback(Config()) is None

    def test_anthropic_como_fallback(self):
        config = Config(
            provedor_fallback=Provedor.ANTHROPIC,
            modelo_fallback="claude-sonnet-5",
            api_key_fallback="sk-ant-fallback",
        )
        assert isinstance(criar_extrator_fallback(config), ExtratorAnthropic)

    def test_openai_compativel_como_fallback(self):
        config = Config(
            provedor_fallback=Provedor.DEEPSEEK,
            modelo_fallback="deepseek-v4-pro",
            api_key_fallback="sk-teste",
        )
        assert isinstance(criar_extrator_fallback(config), ExtratorOpenAICompativel)

    def test_nao_usa_config_do_provedor_principal(self):
        # O extrator de fallback deve ser construído com o modelo/chave de
        # *fallback*, não vazar os do provedor principal.
        config = Config(
            provedor=Provedor.OPENAI,
            modelo="gpt-5-mini",
            api_key="sk-openai-principal",
            provedor_fallback=Provedor.ANTHROPIC,
            modelo_fallback="claude-sonnet-5",
            api_key_fallback="sk-ant-fallback",
        )
        extrator = criar_extrator_fallback(config)
        assert isinstance(extrator, ExtratorAnthropic)
        assert extrator.config.modelo == "claude-sonnet-5"
        assert extrator.config.api_key == "sk-ant-fallback"


class TestExtratorAnthropic:
    def test_resposta_valida_vira_metadados(self, monkeypatch):
        extrator = ExtratorAnthropic(config_anthropic())

        class RespostaFalsa:
            parsed_output = None

        chamadas = []

        def parse_falso(**kwargs):
            chamadas.append(kwargs)
            from organizador_pdf.models import Metadados

            r = RespostaFalsa()
            r.parsed_output = Metadados.model_validate(PAYLOAD_VALIDO)
            return r

        monkeypatch.setattr(extrator.cliente.messages, "parse", parse_falso)

        metadados = extrator.extrair(documento())

        assert metadados.titulo == "Em Busca de Sentido"
        assert chamadas[0]["model"] == "claude-haiku-4-5"
        assert chamadas[0]["output_format"].__name__ == "Metadados"

    def test_parsed_output_none_vira_erro_de_extracao(self, monkeypatch):
        extrator = ExtratorAnthropic(config_anthropic())

        class RespostaFalsa:
            parsed_output = None

        monkeypatch.setattr(extrator.cliente.messages, "parse", lambda **kw: RespostaFalsa())

        with pytest.raises(ErroDeExtracao, match="não devolveu metadados"):
            extrator.extrair(documento())

    def test_trecho_vazio_nao_chama_a_api(self):
        extrator = ExtratorAnthropic(config_anthropic())
        with pytest.raises(ErroDeExtracao, match="vazio"):
            extrator.extrair(documento(""))

    def _extrator_com_transporte(self, handler) -> ExtratorAnthropic:
        cliente_http = DefaultHttpxClient(transport=httpx.MockTransport(handler))
        return ExtratorAnthropic(config_anthropic(), cliente_http=cliente_http)

    def _corpo_de_erro(self, tipo: str, mensagem: str) -> dict:
        return {"type": "error", "error": {"type": tipo, "message": mensagem}}

    def test_401_vira_erro_fatal(self):
        def handler(request):
            return httpx.Response(
                401, json=self._corpo_de_erro("authentication_error", "chave inválida")
            )

        extrator = self._extrator_com_transporte(handler)
        with pytest.raises(ErroFatalDeAPI, match="chave de API"):
            extrator.extrair(documento())

    def test_404_vira_erro_fatal(self):
        def handler(request):
            return httpx.Response(404, json=self._corpo_de_erro("not_found_error", "modelo?"))

        extrator = self._extrator_com_transporte(handler)
        with pytest.raises(ErroFatalDeAPI, match="não encontrado"):
            extrator.extrair(documento())

    def test_429_vira_erro_fatal(self):
        def handler(request):
            return httpx.Response(429, json=self._corpo_de_erro("rate_limit_error", "devagar"))

        extrator = self._extrator_com_transporte(handler)
        with pytest.raises(ErroFatalDeAPI, match="limite de requisições"):
            extrator.extrair(documento())

    def test_400_credito_insuficiente_vira_erro_fatal(self):
        def handler(request):
            return httpx.Response(
                400,
                json=self._corpo_de_erro(
                    "invalid_request_error", "Your credit balance is too low"
                ),
            )

        extrator = self._extrator_com_transporte(handler)
        with pytest.raises(ErroFatalDeAPI, match="saldo de créditos"):
            extrator.extrair(documento())

    def test_400_generico_vira_erro_por_arquivo(self):
        def handler(request):
            return httpx.Response(
                400, json=self._corpo_de_erro("invalid_request_error", "algo errado")
            )

        extrator = self._extrator_com_transporte(handler)
        with pytest.raises(ErroDeExtracao, match="rejeitada"):
            extrator.extrair(documento())

    def test_500_vira_erro_por_arquivo(self):
        def handler(request):
            return httpx.Response(500, json=self._corpo_de_erro("api_error", "instabilidade"))

        extrator = self._extrator_com_transporte(handler)
        with pytest.raises(ErroDeExtracao, match="erro da Anthropic"):
            extrator.extrair(documento())


class TestExtratorOpenAICompativel:
    def _resposta_openai(self, payload: dict) -> dict:
        return {
            "id": "chatcmpl-teste",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": json.dumps(payload)},
                    "finish_reason": "stop",
                }
            ],
        }

    def test_resposta_valida_vira_metadados(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/chat/completions"
            corpo = json.loads(request.content)
            assert corpo["model"] == "gpt-5-mini"
            assert corpo["response_format"]["json_schema"]["strict"] is True
            assert request.headers["authorization"] == "Bearer sk-teste"
            return httpx.Response(200, json=self._resposta_openai(PAYLOAD_VALIDO))

        extrator = ExtratorOpenAICompativel(
            config_openai_compat(Provedor.OPENAI, modelo="gpt-5-mini"),
            cliente=httpx.Client(
                transport=httpx.MockTransport(handler), base_url="https://api.openai.com/v1"
            ),
        )

        metadados = extrator.extrair(documento())

        assert metadados.titulo == "Em Busca de Sentido"
        assert metadados.tipo_publicacao is TipoPublicacao.LIVRO

    def test_deepseek_usa_json_object_em_vez_de_json_schema(self):
        # Caso real: a DeepSeek rejeita response_format=json_schema com 400
        # "This response_format type is unavailable now" — só aceita
        # json_object, e exige a palavra "json" + um exemplo no prompt.
        def handler(request: httpx.Request) -> httpx.Response:
            corpo = json.loads(request.content)
            assert corpo["response_format"] == {"type": "json_object"}
            conteudo_usuario = corpo["messages"][1]["content"]
            assert "json" in conteudo_usuario.lower()
            return httpx.Response(200, json=self._resposta_openai(PAYLOAD_VALIDO))

        extrator = ExtratorOpenAICompativel(
            config_openai_compat(Provedor.DEEPSEEK, modelo="deepseek-v4-flash"),
            cliente=httpx.Client(
                transport=httpx.MockTransport(handler), base_url="https://api.deepseek.com/v1"
            ),
        )

        metadados = extrator.extrair(documento())
        assert metadados.titulo == "Em Busca de Sentido"

    def test_openai_grok_gemini_continuam_usando_json_schema_estrito(self):
        for provedor in (Provedor.OPENAI, Provedor.GROK, Provedor.GEMINI):

            def handler(request: httpx.Request) -> httpx.Response:
                corpo = json.loads(request.content)
                assert corpo["response_format"]["type"] == "json_schema"
                assert corpo["response_format"]["json_schema"]["strict"] is True
                return httpx.Response(200, json=self._resposta_openai(PAYLOAD_VALIDO))

            extrator = criar_extrator(config_openai_compat(provedor))
            extrator.cliente = httpx.Client(
                transport=httpx.MockTransport(handler), base_url=extrator.cliente.base_url
            )
            extrator.extrair(documento())

    def test_base_url_correta_por_provedor(self):
        vistos = []

        def handler(request: httpx.Request) -> httpx.Response:
            vistos.append(str(request.url))
            return httpx.Response(200, json=self._resposta_openai(PAYLOAD_VALIDO))

        for provedor in (Provedor.OPENAI, Provedor.DEEPSEEK, Provedor.GEMINI, Provedor.GROK):
            extrator = criar_extrator(config_openai_compat(provedor))
            extrator.cliente = httpx.Client(
                transport=httpx.MockTransport(handler),
                base_url=extrator.cliente.base_url,
            )
            extrator.extrair(documento())

        assert vistos == [
            "https://api.openai.com/v1/chat/completions",
            "https://api.deepseek.com/v1/chat/completions",
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            "https://api.x.ai/v1/chat/completions",
        ]

    def test_trecho_vazio_nao_chama_a_api(self):
        extrator = criar_extrator(config_openai_compat(Provedor.OPENAI))
        with pytest.raises(ErroDeExtracao, match="vazio"):
            extrator.extrair(documento(""))

    def _extrator(self, handler) -> ExtratorOpenAICompativel:
        return ExtratorOpenAICompativel(
            config_openai_compat(Provedor.OPENAI, modelo="gpt-5-mini"),
            cliente=httpx.Client(
                transport=httpx.MockTransport(handler), base_url="https://api.openai.com/v1"
            ),
        )

    def test_401_vira_erro_fatal(self):
        extrator = self._extrator(lambda r: httpx.Response(401, json={"error": "no"}))
        with pytest.raises(ErroFatalDeAPI, match="chave de API"):
            extrator.extrair(documento())

    def test_404_vira_erro_fatal(self):
        extrator = self._extrator(lambda r: httpx.Response(404, json={"error": "no"}))
        with pytest.raises(ErroFatalDeAPI, match="não encontrado"):
            extrator.extrair(documento())

    def test_429_vira_erro_fatal(self):
        extrator = self._extrator(lambda r: httpx.Response(429, json={"error": "no"}))
        with pytest.raises(ErroFatalDeAPI, match="limite de requisições"):
            extrator.extrair(documento())

    def test_500_vira_erro_por_arquivo(self):
        extrator = self._extrator(lambda r: httpx.Response(500, text="instabilidade"))
        with pytest.raises(ErroDeExtracao, match="erro da OpenAI"):
            extrator.extrair(documento())

    def test_conteudo_nao_json_vira_erro_por_arquivo(self):
        def handler(request):
            return httpx.Response(200, json=self._resposta_openai_texto("isto não é json"))

        extrator = self._extrator(handler)
        with pytest.raises(ErroDeExtracao, match="não é JSON válido"):
            extrator.extrair(documento())

    def _resposta_openai_texto(self, texto: str) -> dict:
        return {"choices": [{"message": {"role": "assistant", "content": texto}}]}

    def test_formato_de_resposta_inesperado_vira_erro_por_arquivo(self):
        extrator = self._extrator(lambda r: httpx.Response(200, json={"choices": []}))
        with pytest.raises(ErroDeExtracao, match="formato inesperado"):
            extrator.extrair(documento())

    def test_conexao_falha_vira_erro_fatal(self):
        def handler(request):
            raise httpx.ConnectError("recusado", request=request)

        extrator = self._extrator(handler)
        with pytest.raises(ErroFatalDeAPI, match="não foi possível conectar"):
            extrator.extrair(documento())
