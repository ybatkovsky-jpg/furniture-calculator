"""
Рендерер страниц PDF в изображения для Vision-моделей.

Использует PyMuPDF (fitz) — быстрый, без внешних зависимостей.
"""

import io
import logging
from pathlib import Path
from typing import List, Optional, Tuple

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)


def render_page(
    pdf_path: str | Path,
    page_num: int = 0,
    dpi: int = 150,
    max_dimension: int = 2048,
) -> bytes:
    """
    Отрендерить одну страницу PDF в JPEG.

    Args:
        pdf_path: путь к PDF
        page_num: номер страницы (0-based)
        dpi: разрешение (150 = хороший баланс качество/размер)
        max_dimension: максимальный размер большей стороны в пикселях

    Returns:
        JPEG-изображение в виде bytes
    """
    doc = fitz.open(str(pdf_path))
    page = doc[page_num]

    # Вычисляем масштаб для DPI
    zoom = dpi / 72.0

    # Если изображение будет слишком большим, уменьшаем масштаб
    rect = page.rect
    max_side = max(rect.width, rect.height) * zoom
    if max_side > max_dimension:
        zoom = max_dimension / max(rect.width, rect.height)

    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat)
    doc.close()

    return pix.tobytes("jpeg")


def render_pages(
    pdf_path: str | Path,
    pages: Optional[List[int]] = None,
    dpi: int = 150,
    max_dimension: int = 2048,
) -> List[Tuple[int, bytes]]:
    """
    Отрендерить несколько страниц PDF в JPEG.

    Args:
        pdf_path: путь к PDF
        pages: список номеров страниц (0-based). None = все страницы.
        dpi: разрешение
        max_dimension: макс. размер стороны

    Returns:
        Список кортежей (номер_страницы, jpeg_bytes)
    """
    doc = fitz.open(str(pdf_path))
    total = doc.page_count

    if pages is None:
        pages = list(range(total))

    logger.info(f"Рендеринг {len(pages)}/{total} страниц из {Path(pdf_path).name}...")

    results = []
    for page_num in pages:
        if page_num >= total:
            logger.warning(f"Страница {page_num} не существует (всего {total})")
            continue

        try:
            jpeg_bytes = render_page(pdf_path, page_num, dpi, max_dimension)
            results.append((page_num, jpeg_bytes))
            logger.debug(f"  Стр. {page_num + 1}: {len(jpeg_bytes) / 1024:.0f} KB")
        except Exception as e:
            logger.error(f"Ошибка рендеринга стр. {page_num + 1}: {e}")

    doc.close()
    return results


def get_page_count(pdf_path: str | Path) -> int:
    """Количество страниц в PDF."""
    doc = fitz.open(str(pdf_path))
    count = doc.page_count
    doc.close()
    return count


def guess_key_pages(
    pdf_path: str | Path,
    room_keywords: List[str],
    ocr_md_text: str = "",
) -> List[int]:
    """
    Найти ключевые страницы по названиям помещений из OCR.

    Использует эвристику: если в md_results есть упоминание помещения
    на странице N, то страница N — ключевая.

    Args:
        pdf_path: путь к PDF
        room_keywords: ключевые слова помещений (из GLM-OCR)
        ocr_md_text: md_results от GLM-OCR

    Returns:
        Список номеров страниц (0-based)
    """
    import re

    key_pages = set()

    # Ищем page=N в тексте OCR около упоминаний комнат
    for keyword in room_keywords:
        # Ищем page=X рядом с keyword
        pattern = re.compile(
            re.escape(keyword) + r'.*?page=(\d+)',
            re.IGNORECASE | re.DOTALL
        )
        for match in pattern.finditer(ocr_md_text):
            key_pages.add(int(match.group(1)))

    # Если ничего не нашли — берём все страницы
    if not key_pages:
        total = get_page_count(pdf_path)
        # Пропускаем титульные (первые 1-2 и последние страницы со штампами)
        key_pages = set(range(1, total - 1))

    return sorted(key_pages)
