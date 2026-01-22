from luminary.infrastructure.llm.factory import LLMProviderFactory


def test_factory_supports_providers():
    # "real" providers should be instantiable without performing network calls
    assert LLMProviderFactory.create("openai", {"api_key": "test"}) is not None
    assert LLMProviderFactory.create("deepseek", {"api_key": "test"}) is not None
    assert LLMProviderFactory.create("openrouter", {"api_key": "test"}) is not None
    assert LLMProviderFactory.create("vllm", {"model": "local-model"}) is not None
