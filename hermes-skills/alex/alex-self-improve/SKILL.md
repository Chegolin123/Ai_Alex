---
name: alex-self-improve
description: "Let the system improve itself: find, change, measure, revert."
version: 1.0.0
author: ALEX
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [self-improvement, metrics, benchmarking]
    category: alex
---

# Самоулучшение

Система умеет улучшать саму себя. Запуск:

```
C:\Users\finni\agent-system\bin\alex.cmd improve 1                 # одно изменение за цикл
C:\Users\finni\agent-system\bin\alex.cmd improve 1 --thinking      # с размышлениями модели вслух
C:\Users\finni\agent-system\bin\alex.cmd improvements              # что уже пробовали и что вышло
C:\Users\finni\agent-system\bin\alex.cmd report                   # текущее состояние и метрики
```

## Что система уже умеет

Замер до → поиск слабого места → **одна** правка → замер после → вердикт.
Нейтральные и ухудшившие изменения откатываются автоматически, правка бэкапится
до применения.

## Приоритет слабых мест

1. `json_parse_failed` в журнале — цикл останавливается, это дороже всего.
2. `tool_call_unknown` — модель зовёт несуществующий инструмент.
3. Повторные попытки: `stage_retry_rate`, `avg_attempts_per_stage`.
4. Троттлинг: `throttle_events` — предложи снизить нагрузку, а не код.
5. Долгие ходы: `avg_llm_turn_sec`.

## Что нельзя

- Предлагать изменение, которое нечем проверить измеримым тестом.
- Предлагать «улучшить промпт» без конкретного текста, что поменять.
- Предлагать то, что уже откатывалось — сначала `C:\Users\finni\agent-system\bin\alex.cmd improvements`.
- Предлагать то, что не влезает в VRAM (см. `C:\Users\finni\agent-system\bin\alex.cmd thermal`).

## Измеренные особенности этой модели

Ornith-1.5 — reasoning-модель, и это важно при работе с ней:

- Без `reasoning_effort: none` она съедает весь бюджет токенов на размышления и
  возвращает пустой ответ. Структурные ходы идут с рассуждениями выключенными.
- «Только JSON» она не держит: выдаёт валидный объект и дописывает мусор.
  Поэтому JSON-схема навязывается грамматикой llama.cpp, а парсер чинит
  остатки.
- Tool calling нативный, текстовый `[Tool Call]` не используется.

## Модель изменений

Менять можно только эти файлы:

`runtime/llm.py`, `runtime/safety.py`, `runtime/thermal.py`, `agent/loop.py`,
`agent/tools.py`, `rag/index.py`, `skills/*.md`, `agent/verify.py`,
`agent/schemas.py`, `agent/cli.py`.

Правка скилла дешевле и обратима, правка кода — последнее средство.

## Откат

Точечно: `C:\Users\finni\agent-system\bin\alex.cmd version --snapshot` перед рискованной правкой, откат через git
тега. Файловые бэкапы лежат в `state/backups/`.
