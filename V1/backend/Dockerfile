FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/backend

WORKDIR /app/backend

COPY backend/requirements.txt /app/backend/requirements.txt
RUN python -m pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && python -m pip install --no-cache-dir -r requirements.txt

COPY backend /app/backend
COPY docs /app/docs

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
