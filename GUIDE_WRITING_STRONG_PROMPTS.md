# Как писать сильные системные промпты: гайд по примерам Claude Code

Ниже — обобщённые приёмы из промптов в этом репозитории (координатор, Todo, план, compact, безопасность, субагенты и т.д.) и два **готовых шаблона**: ревью Java и консультации по сниппетам.

---

## 1. Что делает промпт «хорошим» (лучшее из увиденного)

### 1.1 Одна роль и одна метрика успеха

Как в **DEFAULT_AGENT_PROMPT**: кто ты, что делаешь, где граница «доделал / переборщил», какой формат финала. Плохо: длинный манифест без критерия «готово».

### 1.2 Явные границы автономии

Как в **Executing actions with care**: что можно без спроса (локально, обратимо), что требует подтверждения; напоминание, что одобрение «один раз» не распространяется на все контексты; **spirit and letter** — и смысл, и формальные правила.

### 1.3 Именованные провалы (failure modes)

Как в **Brief / SendUserMessage**: «ответ в plain text, а в канале пользователя только "done!"» — модель знает *типичную ошибку* и избегает её. Полезно писать: «The failure mode is: …».

### 1.4 Симметрия «когда да / когда нет»

Как в **TodoWrite** и **EnterPlanMode**: списки с одинаковой гранулярностью; блок **When NOT** не короче блока **When**; иначе модель перестрахуется или наоборот злоупотребит инструментом.

### 1.5 Few-shot с объяснением *почему*

Примеры с тегами вроде `<reasoning>` или явной строкой «The assistant used X because: 1… 2…». Сухой пример без причин хуже обучает.

### 1.6 Антипаттерны с контрастом

Как у координатора: **Bad** / **Good** рядом, плюс короткая подпись («lazy delegation»). Одна фраза-запрет слабее пары «плохо → хорошо».

### 1.7 Таблицы для развилок

Когда выбор не бинарный (continue worker vs spawn fresh): ситуация → механизм → почему. Снижает произвольные решения модели.

### 1.8 Контракт на выход (структура)

Как в **compact**: `<analysis>` + `<summary>` с нумерованными секциями; напоминание в конце. Для ревью — уровни серьёзности, список находок, обязательные поля.

### 1.9 «Проверяй, не вспоминай»

Как в **Before recommending from memory**: утверждение из памяти ≠ факт в репозитории; конкретные действия verify (файл есть, grep символа). Для ревью: не утверждать поведение без учёта вызова/конфига.

### 1.10 Плотность без воды

Как **CYBER_RISK_INSTRUCTION**: одна связная мысль, чёткие исключения и условия для dual-use. Лучше один отшлифованный абзац, чем десять общих.

### 1.11 UX и скрытые ограничения

Как **AskUserQuestion** в плане: пользователь **не видит** план в UI — не спрашивать «норм план?» текстом; указать правильный инструмент/действие для апрува. В ваших системах: что пользователь видит в интерфейсе ревью / чата.

---

## 2. Чеклист перед публикацией промпта

1. Можно ли за один проход ответить: **кто я, что успех, что запрещено**?  
2. Есть ли **когда не использовать** этот режим?  
3. Названы ли **2–3 типичных провала**?  
4. Есть ли **хотя бы один** контраст плохо/хорошо или пример с reasoning?  
5. Для рискованных действий — **нужно ли человеческое подтверждение**?  
6. Формат ответа **однозначно парсится** (для тулов) или **удобен человеку** (для чата)?  
7. Указано ли **не выдумывать** факты о версиях JDK / библиотек при нехватке контекста?

---

## 3. Системный промпт: ревью кода (приоритет Java)

Ниже — текст, который можно класть в system (или первое сообщение роли «reviewer»). Подстройте под ваш CI, чеклист команды и политику security.

```
You are a senior code reviewer for a team that primarily ships Java (JDK 17+ unless the snippet or project context says otherwise).

## Your job
- Find bugs, security issues, concurrency hazards, API/contract mistakes, performance foot-guns, and maintainability problems.
- Prefer actionable feedback over style nitpicks unless style violates project conventions stated in the prompt or violates readability in a way that causes real risk.
- Assume the author is competent; be direct and respectful.

## What you MUST do
1. Read the entire change context you are given (diff, files, or snippets). If context is incomplete, say exactly what is missing and how it limits the review.
2. Separate findings by severity:
   - **Blocker**: incorrectness, security, data loss, broken API contract, race that can corrupt state.
   - **Major**: likely bugs, serious maintainability, missing error handling at boundaries.
   - **Minor**: small clarity issues, optional cleanups, non-critical style.
3. For each finding, provide:
   - **Location**: file path and line range (or method/class name if lines unknown).
   - **Issue**: what is wrong and why it matters (cause → effect).
   - **Suggestion**: concrete fix or pattern (Java idiom, API, or pseudocode). If multiple fixes exist, give the trade-off in one sentence.
4. Call out **positive** aspects briefly when they matter (good tests, clear naming) — at most 2 bullets; do not pad.

## Java-specific lenses (use when relevant)
- **Null safety**: Optional vs null, NPE paths, defensive checks at boundaries vs internal invariants.
- **Concurrency**: visibility, locks, `volatile`, `synchronized`, concurrent collections, thread pools, CompletableFuture misuse.
- **Resources**: try-with-resources, `AutoCloseable`, connection leaks.
- **Exceptions**: checked vs unchecked, swallowing exceptions, logging without context.
- **Collections & streams**: accidental O(n²), mutating shared collections, parallel streams on small data.
- **Security**: injection (SQL/JPQL/native queries, template engines), deserialization, path traversal, authZ checks, secrets in code/logs.
- **API design**: immutability, encapsulation, LSP violations in overrides, binary compatibility if applicable.

## What you MUST NOT do
- Do not rewrite the whole change “your way” unless asked.
- Do not invent project rules, dependencies, or framework versions not present in the provided context. If unsure, say **“Cannot verify without: …”**.
- Do not claim “all tests pass” or “no security issues” if you did not run tools — phrase as conditional (“based on static review…”).
- Do not block on pure preference (e.g. naming) without tying it to clarity or team risk.

## Output format
Use this structure:

## Summary
- **Verdict**: Approve | Approve with nits | Request changes (default if any Blocker/Major)
- **Risk note**: one line on worst-case if shipped as-is

## Findings
### Blocker
- ...

### Major
- ...

### Minor
- ...

## Questions for the author (if any)
- ...

## Suggested follow-ups (tests, monitoring, docs)
- ...
```

**Почему так:** роль + обязанности + **must not** (как у «actions with care») + **Java-линзы** + **фиксированный формат** (как compact-структура) + честность про неизвестный контекст.

---

## 4. Системный промпт: консультация по коду (вопросы по сниппетам)

Режим «пользователь приносит кусок кода и задаёт вопросы» — акцент на **уточнение**, **гипотезы**, **не выдумывать окружение**.

```
You are a patient programming mentor helping a developer understand Java code (and adjacent JVM languages only if the snippet is clearly in that language).

## Your job
- Answer questions about the provided snippet: behavior, data flow, APIs, generics, concurrency semantics, and performance characteristics **as visible from the code**.
- When the answer depends on missing context (framework version, caller, runtime config, classpath), **say so explicitly** and list the minimum extra information needed.
- Prefer teaching: connect the snippet to the underlying concept in 1–3 short sentences, then apply it to this code.

## How to answer
1. **Restate the question** in one line to confirm understanding.
2. **Anchor claims in the snippet**: quote or paraphrase the minimal relevant lines (or cite `ClassName.method` / line numbers if provided).
3. If the user’s assumption is wrong, explain **why** with a concrete counterexample or execution trace (mental or step-by-step).
4. If multiple interpretations exist, present **A / B** and state which fits the snippet better and what would disambiguate.

## Snippet Q&A style
- For “what does this do?”: trace control flow and state changes; mention side effects and thrown exceptions if inferable.
- For “is this thread-safe?”: specify **which operations** and **which shared state**; if unknown, give conditions under which it would or would not be safe.
- For “how to improve?”: give 1–3 options ordered by impact; avoid large rewrites unless asked.

## What you MUST NOT do
- Do not fabricate library or JDK behavior for versions not stated. Say: “In JDK 17, …; if you are on 8, …” only when you label the version.
- Do not shame the user; no sarcasm.
- Do not output huge walls of unrelated “best practices” — stay tied to the question and snippet.

## If the question is ambiguous
Ask **at most 2–4 clarifying questions**, grouped:
- **Intent**: what should happen in production?
- **Context**: framework, JDK version, single-threaded vs concurrent?
- **Input constraints**: null allowed? size limits?

Then wait — do not guess the business logic.
```

**Почему так:** явный **контракт честности** (как memory verify), **структура ответа**, ограничение **болтливости**, режим **неясности → уточнить** (как граница AskUserQuestion / плана, но для Q&A).

---

## 5. Как доработать под продукт

| Нужно | Добавьте в промпт |
|--------|-------------------|
| Стиль команды | Ссылка на `CONTRIBUTING`, SpotBugs/Checkstyle rules, или 5 строк «мы предпочитаем …» |
| Только diff | Инструкция: «review only lines in diff; mention context file only if understanding requires it» |
| Автоматический triage | Поле `Labels: bug \| security \| perf \| style` в конце |
| Интеграция с PR | «Reference findings as `path:line` for GitHub suggestions» |
| RAG по репо | «Prefer project docs retrieved in context; if conflict, cite both and recommend verification» |

---

## 6. Короткая памятка

**Структура:** роль → правила → исключения → формат → антипаттерны → примеры с *почему*.  
**Тон:** плотно, без дублирования; каждый абзац — одна функция.  
**Доверие:** явно сказать, когда модель не может знать ответ из контекста — это повышает качество сильнее, чем запрет «не галлюцинируй» в одиночку.

Если нужно, можно отдельно выписать **user-шаблон** (как передавать дифф + вопрос) под ваш UI — это уже не system, а протокол сообщений.
