# Luminary - AI Code Reviewer для GitLab

Интеллектуальный ревьюер кода на основе LLM для автоматического анализа Merge Requests в GitLab.

## 🎯 Видение проекта

Развитие от инструмента для автоматического ревью кода до полноценного AI-агента, способного не только находить проблемы, но и предлагать улучшения, объяснять решения и обучаться на основе обратной связи.

**Текущая фаза:** MVP завершен ✅

## 📋 Возможности (MVP)

- ✅ Автоматический анализ изменений в Merge Request
- ✅ Генерация inline-комментариев к коду
- ✅ Валидация комментариев перед отправкой
- ✅ Поддержка множественных LLM провайдеров (OpenRouter, OpenAI, DeepSeek, VLLM)
- ✅ Гибкая конфигурация через `.ai-reviewer.yml`
- ✅ Фильтрация бинарных и служебных файлов
- ✅ Retry стратегия для устойчивости к ошибкам
- ✅ Опциональное retrieval-обогащение через Code Context (REST)
- ✅ Поддержка markdown в комментариях
- ✅ Уровни severity (info, warning, error)

## 🚀 Быстрый старт

### Установка

Проект поддерживает установку через `pip` (удобно для Windows) и через [uv](https://github.com/astral-sh/uv).

```bash
# Установка (pip, editable)
python -m pip install -e .
```

### Использование

```bash
# Анализ одного файла (mock по умолчанию)
luminary file path/to/file.py

# Явный выбор провайдера
luminary file path/to/file.py --provider openrouter

# Режим комментариев
luminary file path/to/file.py --comments-mode inline

# Ревью MR (GitLab)
luminary mr group/project 123 --no-post
```

### LLM провайдеры и переменные окружения

- **openrouter**: `OPENROUTER_API_KEY`
- **openai**: `OPENAI_API_KEY`
- **deepseek**: `DEEPSEEK_API_KEY`
- **vllm** (локальный OpenAI-compatible сервер): `VLLM_API_URL` (опционально), `VLLM_API_KEY` (опционально)

### Конфигурация (пример `.ai-reviewer.yml`)

```yaml
llm:
  provider: openrouter  # mock | openrouter | openai | deepseek | vllm
  model: anthropic/claude-3.5-sonnet
  temperature: 0.7
  max_tokens: 2000
  top_p: 0.9

validator:
  enabled: true
  provider: openrouter
  model: anthropic/claude-3-haiku
  threshold: 0.7

comments:
  mode: both  # inline | summary | both

limits:
  max_context_tokens: 8000
  chunk_overlap_size: 200

retry:
  max_attempts: 3
  initial_delay: 1
  backoff_multiplier: 2

code_context:
  enabled: false
  base_url: http://localhost:8000
  timeout: 10
  repo_name: group/project
  branch: main
  max_queries: 3
  search_limit: 6
  max_hits_per_query: 3
  neighbors_depth: 2
  max_neighbors: 5
  max_context_chars: 20000
  fail_open: true

prompts:
  review: |
    ... custom review prompt ...
  validation: |
    ... custom validation prompt ...
```

## 🐳 Docker

Сборка образа:

```bash
docker build -t luminary:local .
```

Пример запуска (ревью файла):

```bash
docker run --rm -v "%cd%:/work" -w /work luminary:local file examples/sample_code.py --provider mock
```

Пример запуска (ревью MR):

```bash
docker run --rm ^
  -e GITLAB_TOKEN=... ^
  -e OPENROUTER_API_KEY=... ^
  luminary:local mr group/project 123
```

## 🤖 GitLab CI

В репозитории есть пример `.gitlab-ci.yml`, который:
- **собирает Docker образ Luminary** и пушит в GitLab Container Registry
- запускает тесты
- **использует собственный Docker образ** для запуска `luminary mr ...` в MR pipeline

### Pipeline stages:

1. **`build`** - собирает Docker образ Luminary и пушит в `$CI_REGISTRY_IMAGE:latest`
2. **`test`** - запускает pytest
3. **`ai_review`** - использует образ Luminary из Container Registry для ревью MR

### Настройка секретов:

Секреты рекомендуется хранить как GitLab CI/CD variables (Settings → CI/CD → Variables):
- `GITLAB_TOKEN` - токен для доступа к GitLab API
- `OPENROUTER_API_KEY` / `OPENAI_API_KEY` / `DEEPSEEK_API_KEY` - ключ LLM провайдера

**Важно:** Для сборки Docker образа нужен доступ к GitLab Container Registry (обычно включен по умолчанию).

## 🏗️ Архитектура

Проект использует многослойную архитектуру:

```
┌─────────────────────────────────────┐
│   Presentation Layer (CLI)          │
├─────────────────────────────────────┤
│   Application Layer (Orchestration) │
├─────────────────────────────────────┤
│   Domain Layer (Business Logic)     │
├─────────────────────────────────────┤
│   Infrastructure Layer (External)   │
└─────────────────────────────────────┘
```

### Структура проекта

```
luminary/
├── src/luminary/
│   ├── domain/           # Бизнес-логика и модели
│   ├── application/      # Оркестрация (сервисы)
│   ├── infrastructure/   # Внешние интеграции (GitLab, LLM)
│   └── presentation/     # CLI интерфейс
├── docs/                 # Документация
└── tests/                # Тесты
```

## 📚 Документация

- [Архитектурное резюме](docs/ARCHITECTURE_SUMMARY.md) - обзор архитектуры, проблемы и рекомендации
- [Roadmap](ROADMAP.md) - детальный план разработки
- [ADR документы](docs/ADR/) - архитектурные решения
- [Быстрый старт](QUICK_START.md) - инструкции по установке и использованию
- [План миграции на MCP](docs/MCP_MIGRATION_AGENT_PLAN.md) - future roadmap для агентного режима

## 🧩 Пресеты конфигурации

- `examples/ai-reviewer-config-no-code-context.yml` - базовый запуск без внешнего retrieval
- `examples/ai-reviewer-config-code-context-rest.yml` - запуск с Code Context по REST

## 🛠️ Разработка

### Установка зависимостей для разработки

```bash
python -m pip install -e ".[dev]"
```

### Запуск тестов

```bash
python -m pytest
```

### Форматирование кода

```bash
uv run black src/
uv run ruff check src/
```

## 📝 Лицензия

Лицензия будет определена позже.
