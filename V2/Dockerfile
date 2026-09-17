FROM python:3.13-slim

WORKDIR /app

COPY services/requirements.txt /app/services/requirements.txt
RUN pip install --no-cache-dir -r /app/services/requirements.txt

COPY app/__init__.py app/config.py app/db.py app/trace.py /app/app/
COPY services /app/services

ENV PYTHONUNBUFFERED=1
