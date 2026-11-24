# Прогресс по MASTER_IMPLEMENTATION_PLAN (редакция 2)

Дата: 24.11.2026. Ветка `commit-changes`, HEAD `6e897e0` + рабочие изменения.

## Закрыто

### Этап 0 — аудит

`docs/plan_v4_sprint0_audit_2026-11-24.md`: baseline, расхождения, финансовый
конфликт, список ограничений.

### Этап 1A — backend импорта

`app/services/importing/`: изображения, PDF, `details.txt`; дедупликация,
манифест, ошибки. 13 тестов.

### Этап 1B — импорт в вебе

- `POST /api/projects/import` — изображение/PDF/`details.txt`/`.sto`;
- `GET /api/projects/{id}/pages/{index}` — страница, PDF рендерится по требованию;
- `POST /api/projects/{id}/analyze` — распознать выбранную страницу;
- сетка страниц в интерфейсе, выбор страницы;
- `.sto` → `ready_for_recognition=false` (нужна выгрузка `details.txt`);
- уникальные `.tmp` для манифеста (защита от параллельной записи);
- PDF: один `fitz.open` на страницу при рендере, без рендера всего альбома
  при импорте.

### Этап 1C — `FullPipeline` на `ImportedProject`

- `FullPipeline.process_imported(project, max_pages=30)`;
- общий `_process_image_page` для PDF и импортированных страниц;
- тест: `process_imported` не вызывает `render_page` повторно.

### Этап 1D — хранение и backup: **закрыт**

- таблица `web_quotes` (миграция `e7a1c3d5f9b2`);
- таблица `project_statuses` (миграция `b2c4d6e8f0a1`, head): `IMPORTED` →
  `CALCULATED` в БД и манифесте;
- `app/services/web_quote_store.py`: снимок расчёта в БД; `_get_quote`/`_put_quote`
  (кэш + БД), Excel/КП/пересчёт переживают перезапуск;
- `details.json` вынесен из манифеста, `ImportedProject.load_details()`;
- лимиты импорта — `IMPORT_MAX_IMAGE_MB`, `IMPORT_MAX_PDF_MB`;
- `scripts/backup_all.py` — БД + `data/projects/` одной командой;
- проверено восстановление: `furniture_20261124_220839.db`, `integrity_check=ok`,
  `alembic_version=f6a8b0c2d4e6`, таблицы `web_quotes`/`project_statuses`/
  `web_quote_versions` и колонка `validation` на месте.

### Этап 0.5 — эталонные проекты — **закрыт**

- 4 реальные выгрузки ПРО100 как входные фикстуры;
- 22 КП: 19 документов, 122 корректные пары, 110 ×1,10 / 9 ×1,13;
- эталон ценовых коридоров — `tests/fixtures/kp_reference_2026-11-24.json`,
  `app/domain/kp_reference.py`, `tests/test_kp_reference.py`;
- критерий точности — **±10 %** (`app/domain/accuracy.py`).
- Технолог не расписывает каждую деталь (решение 24.11): скриншот PRO100,
  расположение из `details.txt`, кромка везде (видимые 0,8 мм), текстура не крутится.

### Этап 2 — валидация — **закрыт (модель B)**

- `ImportedPage` хранит `room_id`, `room_name`, `view_type`, `view_group`;
  модули наследуют их в `RoomSpec`.
- в вебе у каждой страницы есть выбор помещения, группы проекций и типа проекции
  (`PATCH /api/projects/{id}/pages/{index}`);
- дубли проекций не суммируются: `mark_duplicate_projection_rooms` выбирает
  главную проекцию (фасад → план → разрез → рендер), страницы с совпадением
  состава ≥80 % помечаются `is_duplicate_view` и не создают отдельный расчёт;
- ширина плана сверяется с фасадом: расхождение >10 % даёт предупреждение;
- бот показывает «дубль проекции стр. N, в расчёт не войдёт».
- `scripts/pro100_export.py` делает `screenshot.png` окна PRO100; импорт
  сохраняет снимок и путь в `metadata["screenshot"]`.
- Кромка: правило «везде, на видимых 0,8 мм» соответствует `quantity_calc`
  (`EDGE_VISIBLE_MM=0.8`, `EDGE_INVISIBLE_MM=0.4`).
- Раскрой: текстура не крутится (`grain_locked`), моноцвет можно крутить;
  спорные фактуры — отдельный ручной флаг (не автоматизируем).
- Допуск КП: **±10 %** — `app/domain/accuracy.py`, `tests/test_accuracy.py`.
- Эталон 22 КП: 19 документов, 122 корректные пары, 110 ×1,10 / 9 ×1,13
  (`tests/fixtures/kp_reference_2026-11-24.json`, `app/domain/kp_reference.py`).

- `app/domain/recognition_validation.py`: `RecognizedValue` с источником,
  страницей, confidence, статусом; `PageValidation` с вопросами и блокировками;
- веб отдаёт `validation`, блокирует расчёт при отсутствии размеров и запрещает
  КП при `confidence=low`;
- `FullPipeline.process_imported` и `process` сохраняют `validation` в `RoomSpec`;
- история правок веб-расчёта: `web_quote_versions` (миграция `d4e6f8a0b2c3`),
  версия на распознавание и на каждый «Пересчитать»;
- бот показывает блокирующие вопросы (`format_validation_questions`);
- проверки: дубли модулей, узкие двери, нереальная высота, пересечение bbox,
  слишком много ящиков на высоту;
- `validation` сохраняется в `web_quotes` и `web_quote_versions`; при ручной
  правке источник значений — `manual`, статус — `recognized`;
- тесты `tests/test_recognition_validation.py` (9), `test_bot_validation.py` (2),
  `test_web_quote_versions.py` (2), `test_projection_dedup.py` (4).

Сравнение с деталировкой PRO100 по конкретным деталям — этап 3
(технологический BOM), не этап 2.

## Целевая точность

Решение 24.11.2026: КП считается приемлемым при расхождении **до ±10 %** по
итоговой цене клиента. Проверка — `app/domain/accuracy.py` и `tests/test_accuracy.py`;
ценовые коридоры — 22 КП (`app/domain/kp_reference.py`).

## Этап 3 — технологический BOM — **закрыт 25.11**

Листы, BOM и кромка в цене — по `Part`. Кромка 0,8/0,4/1 мм МДФ
считается по реальным торцам деталей; `PART_BOM_EDGE=1` по умолчанию.

- `app/domain/parts.py`: `Part`, `EdgeOperation`;
- детали: боковины, дно, крыша, полки, перегородки, цоколь, задняя стенка,
  фасады, корпуса ящиков, фасады ящиков, столешница, столы;
- кромка по каждому торцу: видимые 0,8 мм, скрытые 0,4 мм; `edge_totals()`;
- `ModuleBOM.parts` сохраняется в снимке `quote`; у деталей уникальные ID
  (`m1:side`, `m2:side`) и стекло/зеркало/GOLA без кромки;
- `nest_parts()` раскладывает Part по листам; текстура запрещает поворот
  только своей детали;
- `parts_area_m2()`, `parts_by_material()`, `edge_totals()` — площадь и метраж;
- `parts_nesting`, `parts_area_m2`, `parts_edge_totals` сохраняются в результате
  расчёта и снимке `quote`; цена кромки — по `Part`.
- `PART_BOM_PRICING=1` и `PART_BOM_EDGE=1` (по умолчанию) — листы и кромка по `Part`.
- Кромка по `Part`: 0,8 — видимые торцы, 0,4 — скрытые, 1,0 — МДФ-фасады;
  запас 1.3; фасады >2000 — 2 мм.
- Листы и кромка изменили golden-набор: полный `pytest` **776 passed**.
  Кухня 219 738 → 206 676, шкаф 70 858 → 65 295, стол 139 314 → 136 290.
  Это следствие перехода с периметра модулей на реальные торцы деталей.
- Готовые ящики не добавляются в ЛДСП-детали: корпус ящика — только при
  `drawer_boxes=True`; цоколь — только при `has_plinth=True`.
- `parts_from_details()` переводит реальные `details.txt` в `Part`;
- `tests/test_reference_parts.py` — 4 проекта: детали → площадь → раскрой;
  площадь сверена с `docs/pro100_details_summary.json` в допуске ±10 %.
- тесты `tests/test_parts.py` (10).

Пока BOM не используется в цене: `quantity_calc` остаётся единственным
расчётным путём. Присадка/сверление в КП не входят: крепёж и фурнитура
покрываются запасом, точную спецификацию технолог делает после договора.
Из крепежа считаются только позиции прайса/Excel: конфирмат, опоры, подвесы,
цоколь; саморезы/гвозди не выдумываются, если их нет в прайсе
(`tests/test_fasteners.py`).

## Этап 4 — раскрой и закупка — **закрыт 25.11**

- `app/domain/purchase.py`: `PurchaseLine`, `build_purchase_list`;
- листы по материалам из раскроя + кромка по толщинам;
- цены подставляются из выбранного ЛДСП/кромки, ХДФ, МДФ и фасадов;
- в закупке: листы ЛДСП, лист ХДФ, МДФ (если нет фасадов по м²),
  фасады по м², кромка по толщинам;
- список сохраняется в `quote["purchase"]`;
- тест `tests/test_purchase.py`;
- MaxRects / Guillotine / Skyline в `app/domain/nesting_algorithms.py`;
- мультилист и SVG-карты листов;
- `scripts/nesting_benchmark.py` на 4 выгрузках ПРО100;
- 3 детали больше листа попадают в `unplaced` с размерами;
- рабочая смета переключена на **Guillotine** (`nest_parts_guillotine`);
- поворот запрещён текстурированным деталям, однотонные можно крутить;
- MaxRects/Skyline остаются для сравнения, Guillotine — производственный.

## Этап 5 — фурнитура и прайс — **закрыт 25.11**

- у `price_items` появился `sku` (миграция `a8c1e3f5b7d9`, backup
  `furniture_20261125_002027.db`);
- `sku` сохраняется и читается через модель;
- `PriceCatalogItem.sku_or_id` даёт SKU поставщика или `ID-{id}`;
- петли считаются по высоте/весу (`hinges_per_door`), направляющие — по ширине;
- история цен, `pack_size`, `is_active` уже были;
- 23 позиции без цены блокируют КП до счёта поставщика;
- тест `tests/test_price_sku.py`.

## Этап 6 — финансовое ядро — **закрыт 25.11**

- `CostLine.cost_type`: material / hardware / other; сумма групп = сумма строк;
- `cost_type_totals` сохраняется в `quote`, у каждой строки есть `cost_type`;
- сумма групп равна `cost_price` (тест);
- `PricingPolicy.policy_status = "approved"` (решение 24.11);
- `policy_status` сохраняется в снимке рядом с `policy_version`;
- `policy_status=draft` блокирует КП;
- `coerce_policy` больше не пытается сделать `float("approved")`;
- паритет Telegram/Excel/Web: `quote` = результат, сумма строк = `cost_price`;
- тесты `tests/test_cost_types.py`, `tests/test_interface_parity.py`.

## Этап 7 — три комплектации — **закрыт 25.11**

- `kp_variants.build_variants`: `Базовая` / `Стандарт` / `Комфорт`;
- стабильные ID: `base` / `standard` / `comfort`;
- каждый вариант — отдельный `calculate_full_cost` по своим материалам,
  без `×0.8`/`×1.3`;
- конструкция модулей сохраняется, различия — в материалах/фурнитуре;
- тесты `tests/test_kp_variants.py`.

## Этап 8 — веб менеджера — **закрыт 25.11**

- `POST /api/quotes/{id}/approve` — подтверждение состава с автором;
- `GET /api/quotes/{id}/approval` — статус подтверждения;
- КП не выпускается без подтверждения (`409`);
- пересчёт сбрасывает подтверждение;
- версия `approved` пишется в историю;
- кнопка «Подтвердить состав» в веб-интерфейсе;
- `PATCH /api/projects/{id}/status` — статусы `IMPORTED` … `ARCHIVED`;
- `POST /api/recalc/{id}` принимает `materials` и `hardware`;
- тесты `tests/test_web.py`.

## Этап 9 — PDF КП — **закрыт 25.11**

- `app/services/kp_pdf.py` на ReportLab: фирменный PDF, Arial/DejaVu;
- три варианта с наличными/безналом и составом;
- картинки проекта, опции LED/GOLA/наполнение;
- внутри нет себестоимости, маржи и закупочных цен (проверка текста PDF);
- `POST /api/kp-pdf/{id}` с блокировкой без подтверждения;
- DOCX остаётся временным форматом;
- тесты `tests/test_web.py`.

## Этап 10 — надёжность и эксплуатация — **закрыт для локального MVP 25.11**

- backup БД: `backups/furniture-calculator/furniture_20261125_004908.db`,
  12 таблиц, `integrity_check=ok`;
- восстановление проверено на копии: `integrity=ok`, 532 позиции прайса;
- `GET /healthz` — процесс + БД, при сбое БД `503`;
- `PROMPT_VERSION` и логирование `usage` (prompt/completion/total) в vision-запросах;
- защита от второго запуска веб-сервера: `data/web.lock`;
- опциональный `WEB_MANAGER_TOKEN`: при заданном токене API требует
  `X-Manager-Token` (или `Bearer`), иначе `401`;
- тайминг `process_imported` в логе (секунды на проект);
- PostgreSQL и многопользовательские роли — отдельный этап после MVP;
- `data/projects` пока отсутствует, backup проектов не проверен на реальных данных.

## Проверки

| Проверка | Результат |
|---|---|
| `pytest -q` | **776 passed, 1 skipped** |
| `check_phases.py` | 59/59 |
| `ruff check app` | чисто |
| Alembic | `a8c1e3f5b7d9` (head) |

## Следующий шаг

Этапы 1B–1D, 0.5, 2, 3, 4, 5, 6, 7, 8, 9, 10 закрыты для локального MVP.
Отдельно после MVP: PostgreSQL, многопользовательские роли, 23 позиции без цены.
