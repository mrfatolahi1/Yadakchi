FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y make \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home --uid 10001 billing \
    && chown -R billing:billing /app
USER billing

EXPOSE 8000

CMD ["uvicorn", "billing.asgi:application", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
