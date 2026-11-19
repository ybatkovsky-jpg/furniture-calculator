# План v3 — журнал выполнения (2026-11-17)

Отметки: ✅ закрыто с тестом, 🟡 частично, ⬜ не начато.
Каждая строка подтверждается запуском в этом окружении (дата 17.11.2026).

## G0 — безопасность и точка отсчёта

| Пункт | Статус | Доказательство |
|---|---|---|
| Backup БД | ✅ | `..\backups\furniture-calculator\furniture_20261117_144800.db`, `integrity_check=ok` |
| Проверка восстановления | ✅ | копия развёрнута в temp, 8 таблиц, counts совпали (18/2/122/525/53/0/0/1) |
| Baseline | ✅ | `docs/baseline_2026-11-17.md`: 458 тестов, 59/59, `ruff app` 0, 304/27/18/0 |
| Ветка для правок | ✅ | `plan-v3/stage-a` от `commit-changes` (8de50b2) |
| S0-1 ротация ключа | ⛔ блок | требует письменного разрешения собственника |
| S0-2 очистка истории Git | ⛔ блок | требует письменного разрешения собственника |

## G1 — один пересчёт, скидка, стекло, миграции

| ID | Статус | Что сделано | Тесты |
|---|---|---|---|
| P1-01 | ✅ | `CalculationService` — единственная точка пересчёта; 4 обработчика через сервис; AST-сканер | `test_calculation_service.py`, `test_architecture.py` |
| P0-01 | ✅ | Скидка/наценка/бонус — полный пересчёт, `quote` обновляется атомарно | `test_calculation_service.py` |
| P0-03 | ✅ | Стекло — элемент на каждую дверь, без двойного фасада; смешанное стекло блокирует КП | `test_glass_doors_parity.py` |
| P1-11 | ✅ | Открытие сметы учитывает скидку | `test_discount_survives_reopen` |
| P1-12 | ✅ | `init_db` → `alembic upgrade head` | `test_migrations.py` |
| P2-14 | ✅ | Ш/В ≤ 0 или Г < 0 → `ValueError` | `test_dimension_validation.py` |

## G2 — Excel = чат = КП

| ID | Статус | Что сделано | Тесты |
|---|---|---|---|
| P0-02 | ✅ | `fill_template_from_quote`: строки из `quote.lines` (явное `CostLine.field`), финансы из `quote.totals`; «ПРОЧЕЕ» с ценой вместо чужой строки FIRMAX/ЛЕМАКОМ; площадь 3 знака | `test_excel_parity.py`, `test_excel_parity_scenarios.py` (10 сценариев) |
| P1-14 | ✅ | Кнопка «📊 Скачать Excel»: тот же рендер, openpyxl в потоке, временный файл удаляется | `test_excel_export.py` |
| P1-09 | ✅ | Все количества → `CostLine` (штанги, сушки, лотки, бутылочницы, поддоны, внутренние ящики) | `test_all_quantities_have_lines.py` |
| P1-02 | ✅ | `below_cost` по полным затратам без доставки; `min_margin_rate`; блок КП | `test_below_cost.py` |
| P1-08 | ✅ | Бренд/формат ЛДСП только из позиции ЛДСП; SLOTEX 3,6/2,52 м² | `test_ldsp_brand_isolation.py` |
| P1-04 | ✅ | Петли/ручки 10 типов, `door_system` модуля, ручки = двери + ящики | `test_hinges_handles_types.py` |
| P1-05 | ✅ | МДФ за лист → м² по `sheet_width_mm/height_mm`; без формата — блок КП | `test_mdf_facade_units.py` |
| P1-06 | ✅ | AI-ящики по `drawers.count`/`drawer_counts`/`filling`; выбор системы не перетирается | `test_ai_drawers_selection.py` |
| P1-03 | ✅ | КП из снимка: скидка/наценка/бонус строками, сумма = `final_cash` ±1 ₽ | `test_kp.py` |
| P2-01 | ✅ | Наценка со знаком «+», скидка «−» (входит в P1-03) | `test_kp.py` |

## G3 — снимок, версии, уверенность

| ID | Статус | Что сделано | Тесты |
|---|---|---|---|
| P1-10 | ✅ | `quote` хранит `price_book`/`policy`; сохранённый расчёт не меняется от прайса; `price_mode="current"` — явно; `CalculationVersion` (версии 1,2,3…, reason, created_by) | `test_quote_snapshot.py`, `test_calculation_versions.py` |
| P1-13 | ✅ | `confidence=low` или `quality<0,6` → КП заблокировано, причина в `quote.blocking_reasons`; ручной ввод не блокируется | `test_recognition_gate.py` |
| P2-23 | ✅ | `blocking_reasons` — явный список причин | `test_recognition_gate.py` |
| P2-15 | ✅ | prod требует allowlist (или `ALLOW_ANY_USER=true`); пароль БД не в логе | `test_access_control.py` |
| P2-18 | ✅ | `fonts-dejavu-core` в Dockerfile | `test_access_control.py` |
| P2-22 | ✅ | Legacy в «Моих проектах» — «архив», без ложной цены | `test_project_labels.py` |

## Не начато / осталось

| ID | Статус | Комментарий |
|---|---|---|
| P1-07 | ✅ | Режимы `handles/gola/push/lift` на проекте и модуле; GOLA по ширине/высоте, push по петлям, lift вместо петель; UI с явными кнопками; без цены — missing + блок КП. LED-позиции уже в строках (P1-09); отдельный UI LED — позже | `test_handle_modes.py` |
| P2-02 | ✅ | Промежуточное «ИТОГО» Excel = сумма блока, т.е. `subtotal + delivery_client` (как формула шаблона `=SUM(E347:E353)`); второе «ИТОГО» = блок + прибыль. В ячейке комментарий, что прибыль считается без доставки | `test_excel_parity.py::test_intermediate_total_equals_block_sum_with_delivery` |
| P2-05 | ✅ | `show_projects` больше не пишет `project_id` первого проекта; id ставится при открытии сметы/проекта и проверяется `get_owned_project` | `test_nav_project.py` |
| P2-06 | ✅ | Скидка валидируется в сервисе до записи, `PricingError` ловится в обработчике, БД не меняется | `test_invalid_discount_does_not_change_db` |
| P2-07 | ✅ | Подмена бренда кромки видна: предупреждение в расчёте (кромка ≠ ЛДСП) и сообщение при автоподборе | `test_cost_lines_selection.py` |
| P2-08 | ⬜ | Деактивация прайса/идемпотентность истории — требует P2-09 и миграции на живой БД; отдельный заход с бэкапом |
| P2-09 | ⬜ | Уникальный индекс `(name, category, brand, thickness)` — миграция после дедупликации (P2-08) |
| P2-10 | ✅ | Ящичный корпус без фасадов не получает полку по умолчанию (quantity_calc + BOM); флаги техники уже передаются в `has_*`/вычеты ХДФ | `test_quantity_calc.py::test_drawer_only_module_has_no_default_shelf`, `test_bom.py` |
| P2-11 | ✅ | `nest_bom` учитывает `ModuleBOM.quantity`; фасад ящика делит высоту модуля | `test_nesting.py::test_module_quantity_multiplies_parts`, `test_bom.py` |
| P2-12 | ⬜ | Норматив кромки — нужен технолог (какие торцы/толщины/запас 30 %) |
| P2-16 | ⬜ | Редактор модулей (16–24 ч): экран состава, правка полей, удаление, история версий UI |
| P2-17 | 🟡 | `pytest-cov` подключён (`requirements-dev.txt`, CI `--cov-fail-under=59`), покрытие измерено 59,12 %; старый `test_price_import` переведён на `tmp_path`. Цель 60 % и тесты на `image_analyzer`/`pdf_parser` — после G4 |
| P2-19 | ⬜ | 27 позиций без цены, 18 legacy — решение собственника/данные |
| P2-21 | ⬜ | Налоговая база — бухгалтер |
| P2-24 | ✅ | Цена из списка — первая выбранная позиция, не среднее (было 5350 ₽ из 5100/5600) | `test_cost_lines_selection.py` |
| P2-25 | ⬜ | Деньги в копейках/`Decimal` — миграция 8–16 ч, отдельный этап с бэкапом |
| P1-16 | ⬜ | Финансовая модель — решение собственника (коэффициенты, gross-up, provisional) |
| P1-15 | ⬜ | Операции/нормы — этап 5, нужны данные по труду |

## Проверки на момент записи

```
pytest -q                                    → 581 passed
pytest --cov=app --cov-fail-under=59         → 59.12 % (порог пройден)
check_phases.py                              → 59/59
ruff check app                               → All checks passed
alembic current                              → c9d3e5f7a1b2 (head)
```

## Миграции, применённые к data/furniture.db (каждая после копии БД)

| Ревизия | Что |
|---|---|
| `f5a2c9d1e8b7` | `price_items.sheet_width_mm/sheet_height_mm` (P1-05) |
| `b7e1c4a9d2f3` | `calculations.recognition_confidence/quality_score` (P1-13) |
| `c9d3e5f7a1b2` | таблица `calculation_versions` (P1-10) |

Копии: `furniture_20261117_144800.db`, `_150552`, `_152101`, `_152356`.

## Коммиты (локальные, без push)

```
636b201 chore: baseline 2026-11-17
51640f8 feat(calc): CalculationService (P1-01, P0-01, P1-11, P2-04)
4cbc138 fix(geometry): стекло по дверям, размеры <= 0 (P0-03, P2-14)
561089d feat(db): alembic upgrade head на старте (P1-12)
ed8ff32 fix(calc): полные строки, below_cost, бренд ЛДСП (P1-09, P1-02, P1-08)
3cc8d4b fix(hardware): петли/ручки по типам и door_system (P1-04)
aeaa26a fix(facade): МДФ за лист -> м² (P1-05)
c405b4b fix(drawers): AI-ящики (P1-06)
6fa3d58 docs: статус плана v3 (первый этап)
c1effdb feat(excel): Excel из снимка quote — 10 сценариев (P0-02)
e3fb671 feat(bot): кнопка «Скачать Excel» (P1-14)
9044c62 fix(kp): строки скидки/наценки/бонуса (P1-03, P2-01)
94623b0 feat(recognition): low confidence блокирует КП (P1-13, P2-23)
9be99f7 feat(quote): снимок цен и история версий (P1-10)
7632b0b fix(security): prod-allowlist, маска URL, шрифты, архив (P2-15, P2-18, P2-22)
a0202fd feat(handles): GOLA/push/lift без тупика КП (P1-07)
0683dee docs: статус плана v3 — 560 тестов
```
