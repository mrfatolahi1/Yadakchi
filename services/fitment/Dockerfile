FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

RUN addgroup --system fitment && adduser --system --ingroup fitment fitment
COPY requirements.txt ./
RUN pip install --no-cache-dir --retries 10 --timeout 120 -r requirements.txt

COPY . .
RUN chown -R fitment:fitment /app
USER fitment

EXPOSE 8000
CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py seed_vehicles && gunicorn fitment.wsgi:application --bind 0.0.0.0:8000 --workers 2"]
