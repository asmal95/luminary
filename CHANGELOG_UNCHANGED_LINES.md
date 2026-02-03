# Изменения: Поддержка комментариев к неизмененным строкам

## Дата: 2026-02-04

## Проблема
Ранее комментарии к неизмененным строкам кода (которые не были изменены в MR, но находятся в контексте diff) создавались как отдельные текстовые комментарии в формате:

```
[Comment for src/main/java/Example.java:63]
Your comment text here
Location: Line 63
```

Но GitLab API позволяет создавать inline-комментарии даже для неизмененных строк, если правильно указать параметры `new_line` и `old_line`.

## Решение

### 1. Добавлено поле `line_type` в модель `Comment`
- **Файл:** `src/luminary/domain/models/comment.py`
- Новое поле: `line_type: str = "new"` (возможные значения: `"new"`, `"old"`, `"unchanged"`)
- Позволяет хранить информацию о типе строки для каждого комментария

### 2. Добавлен метод `get_line_type()` в модель `FileChange`
- **Файл:** `src/luminary/domain/models/file_change.py`
- Метод анализирует hunks diff и определяет тип строки по номеру:
  - `"new"` - добавленная строка (начинается с `+`)
  - `"old"` - удаленная строка (начинается с `-`)
  - `"unchanged"` - контекстная строка (начинается с пробела) или строка вне hunks

### 3. Обновлен процесс создания комментариев
- **Файл:** `src/luminary/application/review_service.py`
- Метод `_parse_comment_item()` теперь определяет `line_type` при создании комментария
- Метод `_parse_llm_response()` передает `FileChange` вместо просто `file_path`

### 4. Исправлена логика создания position в GitLab API
- **Файл:** `src/luminary/infrastructure/gitlab/client.py`
- Метод `_post_inline_comment()` теперь правильно устанавливает `new_line` и `old_line`:
  - Для `"unchanged"`: оба поля устанавливаются в значение `line_number`
  - Для `"new"`: только `new_line` устанавливается, `old_line = None`
  - Для `"old"`: только `old_line` устанавливается, `new_line = None`
- `line_code` теперь опциональный - не включается в `position`, если он пустой

### 5. Обновлен `MRReviewService`
- **Файл:** `src/luminary/application/mr_review_service.py`
- Метод `_post_comments_to_gitlab()` теперь передает `line_type` в `post_comment()`

## Тесты

### Добавлены новые тесты
- **Файл:** `tests/test_unchanged_lines.py`
- 6 новых тестов для проверки функционала определения типа строки
- Тесты проверяют правильность работы `get_line_type()` для разных сценариев

### Обновлены существующие тесты
- **Файл:** `tests/test_review_service.py`
- Обновлены тесты класса `TestParseLLMResponse` для передачи `FileChange` вместо строки
- **Файл:** `tests/test_gitlab_client.py`
- Обновлен тест `test_post_inline_comment_no_line_code_attempts_without_it`

### Результаты тестирования
✅ Все 140 тестов проходят успешно

## Результат

Теперь комментарии к неизмененным строкам:
1. Создаются как полноценные inline-комментарии в GitLab
2. Отображаются непосредственно на соответствующих строках кода
3. Имеют такой же UX, как и комментарии к измененным строкам
4. Не требуют отдельного формата `[Comment for file:line]`

## Совместимость

✅ Изменения обратно совместимы:
- Старые комментарии с `line_type="new"` (по умолчанию) продолжают работать как раньше
- Все существующие тесты проходят без изменений функционала
- Новое поле `line_type` имеет значение по умолчанию `"new"`

## Примеры использования

### До изменений
```
[Comment for src/main/java/Example.java:63]
The group index here might be incorrect.
Location: Line 63
```

### После изменений
Комментарий отображается inline на строке 63, даже если эта строка не была изменена в MR.
