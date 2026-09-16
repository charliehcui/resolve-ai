FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

WORKDIR /app

COPY simulator/requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY simulator /app/simulator

EXPOSE 8001

CMD ["python", "-m", "uvicorn", "simulator.app:app", "--host", "0.0.0.0", "--port", "8001"]
