FROM python:3.13-slim

WORKDIR /app

COPY simulator/services/requirements.txt /app/simulator/services/requirements.txt
RUN pip install --no-cache-dir -r /app/simulator/services/requirements.txt

COPY backend/__init__.py /app/backend/__init__.py
COPY backend/app/__init__.py backend/app/config.py backend/app/database.py backend/app/trace.py /app/backend/app/
COPY simulator/__init__.py /app/simulator/__init__.py
COPY simulator/services /app/simulator/services

ENV PYTHONUNBUFFERED=1
