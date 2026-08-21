# ADR-001: Deterministic Workflow with a Bounded Investigation Agent

**Status:** Accepted  
**Date:** 2026-08-21

## Context

Technical support investigations are not fully predictable. The next useful data source depends on the ticket and on evidence returned by earlier tools.

However, permissions, tenant isolation, ticket state changes, approvals, and write actions must be predictable and testable. A language model is not a safe authority for these rules.

## Decision

ResolveAI will use a deterministic outer workflow with one bounded investigation agent inside it.

The outer workflow will control:

- Ticket validation and status transitions.
- Required customer and account baseline checks.
- Evidence validation.
- Investigation limits and stopping conditions.
- Action allowlists and risk rules.
- Human approval.
- Idempotent action execution.
- Result verification, resolution, and escalation.

The investigation agent will:

- Use only approved read-only tools.
- Choose the next evidence to collect.
- Test a small number of root-cause hypotheses.
- Stop after at most two investigation rounds or six tool calls.
- Return control when evidence is sufficient, the budget is exhausted, or escalation is required.

All write actions will be executed outside the agent after deterministic policy checks and any required human approval.

## Why this decision

This design puts flexible reasoning where it is useful and deterministic code where mistakes could change data or cross a security boundary.

It also makes each case easier to test, pause, resume, audit, and explain.

## Alternatives considered

### One unrestricted agent

Rejected because it would mix investigation, permissions, business state, and write execution in one model-controlled loop. This is difficult to secure and test.

### Fully rule-based investigation

Rejected because support tickets are incomplete and investigation paths change after each piece of evidence. A large fixed decision tree would become difficult to maintain.

### Multiple specialized agents

Rejected for the first release because it adds coordination, cost, latency, and evaluation complexity before a single bounded agent has been shown to be insufficient.

## Consequences

### Positive

- Security rules can be tested without calling a model.
- Agent tools remain read-only.
- Investigations have clear budgets and stopping conditions.
- Human approval and process recovery have explicit places in the workflow.
- Tool calls, evidence, decisions, and actions can be audited.

### Negative

- More ordinary application code is required around the model.
- Workflow state and transitions must be designed carefully.
- Adding a new write action requires schema, policy, approval, idempotency, and verification work.

These costs are accepted because ResolveAI is a resolution system, not a chat demonstration.
