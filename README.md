# ResolveAI

ResolveAI is an evidence-driven investigation and resolution system for internal B2B SaaS technical support teams.

## Current status

The project is currently in **Phase 1: Engineering Scaffold**. Product scope is complete, and the frontend now reports the backend API health state.

## Problem

Technical support engineers often need to check customer accounts, entitlements, background jobs, webhook deliveries, API usage, logs, and product documentation before they can explain an issue.

ResolveAI will collect this evidence, produce reviewable diagnoses, and either recommend a resolution, propose a safe action, or prepare an escalation package. It will not allow an AI agent to bypass permissions, approval, or audit rules.

## Architecture principle

ResolveAI uses a deterministic outer workflow with a bounded, read-only investigation agent.

- Normal code controls permissions, ticket state, risk rules, approvals, and write actions.
- The investigation agent decides which approved read-only tools to use next.
- Low-risk write actions require policy checks and human approval.
- Unsupported or high-risk cases are escalated instead of guessed.

## Documentation

- [Product specification](docs/product-spec.md)
- [ADR-001: Deterministic workflow with a bounded agent](docs/adr/001-deterministic-workflow-bounded-agent.md)

## Planned stack

- Frontend: Next.js and TypeScript
- Backend: FastAPI and Python
- Workflow: LangGraph and LangChain
- Data: PostgreSQL, PostgreSQL full-text search, and pgvector
- Local development: Docker Compose

## Repository structure

| Path | Purpose |
|---|---|
| `frontend/` | Browser-based support console. |
| `backend/` | API, business rules, workflow, tools, and database access. |
| `simulator/` | ResolveLab synthetic SaaS system and reproducible fault scenarios. |
| `evals/` | Evaluation datasets, evaluators, runners, and reports. |
| `infra/` | Local containers and deployment infrastructure. |
| `docs/` | Product, architecture, security, and decision documents. |

Backend setup instructions are available in [`backend/README.md`](backend/README.md).
