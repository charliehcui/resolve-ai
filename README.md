# ResolveAI

ResolveAI is an evidence-driven investigation and resolution system for internal B2B SaaS technical support teams.

## Current status

The project is currently in **Phase 0: Product Scope**. Application code has not been added yet.

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

Setup instructions will be added in Phase 1 after the project scaffold exists.
