# Backend

This directory contains the ResolveAI FastAPI application.

The backend is separate from the frontend so that model credentials, database access, permission checks, and write actions remain on the server.

## Current endpoints

- `GET /health/live` confirms that the API process is running.
- `GET /health/ready` confirms that the API is ready to receive requests. Database checks will be added to this endpoint later.

## Local setup

Run these commands from the repository root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
```

Using the virtual environment's Python executable directly avoids PowerShell activation-policy problems.

## Run the API

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload
```

Then open:

- API documentation: <http://127.0.0.1:8000/docs>
- Liveness endpoint: <http://127.0.0.1:8000/health/live>
- Readiness endpoint: <http://127.0.0.1:8000/health/ready>

## Run tests

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests
```
