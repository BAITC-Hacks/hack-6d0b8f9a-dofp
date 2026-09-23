# Передача B → C: настоящий снимок и общий запуск

23 сентября 2026. Наши изменения находятся в `codex/scoring-rules`.
Прочитанный план команды описывал B на bc1ffad; пакетный запуск, CSV и снимок
были опубликованы уже в 430c6e0. Текущая доработка подключает опубликованный
API C a749d9c и закрывает замечания ревью в зоне B.

## Готово

- `python -m moneygraph run --data ./data --out ./out --serve` вызывает настоящий
  `moneygraph.api.create_app(snapshot.directory)` после проверки SHA-256.
- Manifest сохраняет опубликованную схему B: schema_version="moneygraph.snapshot.v1", run_id,
  period.start/end, rules_version, demo=false. В `artifacts` лежит отображение
  имя файла → SHA-256. graph.json и JSON-массив top_gids принимаются C без адаптера.
- Перед публикацией все восемь артефактов перечитываются и сверяются: роли/ранги,
  покрытия, деньги, число операций, агрегаты кластеров, полный FeatureTable,
  explanations и quality. Дата со временем и дробный тип счётчика отклоняются.
- Hypothesis содержит состав ролей, seed, границу, внутренний и межкластерный
  входящий/исходящий оборот, ключевые узлы. Недостаточные данные обозначены явно.
- Транзакции внутри снимка; исходный --data API больше не нужен.
- Повторное чтение features.parquet принимает numpy-массивы observation_flags.
- Пакетные зависимости и отдельный extra `[serve]` проверены в виртуальном окружении.

Совместно прошли **112 тестов** A+B+C. Проверены настоящий HTTP-процесс --serve,
карточки seed/depth4/изоляты/лидеры, вклады и caps, транзакции, направления связей,
локальный HTML/JS/CSS и побайтовое совпадение всех трёх скачанных CSV.
Файлы A/C не изменены. Код C использовался во временном checkout; merge C не делался.
В финальном прогоне — 36 подслучаев без пропусков; одна сторонняя deprecation
warning Starlette/httpx. Включена новая проверка C всех 2 248 карточек.
Дополнительные 10 тестов графа A e951658 проверены отдельно; расчётный код A
побайтово совпадает с уже подключённым 3651a714.

Проверенный run_id в среде B:
`65b00b7b66581b47969ffa4b3abac6d1ed766a5fabbbce0384598217e6eec45f`.
Локальный каталог: `/workspace/scratch/71717ab1d413/hack-6d0b8f9a-dofp/out/runs/65b00b7b66581b47969ffa4b3abac6d1ed766a5fabbbce0384598217e6eec45f`.
Другая ОС/окружение получает свой run_id по identity; каталог воспроизводится
командой ниже. Файлы этого снимка в Git не добавлены.

## Принять результат без merge

Из своего checkout C, с активным Python 3.12 окружением и локальным data:

```bash
git fetch origin
git worktree add --detach ../moneygraph-b origin/codex/scoring-rules
python -m pip install '../moneygraph-b[serve]'
```

Каталог worktree создаётся один раз; не заменять существующую папку с работой.
Linux/macOS:

```bash
PYTHONPATH=../moneygraph-b:. python -m moneygraph run --data ./data --out ./out --serve
```

Windows PowerShell:

```powershell
$env:PYTHONPATH = "../moneygraph-b;."
python -m moneygraph run --data ./data --out ./out --serve
```

CLI печатает точный каталог `out/runs/<run_id>` и адрес http://127.0.0.1:8000.
Каталог генерируется на машине C: пути из среды B не являются общим сетевым диском.
Готовый снимок также можно открыть так:

```python
from pathlib import Path
from moneygraph.io.snapshots import open_current
from moneygraph.api import create_app

snapshot = open_current(Path("out"))
app = create_app(snapshot.directory)
```

## Что остаётся C / команде

1. Проверить экран в браузере на своём ноутбуке и провести интеграцию веток.
   HTTP-проверка B не подменяет визуальную/интерактивную приёмку C.
2. Новая версия C a749d9c уже закрыла проверку хешей и сверку агрегатов своего
   загрузчика. Использовать именно её или совместимую более новую версию:
   исходный C 6361328d не понимал опубликованное имя схемы B.
3. Принять решение о цели очереди. При замене support на min(support, role_score)
   совпадают **7/20 лидеров**, 19 прежних лидеров имеют cap по ambiguous_roles.
   Текущая очередь отдаёт приоритет исследованию сильных структурных сигналов,
   даже если конкретная роль неоднозначна. Формулу самовольно не меняли.
   [Числа и обоснование](scoring-sensitivity.md).

Настоящие транзакции, клиентские gid и сформированные выгрузки в Git не публикуются.
