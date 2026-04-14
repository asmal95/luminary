# Интеграция Code Context в агентные системы

Документ покрывает два сценария:

1. Интеграция в **своего агента** (например, агент для code review).
2. Подключение к **готовым агентам** через MCP (например, OpenCode).

Для отдельного browser-extension сценария (PR review в расширении) см. подробный runbook:
[browser-review-extension-integration.md](browser-review-extension-integration.md)

Для управления реестром Git-репозиториев через REST (register/clone/sync/index/delete): [repo-management-api.md](repo-management-api.md)

## 1. Перед интеграцией

Убедитесь, что:
- индекс построен (`code-context --repo <path> --full`);
- `.env` заполнен (PG/Neo4j/Ollama);
- для REST запущен `code-context --serve` (или `uvicorn ...`);
- для MCP запущен `code-context --mcp` (stdio) или `--mcp-transport streamable-http`.

---

## 2. Интеграция в своего агента (пример: code review)

## 2.1 Базовая схема

Для review-агента рабочий flow обычно такой:

1. Получить diff/описание PR.
2. Сгенерировать 2-5 поисковых запросов по изменению.
3. Для каждого запроса вызвать `search`.
4. Для ключевых хитов взять `neighbors` и/или `file_context`.
5. Собрать компактный контекст и передать в LLM для ревью.

Это даёт LLM не только diff, но и соседний код, что снижает ложные замечания.

## 2.2 Пример через REST (Python)

```python
from code_context.client import CodeContextClient
from openai import OpenAI

cc = CodeContextClient("http://localhost:8000")
llm = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")


def review_change(title: str, diff_summary: str, repo_path: str) -> str:
    queries = [
        f"{title}",
        f"{diff_summary}",
        "security validation authorization",
    ]
    blocks = []
    for q in queries:
        hits = cc.search(q, repo_name="group/repo", branch="main", repo_path=repo_path, limit=6)
        for h in hits[:3]:
            blocks.append(f"{h['file_path']} :: {h['node_type']}\n{h['node_text']}")
            sid = h.get("symbol_id")
            if sid:
                n = cc.get_symbol_neighbors(sid, depth=2)
                blocks.extend([f"[same-file] {x.get('kind')} {x.get('name')}" for x in n[:5]])

    context = "\n\n".join(blocks)[:20000]
    prompt = f"Контекст проекта:\n{context}\n\nИзменение:\n{diff_summary}\n\nСделай code review."
    resp = llm.chat.completions.create(
        model="llama3",
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content
```

## 2.3 Когда вызывать какие функции

- `search`:
  - для первичного retrieval по смыслу;
  - быстрый "вход" в релевантные файлы.
- `get_symbol_neighbors`:
  - когда нужен локальный контекст вокруг найденного символа.
- `get_file_context`:
  - когда нужно увидеть файл целиком (например, перед автоправкой).

## 2.4 Рекомендуемый flow с managed repos API

Если агент работает не с одним локальным путём, а с набором проектов:

1. На старте сессии вызвать `GET /repos` и убедиться, что нужный `repo_name` зарегистрирован.
2. Если проекта нет — выполнить `POST /repos` (`clone_now=true`, при необходимости `index_now=true`).
3. Перед review/QA запускать `POST /repos/{repo_name}/sync` для актуализации ветки и индекса.
4. В вызовах retrieval всегда передавать `repo_name` + `branch`.

Это позволяет в одном инстансе сервиса одновременно обслуживать несколько репозиториев и веток.

---

## 3. Интеграция в готовые агенты через MCP

## 3.1 Что нужно запустить

### Локально (stdio)

```bash
code-context --mcp
```

### По сети (streamable-http)

```bash
code-context --mcp --mcp-transport streamable-http --mcp-host 0.0.0.0 --mcp-port 8010
```

Branch-aware индексация с optional sync:

```bash
code-context --repo /path/to/repo --repo-name group/repo --branch main --sync --incremental
```

---

## 3.2 OpenCode (пример подключения)

Если OpenCode поддерживает MCP по команде (stdio), обычно указывается:

```json
{
  "command": "code-context",
  "args": ["--mcp"]
}
```

Если нужен HTTP MCP:

```json
{
  "url": "http://<host>:8010/mcp"
}
```

Точное имя полей зависит от версии OpenCode и его конфигурационного файла, но суть одна (в некоторых версиях путь может быть `/mcp` или корень, проверяйте логи сервера при подключении):
- либо запускать `code-context` как subprocess,
- либо подключаться к URL MCP-сервера.

## 3.3 Что агент увидит

После подключения MCP агент получает 3 tool'а:

- `search_code`
- `get_symbol_neighbors`
- `get_file_context`

И может вызывать их автоматически в процессе ответа/ревью.

---

## 4. Практические шаблоны для prompt/tool policy

Для готовых агентов полезно добавить policy:

- "Сначала всегда вызывай `search_code` с коротким запросом."
- "Если нужен контекст функции/класса — вызывай `get_symbol_neighbors`."
- "Если нужно предложить правку — сначала вызови `get_file_context`."

Это уменьшает вероятность "ответа из головы" без обращения к индексу.

---

## 5. Частые ошибки интеграции

- **Пустая выдача**:
  - не указан верный `repo_name`/`branch` (или `repo_path` в legacy-режиме);
  - индекс ещё не построен;
  - фильтры (`INDEX_IGNORE_DIRS`) исключили нужные файлы.
- **Ошибки подключения**:
  - неверные `PG_*`/`NEO4J_*` в `.env`;
  - недоступен `OLLAMA_BASE_URL`.
- **MCP не поднимается**:
  - неверный путь к `code-context` в клиенте;
  - занят порт для `streamable-http`.
- **404 на `/repos/{repo_name}`**:
  - это штатно для незарегистрированного репозитория;
  - убедиться, что `repo_name` передаётся как обычный path (например `group/repo`), без ручного URL-encoding;
  - проверить, что запись существует в `GET /repos`.

---

## 6. Рекомендованный минимальный rollout

1. Запустить `--serve` и проверить `POST /search`.
2. Встроить REST-клиент в своего агента и сравнить качество ответов "до/после retrieval".
3. Подключить MCP к одному готовому агенту (например, OpenCode).
4. Зафиксировать tool policy (когда какие tools вызывать).

---

## 7. Luminary (REST, optional)

В Luminary интеграция через REST сделана опциональной и включается через `.ai-reviewer.yml`:

```yaml
code_context:
  enabled: true
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
```

- При `enabled: false` поведение Luminary полностью прежнее.
- При `fail_open: true` ошибки retrieval не прерывают ревью (идёт fallback на обычный prompt без внешнего контекста).

Готовые пресеты:
- `examples/ai-reviewer-config-code-context-rest.yml` — запуск с REST retrieval.
- `examples/ai-reviewer-config-no-code-context.yml` — запуск без Code Context.

План будущей миграции на MCP:
- `docs/MCP_MIGRATION_AGENT_PLAN.md`
