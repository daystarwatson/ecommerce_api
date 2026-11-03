FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN SECRET_KEY=temporary-build-only-key python manage.py collectstatic --noinput

EXPOSE 8080

CMD ["gunicorn", "ecommerce_project.wsgi:application", "--bind", "0.0.0.0:8080"]