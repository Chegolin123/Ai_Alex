---
name: alex-system
description: "Inspect and control the ALEX machine: GPU, VRAM, RAG, versions."
version: 1.0.0
author: ALEX
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [system, gpu, thermal, rag, versioning]
    category: alex
---

# Состояние машины

## Железо и нагрев

```
C:\Users\finni\agent-system\bin\alex.cmd thermal      # температура, VRAM, питание, пороги
C:\Users\finni\agent-system\bin\alex.cmd gate         # код 1, если горячо - подожди перед нагрузкой
```

RTX 2070 SUPER, 8 ГБ VRAM. LLM занимает почти всё: 128k контекста с
q8_0-кэшем весит ~7.9 ГБ, свободно остаётся ~300 МБ. Пока работает модель —
игры не запустятся, переключайся ярлыком «Игровой режим».

Пороги: 70 °C тёпло, 80 °C пауза 30 с, 85 °C пауза 120 с.

## Сервер модели

| Что | Значение |
|---|---|
| рантайм | llama.cpp llama-server |
| модель | Ornith-1.5-9B Q4_K_M (5.77 ГБ) |
| контекст | 131072, KV-кэш q8_0 |
| endpoint | `http://127.0.0.1:8080/v1` |
| генерация | ~56 tok/s |

Если модель не отвечает: `C:\Users\finni\agent-system\bin\alex.cmd run "powershell -File C:\Users\finni\agent-system\bin\start-runtime.ps1"`.
Скрипт сам считает бюджет VRAM и при нехватке снижает контекст.

## Знание о системе

```
C:\Users\finni\agent-system\bin\alex.cmd rag "<запрос>"        # поиск по конфигам, коду и журналам системы
C:\Users\finni\agent-system\bin\alex.cmd index                 # переиндексировать
```

Индекс FTS5 лежит в `state/rag.db`, слова ищутся по AND: если «ничего не
найдено», попробуй одно слово вместо двух.

## Версии и отчёты

```
C:\Users\finni\agent-system\bin\alex.cmd report                # сводка: LLM, метрики, улучшения, версии
C:\Users\finni\agent-system\bin\alex.cmd version               # состояние репозитория
C:\Users\finni\agent-system\bin\alex.cmd version --snapshot    # снимок + отчёт + тег + отправка в GitHub
C:\Users\finni\agent-system\bin\alex.cmd state                 # полный срез в JSON
```

Репозиторий: `https://github.com/Chegolin123/Ai_Alex`. Отчёт каждого снимка
появляется в `reports/`, история — в `CHANGELOG.md`.

## Расписание

| Задача | Когда |
|---|---|
| `agent-runtime-startup` | при старте Windows |
| `agent-improve-hourly` | раз в час, цикл самоулучшения |
| `agent-metrics-daily` | в 11:00, метрики и RAG |
| `agent-version-daily` | в 23:30, снимок версии в GitHub |

## Hermes

CLI: `C:\Users\finni\AppData\Local\hermes\bin\hermes.exe`, конфиг
`C:\Users\finni\AppData\Local\hermes\config.yaml`. Права доступа к файлам и
командам полные (`approvals.mode: off`), блокировки разрушающих команд при этом
не отключаются.
