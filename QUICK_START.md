# 🚀 Быстрый старт Luminary

## Установка

```bash
# Активировать виртуальное окружение
.venv\Scripts\Activate.ps1

# Или установить зависимости заново
pip install -e .
```

## Запуск

### Базовый запуск (mock провайдер)
```bash
luminary examples/sample_code.py
```

### С выбором провайдера
```bash
# Mock (для тестирования)
luminary examples/sample_code.py --provider mock

# OpenRouter (нужен API ключ)
export OPENROUTER_API_KEY=your_key
luminary examples/sample_code.py --provider openrouter
```

### С опциями
```bash
# Подробное логирование
luminary examples/sample_code.py --verbose

# Отключить валидацию
luminary examples/sample_code.py --no-validate

# Указать конфиг файл
luminary examples/sample_code.py --config .ai-reviewer.yml
```

## Тесты

```bash
# Все тесты
pytest tests/

# С подробным выводом
pytest tests/ -v

# Конкретный тест
pytest tests/test_mock_provider.py::test_mock_provider_basic
```

## Конфигурация

Создайте `.ai-reviewer.yml` в корне проекта:

```yaml
llm:
  provider: mock  # или openrouter
  model: anthropic/claude-3.5-sonnet
  temperature: 0.7

validator:
  enabled: false
  threshold: 0.7
```

## Проверка компонентов

```bash
# Проверить импорты
python -c "from luminary.infrastructure.llm.factory import LLMProviderFactory; print('OK')"

# Проверить конфиг
python -c "from luminary.infrastructure.config.config_manager import ConfigManager; cm = ConfigManager(); print(cm.get('llm.provider'))"
```

## Help

```bash
luminary --help
```
