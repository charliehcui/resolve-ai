# ResolveAI

ResolveAI is an AI-powered technical support system for B2B SaaS products. It helps non-technical customers explain problems, automatically gathers relevant context, attempts safe self-service diagnosis, and creates an evidence-rich support ticket when the issue cannot be resolved.

The ticket is then investigated by a separate support agent. If the support agent still cannot resolve the issue, it prepares a complete escalation package for a human engineer.

## Problem

Customers often know that something is not working but do not know what information technical support needs. They may not understand system errors, account settings, product versions, or diagnostic data.

This creates a slow support process:

```text
Customer reports a vague problem
        ↓
Support repeatedly asks for missing information
        ↓
The same context is collected again during each handoff
        ↓
An engineer eventually starts a new investigation
```

ResolveAI reduces this repeated communication by keeping one structured support context from the first customer message to the final resolution.

## Core workflow

```text
Customer describes a problem in plain language
        ↓
Customer Diagnostic Agent understands the issue
        ↓
Automatically collects available product and account context
        ↓
Retrieves relevant customer-facing product knowledge
        ↓
Provides simple guidance and verifies the result
        ↓
Resolved? ── Yes ──→ Close the diagnostic session
    │ No
    ↓
Create a structured, evidence-rich ticket
        ↓
Support Investigation Agent queries internal systems and knowledge
        ↓
Produce a supported resolution, request approval for a safe action,
or prepare an escalation package for a human engineer
```

## Agent design

ResolveAI uses two bounded agent workflows connected by a validated handoff.

### Customer Diagnostic Agent

- Communicates with customers using plain language.
- Identifies the real problem behind an incomplete description.
- Collects information the system already knows before asking questions.
- Uses customer-visible retrieval-augmented generation (RAG).
- Calls only customer-scoped read tools.
- Resolves simple issues or creates a structured ticket.

### Support Investigation Agent

- Receives the structured ticket and completed customer-side diagnosis.
- Calls approved internal read-only tools.
- Collects and validates evidence.
- Produces a supported diagnosis and resolution.
- Proposes safe actions without executing them directly.
- Escalates unresolved or high-risk cases to a human engineer.

The agents do not freely chat with each other. They exchange a validated `DiagnosticHandoff` containing the problem summary, customer impact, collected facts, attempted steps, citations, and unresolved questions.

## Safety model

- The server controls customer identity, data access, and tool permissions.
- Customer and support agents use separate tool and knowledge scopes.
- The customer agent cannot access internal documents or internal tools.
- Agents cannot directly execute write actions.
- Safe actions require deterministic policy checks and human approval.
- Results are verified after guidance or an approved action.
- Insufficient or conflicting evidence leads to escalation instead of guessing.
- Hidden reasoning is not stored or shown in the user interface.

## Current development focus

The repository is aligned with the current product scope. It contains the backend, database, simulator, structured model output, read-only support tools, a bounded Support Investigation Agent, and its three-node LangGraph workflow.

The lightweight Vite interface checks the FastAPI and database readiness status. The next implementation step begins the Customer Diagnostic Agent with the first customer message and structured problem understanding.

## Planned technology stack

| Area | Technology |
|---|---|
| Frontend | Vite, React, TypeScript, Tailwind CSS |
| Backend | FastAPI, Python, Pydantic |
| Agents and tools | LangChain |
| Stateful workflows | LangGraph |
| Model | ChatGroq |
| Database | PostgreSQL, SQLAlchemy, Alembic |
| Knowledge retrieval | pgvector, PostgreSQL full-text search |
| Tracing and evaluation | LangSmith, Pytest, versioned JSON datasets |
| Local delivery | Docker Compose |
| Continuous integration | GitHub Actions |

## Documentation

- [ResolveAI V2 Project Plan](project_plan.md)
- [ResolveAI V2 Zero-to-One Build Plan](ResolveAI_V2_Zero_to_One_Build_Plan.md)
- [Backend setup](backend/README.md)

## Repository structure

| Path | Purpose |
|---|---|
| `frontend/` | Customer and support views in one lightweight application. |
| `backend/` | APIs, agent workflows, tools, retrieval, rules, and database access. |
| `simulator/` | ResolveLab synthetic SaaS data and reproducible support scenarios. |
| `evals/` | Versioned cases, evaluators, runners, and result reports. |
| `infra/` | Local containers and deployment files. |
| `docs/` | Product, architecture, security, and decision documents. |
