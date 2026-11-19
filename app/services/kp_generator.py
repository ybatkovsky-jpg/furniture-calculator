"""
Генератор PDF коммерческих предложений.

Использует ReportLab для создания профессиональных PDF документов.
"""

import logging
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import ClassVar

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.config import settings
from app.models.calculation import Calculation, CommercialOffer
from app.models.project import Project

logger = logging.getLogger(__name__)


class KPGenerator:
    """Генератор PDF коммерческих предложений."""

    def __init__(self):
        self.company_name = settings.company_name
        self.company_address = settings.company_address
        self.company_phone = settings.company_phone
        self.company_email = settings.company_email
        self.kp_prefix = settings.kp_number_prefix

        # Создать директорию для PDF если не существует
        os.makedirs("pdfs", exist_ok=True)

        # Настроить стили
        self.styles = getSampleStyleSheet()
        self._setup_styles()

    def _setup_styles(self):
        """Настроить стили для PDF.

        P2-14: шрифт ищем по абсолютным путям (проект → Windows → Linux),
        а не по относительному имени: в Docker относительный DejaVuSans.ttf
        не находился, и КП уезжало в Helvetica без кириллицы.
        """
        project_root = Path(__file__).resolve().parents[2]
        candidates = [
            ("DejaVuSans", project_root / "DejaVuSans.ttf"),
            ("DejaVuSans", project_root / "templates" / "DejaVuSans.ttf"),
            ("DejaVuSans", Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")),
            ("DejaVuSans", Path("/usr/share/fonts/dejavu/DejaVuSans.ttf")),
            ("Arial", Path("C:/Windows/Fonts/arial.ttf")),
        ]
        bold_candidates = [
            ("DejaVuSans-Bold", project_root / "DejaVuSans-Bold.ttf"),
            ("DejaVuSans-Bold", project_root / "templates" / "DejaVuSans-Bold.ttf"),
            ("DejaVuSans-Bold", Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")),
            ("DejaVuSans-Bold", Path("/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf")),
            ("Arial-Bold", Path("C:/Windows/Fonts/arialbd.ttf")),
        ]
        default_font = "Helvetica"
        for font_name, font_path in candidates:
            if not font_path.exists():
                continue
            try:
                pdfmetrics.registerFont(TTFont(font_name, str(font_path)))
                default_font = font_name
                break
            except Exception:
                logger.debug("Шрифт %s не загрузился", font_path, exc_info=True)
                continue
        if default_font == "Helvetica":
            logger.warning("Кириллический шрифт не найден — КП может отображаться некорректно")

        # День 4: таблица КП раньше рисовалась Helvetica, даже когда стили
        # получили DejaVu/Arial — кириллица в таблице ломалась.
        bold_font = default_font
        for font_name, font_path in bold_candidates:
            if not font_path.exists():
                continue
            try:
                pdfmetrics.registerFont(TTFont(font_name, str(font_path)))
                bold_font = font_name
                break
            except Exception:
                logger.debug("Шрифт %s не загрузился", font_path, exc_info=True)
                continue
        self.font_name = default_font
        self.font_bold = bold_font

        # Заголовок компании
        self.styles.add(ParagraphStyle(
            name='CompanyHeader',
            parent=self.styles['Heading1'],
            fontSize=16,
            spaceAfter=30,
            alignment=1,  # center
            textColor=colors.darkblue,
            fontName=default_font,
        ))

        # Заголовок КП
        self.styles.add(ParagraphStyle(
            name='KPTitle',
            parent=self.styles['Heading2'],
            fontSize=14,
            spaceAfter=20,
            alignment=1,
            fontName=default_font,
        ))

        # Данные клиента
        self.styles.add(ParagraphStyle(
            name='ClientInfo',
            parent=self.styles['Normal'],
            fontSize=11,
            spaceAfter=10,
            fontName=default_font,
        ))

        # Нормальный текст
        self.styles.add(ParagraphStyle(
            name='NormalText',
            parent=self.styles['Normal'],
            fontSize=10,
            spaceAfter=5,
            fontName=default_font,
        ))

        # Итого
        self.styles.add(ParagraphStyle(
            name='Total',
            parent=self.styles['Normal'],
            fontSize=12,
            spaceAfter=10,
            alignment=2,  # right
            textColor=colors.darkred,
            fontName=default_font,
        ))

    def _format_currency(self, amount: float) -> str:
        """Форматировать сумму в рублях."""
        if isinstance(amount, Decimal):
            amount = float(amount)
        return f"{amount:,.0f} ₽".replace(",", " ")

    def _generate_kp_number(self) -> str:
        """Генерировать уникальный номер КП."""
        # Получить следующий номер из базы данных
        # Для простоты используем timestamp
        # P2-14: секунды не уникальны — два КП подряд получали один номер и
        # падали на unique(offer_number). Добавляем микросекунды.
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        return f"{self.kp_prefix}{timestamp}"

    _KIND_TITLES: ClassVar[dict[str, str]] = {
        "ldsp": "ЛДСП",
        "hdf": "ХДФ",
        "edge": "Кромка",
        "facade": "Фасады",
        "hinge": "Петли",
        "drawer": "Ящики",
        "drawer_internal": "Ящики внутренние",
        "handle": "Ручки",
        "gola": "GOLA",
        "led": "Подсветка LED",
        "countertop": "Столешница",
        "fastener": "Крепёж",
        "glass": "Стекло",
        "rod": "Штанги",
        "bottle_holder": "Бутылочницы",
        "cutlery_tray": "Лотки",
        "drying_rack": "Сушки",
        "hygienic_mat": "Гигиенические поддоны",
    }

    @staticmethod
    def _row_sum(items: list[list[str]]) -> float:
        total = 0.0
        for row in items[1:]:
            total += float(
                row[2].replace(" ₽", "").replace(" ", "").replace("\u00a0", "")
            )
        return total

    def _create_items_table_from_quote(self, quote: dict) -> list[list[str]]:
        """P1-03: таблица КП из снимка `quote` (строки + totals).

        Сумма строк = `final_cash` (допуск ₽ — документ округляет до рубля):
        материалы (по видам) + услуги + сопровождение − скидка / + наценка
        + бонус дизайнера + доставка. Скидка и бонус — строки таблицы, а не
        текст после неё, поэтому документ объясняет цену.
        """
        totals = quote.get("totals") or {}
        items: list[list[str]] = [["Наименование", "Количество", "Стоимость"]]

        def add(name: str, quantity: str, amount: float) -> None:
            if abs(float(amount or 0)) >= 0.005:
                items.append([name, quantity, self._format_currency(float(amount))])

        # Материалы и комплектующие — по видам строк.
        by_kind: dict[str, float] = {}
        for line in quote.get("lines") or []:
            amount = float(line.get("amount") or 0)
            if amount <= 0:
                continue
            kind = str(line.get("kind") or "")
            by_kind[kind] = by_kind.get(kind, 0.0) + amount
        for kind, amount in by_kind.items():
            add(self._KIND_TITLES.get(kind, "Прочие материалы"), "по расчёту", amount)

        # Услуги и производные.
        add("Изготовление", "услуга", totals.get("manufacturing"))
        add("Монтаж", "услуга", totals.get("installation"))
        add("Замер", "услуга", totals.get("measurement"))
        add("Проектировка", "услуга", totals.get("design"))
        add("Прочие расходы", "услуга", totals.get("overhead"))
        accompaniment = sum(
            float(totals.get(field) or 0)
            for field in ("profit", "tech_director", "manager_salary", "designer_salary")
        )
        add("Сопровождение заказа", "услуга", accompaniment)

        # Скидка / наценка — знак явный (P2-01: раньше «Наценка: -X»).
        discount = float(totals.get("discount_amount") or 0)
        if discount > 0:
            add("Скидка", "—", -discount)
        elif discount < 0:
            items.append(["Наценка", "—", "+" + self._format_currency(abs(discount))])

        bonus = float(totals.get("designer_bonus_amount") or 0)
        if bonus > 0:
            add("Бонус дизайнера", "—", bonus)

        add("Доставка", "услуга", totals.get("delivery_client"))

        # Округление до рубля: сумма отформатированных строк = итог.
        final_cash = float(totals.get("final_cash") or 0)
        delta = round(final_cash) - round(self._row_sum(items))
        if delta:
            add("Округление до рубля", "—", delta)
        return items

    def _create_items_table(self, calculation: Calculation) -> list[list[str]]:
        """Создать таблицу с позициями для КП."""
        quote = getattr(calculation, "quote", None) or {}
        if quote.get("lines") and quote.get("totals"):
            return self._create_items_table_from_quote(quote)

        items = []

        # Заголовки таблицы
        headers = ["Наименование", "Количество", "Стоимость"]
        items.append(headers)

        # Функция для добавления позиции
        def add_item(name: str, quantity: str, cost: float):
            if cost and cost > 0:
                items.append([name, quantity, self._format_currency(cost)])

        # Материалы
        material_cost = getattr(calculation, 'material_cost', 0) or 0
        if material_cost > 0:
            selected_materials = getattr(calculation, 'selected_materials', None) or {}
            if "ldsp" in selected_materials:
                ldsp = selected_materials["ldsp"]
                add_item(f"ЛДСП {ldsp.get('name', '')}", "по расчёту", material_cost)
            else:
                add_item("Материалы", "по расчёту", material_cost)

        # Кромка
        edge_cost = getattr(calculation, 'edge_cost', 0) or 0
        if edge_cost > 0:
            add_item("Кромка ПВХ", "по расчёту", edge_cost)

        # Фасады
        facade_cost = getattr(calculation, 'facade_cost', 0) or 0
        if facade_cost > 0:
            add_item("Фасады", "по расчёту", facade_cost)

        # Фурнитура
        hardware_cost = getattr(calculation, 'hardware_cost', 0) or 0
        if hardware_cost > 0:
            add_item("Фурнитура", "комплект", hardware_cost)

        # Стекло
        glass_cost = getattr(calculation, 'glass_cost', 0) or 0
        if glass_cost > 0:
            add_item("Стекло", "по расчёту", glass_cost)

        # Подсветка
        lighting_cost = getattr(calculation, 'lighting_cost', 0) or 0
        if lighting_cost > 0:
            add_item("Подсветка LED", "комплект", lighting_cost)

        # Столешница
        countertop_cost = getattr(calculation, 'countertop_cost', 0) or 0
        if countertop_cost > 0:
            add_item("Столешница", "шт.", countertop_cost)

        # Аксессуары
        accessories_cost = getattr(calculation, 'accessories_cost', 0) or 0
        if accessories_cost > 0:
            add_item("Аксессуары", "комплект", accessories_cost)

        # Крепёж (P0-02/P1-18): конфирмат, опоры, подвесы, цоколь
        fastener_cost = getattr(calculation, 'fastener_cost', 0) or 0
        if fastener_cost > 0:
            add_item("Крепёж", "комплект", fastener_cost)

        # Услуги
        manufacturing = getattr(calculation, 'manufacturing', 0) or 0
        add_item("Изготовление", "услуга", manufacturing)

        installation = getattr(calculation, 'installation', 0) or 0
        add_item("Монтаж", "услуга", installation)

        measurement_fee = getattr(calculation, 'measurement_fee', 0) or 0
        add_item("Замер", "услуга", measurement_fee)

        # P1-10: поле называется delivery_fee_client; delivery_client не
        # существовало, поэтому доставка в КП всегда была 0.
        delivery_client = getattr(calculation, 'delivery_fee_client', 0) or 0
        if delivery_client > 0:
            add_item("Доставка", "услуга", delivery_client)

        design_fee = getattr(calculation, 'design_fee', 0) or 0
        add_item("Проектировка", "услуга", design_fee)

        # День 4: чтобы сумма строк таблицы сходилась с итогом, показываем
        # прочие расходы и сопровождение (прибыль + тех. директор + менеджер +
        # дизайнер). Раньше этих строк не было, и таблица не сходилась с итогом.
        overhead = getattr(calculation, 'overhead', 0) or 0
        add_item("Прочие расходы", "услуга", overhead)

        accompaniment = sum(
            getattr(calculation, field, 0) or 0
            for field in (
                "profit",
                "tech_director_salary",
                "manager_salary",
                "designer_salary",
            )
        )
        add_item("Сопровождение заказа", "услуга", accompaniment)

        return items

    def generate_kp_pdf(self, calculation: Calculation, project: Project, project_name: str = "") -> tuple[str, str]:
        """
        Генерировать PDF коммерческого предложения.

        Args:
            calculation: Объект расчёта
            project_name: Название проекта

        Returns:
            str: Путь к созданному PDF файлу
        """
        # Генерировать номер КП
        kp_number = self._generate_kp_number()

        # Создать имя файла
        filename = f"KP_{kp_number}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        filepath = os.path.join("pdfs", filename)

        # Создать PDF документ
        doc = SimpleDocTemplate(
            filepath,
            pagesize=A4,
            rightMargin=20*mm,
            leftMargin=20*mm,
            topMargin=20*mm,
            bottomMargin=20*mm
        )

        # Собрать содержимое
        story = []

        # Логотип и шапка компании
        story.append(Image("pro_mebel_small.png", width=80, height=80))
        story.append(Spacer(1, 10))
        story.append(Paragraph(self.company_name, self.styles['CompanyHeader']))
        story.append(Paragraph(self.company_address, self.styles['NormalText']))
        story.append(Paragraph(f"Тел: {self.company_phone}", self.styles['NormalText']))
        story.append(Paragraph(f"Email: {self.company_email}", self.styles['NormalText']))
        story.append(Spacer(1, 20))

        # Заголовок КП
        kp_title = f"КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ {kp_number}"
        story.append(Paragraph(kp_title, self.styles['KPTitle']))

        kp_date = f"от {datetime.now().strftime('%d.%m.%Y')}"
        story.append(Paragraph(kp_date, self.styles['NormalText']))
        story.append(Spacer(1, 20))

        # Данные клиента
        client_info = project
        story.append(Paragraph("Уважаемый клиент!", self.styles['NormalText']))
        story.append(Spacer(1, 10))

        if project_name:
            story.append(Paragraph(f"Проект: {project_name}", self.styles['ClientInfo']))

        story.append(Paragraph(f"Заказчик: {client_info.client_name}", self.styles['ClientInfo']))
        story.append(Paragraph(f"Телефон: {client_info.client_phone}", self.styles['ClientInfo']))

        if client_info.client_address:
            story.append(Paragraph(f"Адрес: {client_info.client_address}", self.styles['ClientInfo']))

        story.append(Spacer(1, 20))

        # Таблица позиций
        items_data = self._create_items_table(calculation)

        if len(items_data) > 1:  # Есть позиции кроме заголовков
            # Создать таблицу
            table = Table(items_data, colWidths=[100*mm, 30*mm, 35*mm])

            # Стили таблицы
            table_style = TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('ALIGN', (1, 0), (2, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, -1), self.font_name),
                ('FONTNAME', (0, 0), (-1, 0), self.font_bold),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ])

            table.setStyle(table_style)
            story.append(table)
            story.append(Spacer(1, 15))

        # P1-03: строки скидки/бонуса/наценки уже внутри таблицы (если есть снимок
        # с totals) — отдельный текст после таблицы не нужен и дублировал бы суммы.
        _quote_has_totals = bool((getattr(calculation, "quote", None) or {}).get("totals"))

        # Скидка (если есть)
        discount_text = ""
        quote = getattr(calculation, "quote", None) or {}
        discount_amount = (
            quote.get("discount_amount")
            or getattr(calculation, "discount_amount", 0)
            or 0
        )
        discount_type = getattr(calculation, 'discount_type', None)
        discount_value = getattr(calculation, 'discount_value', 0)
        markup_type = getattr(calculation, 'markup_type', None)

        if discount_amount != 0 and not _quote_has_totals:
            if discount_type == "percent":
                discount_text = f"Скидка {discount_value}%: {self._format_currency(-abs(discount_amount))}"
            elif discount_type == "fixed":
                discount_text = f"Скидка: {self._format_currency(-abs(discount_amount))}"
            elif markup_type in ["percent", "fixed"]:
                discount_text = f"Наценка: +{self._format_currency(abs(discount_amount))}"

            if discount_text:
                story.append(Paragraph(discount_text, self.styles['NormalText']))
                story.append(Spacer(1, 10))

        # Итоговые суммы
        story.append(Spacer(1, 20))

        final_cash = calculation.final_price_cash or calculation.total_base_cash
        final_noncash = calculation.final_price_noncash or (final_cash * 1.10)

        total_cash_text = f"Итого наличными: {self._format_currency(final_cash)}"
        total_noncash_text = f"Итого безналичными: {self._format_currency(final_noncash)}"

        story.append(Paragraph(total_cash_text, self.styles['Total']))
        story.append(Paragraph(total_noncash_text, self.styles['Total']))

        # Генерировать PDF
        doc.build(story)

        return filepath, kp_number

    def create_commercial_offer_record(
        self,
        calculation: Calculation,
        kp_number: str,
        pdf_path: str
    ) -> CommercialOffer:
        """
        Создать запись CommercialOffer в базе данных.

        Args:
            calculation: Расчёт
            kp_number: Номер КП
            pdf_path: Путь к PDF файлу

        Returns:
            CommercialOffer: Созданная запись
        """
        # Определить итоговые цены
        total_cash = calculation.final_price_cash or calculation.total_base_cash
        total_noncash = calculation.final_price_noncash or (total_cash * 1.10)

        # Создать запись КП
        kp_record = CommercialOffer(
            calculation_id=calculation.id,
            offer_number=kp_number,
            pdf_filename=os.path.basename(pdf_path),
            pdf_path=pdf_path,
            total_cash=total_cash,
            total_noncash=total_noncash,
        )

        return kp_record


# Глобальный экземпляр генератора
kp_generator = KPGenerator()