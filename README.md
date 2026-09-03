# ResolveAI

ResolveAI is an agent-based technical support system for B2B SaaS products. It handles the support process from the customer's first message to a verified resolution or a complete handoff to a human engineer.

Customers do not need to understand error codes, system logs, account settings, or internal product terms. They describe what went wrong in plain language, and ResolveAI collects the technical context needed to investigate the problem.

## The problem

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

## Main outcomes

ResolveAI supports four complete outcomes:

1. **Customer resolution** — the customer follows a safe step, the system verifies recovery, and no ticket is created.
2. **Support resolution** — an evidence-rich ticket is created automatically and the support agent finds a verified solution.
3. **Approved action** — the support agent proposes a safe system change, a human approves it, and the result is verified.
4. **Engineer escalation** — unresolved or high-risk cases are sent to an engineer with the customer context and investigation evidence already attached.

## Core capabilities

- Multi-turn customer conversations with state recovery.
- Automatic collection of customer-scoped SaaS information.
- Separate customer and internal knowledge retrieval.
- Structured handoff from customer diagnosis to support investigation.
- Evidence-based tool use and source citations.
- Automatic ticket creation without asking the customer to repeat information.
- Human approval before any operation changes system state.
- Verification after customer guidance or an approved action.
- Complete engineer escalation packages for unresolved cases.
- Repeatable agent evaluation using versioned test cases and execution traces.

## Safety model

- The server controls customer identity, data access, and tool permissions.
- Customer and support agents use separate tool and knowledge scopes.
- The customer agent cannot access internal documents or internal tools.
- Agents cannot directly execute write actions.
- Safe actions require deterministic policy checks and human approval.
- Results are verified after guidance or an approved action.
- Insufficient or conflicting evidence leads to escalation instead of guessing.
- Hidden reasoning is not stored or shown in the user interface.

## System structure

```text
React Customer View
        ↓
FastAPI
        ↓
Customer Diagnostic Graph
        ├── Customer-scoped tools
        ├── Customer knowledge retrieval
        └── Diagnostic handoff
                    ↓
              PostgreSQL Ticket
                    ↓
Support Investigation Graph
        ├── Internal read-only tools
        ├── Internal knowledge retrieval
        ├── Evidence and resolution
        ├── Human approval
        └── Engineer escalation
                    ↓
            React Support View
```

ResolveLab provides deterministic SaaS account, product, activity, delivery, and platform data so every support scenario can be reproduced without using real customer information.

## Technology stack

| Area | Technology |
|---|---|
| Frontend | Vite, React, TypeScript, Tailwind CSS |
| Backend | FastAPI, Python, Pydantic |
| Agents and tools | LangChain |
| Stateful workflows | LangGraph |
| Model | ChatGroq |
| Database | PostgreSQL, SQLAlchemy, Alembic |
| Knowledge retrieval | pgvector, PostgreSQL full-text search |
| State recovery | PostgreSQL LangGraph checkpointer |
| Tracing and evaluation | LangSmith, Pytest, versioned JSON datasets |
| SaaS simulator | ResolveLab with FastAPI |
| Local delivery | Docker Compose |
| Continuous integration | GitHub Actions |

## Evaluation

ResolveAI evaluates the complete support process instead of checking only whether a response sounds reasonable. Its evaluation cases measure:

- Problem understanding and unnecessary customer questions.
- Correct use of customer and internal tools.
- Ticket completeness and information preserved during handoff.
- Retrieval quality and citation validity.
- Evidence coverage and supported conclusions.
- Correct routing to customer resolution, support resolution, approval, or escalation.
- Forbidden tool calls, permission boundaries, and approval bypass attempts.
- State recovery, latency, model usage, and execution cost.

The result is a technical support workflow in which agents handle language, investigation, and evidence collection while normal application code controls identity, permissions, approvals, and system changes.
