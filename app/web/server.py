"""Веб-интерфейс для локальной обкатки: чертёж → смета.

Запуск:
    .\\.venv\\Scripts\\python.exe -m uvicorn app.web.server:app --host 127.0.0.1 --port 8000

Только для локального использования: сервер слушает 127.0.0.1, авторизации нет.
Расчёт идёт тем же путём, что у бота и Excel: analyze_page → calculate_full_cost,
документы берут снимок `quote` (quote_from_result), второго счётчика нет.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import shutil
import sqlite3
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from sqlalchemy import text

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.domain.kp_reference import check_price_corridor
from app.domain.recognition_validation import validate_modules
from app.services.calc_engine import calculate_full_cost
from app.services.calculation_persist import quote_from_result
from app.services.image_analyzer import GeminiImageAnalyzer
from app.services.importing import (
    ImportedProject,
    ProjectImportError,
    ProjectImportService,
    safe_filename,
)
from app.services.kp_docx import build_kp_docx
from app.services.kp_pdf import build_kp_pdf
from app.services.kp_variants import build_variants
from app.services.price_book import load_price_book_sync
from app.services.project_status_store import (
    PROJECT_STATUSES,
    get_project_status,
    is_valid_status,
    set_project_status,
)
from app.services.recognition_cache import module_to_dict
from app.services.template_filler import fill_template_from_quote
from app.services.web_quote_store import load_web_quote, save_web_quote
from app.services.web_quote_versions import (
    list_web_quote_versions,
    save_web_quote_version,
)

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "templates" / "Таблица для расчетов пустая.xlsx"
DB_PATH = ROOT / "data" / "furniture.db"
OUT_DIR = ROOT / "temp" / "web"
INCOMING_DIR = OUT_DIR / "incoming"
PROJECTS_DIR = (
    Path(settings.projects_dir)
    if Path(settings.projects_dir).is_absolute()
    else ROOT / settings.projects_dir
)

# Снимки расчётов: быстрый кэш процесса + постоянное хранение в web_quotes.
QUOTES: dict[str, dict[str, Any]] = {}


async def _get_quote(quote_id: str) -> dict[str, Any] | None:
    """Кэш → БД. После перезапуска сервера ссылка на Excel/КП остаётся живой."""
    data = QUOTES.get(quote_id)
    if data is not None:
        return data
    loaded = await load_web_quote(quote_id)
    if loaded is not None:
        QUOTES[quote_id] = loaded
    return loaded


async def _put_quote(quote_id: str, data: dict[str, Any]) -> None:
    QUOTES[quote_id] = data
    await save_web_quote(quote_id, data)

TYPE_LABELS = {
    "lower_base": "Нижняя база",
    "upper_base": "Верхний модуль",
    "penal": "Пенал",
    "wardrobe": "Шкаф",
    "table": "Стол",
    "counter": "Стойка",
    "open_unit": "Открытая тумба",
    "shelf_unit": "Стеллаж",
    "column": "Колонна",
    "tumbler": "Тумба",
    "corner": "Угловой",
}

app = FastAPI(title="ПРО МЕБЕЛЬ — расчёт", docs_url=None, redoc_url=None)


@app.middleware("http")
async def manager_auth(request: Request, call_next):
    """Опциональный токен менеджера; без токена веб работает только локально."""
    token = (settings.web_manager_token or "").strip()
    if token and request.url.path.startswith("/api/"):
        supplied = request.headers.get("X-Manager-Token", "")
        if not supplied:
            authorization = request.headers.get("Authorization", "")
            if authorization.lower().startswith("bearer "):
                supplied = authorization[7:].strip()
        if not hmac.compare_digest(supplied, token):
            return JSONResponse({"detail": "Нужен токен менеджера"}, status_code=401)
    return await call_next(request)


@app.get("/healthz")
async def healthz() -> JSONResponse:
    """Проверка живости: процесс и БД."""
    database_ok = True
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("select 1"))
    except Exception:  # noqa: BLE001 — health не должен падать
        database_ok = False
    payload = {"status": "ok" if database_ok else "degraded", "database": database_ok}
    return JSONResponse(payload, status_code=200 if database_ok else 503)


def _price_item(connection: sqlite3.Connection, category: str) -> dict | None:
    """Самый дешёвый активный материал категории — значение по умолчанию."""
    row = connection.execute(
        "select * from price_items where is_active=1 and category=? "
        "and unit_price is not null order by unit_price asc limit 1",
        (category,),
    ).fetchone()
    return dict(row) if row else None


def default_materials() -> tuple[dict, dict]:
    """Материалы и фурнитура по умолчанию из прайса.

    MVP: выбор материалов в вебе пока не делается, берём базовые позиции
    из БД, чтобы смета была в рублях, а не нулевая.
    """
    connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        ldsp = _price_item(connection, "ЛДСП")
        edge = _price_item(connection, "Кромка")
        facade = _price_item(connection, "Фасады") or _price_item(connection, "МДФ плиты")
        hinges = _price_item(connection, "Петли")
        drawers = _price_item(connection, "Ящики")
    finally:
        connection.close()

    materials: dict[str, Any] = {}
    if ldsp:
        materials["ldsp"] = {
            "id": ldsp["id"],
            "name": ldsp["name"],
            "price": ldsp["unit_price"],
            "price_unit": ldsp["price_unit"],
            "thickness_mm": ldsp["thickness_mm"],
            "brand": ldsp["brand"],
        }
    if edge:
        materials["edge"] = {
            "id": edge["id"],
            "name": edge["name"],
            "price": edge["unit_price"],
            "price_unit": edge["price_unit"],
        }
    if facade:
        materials["facades"] = {
            "id": facade["id"],
            "name": facade["name"],
            "price": facade["unit_price"],
            "price_unit": facade["price_unit"],
        }

    hardware: dict[str, Any] = {}
    if hinges:
        hardware["hinges"] = {
            "id": hinges["id"],
            "name": hinges["name"],
            "price": hinges["unit_price"],
        }
    if drawers:
        hardware["drawers"] = {
            "id": drawers["id"],
            "name": drawers["name"],
            "price": drawers["unit_price"],
        }
    return materials, hardware


def _module_row(module: Any) -> dict:
    """Строка таблицы: тип по-русски, габариты, двери, ящики."""
    kind = str(getattr(module, "type", "") or "")
    facades = getattr(module, "facades", None) or {}
    drawers = getattr(module, "drawers", None) or {}
    return {
        "type": TYPE_LABELS.get(kind, kind or "модуль"),
        "type_key": kind,
        "width": int(getattr(module, "width", 0) or 0),
        "depth": int(getattr(module, "depth", 0) or 0),
        "height": int(getattr(module, "height", 0) or 0),
        "quantity": int(getattr(module, "quantity", 1) or 1),
        "doors": int(facades.get("count") or 0) if isinstance(facades, dict) else 0,
        "drawers": int(drawers.get("count") or 0) if isinstance(drawers, dict) else 0,
    }


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return INDEX_HTML


UPLOAD_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".pdf", ".sto", ".txt"}


def _project_payload(project: ImportedProject) -> dict[str, Any]:
    """Краткая карточка проекта: без тяжёлых строк деталировки."""
    pages = []
    for page in project.pages:
        can_render = project.source_type == "pdf" and page.source_page is not None
        ready = bool(page.image_path) or can_render
        pages.append(
            {
                "index": page.index,
                "source_page": page.source_page,
                "width_mm": page.width_mm,
                "height_mm": page.height_mm,
                "width_px": page.width_px,
                "height_px": page.height_px,
                "room_id": page.room_id,
                "room_name": page.room_name,
                "view_type": page.view_type,
                "view_group": page.view_group,
                "ready": ready,
                "url": (
                    f"/api/projects/{project.project_id}/pages/{page.index}"
                    if ready
                    else None
                ),
            }
        )
    metadata = project.metadata or {}
    details_count = int(metadata.get("details_count") or 0)
    return {
        "ok": True,
        "project_id": project.project_id,
        "source_type": project.source_type,
        "original_filename": project.original_filename,
        "status": project.status,
        "pages": pages,
        "warnings": list(project.warnings or []),
        "is_duplicate": project.is_duplicate,
        "ready_for_recognition": bool(pages) or details_count > 0,
        "metadata": {
            "format": metadata.get("format"),
            "page_count": metadata.get("page_count", len(project.pages)),
            "details_count": details_count,
            "panels_count": int(metadata.get("panels_count") or 0),
            "parsed": bool(metadata.get("parsed", True)),
        },
    }


async def _save_upload(file: UploadFile) -> Path:
    name = Path(file.filename or "upload.jpg").name
    suffix = Path(name).suffix.lower() or ".jpg"
    if suffix not in UPLOAD_SUFFIXES:
        raise HTTPException(
            400,
            "Поддерживаются JPG, PNG, WEBP, BMP, PDF и выгрузка ПРО100 "
            "(details.txt или .sto)",
        )
    if suffix == ".txt" and name.lower() != "details.txt":
        raise HTTPException(
            400,
            "Текстовый файл поддерживается только как details.txt из ПРО100",
        )

    INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    upload_dir = INCOMING_DIR / uuid.uuid4().hex
    upload_dir.mkdir()
    target = upload_dir / safe_filename(name)
    try:
        target.write_bytes(await file.read())
    except OSError as exc:
        raise HTTPException(500, f"Не удалось сохранить файл: {exc}") from exc
    return target


@app.post("/api/projects/import")
async def project_import(file: UploadFile = File(...)) -> JSONResponse:  # noqa: B008
    """Принять изображение, PDF или выгрузку ПРО100 в единое хранилище."""
    source = await _save_upload(file)
    try:
        project = await ProjectImportService(PROJECTS_DIR).import_project(source)
    except ProjectImportError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        shutil.rmtree(source.parent, ignore_errors=True)
    await set_project_status(project.project_id, project.status)
    return JSONResponse(_project_payload(project))


@app.get("/api/projects/{project_id}/status")
async def project_status(project_id: str) -> JSONResponse:
    status = await get_project_status(project_id)
    return JSONResponse(
        {
            "project_id": project_id,
            "status": status,
            "allowed": list(PROJECT_STATUSES),
        }
    )


@app.patch("/api/projects/{project_id}/status")
async def update_project_status(
    project_id: str,
    payload: dict = Body(default={}),  # noqa: B008
) -> JSONResponse:
    status = str(payload.get("status") or "").upper()
    if not is_valid_status(status):
        raise HTTPException(
            400,
            f"Недопустимый статус: {status}. Разрешены: {', '.join(PROJECT_STATUSES)}",
        )
    if not await set_project_status(project_id, status):
        raise HTTPException(500, "Не удалось сохранить статус проекта")
    return JSONResponse({"ok": True, "project_id": project_id, "status": status})


@app.patch("/api/projects/{project_id}/pages/{page_index}")
async def update_project_page(
    project_id: str,
    page_index: int,
    payload: dict = Body(default={}),  # noqa: B008
) -> JSONResponse:
    """Назначить странице помещение и проекцию (модель B)."""
    service = ProjectImportService(PROJECTS_DIR)
    project = service.store.load(project_id)
    if project is None:
        raise HTTPException(404, "Проект не найден")
    if page_index < 0 or page_index >= len(project.pages):
        raise HTTPException(404, "Страница не найдена")
    page = project.pages[page_index]

    view_type = payload.get("view_type")
    allowed_views = {None, "", "plan", "elevation", "section", "render", "detail"}
    if view_type not in allowed_views:
        raise HTTPException(400, "Неизвестный тип проекции")

    for field_name in ("room_id", "room_name", "view_group"):
        if field_name in payload:
            value = payload.get(field_name)
            text = str(value).strip() if value is not None else ""
            setattr(page, field_name, text[:120] or None)
    if "room_name" in payload and "room_id" not in payload:
        page.room_id = page.room_name
    page.view_type = (str(view_type).strip() or None) if view_type else None
    service.store.save_manifest(project)
    return JSONResponse(
        {
            "ok": True,
            "page": {
                "index": page.index,
                "room_id": page.room_id,
                "room_name": page.room_name,
                "view_type": page.view_type,
                "view_group": page.view_group,
            },
        }
    )


@app.get("/api/projects/{project_id}/pages/{page_index}")
async def project_page(project_id: str, page_index: int) -> FileResponse:
    """Отдать страницу проекта, при необходимости отрендерив её из PDF."""
    service = ProjectImportService(PROJECTS_DIR)
    try:
        page = await asyncio.to_thread(service.ensure_page_image, project_id, page_index)
    except ProjectImportError as exc:
        raise HTTPException(404, str(exc)) from exc
    if not page.image_path:
        raise HTTPException(404, "Страница не отрендерена")
    path = Path(page.image_path).resolve()
    project = service.store.load(project_id)
    storage = Path(project.storage_dir).resolve() if project else None
    if storage is None or not path.is_relative_to(storage):
        raise HTTPException(403, "Некорректный путь страницы")
    if not path.is_file():
        raise HTTPException(404, "Файл страницы отсутствует")
    media = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return FileResponse(path, media_type=media)


async def _analyze_image_path(
    image_path: Path,
    *,
    project_id: str | None = None,
    page_index: int = 0,
    pages_total: int = 1,
) -> dict[str, Any]:
    """Распознать одну страницу и посчитать смету; без HTTP-обёртки."""
    analyzer = GeminiImageAnalyzer()
    try:
        modules, zone_type, _materials, confidence, meta = await analyzer.analyze_page(
            image_path, return_meta=True
        )
    except Exception as exc:
        logger.exception("Распознавание не удалось")
        raise HTTPException(502, f"Распознавание не удалось: {exc}") from exc
    finally:
        await analyzer.close()

    if not modules:
        flags = list(meta.get("quality_flags") or [])
        return {
            "ok": False,
            "reason": flags[0] if flags else "модули не найдены",
            "flags": flags,
            "quality_score": meta.get("quality_score"),
        }

    validation = validate_modules(
        modules,
        page_index=page_index,
        confidence=confidence,
        meta=meta,
    )
    if not validation.can_calculate:
        return {
            "ok": False,
            "reason": validation.blocking[0],
            "flags": validation.blocking,
            "questions": validation.questions,
            "validation": validation.as_dict(),
            "quality_score": meta.get("quality_score"),
        }

    materials, hardware = default_materials()
    module_dicts = [module_to_dict(module) for module in modules]
    result = calculate_full_cost(
        module_dicts,
        materials,
        hardware,
        [],
        zone_type=zone_type,
        price_book=load_price_book_sync(),
    )
    quote = quote_from_result(result, reason="web")
    quote_id = uuid.uuid4().hex
    await _put_quote(
        quote_id,
        {
            "quote": quote,
            "modules": module_dicts,
            "validation": validation.as_dict(),
            "zone": zone_type,
            "image": str(image_path),
            "project_id": project_id,
            "page_index": page_index,
        },
    )
    await save_web_quote_version(
        quote_id,
        {
            "quote": quote,
            "modules": module_dicts,
            "validation": validation.as_dict(),
        },
        reason="initial",
    )

    breakdown = result.cost_breakdown
    flags = list(meta.get("quality_flags") or []) + list(validation.warnings)
    corridor = check_price_corridor(
        float(breakdown.final_cash),
        str(zone_type or "Прочее"),
    )
    if corridor.get("ok") is False:
        flags.append(f"Цена вне коридора 22 КП: {corridor['reason']}")
    can_issue = bool(result.can_issue_kp) and str(confidence).lower() != "low"
    payload: dict[str, Any] = {
        "ok": True,
        "quote_id": quote_id,
        "zone": zone_type,
        "confidence": confidence,
        "total_width_mm": int(meta.get("total_width_mm") or 0)
        or sum(row["width"] for row in (_module_row(m) for m in modules)),
        "doors": int(meta.get("door_count") or 0),
        "drawers": int(meta.get("drawer_count") or 0),
        "modules": [_module_row(module) for module in modules],
        "totals": {
            "cost_price": round(breakdown.cost_price),
            "subtotal": round(breakdown.subtotal),
            "final_cash": round(breakdown.final_cash),
            "final_noncash": round(result.total_noncash),
        },
        "flags": flags,
        "quality_score": meta.get("quality_score"),
        "can_issue_kp": can_issue,
        "missing_prices": list(result.missing_prices or []),
        "validation": validation.as_dict(),
    }
    if project_id:
        payload["project_id"] = project_id
        payload["page_index"] = page_index
        payload["pages_total"] = pages_total
    return payload


@app.post("/api/projects/{project_id}/analyze")
async def analyze_project_page(
    project_id: str,
    payload: dict = Body(default={}),  # noqa: B008
) -> JSONResponse:
    """Распознать выбранную страницу импортированного проекта."""
    service = ProjectImportService(PROJECTS_DIR)
    project = service.store.load(project_id)
    if project is None:
        raise HTTPException(404, "Проект не найден")
    try:
        page_index = int(payload.get("page_index") or 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "page_index должен быть числом") from exc
    if page_index < 0 or page_index >= len(project.pages):
        raise HTTPException(404, "Страница не найдена")
    try:
        page = await asyncio.to_thread(service.ensure_page_image, project_id, page_index)
    except ProjectImportError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not page.image_path:
        raise HTTPException(
            400,
            "Страница не отрендерена. Загрузите PDF заново или выберите другую страницу",
        )
    data = await _analyze_image_path(
        Path(page.image_path),
        project_id=project_id,
        page_index=page_index,
        pages_total=len(project.pages),
    )
    if data.get("ok"):
        project.status = "CALCULATED"
        service.store.save_manifest(project)
        await set_project_status(project_id, project.status)
        data["project_status"] = project.status
    return JSONResponse(data)


@app.post("/api/analyze")
async def analyze(file: UploadFile = File(...)) -> JSONResponse:  # noqa: B008
    """Принять изображение, распознать состав и посчитать смету (старый путь)."""
    suffix = Path(file.filename or "upload.jpg").suffix.lower() or ".jpg"
    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
        raise HTTPException(400, "Поддерживаются изображения: JPG, PNG, WEBP, BMP")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = OUT_DIR / f"{uuid.uuid4().hex}{suffix}"
    try:
        temp_path.write_bytes(await file.read())
    except OSError as exc:
        raise HTTPException(500, f"Не удалось сохранить файл: {exc}") from exc

    data = await _analyze_image_path(temp_path)
    return JSONResponse(data)



@app.get("/api/quotes/{quote_id}/versions")
async def quote_versions(quote_id: str) -> JSONResponse:
    """История редакций состава для веб-расчёта."""
    return JSONResponse({"versions": await list_web_quote_versions(quote_id)})


@app.get("/api/quotes/{quote_id}/approval")
async def quote_approval(quote_id: str) -> JSONResponse:
    data = await _get_quote(quote_id)
    if not data:
        raise HTTPException(404, "Расчёт не найден")
    approval = (data.get("quote") or {}).get("approval") or {}
    return JSONResponse({"approval": approval, "approved": bool(approval)})


@app.post("/api/quotes/{quote_id}/approve")
async def approve_quote(
    quote_id: str,
    payload: dict = Body(default={}),  # noqa: B008
) -> JSONResponse:
    """Подтвердить состав перед выпуском КП."""
    data = await _get_quote(quote_id)
    if not data:
        raise HTTPException(404, "Расчёт не найден")
    approved_by = str(payload.get("approved_by") or "manager").strip()[:120]
    approval = {
        "approved_by": approved_by,
        "approved_at": datetime.now(UTC).isoformat(),
    }
    data.setdefault("quote", {})["approval"] = approval
    await _put_quote(quote_id, data)
    await save_web_quote_version(
        quote_id,
        {
            "quote": data["quote"],
            "modules": data.get("modules") or [],
            "validation": data.get("validation") or {},
        },
        reason="approved",
        created_by=approved_by,
    )
    return JSONResponse({"ok": True, "approval": approval})


@app.get("/api/excel/{quote_id}")
async def excel(quote_id: str) -> FileResponse:
    """Excel из снимка расчёта — тот же путь, что у бота."""
    data = await _get_quote(quote_id)
    if not data:
        raise HTTPException(404, "Расчёт не найден — распознайте заново")
    quote = data["quote"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    output = OUT_DIR / f"расчёт-{quote_id[:8]}.xlsx"
    fill_template_from_quote(quote, str(TEMPLATE), str(output))
    return FileResponse(
        output,
        filename="Расчёт.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _company_block() -> dict:
    """Шапка КП: название, слоган, реквизиты, контакты."""
    return {
        "name": "ПРОМЕБЕЛЬ",
        "tagline": "качество и индивидуальность",
        "phone": settings.company_phone,
        "email": settings.company_email,
        "address": settings.company_address,
        "logo_path": (ROOT / settings.company_logo_path) if settings.company_logo_path else None,
        "requisites": [
            settings.company_legal_name,
            settings.company_legal_address,
            f"ИНН {settings.company_inn}",
            f"р/с {settings.company_account}",
            f"Банк {settings.company_bank}",
            f"БИК {settings.company_bik}",
            f"кор/с {settings.company_kor_account}",
        ],
    }


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


@app.post("/api/kp/{quote_id}")
async def kp_docx(
    quote_id: str,
    payload: str = Form("{}"),
    images: list[UploadFile] = File(default=[]),  # noqa: B008 — идиома FastAPI
) -> FileResponse:
    """Собрать КП в DOCX: варианты, опции и картинки мебели из проекта."""
    data = await _get_quote(quote_id)
    if not data:
        raise HTTPException(404, "Расчёт не найден — распознайте заново")
    if not (data.get("quote") or {}).get("approval"):
        raise HTTPException(409, "Сначала подтвердите состав кнопкой «Подтвердить состав»")

    try:
        payload_data = json.loads(payload or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"Некорректные параметры КП: {exc}") from exc

    names = payload_data.get("variants") or ["Базовая", "Стандарт", "Комфорт"]
    options = {
        "gola": bool(payload_data.get("gola")),
        "led": bool(payload_data.get("led")),
        "accessories": bool(payload_data.get("accessories")),
    }
    discount = float(payload_data.get("discount") or 0)
    client = str(payload_data.get("client") or "")
    validity_days = int(
        payload_data.get("validity_days") or settings.company_kp_validity_days
    )
    with_image = bool(payload_data.get("image", False))

    gallery: list[str] = []
    if images:
        images_dir = OUT_DIR / f"images-{quote_id[:8]}"
        images_dir.mkdir(parents=True, exist_ok=True)
        for index, upload in enumerate(images[:6], start=1):
            suffix = Path(upload.filename or "photo.jpg").suffix.lower()
            if suffix not in IMAGE_SUFFIXES:
                continue
            target = images_dir / f"{index:02}{suffix}"
            target.write_bytes(await upload.read())
            gallery.append(str(target))

    variants = build_variants(
        data["modules"],
        zone_type=data.get("zone"),
        options=options,
        discount_percent=discount,
    )
    variants = [variant for variant in variants if variant.name in names]
    if not variants:
        raise HTTPException(400, "Не выбран ни один вариант КП")

    options_text = []
    if options["led"]:
        options_text.append("Светодиодная подсветка рабочих зон")
    if options["gola"]:
        options_text.append("Профиль GOLA вместо ручек")
    if options["accessories"]:
        options_text.append("Внутреннее наполнение: сушка, лоток, поддон")

    conditions = [
        "Срок изготовления — 30 рабочих дней с момента согласования",
        "Предоплата 50 %, остаток при приёмке",
        f"Предложение действительно {validity_days} дней",
        "Замер, доставка и монтаж включены в стоимость",
        "Гарантия на корпусную мебель — 24 месяца",
    ]

    today = date.today()
    document = build_kp_docx(
        variants=variants,
        company=_company_block(),
        kp_date=today,
        valid_until=today + timedelta(days=validity_days),
        client=client,
        options_text=options_text,
        conditions=conditions,
        image_path=data.get("image") if with_image else None,
        images=gallery,
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    output = OUT_DIR / f"КП-{quote_id[:8]}.docx"
    output.write_bytes(document)
    return FileResponse(
        output,
        filename="Коммерческое предложение.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


ALLOWED_TYPES = {
    "lower_base", "upper_base", "penal", "wardrobe", "table", "counter",
    "open_unit", "shelf_unit", "column", "tumbler", "corner",
}


@app.post("/api/kp-pdf/{quote_id}")
async def kp_pdf(
    quote_id: str,
    payload: str = Form("{}"),
    images: list[UploadFile] = File(default=[]),  # noqa: B008
) -> FileResponse:
    """Фирменный PDF из того же снимка и вариантов, что DOCX."""
    data = await _get_quote(quote_id)
    if not data:
        raise HTTPException(404, "Расчёт не найден — распознайте заново")
    if not (data.get("quote") or {}).get("approval"):
        raise HTTPException(409, "Сначала подтвердите состав кнопкой «Подтвердить состав»")
    try:
        payload_data = json.loads(payload or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"Некорректные параметры КП: {exc}") from exc

    names = payload_data.get("variants") or ["Базовая", "Стандарт", "Комфорт"]
    options = {
        "gola": bool(payload_data.get("gola")),
        "led": bool(payload_data.get("led")),
        "accessories": bool(payload_data.get("accessories")),
    }
    variants = [
        variant
        for variant in build_variants(
            data["modules"],
            zone_type=data.get("zone"),
            options=options,
            discount_percent=float(payload_data.get("discount") or 0),
        )
        if variant.name in names
    ]
    if not variants:
        raise HTTPException(400, "Не выбран ни один вариант КП")

    gallery: list[str] = []
    if images:
        images_dir = OUT_DIR / f"pdf-images-{quote_id[:8]}"
        images_dir.mkdir(parents=True, exist_ok=True)
        for index, upload in enumerate(images[:6], start=1):
            suffix = Path(upload.filename or "photo.jpg").suffix.lower()
            if suffix not in IMAGE_SUFFIXES:
                continue
            target = images_dir / f"{index:02}{suffix}"
            target.write_bytes(await upload.read())
            gallery.append(str(target))

    options_text = []
    if options["led"]:
        options_text.append("Светодиодная подсветка рабочих зон")
    if options["gola"]:
        options_text.append("Профиль GOLA")
    if options["accessories"]:
        options_text.append("Наполнение: сушка, лоток, поддон")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    output = OUT_DIR / f"КП-{quote_id[:8]}.pdf"
    build_kp_pdf(
        output,
        client=str(payload_data.get("client") or ""),
        variants=variants,
        company=_company_block(),
        validity_days=int(
            payload_data.get("validity_days") or settings.company_kp_validity_days
        ),
        options_text=options_text,
        images=gallery,
    )
    return FileResponse(output, filename="Коммерческое предложение.pdf",
                        media_type="application/pdf")


def _validate_modules(modules: Any) -> list[dict]:
    """Проверить состав, который менеджер правит в редакторе.

    Ошибки возвращаем списком, а не молча подставляем значения: смета
    не должна считаться по нулевым или отрицательным размерам.
    """
    if not isinstance(modules, list) or not modules:
        raise HTTPException(400, "Состав пустой — добавьте хотя бы один модуль")

    cleaned: list[dict] = []
    for index, module in enumerate(modules, start=1):
        if not isinstance(module, dict):
            raise HTTPException(400, f"Модуль #{index}: неверный формат")
        kind = str(module.get("type") or "")
        if kind not in ALLOWED_TYPES:
            raise HTTPException(400, f"Модуль #{index}: неизвестный тип «{kind}»")
        try:
            width = int(module.get("width") or 0)
            depth = int(module.get("depth") or 0)
            height = int(module.get("height") or 0)
            quantity = int(module.get("quantity") or 1)
            doors = int((module.get("facades") or {}).get("count") or 0)
            drawers = int((module.get("drawers") or {}).get("count") or 0)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, f"Модуль #{index}: размеры должны быть числами") from exc

        if width <= 0 or height <= 0:
            raise HTTPException(400, f"Модуль #{index}: ширина и высота должны быть больше нуля")
        if depth < 0 or quantity < 1 or doors < 0 or drawers < 0:
            raise HTTPException(400, f"Модуль #{index}: проверьте глубину, количество, двери и ящики")
        if width > 6000 or height > 3500:
            raise HTTPException(400, f"Модуль #{index}: размеры выглядят нереально ({width}×{height})")

        cleaned.append({
            "type": kind,
            "width": width,
            "depth": depth,
            "height": height,
            "quantity": quantity,
            "facades": {"count": doors},
            "drawers": {"count": drawers},
            "is_corner": bool(module.get("is_corner")),
        })
    return cleaned


@app.post("/api/recalc/{quote_id}")
async def recalc(quote_id: str, payload: dict = Body(default={})) -> JSONResponse:  # noqa: B008
    """Пересчитать смету по составу, отредактированному в вебе."""
    data = await _get_quote(quote_id)
    if not data:
        raise HTTPException(404, "Расчёт не найден — распознайте заново")

    modules = _validate_modules(payload.get("modules"))
    default_materials_, default_hardware = default_materials()
    materials = payload.get("materials") or default_materials_
    hardware = payload.get("hardware") or default_hardware
    if not isinstance(materials, dict) or not isinstance(hardware, dict):
        raise HTTPException(400, "Материалы и фурнитура должны быть объектами")
    result = calculate_full_cost(
        modules, materials, hardware, [], zone_type=data.get("zone"),
        price_book=load_price_book_sync(),
    )
    breakdown = result.cost_breakdown

    # Обновляем снимок: Excel и КП должны взять исправленный состав.
    data["modules"] = modules
    data["quote"] = quote_from_result(result, reason="web-edit")
    data["validation"] = validate_modules(
        modules,
        confidence="high",
        source_type="manual",
    ).as_dict()
    await _put_quote(quote_id, data)
    await save_web_quote_version(
        quote_id,
        {
            "quote": data["quote"],
            "modules": modules,
            "validation": data["validation"],
        },
        reason="web-edit",
        created_by="manager",
    )

    doors = sum(module["facades"]["count"] * module["quantity"] for module in modules)
    drawers = sum(module["drawers"]["count"] * module["quantity"] for module in modules)
    rows = []
    for module in modules:
        facades = module.get("facades") or {}
        module_drawers = module.get("drawers") or {}
        rows.append({
            "type": TYPE_LABELS.get(module["type"], module["type"]),
            "type_key": module["type"],
            "width": module["width"],
            "depth": module["depth"],
            "height": module["height"],
            "quantity": module["quantity"],
            "doors": int(facades.get("count") or 0),
            "drawers": int(module_drawers.get("count") or 0),
        })
    return JSONResponse({
        "ok": True,
        "modules": rows,
        "doors": doors,
        "drawers": drawers,
        "total_width_mm": sum(module["width"] for module in modules if module["type"] != "upper_base"),
        "totals": {
            "cost_price": round(breakdown.cost_price),
            "subtotal": round(breakdown.subtotal),
            "final_cash": round(breakdown.final_cash),
            "final_noncash": round(result.total_noncash),
        },
        "can_issue_kp": bool(result.can_issue_kp),
        "missing_prices": list(result.missing_prices or []),
        "validation": data["validation"],
    })


INDEX_HTML = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>ПРО МЕБЕЛЬ — расчёт по чертежу</title>
<style>
  :root { color-scheme: light; }
  body { margin:0; font-family: "Segoe UI", system-ui, sans-serif; background:#f4f5f7; color:#1d2330; }
  header { background:#1d2330; color:#fff; padding:18px 28px; }
  header h1 { margin:0; font-size:18px; font-weight:600; letter-spacing:.2px; }
  header span { color:#9aa4b5; font-size:13px; margin-left:10px; }
  main { max-width:980px; margin:26px auto; padding:0 20px; }
  .drop { background:#fff; border:2px dashed #c3cad6; border-radius:12px; padding:34px;
          text-align:center; transition:.15s; cursor:pointer; }
  .drop.hover { border-color:#2f6fed; background:#f0f5ff; }
  .drop p { margin:6px 0; color:#5b6472; }
  .drop strong { font-size:16px; color:#1d2330; }
  .hidden { display:none; }
  .spinner { width:22px; height:22px; border:3px solid #d6dce6; border-top-color:#2f6fed;
             border-radius:50%; animation:spin .8s linear infinite; display:inline-block;
             vertical-align:middle; margin-right:10px; }
  @keyframes spin { to { transform:rotate(360deg); } }
  .card { background:#fff; border-radius:12px; padding:22px 24px; margin-top:20px;
          box-shadow:0 1px 3px rgba(20,30,50,.08); }
  .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; }
  .kpi { background:#f7f9fc; border-radius:10px; padding:14px 16px; }
  .kpi b { display:block; font-size:22px; margin-top:4px; }
  .kpi span { color:#6b7484; font-size:12px; text-transform:uppercase; letter-spacing:.4px; }
  table { width:100%; border-collapse:collapse; margin-top:14px; font-size:14px; }
  th, td { text-align:left; padding:9px 10px; border-bottom:1px solid #eceff4; }
  th { color:#6b7484; font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.4px; }
  td.num, th.num { text-align:right; font-variant-numeric:tabular-nums; }
  .flag { background:#fff6e5; border-left:4px solid #e0a02a; padding:10px 14px;
          border-radius:8px; margin-top:10px; font-size:14px; }
  .button { display:inline-block; margin-top:16px; margin-right:10px; padding:11px 20px;
            background:#2f6fed; color:#fff; border:0; border-radius:9px; font-size:14px;
            cursor:pointer; text-decoration:none; }
  .button.secondary { background:#e8edf6; color:#243044; }
  .error { background:#fdecec; border-left:4px solid #d64545; padding:12px 16px;
           border-radius:8px; margin-top:16px; }
  .muted { color:#6b7484; font-size:13px; }
  .pages { display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:12px; margin-top:12px; }
  .page-button { border:1px solid #d6dce6; background:#fff; border-radius:10px; padding:8px;
                 cursor:pointer; text-align:left; }
  .page-button:hover { border-color:#2f6fed; }
  .page-button img { width:100%; height:110px; object-fit:contain; background:#f7f9fc;
                     border-radius:6px; display:block; }
  .page-button span { display:block; margin-top:6px; font-size:12px; color:#5b6472; }
  .page-meta { display:grid; grid-template-columns:1fr; gap:4px; margin-top:6px; }
  .page-meta input, .page-meta select { width:100%; box-sizing:border-box; padding:4px 6px;
    border:1px solid #d6dce6; border-radius:6px; font-size:12px; }
</style>
</head>
<body>
<header><h1>ПРО МЕБЕЛЬ<span>расчёт по чертежу</span></h1></header>
<main>
  <div id="drop" class="drop">
    <p><strong>Перетащите чертёж сюда</strong></p>
    <p>или нажмите, чтобы выбрать файл</p>
    <p class="muted">JPG, PNG, WEBP, BMP, PDF или выгрузка ПРО100</p>
    <input id="file" type="file" accept="image/*,.pdf,.sto,.txt" class="hidden">
  </div>

  <div id="status" class="card hidden"><span class="spinner"></span><span id="status-text">Распознаю…</span></div>
  <div id="error" class="error hidden"></div>

  <div id="pages-card" class="card hidden">
    <h3 style="margin:0 0 4px;font-size:15px">Страницы проекта</h3>
    <p id="pages-hint" class="muted" style="margin:0"></p>
    <div id="pages" class="pages"></div>
  </div>

  <div id="result" class="hidden">
    <div class="card">
      <div class="cards" id="kpi"></div>
      <div id="flags"></div>
    </div>
    <div class="card">
      <h3 style="margin:0 0 4px;font-size:15px">Состав</h3>
      <p class="muted" style="margin:0 0 6px">Состав можно править: измените размеры,
        двери или ящики и нажмите «Пересчитать». Excel и КП возьмут исправленный состав.</p>
      <table>
        <thead><tr>
          <th>Модуль</th><th class="num">Ш</th><th class="num">Г</th><th class="num">В</th>
          <th class="num">Корпусов</th><th class="num">Двери</th><th class="num">Ящики</th>
          <th></th>
        </tr></thead>
        <tbody id="modules"></tbody>
      </table>
      <div style="margin-top:10px">
        <button class="button secondary" id="add-module">+ Добавить модуль</button>
        <button class="button" id="recalc" style="background:#1d2330">Пересчитать</button>
        <button class="button" id="approve">Подтвердить состав</button>
        <span id="approval-status" class="muted"></span>
      </div>
      <button class="button" id="excel">Скачать Excel</button>
      <button class="button secondary" id="reset">Новый расчёт</button>
      <div id="versions" class="muted" style="margin-top:10px"></div>

      <div style="margin-top:22px;border-top:1px solid #eceff4;padding-top:16px">
        <h3 style="margin:0 0 10px;font-size:15px">Коммерческое предложение</h3>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px">
          <label class="muted">Заказчик<br><input id="kp-client" type="text" placeholder="ФИО или объект" style="width:100%;padding:7px;border:1px solid #d6dce6;border-radius:7px"></label>
          <label class="muted">Скидка, %<br><input id="kp-discount" type="number" min="0" max="30" value="0" style="width:100%;padding:7px;border:1px solid #d6dce6;border-radius:7px"></label>
          <label class="muted">Действует, дней<br><input id="kp-validity" type="number" min="1" max="90" value="14" style="width:100%;padding:7px;border:1px solid #d6dce6;border-radius:7px"></label>
        </div>
        <div style="margin-top:10px" class="muted">
          <label><input type="checkbox" class="kp-variant" value="Базовая" checked> Базовая</label>
          <label style="margin-left:14px"><input type="checkbox" class="kp-variant" value="Стандарт" checked> Стандарт</label>
          <label style="margin-left:14px"><input type="checkbox" class="kp-variant" value="Комфорт" checked> Комфорт</label>
        </div>
        <div style="margin-top:8px" class="muted">
          <label><input type="checkbox" id="kp-led"> LED-подсветка</label>
          <label style="margin-left:14px"><input type="checkbox" id="kp-gola"> Профиль GOLA</label>
          <label style="margin-left:14px"><input type="checkbox" id="kp-accessories"> Наполнение (сушка, лоток, поддон)</label>
        </div>
        <div style="margin-top:10px">
          <label class="muted">Картинки мебели для КП (рендеры, фото из проекта)<br>
            <input id="kp-images" type="file" accept="image/*" multiple style="margin-top:6px">
          </label>
          <div id="kp-preview" style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px"></div>
        </div>
        <div style="margin-top:8px" class="muted">
          <label><input type="checkbox" id="kp-image"> Добавить эскиз чертежа</label>
        </div>
        <button class="button" id="kp-download" style="margin-top:12px">Скачать КП (Word)</button>
        <button class="button secondary" id="kp-pdf" style="margin-top:12px">Скачать КП (PDF)</button>
      </div>
    </div>
  </div>
</main>
<script>
const nativeFetch = window.fetch.bind(window);
window.fetch = async (input, init = {}) => {
  const headers = new Headers(init.headers || {});
  const token = localStorage.getItem('managerToken');
  if (token) headers.set('X-Manager-Token', token);
  const response = await nativeFetch(input, { ...init, headers });
  if (response.status === 401) {
    const entered = prompt('Введите токен менеджера');
    if (entered) {
      localStorage.setItem('managerToken', entered.trim());
      location.reload();
    }
  }
  return response;
};
const drop = document.getElementById('drop');
const fileInput = document.getElementById('file');
const status = document.getElementById('status');
const statusText = document.getElementById('status-text');
const errorBox = document.getElementById('error');
const result = document.getElementById('result');
const pagesCard = document.getElementById('pages-card');
const pagesBox = document.getElementById('pages');
const pagesHint = document.getElementById('pages-hint');
let quoteId = null;
let projectId = null;
let currentModules = [];

const TYPE_OPTIONS = [
  ["lower_base", "Нижняя база"], ["upper_base", "Верхний модуль"], ["penal", "Пенал"],
  ["corner", "Угловой модуль"], ["wardrobe", "Шкаф"], ["open_unit", "Открытая тумба"],
  ["shelf_unit", "Стеллаж"], ["table", "Стол"], ["counter", "Стойка"],
  ["tumbler", "Тумба"], ["column", "Колонна"],
];

function numberCell(index, field, value) {
  return `<td class="num"><input class="cell-input" type="number" min="0"
    data-index="${index}" data-field="${field}" value="${value}"></td>`;
}

function renderModules() {
  document.getElementById('modules').innerHTML = currentModules.map((module, index) => {
    const options = TYPE_OPTIONS.map(([key, label]) =>
      `<option value="${key}"${module.type_key === key ? " selected" : ""}>${label}</option>`
    ).join('');
    return `<tr>
      <td><select class="cell-input" data-index="${index}" data-field="type_key">${options}</select></td>
      ${numberCell(index, 'width', module.width)}
      ${numberCell(index, 'depth', module.depth)}
      ${numberCell(index, 'height', module.height)}
      ${numberCell(index, 'quantity', module.quantity)}
      ${numberCell(index, 'doors', module.doors)}
      ${numberCell(index, 'drawers', module.drawers)}
      <td class="num"><button class="row-remove" data-index="${index}"
        title="Удалить" style="border:0;background:none;cursor:pointer;color:#d64545">✕</button></td>
    </tr>`;
  }).join('');
}

function collectModules() {
  return currentModules.map(module => ({
    type: module.type_key,
    width: Number(module.width) || 0,
    depth: Number(module.depth) || 0,
    height: Number(module.height) || 0,
    quantity: Number(module.quantity) || 1,
    facades: { count: Number(module.doors) || 0 },
    drawers: { count: Number(module.drawers) || 0 },
  }));
}

drop.addEventListener('click', () => fileInput.click());
drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('hover'); });
drop.addEventListener('dragleave', () => drop.classList.remove('hover'));
drop.addEventListener('drop', e => {
  e.preventDefault(); drop.classList.remove('hover');
  if (e.dataTransfer.files.length) upload(e.dataTransfer.files[0]);
});
fileInput.addEventListener('change', () => { if (fileInput.files.length) upload(fileInput.files[0]); });

function money(value) {
  return new Intl.NumberFormat('ru-RU').format(Math.round(value)) + ' ₽';
}

async function upload(file) {
  errorBox.classList.add('hidden');
  result.classList.add('hidden');
  pagesCard.classList.add('hidden');
  status.classList.remove('hidden');
  statusText.textContent = 'Загружаю проект…';

  const form = new FormData();
  form.append('file', file);
  try {
    const response = await fetch('/api/projects/import', { method: 'POST', body: form });
    const project = await response.json();
    if (!response.ok) throw new Error(project.detail || 'Не удалось импортировать проект');
    if (!project.ready_for_recognition) {
      status.classList.add('hidden');
      errorBox.textContent = (project.warnings || []).join(' ') ||
        'Проект сохранён, но автоматический разбор этого формата не поддерживается.';
      errorBox.classList.remove('hidden');
      return;
    }
    projectId = project.project_id;
    renderProject(project);
    if (project.pages.length === 1) {
      await analyzePage(projectId, 0);
    } else {
      status.classList.add('hidden');
      pagesCard.classList.remove('hidden');
    }
  } catch (err) {
    status.classList.add('hidden');
    errorBox.textContent = err.message || 'Не удалось связаться с сервером';
    errorBox.classList.remove('hidden');
  }
}

function renderProject(project) {
  const pages = project.pages || [];
  pagesHint.textContent = pages.length
    ? `Найдено страниц: ${pages.length}. Выберите страницу для распознавания.`
    : 'Страниц нет: проект содержит только деталировку.';
  pagesBox.innerHTML = pages.map(page => {
    const preview = page.url
      ? `<img src="${page.url}" alt="Страница ${page.index + 1}" loading="lazy">`
      : '<div class="muted" style="height:110px">нет изображения</div>';
    const viewOptions = [
      ['', 'проекция?'], ['plan', 'план'], ['elevation', 'фасад'],
      ['section', 'разрез'], ['render', 'рендер'], ['detail', 'деталь'],
    ].map(([value, label]) =>
      `<option value="${value}"${(page.view_type || '') === value ? ' selected' : ''}>${label}</option>`
    ).join('');
    return `<div class="page-card" data-page="${page.index}">
      <button class="page-button" data-page="${page.index}" ${page.ready ? '' : 'disabled'}>
        ${preview}<span>Страница ${page.index + 1}${page.width_mm ? ` · ${Math.round(page.width_mm)}×${Math.round(page.height_mm)} мм` : ''}</span>
      </button>
      <div class="page-meta">
        <input class="page-room" placeholder="Помещение" value="${escapeHtml(page.room_name || '')}">
        <input class="page-group" placeholder="Группа проекций" value="${escapeHtml(page.view_group || '')}">
        <select class="page-view">${viewOptions}</select>
      </div>
    </div>`;
  }).join('');
  pagesBox.querySelectorAll('.page-button').forEach(button => {
    button.addEventListener('click', () => analyzePage(projectId, Number(button.dataset.page)));
  });
  pagesBox.querySelectorAll('.page-room, .page-group, .page-view').forEach(input => {
    input.addEventListener('change', () => savePageMeta(input.closest('.page-card')));
  });
  if (project.warnings && project.warnings.length) {
    errorBox.innerHTML = project.warnings.map(w => `<div>⚠ ${escapeHtml(w)}</div>`).join('');
    errorBox.classList.remove('hidden');
  }
}

async function savePageMeta(card) {
  if (!card) return;
  const pageIndex = Number(card.dataset.page);
  const payload = {
    room_name: card.querySelector('.page-room').value,
    view_group: card.querySelector('.page-group').value,
    view_type: card.querySelector('.page-view').value,
  };
  try {
    const response = await fetch(`/api/projects/${projectId}/pages/${pageIndex}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const data = await response.json();
      alert(data.detail || 'Не удалось сохранить проекцию');
    }
  } catch (err) {
    alert('Не удалось сохранить проекцию');
  }
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[ch]));
}

async function analyzePage(id, pageIndex) {
  errorBox.classList.add('hidden');
  result.classList.add('hidden');
  status.classList.remove('hidden');
  statusText.textContent = `Распознаю страницу ${pageIndex + 1} — это занимает до минуты…`;
  try {
    const response = await fetch(`/api/projects/${id}/analyze`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ page_index: pageIndex }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Ошибка распознавания');
    if (!data.ok) {
      status.classList.add('hidden');
      const questions = (data.questions || []).join('; ');
      errorBox.textContent = 'Не удалось получить состав: ' +
        (data.reason || 'модули не найдены') + (questions ? '. ' + questions : '');
      errorBox.classList.remove('hidden');
      return;
    }
    pagesCard.classList.add('hidden');
    render(data);
  } catch (err) {
    status.classList.add('hidden');
    errorBox.textContent = err.message || 'Не удалось связаться с сервером';
    errorBox.classList.remove('hidden');
  }
}

function render(data) {
  quoteId = data.quote_id;
  const totals = data.totals;
  document.getElementById('kpi').innerHTML = [
    ['Габарит', (data.total_width_mm || 0) + ' мм'],
    ['Двери', data.doors],
    ['Ящики', data.drawers],
    ['Себестоимость', money(totals.cost_price)],
    ['Клиенту (наличные)', money(totals.final_cash)],
    ['Клиенту (безнал)', money(totals.final_noncash)],
  ].map(([label, value]) => `<div class="kpi"><span>${label}</span><b>${value}</b></div>`).join('');

  const flags = [...(data.flags || []), ...((data.validation || {}).questions || [])];
  if ((data.missing_prices || []).length) {
    flags.push('без цены в прайсе: ' + data.missing_prices.join(', '));
  }
  if (data.can_issue_kp === false) {
    flags.push('КП блокируется — есть позиции без цены, нужен счёт поставщика');
  }
  document.getElementById('flags').innerHTML = flags.length
    ? flags.map(f => `<div class="flag">⚠ ${f}</div>`).join('')
    : '';

  currentModules = (data.modules || []).map(m => ({ ...m }));
  renderModules();
  loadVersions();

  status.classList.add('hidden');
  pagesCard.classList.add('hidden');
  result.classList.remove('hidden');
}

async function loadVersions() {
  if (!quoteId) return;
  const box = document.getElementById('versions');
  try {
    const response = await fetch(`/api/quotes/${quoteId}/versions`);
    if (!response.ok) return;
    const data = await response.json();
    const versions = data.versions || [];
    box.textContent = versions.length
      ? 'История: ' + versions.map(v => `v${v.version_no} (${v.reason})`).join(', ')
      : '';
  } catch (err) {
    box.textContent = '';
  }
}

// Изменение ячейки таблицы
document.getElementById('modules').addEventListener('input', (event) => {
  const target = event.target;
  if (!target.dataset || target.dataset.index === undefined) return;
  const index = Number(target.dataset.index);
  const field = target.dataset.field;
  if (!currentModules[index]) return;
  currentModules[index][field] = target.value;
});

// Удаление строки
document.getElementById('modules').addEventListener('click', (event) => {
  if (!event.target.classList.contains('row-remove')) return;
  const index = Number(event.target.dataset.index);
  currentModules.splice(index, 1);
  renderModules();
});

document.getElementById('add-module').addEventListener('click', () => {
  currentModules.push({
    type_key: 'lower_base', type: 'Нижняя база',
    width: 600, depth: 560, height: 850, quantity: 1, doors: 1, drawers: 0,
  });
  renderModules();
});

document.getElementById('recalc').addEventListener('click', async () => {
  if (!quoteId) return;
  const button = document.getElementById('recalc');
  button.disabled = true;
  button.textContent = 'Считаю…';
  try {
    const response = await fetch('/api/recalc/' + quoteId, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ modules: collectModules() }),
    });
    const data = await response.json();
    if (!response.ok) {
      alert(data.detail || 'Не удалось пересчитать');
      return;
    }
    currentModules = data.modules.map(m => ({ ...m }));
    renderModules();
    const totals = data.totals;
    document.getElementById('kpi').innerHTML = [
      ['Габарит', (data.total_width_mm || 0) + ' мм'],
      ['Двери', data.doors],
      ['Ящики', data.drawers],
      ['Себестоимость', money(totals.cost_price)],
      ['Клиенту (наличные)', money(totals.final_cash)],
      ['Клиенту (безнал)', money(totals.final_noncash)],
    ].map(([label, value]) => `<div class="kpi"><span>${label}</span><b>${value}</b></div>`).join('');
    const flags = [...(data.missing_prices || []).map(p => 'без цены в прайсе: ' + p)];
    document.getElementById('flags').innerHTML = flags.length
      ? flags.map(f => `<div class="flag">⚠ ${f}</div>`).join('') : '';
    loadVersions();
    document.getElementById('approval-status').textContent = 'не подтверждено';
  } finally {
    button.disabled = false;
    button.textContent = 'Пересчитать';
  }
});

document.getElementById('approve').addEventListener('click', async () => {
  if (!quoteId) return;
  const button = document.getElementById('approve');
  button.disabled = true;
  try {
    const response = await fetch('/api/quotes/' + quoteId + '/approve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ approved_by: 'manager' }),
    });
    if (!response.ok) { alert('Не удалось подтвердить состав'); return; }
    const data = await response.json();
    document.getElementById('approval-status').textContent =
      'подтверждено: ' + (data.approval.approved_by || 'manager');
  } finally {
    button.disabled = false;
  }
});

document.getElementById('excel').addEventListener('click', () => {
  if (quoteId) window.location = '/api/excel/' + quoteId;
});
document.getElementById('kp-images').addEventListener('change', (event) => {
  const box = document.getElementById('kp-preview');
  box.innerHTML = '';
  for (const file of event.target.files) {
    const img = document.createElement('img');
    img.src = URL.createObjectURL(file);
    img.style.cssText = 'width:96px;height:72px;object-fit:cover;border-radius:8px;border:1px solid #d6dce6';
    box.appendChild(img);
  }
});

async function downloadKp(endpoint, filename) {
  if (!quoteId) return;
  const variants = [...document.querySelectorAll('.kp-variant:checked')].map(el => el.value);
  if (!variants.length) { alert('Выберите хотя бы один вариант'); return; }
  const payload = {
    variants,
    client: document.getElementById('kp-client').value,
    discount: Number(document.getElementById('kp-discount').value || 0),
    validity_days: Number(document.getElementById('kp-validity').value || 14),
    led: document.getElementById('kp-led').checked,
    gola: document.getElementById('kp-gola').checked,
    accessories: document.getElementById('kp-accessories').checked,
    image: document.getElementById('kp-image').checked,
  };
  const form = new FormData();
  form.append('payload', JSON.stringify(payload));
  const files = document.getElementById('kp-images').files;
  for (const file of files) form.append('images', file);
  const response = await fetch(endpoint + quoteId, { method: 'POST', body: form });
  if (!response.ok) { alert('Не удалось собрать КП: ' + response.status); return; }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

document.getElementById('kp-download').addEventListener('click', () =>
  downloadKp('/api/kp/', 'Коммерческое предложение.docx'));
document.getElementById('kp-pdf').addEventListener('click', () =>
  downloadKp('/api/kp-pdf/', 'Коммерческое предложение.pdf'));
document.getElementById('reset').addEventListener('click', () => {
  result.classList.add('hidden');
  pagesCard.classList.add('hidden');
  errorBox.classList.add('hidden');
  fileInput.value = '';
  quoteId = null;
  projectId = null;
  currentModules = [];
});
</script>
</body>
</html>
"""
