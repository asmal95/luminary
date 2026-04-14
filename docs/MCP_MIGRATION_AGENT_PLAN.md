# План миграции Luminary на MCP (future)

Этот документ описывает будущий переход с REST retrieval на MCP-подключение Code Context.
Цель: сохранить текущую функциональность и добавить режим, в котором агент использует MCP tools.

## Когда делать миграцию

- REST-режим стабильно работает в проде (нет критичных регрессий по качеству/латентности).
- Есть потребность переиспользовать ту же retrieval-логику в нескольких агентных клиентах.
- Команда готова поддерживать отдельный MCP runtime и политику вызовов tools.

## Целевое состояние

- Luminary поддерживает два режима retrieval:
  - `rest` (текущий, дефолтный)
  - `mcp` (новый, опциональный)
- Выбор режима делается через конфиг.
- Поведение fail-open/fail-closed сохраняется одинаковым между режимами.

## Этапы реализации

### Этап 1. Подготовка конфигурации

1. Расширить секцию `code_context` новыми полями:
   - `mode: rest | mcp`
   - `mcp_transport: stdio | streamable-http`
   - `mcp_command`, `mcp_args`, `mcp_url`
2. Сохранить обратную совместимость:
   - если `mode` не задан, считать `rest`.

### Этап 2. Абстракция retrieval-адаптера

1. Вынести интерфейс retriever (например `BaseContextRetriever`):
   - `retrieve_for_file_change(file_change) -> Optional[str]`
2. Текущий `CodeContextRetriever` оставить как REST-реализацию.
3. Добавить MCP-реализацию с тем же контрактом.

### Этап 3. MCP transport layer

1. Реализовать MCP client-обертку в `infrastructure`:
   - stdio-подключение к `code-context --mcp`
   - HTTP-подключение к `.../mcp`
2. Добавить thin-mapping вызовов:
   - `search_code`
   - `get_symbol_neighbors`
   - `get_file_context`
3. Добавить таймауты и обработку ошибок transport-уровня.

### Этап 4. Политика вызова tools для агента

Рекомендуемая policy:

1. Сначала всегда `search_code` (2-3 коротких query).
2. Для лучших хитов — `get_symbol_neighbors`.
3. `get_file_context` только когда:
   - нужен полный контекст файла;
   - готовится конкретная правка.
4. Ограничивать объем контекста (символы/блоки), чтобы не раздувать prompt.

## Agent runbook (чеклист)

1. Проверить, что MCP сервер поднят:
   - stdio: `code-context --mcp`
   - streamable-http: `code-context --mcp --mcp-transport streamable-http --mcp-port 8010`
2. Выполнить smoke-тест tools (`search_code` -> `get_symbol_neighbors`).
3. Запустить `luminary mr ... --no-post` на одном MR.
4. Сравнить качество комментариев и latency против REST baseline.
5. Включить posting только после успешного dry-run.

## Риски и меры

- Риск: нестабильность MCP transport.
  - Мера: fallback на REST при `fail_open=true`.
- Риск: рост latency из-за лишних tool calls.
  - Мера: лимиты на количество запросов/хитов/neighbor depth.
- Риск: разное поведение REST vs MCP.
  - Мера: единый retriever interface + общие integration tests.

## Минимальный тест-план для MCP

- Unit:
  - маппинг MCP responses -> внутренние блоки контекста;
  - обработка timeout/transport errors.
- Integration:
  - режим `mode: mcp` с mock MCP server;
  - fail-open и fail-closed сценарии;
  - регрессия: `mode: rest` не изменился.
- E2E:
  - dry-run MR review с включенным MCP и сравнение метрик.
