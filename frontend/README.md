# Frontend

This directory contains the small ResolveAI browser interface built with Vite, React, TypeScript, and Tailwind CSS.

The current page calls the FastAPI readiness endpoint and displays whether the backend and database are ready.

## Local setup

Install dependencies from the repository root:

```powershell
npm.cmd --prefix frontend install
```

The default backend URL is `http://127.0.0.1:8000`. To change it, copy the example file:

```powershell
Copy-Item frontend\.env.example frontend\.env.local
```

The browser reads `VITE_BACKEND_URL` when the frontend starts.

## Run the frontend

Start the FastAPI backend first, then run:

```powershell
npm.cmd --prefix frontend run dev
```

Open <http://127.0.0.1:3000>.

## Quality checks

```powershell
npm.cmd --prefix frontend run lint
npm.cmd --prefix frontend run build
```
