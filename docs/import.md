# Единый импорт проекта (этап 1)

Один вход для всех источников: изображение, PDF, выгрузка ПРО100. Результат —
`ImportedProject`, с которым работают следующие этапы конвейера.

## Форматы

| Источник | Что принимается | Что получается |
|---|---|---|
| Изображение | `.jpg`, `.jpeg`, `.png`, `.webp`, `.bmp` до 20 МБ | одна страница, исходник сохранён |
| PDF | `.pdf` до 50 МБ | страницы JPEG (первые 100), размеры в мм, встроенные картинки |
| ПРО100, экспорт | каталог или `details.txt` | строки деталировки (`metadata.details`), исходники |
| ПРО100, проект | `.sto` | файл сохранён, `parsed=false`: формат не разобран |

PDF с паролем отклоняется с сообщением «снимите защиту». Битый файл — понятная
ошибка, а не трассировка.

## Хранение

```text
data/projects/<project_id>/
  project.json          манифест (повторный импорт читает его)
  details.json          строки деталировки ПРО100 (если есть)
  source/               исходные файлы + screenshot.png из PRO100
  pages/page-001.jpg    страницы для распознавания
  images/               картинки, извлечённые из PDF
```

`project_id` — первые 16 hex SHA-256 содержимого. Поэтому повторная загрузка
того же файла возвращает тот же проект (`is_duplicate=true`) и не плодит копии.

## CLI

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe scripts\import_project.py "pdfs\альбом.pdf"
.\.venv\Scripts\python.exe scripts\import_project.py "docs\pro100_reports\Кухня"
.\.venv\Scripts\python.exe scripts\import_project.py "input_images\лист.jpg" --storage temp\projects
```

Вывод — JSON: `project_id`, тип, число страниц, сохранённые файлы,
предупреждения.

## Python

```python
import asyncio
from app.services.importing import ProjectImportService

async def main() -> None:
    project = await ProjectImportService().import_project("pdfs/альбом.pdf")
    print(project.project_id, len(project.pages))
    for page in project.pages:
        print(page.index, page.image_path, page.width_mm, page.height_mm)

asyncio.run(main())
```

## Почему пакет называется `importing`

`app/services/import/` из плана нельзя импортировать обычным
`from app.services.import import ...`: `import` — ключевое слово Python.
Поэтому пакет называется `app/services/importing/`; ответственность та же.

## Веб

Веб вызывает тот же сервис:

- `POST /api/projects/import` — загрузка;
- `GET /api/projects/{id}/pages/{index}` — страница (PDF рендерится по требованию);
- `POST /api/projects/{id}/analyze` — распознать выбранную страницу.

## Ограничения

- `.sto` не парсится — нужен проверенный формат или выгрузка `details.txt`;
- `scripts/pro100_export.py` дополнительно делает `screenshot.png` окна PRO100;
  если снимок не удался, выгрузка продолжается без него;
- страницы хранят `room_id`/`room_name`/`view_type`/`view_group` (модель B):
  модули наследуют помещение и проекцию при сборке `RoomSpec`;
- PDF при импорте не рендерится целиком: страница создаётся при открытии,
  поэтому первый показ страницы может занять секунду;
- физический размер изображения берётся по DPI только если DPI правдоподобен,
  иначе `None` (размер нельзя выдумывать);
- `data/projects/` копируется отдельно: `scripts/backup_projects.py`
  (в `scripts/backup_db.py` входит только SQLite).
