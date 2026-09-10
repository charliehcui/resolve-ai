# Frontend

This directory contains the ResolveAI customer support interface built with Vite, React, TypeScript, and Tailwind CSS.

The current page starts and continues customer support sessions through the FastAPI API. It displays the conversation, problem summary, automatically collected customer information, customer-visible citations, verification result, and current session status. The local demo uses `customer_001` from ResolveLab.

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
