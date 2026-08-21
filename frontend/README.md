# Frontend

This directory contains the ResolveAI Next.js support console.

The current page calls the FastAPI readiness endpoint from a Next.js Server Component and displays either `Ready` or `Unavailable`.

## Local setup

Install dependencies from the repository root:

```powershell
npm.cmd --prefix frontend install
```

The default backend URL is `http://127.0.0.1:8000`. To override it, copy the example file:

```powershell
Copy-Item frontend\.env.example frontend\.env.local
```

`BACKEND_URL` does not use the `NEXT_PUBLIC_` prefix because it is read only by the Next.js server.

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
