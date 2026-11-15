FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Непривилегированный пользователь: процесс не должен работать от root.
RUN useradd --create-home --uid 1000 appuser

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser:appuser . .

USER appuser

# Точка входа — Telegram-бот (polling). HTTP-сервера в приложении нет,
# поэтому uvicorn здесь не используется (P1-12).
CMD ["python", "-m", "app.main"]
