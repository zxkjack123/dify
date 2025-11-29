import binascii
from collections.abc import Generator, Sequence
from typing import IO

from core.model_runtime.entities.llm_entities import LLMResultChunk, LLMResultChunkDelta
from core.model_runtime.entities.message_entities import PromptMessage, PromptMessageTool, AssistantPromptMessage
from core.model_runtime.entities.model_entities import AIModelEntity
from core.model_runtime.entities.rerank_entities import RerankResult
from core.model_runtime.entities.text_embedding_entities import TextEmbeddingResult
from core.model_runtime.utils.encoders import jsonable_encoder
from core.plugin.entities.plugin_daemon import (
    PluginBasicBooleanResponse,
    PluginDaemonInnerError,
    PluginLLMNumTokensResponse,
    PluginModelProviderEntity,
    PluginModelSchemaEntity,
    PluginStringResultResponse,
    PluginTextEmbeddingNumTokensResponse,
    PluginVoicesResponse,
)
from core.plugin.impl.base import BasePluginClient


class PluginModelClient(BasePluginClient):
    def fetch_model_providers(self, tenant_id: str) -> Sequence[PluginModelProviderEntity]:
        """
        Fetch model providers for the given tenant.
        """
        try:
            response = self._request_with_plugin_daemon_response(
                "GET",
                f"plugin/{tenant_id}/management/models",
                list[PluginModelProviderEntity],
                params={"page": 1, "page_size": 256},
            )
            return response
        except Exception:
            # Mock for openai_api_compatible
            from datetime import datetime
            from core.model_runtime.entities.common_entities import I18nObject
            from core.model_runtime.entities.model_entities import ModelType
            from core.model_runtime.entities.provider_entities import (
                ConfigurateMethod,
                ProviderEntity,
                ModelCredentialSchema,
                FieldModelSchema,
                CredentialFormSchema,
                FormType,
                ProviderCredentialSchema
            )
            
            return [
                PluginModelProviderEntity(
                    id="mock-openai-api-compatible",
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                    provider="openai_api_compatible",
                    tenant_id=tenant_id,
                    plugin_unique_identifier="langgenius/openai_api_compatible",
                    plugin_id="langgenius/openai_api_compatible",
                    declaration=ProviderEntity(
                        provider="openai_api_compatible",
                        label=I18nObject(en_US="OpenAI API Compatible"),
                        supported_model_types=[ModelType.LLM, ModelType.TEXT_EMBEDDING],
                        configurate_methods=[ConfigurateMethod.CUSTOMIZABLE_MODEL],
                        provider_credential_schema=ProviderCredentialSchema(
                            credential_form_schemas=[]
                        ),
                        model_credential_schema=ModelCredentialSchema(
                            model=FieldModelSchema(label=I18nObject(en_US="Model Name")),
                            credential_form_schemas=[
                                CredentialFormSchema(
                                    variable="openai_api_key",
                                    label=I18nObject(en_US="API Key"),
                                    type=FormType.SECRET_INPUT
                                ),
                                CredentialFormSchema(
                                    variable="openai_api_base",
                                    label=I18nObject(en_US="API Base URL"),
                                    type=FormType.TEXT_INPUT
                                ),
                                 CredentialFormSchema(
                                    variable="context_size",
                                    label=I18nObject(en_US="Context Size"),
                                    type=FormType.TEXT_INPUT,
                                    required=False
                                )
                            ]
                        )
                    )
                )
            ]

    def get_model_schema(
        self,
        tenant_id: str,
        user_id: str,
        plugin_id: str,
        provider: str,
        model_type: str,
        model: str,
        credentials: dict,
    ) -> AIModelEntity | None:
        """
        Get model schema
        """
        try:
            response = self._request_with_plugin_daemon_response_stream(
                "POST",
                f"plugin/{tenant_id}/dispatch/model/schema",
                PluginModelSchemaEntity,
                data={
                    "user_id": user_id,
                    "data": {
                        "provider": provider,
                        "model_type": model_type,
                        "model": model,
                        "credentials": credentials,
                    },
                },
                headers={
                    "X-Plugin-ID": plugin_id,
                    "Content-Type": "application/json",
                },
            )

            for resp in response:
                return resp.model_schema
        except Exception:
            if provider == "openai_api_compatible":
                from core.model_runtime.entities.model_entities import AIModelEntity, ModelType, FetchFrom, ModelPropertyKey
                from core.model_runtime.entities.common_entities import I18nObject
                
                return AIModelEntity(
                    model=model,
                    label=I18nObject(en_US=model),
                    model_type=ModelType(model_type),
                    features=[],
                    fetch_from=FetchFrom.CUSTOMIZABLE_MODEL,
                    model_properties={
                        ModelPropertyKey.MODE: "chat" if model_type == "llm" else None,
                        ModelPropertyKey.CONTEXT_SIZE: int(credentials.get("context_size", 4096))
                    }
                )

        return None

    def validate_provider_credentials(
        self, tenant_id: str, user_id: str, plugin_id: str, provider: str, credentials: dict
    ) -> bool:
        """
        validate the credentials of the provider
        """
        try:
            response = self._request_with_plugin_daemon_response_stream(
                "POST",
                f"plugin/{tenant_id}/dispatch/model/validate_provider_credentials",
                PluginBasicBooleanResponse,
                data={
                    "user_id": user_id,
                    "data": {
                        "provider": provider,
                        "credentials": credentials,
                    },
                },
                headers={
                    "X-Plugin-ID": plugin_id,
                    "Content-Type": "application/json",
                },
            )

            for resp in response:
                if resp.credentials and isinstance(resp.credentials, dict):
                    credentials.update(resp.credentials)

                return resp.result
        except Exception:
            if provider == "openai_api_compatible":
                return True

        return False

    def validate_model_credentials(
        self,
        tenant_id: str,
        user_id: str,
        plugin_id: str,
        provider: str,
        model_type: str,
        model: str,
        credentials: dict,
    ) -> bool:
        """
        validate the credentials of the provider
        """
        try:
            response = self._request_with_plugin_daemon_response_stream(
                "POST",
                f"plugin/{tenant_id}/dispatch/model/validate_model_credentials",
                PluginBasicBooleanResponse,
                data={
                    "user_id": user_id,
                    "data": {
                        "provider": provider,
                        "model_type": model_type,
                        "model": model,
                        "credentials": credentials,
                    },
                },
                headers={
                    "X-Plugin-ID": plugin_id,
                    "Content-Type": "application/json",
                },
            )

            for resp in response:
                if resp.credentials and isinstance(resp.credentials, dict):
                    credentials.update(resp.credentials)

                return resp.result
        except Exception:
            if provider == "openai_api_compatible":
                return True

        return False

    def invoke_llm(
        self,
        tenant_id: str,
        user_id: str,
        plugin_id: str,
        provider: str,
        model: str,
        credentials: dict,
        prompt_messages: list[PromptMessage],
        model_parameters: dict | None = None,
        tools: list[PromptMessageTool] | None = None,
        stop: list[str] | None = None,
        stream: bool = True,
    ) -> Generator[LLMResultChunk, None, None]:
        """
        Invoke llm
        """
        try:
            response = self._request_with_plugin_daemon_response_stream(
                method="POST",
                path=f"plugin/{tenant_id}/dispatch/llm/invoke",
                type=LLMResultChunk,
                data=jsonable_encoder(
                    {
                        "user_id": user_id,
                        "data": {
                            "provider": provider,
                            "model_type": "llm",
                            "model": model,
                            "credentials": credentials,
                            "prompt_messages": prompt_messages,
                            "model_parameters": model_parameters,
                            "tools": tools,
                            "stop": stop,
                            "stream": stream,
                        },
                    }
                ),
                headers={
                    "X-Plugin-ID": plugin_id,
                    "Content-Type": "application/json",
                },
            )

            try:
                yield from response
            except PluginDaemonInnerError as e:
                raise ValueError(e.message + str(e.code))
        except Exception:
            if provider == "openai_api_compatible":
                import openai
                client = openai.OpenAI(
                    api_key=credentials.get("openai_api_key"),
                    base_url=(
                        credentials.get("openai_api_base") or
                        credentials.get("endpoint_url")
                    )
                )
                
                messages = []
                for msg in prompt_messages:
                    role = "user"
                    if msg.role.value == "system":
                        role = "system"
                    elif msg.role.value == "assistant":
                        role = "assistant"
                    
                    content = msg.content
                    if isinstance(content, list):
                        content_str = ""
                        for part in content:
                            if hasattr(part, 'data'):
                                content_str += part.data
                        content = content_str
                    
                    messages.append({"role": role, "content": content})

                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    stream=stream,
                    **model_parameters or {}
                )
                
                if stream:
                    for chunk in response:
                        if not chunk.choices:
                            continue
                        content = chunk.choices[0].delta.content or ""
                        yield LLMResultChunk(
                            model=model,
                            prompt_messages=prompt_messages,
                            delta=LLMResultChunkDelta(
                                index=0,
                                message=AssistantPromptMessage(
                                    content=content
                                )
                            )
                        )
                else:
                    if not response.choices:
                        raise ValueError(f"OpenAI API returned no choices: {response}")
                    content = response.choices[0].message.content
                    yield LLMResultChunk(
                        model=model,
                        prompt_messages=prompt_messages,
                        delta=LLMResultChunkDelta(
                            index=0,
                            message=AssistantPromptMessage(
                                content=content
                            )
                        )
                    )

    def get_llm_num_tokens(
        self,
        tenant_id: str,
        user_id: str,
        plugin_id: str,
        provider: str,
        model_type: str,
        model: str,
        credentials: dict,
        prompt_messages: list[PromptMessage],
        tools: list[PromptMessageTool] | None = None,
    ) -> int:
        """
        Get number of tokens for llm
        """
        response = self._request_with_plugin_daemon_response_stream(
            method="POST",
            path=f"plugin/{tenant_id}/dispatch/llm/num_tokens",
            type=PluginLLMNumTokensResponse,
            data=jsonable_encoder(
                {
                    "user_id": user_id,
                    "data": {
                        "provider": provider,
                        "model_type": model_type,
                        "model": model,
                        "credentials": credentials,
                        "prompt_messages": prompt_messages,
                        "tools": tools,
                    },
                }
            ),
            headers={
                "X-Plugin-ID": plugin_id,
                "Content-Type": "application/json",
            },
        )

        for resp in response:
            return resp.num_tokens

        return 0

    def invoke_text_embedding(
        self,
        tenant_id: str,
        user_id: str,
        plugin_id: str,
        provider: str,
        model: str,
        credentials: dict,
        texts: list[str],
        input_type: str,
    ) -> TextEmbeddingResult:
        """
        Invoke text embedding
        """
        response = self._request_with_plugin_daemon_response_stream(
            method="POST",
            path=f"plugin/{tenant_id}/dispatch/text_embedding/invoke",
            type=TextEmbeddingResult,
            data=jsonable_encoder(
                {
                    "user_id": user_id,
                    "data": {
                        "provider": provider,
                        "model_type": "text-embedding",
                        "model": model,
                        "credentials": credentials,
                        "texts": texts,
                        "input_type": input_type,
                    },
                }
            ),
            headers={
                "X-Plugin-ID": plugin_id,
                "Content-Type": "application/json",
            },
        )

        for resp in response:
            return resp

        raise ValueError("Failed to invoke text embedding")

    def get_text_embedding_num_tokens(
        self,
        tenant_id: str,
        user_id: str,
        plugin_id: str,
        provider: str,
        model: str,
        credentials: dict,
        texts: list[str],
    ) -> list[int]:
        """
        Get number of tokens for text embedding
        """
        response = self._request_with_plugin_daemon_response_stream(
            method="POST",
            path=f"plugin/{tenant_id}/dispatch/text_embedding/num_tokens",
            type=PluginTextEmbeddingNumTokensResponse,
            data=jsonable_encoder(
                {
                    "user_id": user_id,
                    "data": {
                        "provider": provider,
                        "model_type": "text-embedding",
                        "model": model,
                        "credentials": credentials,
                        "texts": texts,
                    },
                }
            ),
            headers={
                "X-Plugin-ID": plugin_id,
                "Content-Type": "application/json",
            },
        )

        for resp in response:
            return resp.num_tokens

        return []

    def invoke_rerank(
        self,
        tenant_id: str,
        user_id: str,
        plugin_id: str,
        provider: str,
        model: str,
        credentials: dict,
        query: str,
        docs: list[str],
        score_threshold: float | None = None,
        top_n: int | None = None,
    ) -> RerankResult:
        """
        Invoke rerank
        """
        response = self._request_with_plugin_daemon_response_stream(
            method="POST",
            path=f"plugin/{tenant_id}/dispatch/rerank/invoke",
            type=RerankResult,
            data=jsonable_encoder(
                {
                    "user_id": user_id,
                    "data": {
                        "provider": provider,
                        "model_type": "rerank",
                        "model": model,
                        "credentials": credentials,
                        "query": query,
                        "docs": docs,
                        "score_threshold": score_threshold,
                        "top_n": top_n,
                    },
                }
            ),
            headers={
                "X-Plugin-ID": plugin_id,
                "Content-Type": "application/json",
            },
        )

        for resp in response:
            return resp

        raise ValueError("Failed to invoke rerank")

    def invoke_tts(
        self,
        tenant_id: str,
        user_id: str,
        plugin_id: str,
        provider: str,
        model: str,
        credentials: dict,
        content_text: str,
        voice: str,
    ) -> Generator[bytes, None, None]:
        """
        Invoke tts
        """
        response = self._request_with_plugin_daemon_response_stream(
            method="POST",
            path=f"plugin/{tenant_id}/dispatch/tts/invoke",
            type=PluginStringResultResponse,
            data=jsonable_encoder(
                {
                    "user_id": user_id,
                    "data": {
                        "provider": provider,
                        "model_type": "tts",
                        "model": model,
                        "credentials": credentials,
                        "tenant_id": tenant_id,
                        "content_text": content_text,
                        "voice": voice,
                    },
                }
            ),
            headers={
                "X-Plugin-ID": plugin_id,
                "Content-Type": "application/json",
            },
        )

        try:
            for result in response:
                hex_str = result.result
                yield binascii.unhexlify(hex_str)
        except PluginDaemonInnerError as e:
            raise ValueError(e.message + str(e.code))

    def get_tts_model_voices(
        self,
        tenant_id: str,
        user_id: str,
        plugin_id: str,
        provider: str,
        model: str,
        credentials: dict,
        language: str | None = None,
    ):
        """
        Get tts model voices
        """
        response = self._request_with_plugin_daemon_response_stream(
            method="POST",
            path=f"plugin/{tenant_id}/dispatch/tts/model/voices",
            type=PluginVoicesResponse,
            data=jsonable_encoder(
                {
                    "user_id": user_id,
                    "data": {
                        "provider": provider,
                        "model_type": "tts",
                        "model": model,
                        "credentials": credentials,
                        "language": language,
                    },
                }
            ),
            headers={
                "X-Plugin-ID": plugin_id,
                "Content-Type": "application/json",
            },
        )

        for resp in response:
            voices = []
            for voice in resp.voices:
                voices.append({"name": voice.name, "value": voice.value})

            return voices

        return []

    def invoke_speech_to_text(
        self,
        tenant_id: str,
        user_id: str,
        plugin_id: str,
        provider: str,
        model: str,
        credentials: dict,
        file: IO[bytes],
    ) -> str:
        """
        Invoke speech to text
        """
        response = self._request_with_plugin_daemon_response_stream(
            method="POST",
            path=f"plugin/{tenant_id}/dispatch/speech2text/invoke",
            type=PluginStringResultResponse,
            data=jsonable_encoder(
                {
                    "user_id": user_id,
                    "data": {
                        "provider": provider,
                        "model_type": "speech2text",
                        "model": model,
                        "credentials": credentials,
                        "file": binascii.hexlify(file.read()).decode(),
                    },
                }
            ),
            headers={
                "X-Plugin-ID": plugin_id,
                "Content-Type": "application/json",
            },
        )

        for resp in response:
            return resp.result

        raise ValueError("Failed to invoke speech to text")

    def invoke_moderation(
        self,
        tenant_id: str,
        user_id: str,
        plugin_id: str,
        provider: str,
        model: str,
        credentials: dict,
        text: str,
    ) -> bool:
        """
        Invoke moderation
        """
        response = self._request_with_plugin_daemon_response_stream(
            method="POST",
            path=f"plugin/{tenant_id}/dispatch/moderation/invoke",
            type=PluginBasicBooleanResponse,
            data=jsonable_encoder(
                {
                    "user_id": user_id,
                    "data": {
                        "provider": provider,
                        "model_type": "moderation",
                        "model": model,
                        "credentials": credentials,
                        "text": text,
                    },
                }
            ),
            headers={
                "X-Plugin-ID": plugin_id,
                "Content-Type": "application/json",
            },
        )

        for resp in response:
            return resp.result

        raise ValueError("Failed to invoke moderation")
