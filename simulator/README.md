# Simulator

This directory contains ResolveLab, a deterministic simulated B2B SaaS system.

It provides fixed customer account, event notification delivery, and platform status data. This keeps support investigations reproducible and avoids real customer data.

## Run the simulator

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn simulator.app:app --port 8001
```

## Run tests

```powershell
.\.venv\Scripts\python.exe -m pytest simulator\tests
```
