# 🚀 Быстрый старт Luminary

## Установка

```bash
# Активировать виртуальное окружение
.venv\Scripts\Activate.ps1

# Или установить зависимости заново
python -m pip install -e .
```

## Запуск

### Базовый запуск (mock провайдер)
```bash
luminary file examples/sample_code.py
```

### С выбором провайдера
```bash
# Mock (для тестирования)
luminary file examples/sample_code.py --provider mock

# OpenRouter (нужен API ключ, bash/zsh)
export OPENROUTER_API_KEY="your_key"
luminary file examples/sample_code.py --provider openrouter

# OpenAI (bash/zsh)
export OPENAI_API_KEY="your_key"
luminary file examples/sample_code.py --provider openai

# DeepSeek (bash/zsh)
export DEEPSEEK_API_KEY="your_key"
luminary file examples/sample_code.py --provider deepseek

# vLLM (локальный сервер, bash/zsh)
export VLLM_API_URL="http://localhost:8000/v1/chat/completions"
luminary file examples/sample_code.py --provider vllm
```

Для PowerShell используйте:
```powershell
$env:OPENROUTER_API_KEY = "your_key"
$env:OPENAI_API_KEY = "your_key"
$env:DEEPSEEK_API_KEY = "your_key"
$env:VLLM_API_URL = "http://localhost:8000/v1/chat/completions"
```

### С опциями
```bash
# Подробное логирование
luminary file examples/sample_code.py --verbose

# Отключить валидацию
luminary file examples/sample_code.py --no-validate

# Режим комментариев
luminary file examples/sample_code.py --comments-mode inline

# Указать конфиг файл
luminary file examples/sample_code.py --config .ai-reviewer.yml
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
  provider: mock  # mock | openrouter | openai | deepseek | vllm
  model: anthropic/claude-3.5-sonnet
  temperature: 0.7

validator:
  enabled: false
  threshold: 0.7

comments:
  mode: both  # inline | summary | both

retry:
  max_attempts: 3
  initial_delay: 1
  backoff_multiplier: 2

code_context:
  enabled: false
  base_url: http://localhost:8000
  repo_name: group/project
  branch: main
  fail_open: true
```

Или возьмите готовый пресет из `examples/`:
- `ai-reviewer-config-no-code-context.yml` — без интеграции Code Context
- `ai-reviewer-config-code-context-rest.yml` — с REST retrieval

## Проверка компонентов

```bash
# Проверить импорты
python -c "from luminary.infrastructure.llm.factory import LLMProviderFactory; print('OK')"

# Проверить конфиг
python -c "from luminary.infrastructure.config.config_manager import ConfigManager; cm = ConfigManager(); print(cm.get('llm.provider'))"
```

## Docker

Сборка образа:
```bash
docker build -t luminary:local .
```

Запуск:
```bash
docker run --rm -v "%cd%:/work" -w /work luminary:local file examples/sample_code.py --provider mock
```

## GitLab CI

В репозитории есть `.gitlab-ci.yml` который автоматически собирает Docker образ и использует его для ревью MR.

См. [README.md](README.md) для подробностей по настройке CI/CD переменных.

## Help

```bash
luminary --help
```
