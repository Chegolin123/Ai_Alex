---
name: alex-mediator
description: "Liaison between the human and the autonomous ALEX agents."
version: 1.0.0
author: ALEX
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [mediator, human-interface, questions, reporting]
    category: alex
---

# Посредник

Подробности роли приходят из системного промпта сессии. Этот skill — про
механику.

## Очередь вопросов

Автономные агенты работают без человека и не могут спросить. Всё, что мешает,
они пишут сюда: `C:\Users\finni\agent-system\state\questions.jsonl`.

```powershell
C:\Users\finni\agent-system\bin\alex.cmd questions
C:\Users\finni\agent-system\bin\alex.cmd questions --status open
C:\Users\finni\agent-system\bin\alex.cmd answer q003 "текст ответа человека"
```

Приоритет `high` — человек должен ответить в первую очередь. Ответ сохраняется
в той же записи, история не теряется.

## Регламент

1. Начало разговора: `questions`, затем `progress`.
2. Задача от человека → `alex.cmd cycle "<цель>"`, затем доклад по итогу.
3. «Что нового» / «как дела» → `progress` и `report`, без пересказа кода.
4. Ответ человека на вопрос → сразу `alex.cmd answer <id> "<текст>"`.
5. Сам не знаешь, как решить → `alex.cmd ask "<вопрос>" --priority high`.

## Что нельзя

- Называть улучшения, которых нет. `alex.cmd progress` возвращает код 1, если
  система ничего не улучшила, — пересказывай вердикт как есть.
- Обещать, что фоновый агент что-то сделает: он работает по расписанию.
- Править файлы системы без прямой просьбы человека.
