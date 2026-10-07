# Агентная система на ALEX — Ornith-1.5-9B

Самообучающаяся система: план → субагент → детерминированная проверка, плюс цикл
самоулучшения, который находит собственные дефекты, меняет ровно один компонент,
мерит и откатывает при регрессе.

## Что где

| Путь | Что |
|---|---|
| `runtime/llm.py` | клиент llama-server, разбор reasoning/tool calls, починка JSON |
| `runtime/thermal.py` | температура и VRAM через nvidia-smi, паузы 80/85 °C |
| `runtime/safety.py` | блокировка деструктивных команд |
| `runtime/state.py` | журнал событий, метрики, бэкапы |
| `agent/loop.py` | цикл plan → sub_agent → verify, лимиты попыток |
| `agent/verify.py` | детерминированные проверки до обращения к модели |
| `agent/self_improve.py` | цикл самоулучшения |
| `agent/schemas.py` | JSON-схемы, принудительно навязанные как грамматика |
| `agent/tools.py` | shell / file_read / file_write / rag_search / gpu_status |
| `agent/metrics.py` | сбор метрик в `state/metrics.json` |
| `agent/report.py` | сводка состояния |
| `skills/*.md` | plan, orchestrator, sub_agent, verify, plan_trouble, self_improve |
| `rag/index.py` | индекс FTS5 по собственным файлам системы |
| `tests/test_safety.py` | 15 блокировок + 13 разрешённых команд |
| `state/` | inventory, runtime_decision, events.jsonl, metrics.json, improvements.jsonl, backups |

## Запуск

```powershell
# поднять LLM-сервер (128k, q8_0, полный оффлоад в VRAM)
powershell -ExecutionPolicy Bypass -File bin\start-runtime.ps1

# состояние системы
python agent\report.py

# обычная задача
python -c "import sys;sys.path.insert(0,'.');from agent.loop import run_cycle;print(run_cycle('...'))"

# цикл самоулучшения
python agent\self_improve.py 1

# индекс RAG
python rag\index.py

# тесты
python tests\test_safety.py
```

## Ярлыки на рабочем столе

Созданы на личном и на общем рабочем столе:

- **Рабочий режим** — сервер + автозапуск + индекс + первый цикл самоулучшения
- **Игровой режим** — останавливает всё и отдаёт VRAM играм

## Агент версий (GitHub)

Репозиторий системы: **https://github.com/Chegolin123/Ai_Alex** — ветка `main`,
теги вида `v0.2.1`. Рабочая копия и есть репозиторий: `C:\Users\finni\agent-system`.

Git в системе не установлен, используется портативный из тулчейна Hermes —
агент обращается к нему по полному пути и не зависит от `PATH`.

```powershell
python agent\version_agent.py status              # ветка, теги, незакоммиченное, секреты
python agent\version_agent.py snapshot            # снимок + отчёт + тег
python agent\version_agent.py snapshot --push     # ...и отправить в GitHub
python agent\version_agent.py snapshot --bump major
python agent\version_agent.py push               # отправить без нового снимка
python agent\version_agent.py history             # теги и коммиты
python agent\version_agent.py rollback --tag v0.2.0
python agent\version_agent.py doctor              # проверка утечек секретов
```

Что делает `snapshot`: переиндексирует RAG, собирает метрики, кладёт отчёт в
`reports/YYYY-MM-DD_HHMM.md` (состояние LLM, GPU, VRAM, метрики, таблица решений
самоулучшения), коммитит, обновляет `CHANGELOG.md`, ставит тег.

`rollback` — откат всей системы на тег. Обычные бэкапы в `state/backups/`
остаются для точечных правок одного файла.

### Секреты

`config.json` с API-ключом в `.gitignore` и **не должен** попадать в репозиторий.
Ключ читается из него во всех скриптах. `version_agent.py doctor` проверяет, что
в индексе нет ни одного секрета; при первом снимке это выявило утечку ключа в
`bin/start-runtime.ps1` и `probe/probe_runtime.py` — история была пересоздана,
потому что репозиторий ещё не отправлялся.

Ежедневный снимок с отправкой: задача `agent-version-daily` в 23:30.

## Автозапуск при старте Windows

Зарегистрирован (одним UAC через `bin\setup-admin.ps1`):

| Задача | Когда |
|---|---|
| `agent-runtime-startup` | при старте Windows, поднимает LLM-сервер |
| `agent-improve-hourly` | раз в час, цикл самоулучшения |
| `agent-metrics-daily` | раз в сутки в 11:00, метрики и RAG |
| `agent-version-daily` | раз в сутки в 23:30, снимок версии + push в GitHub |

У повторяющегося триггера обязательно задан `-RepetitionDuration` — без него
Windows считает триггер однократным, и задача срабатывает ровно один раз. На
этом почасовой цикл молча не работал.

Периодические задачи вызывают обёртку `bin\tick-*.ps1` одной строкой без кавычек.
Регистрация inline-команды вида `cmd /c "...python.exe ... >> log"` давала задачи,
которые запускались и умирали с `0x1`, ни разу не создав лог.

Проверка: `powershell -ExecutionPolicy Bypass -File bin\check-tasks.ps1` — покажет
триггеры, результат последнего запуска и состояние логов.

Отключить и включить вручную:

```powershell
powershell -ExecutionPolicy Bypass -File bin\register-autostart.ps1 -Mode disable
powershell -ExecutionPolicy Bypass -File bin\register-autostart.ps1 -Mode enable
powershell -ExecutionPolicy Bypass -File bin\register-autostart.ps1 -Mode status
```

## Что показали замеры этого рантайма

Всё ниже измерено на этой машине, а не взято из документации:

| Что | Результат |
|---|---|
| модель в VRAM | целиком, spill на CPU нет, 7898 / 8192 МБ |
| генерация | 56 tok/s, префилл 1140 tok/s |
| контекст | 131072 при q8_0; 262144 не помещается |
| reasoning | приходит в поле `reasoning_content`, не инлайн `<think>` |
| tool calling | нативный `tool_calls` |
| JSON-only | без принудительной схемы ломается («Extra data») |
| схема | `response_format: json_schema` решает проблему полностью |

Три находки, изменившие архитектуру:

1. **Рассуждения съедали бюджет.** Reasoning-модель на `max_tokens: 900` возвращала
   пустой контент с `finish_reason: length`. Для структурных ходов рассуждения
   выключены (`reasoning_effort: none`).
2. **Один ход с инструментами мало.** Субагент успевал прочитать файл и уже не мог
   его записать. Сделан полноценный агентный цикл с несколькими раундами.
3. **Проверка не должна доверять модели.** Verify-агент без доступа к файсам
   отвечал `passed` на этапе, где ничего не изменилось. Проверка перенесена в код.

## Политика самоулучшения

- одно изменение за цикл
- бэкап до правки, откат при регрессии **и при нейтральном результате**
- правка применяется только явным блоком `<<FILE: ... >>`, иначе отклоняется
- после правки модуль обязан импортироваться и компилироваться
- вердикт считается по среднему за 3 прогона, изменение должно превышать шум бенчмарка
- не более одного файла за цикл, файл только из белого списка

## Hermes Agent

Установлен (`ee320cb6c1`) и **работает**: `Hermes Agent v0.21.5+8488.gee320cb`,
Python 3.14.7. Проверено сквозным запросом к локальной модели — ответ за 41 с,
код возврата 0.

Права доступа настроены в `%LOCALAPPDATA%\hermes\config.yaml`: `approvals.mode:
off` плюс `approve` для cron и неинтерактивных запусков, модель — локальный
`Ornith-1.5-9B` через `http://127.0.0.1:8080/v1`. Жёсткие блокировки
деструктивных команд при этом не обходятся.

Запуск:

```powershell
$env:Path = "C:\Users\finni\AppData\Local\hermes\bin;$env:Path"
hermes                      # интерактивно
hermes -z "вопрос"          # один запрос
```

### Найденная причина поломки

Симптом был: `hermes.exe` завершается с кодом 1 без единого слова, а папки тулчейна
(`python`, `node`, `git`, `uv`) выглядят пустыми.

Причина — **не очистка папок, а права доступа**. Инсталлятор запускался с
повышением прав, из-за чего тулчейн оставался читаемым только для администраторов.
Обычный пользователь видел «пустые» папки (доступ запрещён → листинг возвращает
нулевой результат), и `hermes.exe` не мог запустить интерпретатор.

Лечится выдачей прав на чтение и выполнение:

```powershell
powershell -ExecutionPolicy Bypass -File bin\fix-hermes-acl.ps1   # уже выполнено
```

`bin\repair-hermes.ps1` теперь делает это сам и проверяет доступ реальным
запуском `python --version`, а не листингом папок, — поэтому повторный ремонт
больше не нужен и не ломает права заново.

