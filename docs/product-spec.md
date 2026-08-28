# ResolveAI Product Specification

**Status:** Phase 0 — Accepted scope  
**Date:** 2026-08-21

## 1. Product summary

ResolveAI is an internal technical support investigation and resolution system for B2B SaaS teams.

It receives a support ticket, gathers evidence from approved data sources, produces a reviewable diagnosis, and either recommends a resolution, proposes a safe action, or prepares an escalation package.

ResolveAI is not a general customer-facing chatbot.

## 2. Target users and roles

### `support_agent`

- Creates and reads tickets.
- Starts an investigation.
- Reviews evidence, diagnoses, and proposed resolutions.
- Can escalate a ticket.
- Cannot approve a protected write action.

### `approver`

- Has the capabilities of `support_agent`.
- Reviews a proposed action and can approve, edit, or reject it.
- Cannot bypass action validation or policy checks.

### `admin`

- Manages users, roles, and system settings.
- Can inspect audit records.
- Cannot give the investigation agent unrestricted tools.

### Role safety rules

- The investigation agent receives ticket and customer context from the server. It cannot choose or change the customer ID.
- Protected write actions require an `approver` decision in the first release.

## 3. Supported issue categories

The first release supports only these four categories:

1. **Webhook delivery failure** — a webhook was not delivered or repeatedly failed.
2. **Background job failure** — an import, export, or sync job is stuck or failed.
3. **API access or rate limit issue** — requests fail because of authentication, authorization, or usage limits.
4. **Account or entitlement mismatch** — account status, plan access, feature flags, or entitlement cache does not match the expected state.

Tickets outside this scope must be escalated or handled manually.

## 4. Standard case flow

1. A support agent creates a ticket.
2. The system validates and classifies the issue.
3. If important information is missing, the system drafts clarification questions.
4. The system loads the customer's account and entitlement baseline.
5. The system retrieves relevant support documentation.
6. A bounded investigation agent selects read-only tools and collects evidence.
7. The system validates the evidence and produces ranked root-cause hypotheses.
8. The system drafts a resolution, proposes an approved safe action, or builds an escalation package.
9. A protected action waits for human approval.
10. After execution, the system reads the current state again to verify the result.
11. The ticket is resolved or escalated, and every important event is added to the audit trail.

## 5. End-to-end user stories

Five stories are used because the first four cover every supported issue category, while the fifth covers the important escalation path. Together they also exercise the three main outcomes: explain, act with approval, and escalate.

### US-01 — Diagnose a webhook endpoint rejection

As a support agent, I want ResolveAI to find a failed webhook delivery and its HTTP 401 response so that I can tell the customer to fix the endpoint credentials without replaying a request that will fail again.

**Category:** Webhook delivery failure  
**Expected outcome:** Evidence-backed explanation; no write action

### US-02 — Retry a temporary export failure

As a support agent, I want ResolveAI to identify a temporary dependency error in a failed export job so that it can propose a retry, wait for approval, execute it once, and verify the new job state.

**Category:** Background job failure  
**Expected outcome:** Approved action followed by verification

### US-03 — Explain an API rate limit

As a support agent, I want ResolveAI to compare recent API usage with the customer's plan limit so that I can explain an HTTP 429 response without changing the subscription.

**Category:** API access or rate limit issue  
**Expected outcome:** Evidence-backed explanation; prohibited subscription change is not proposed

### US-04 — Refresh stale entitlement data

As a support agent, I want ResolveAI to detect that the stored entitlement cache is older than the current subscription state so that it can propose a cache refresh, wait for approval, and verify access afterward.

**Category:** Account or entitlement mismatch  
**Expected outcome:** Approved action followed by verification

### US-05 — Escalate a platform webhook outage

As a support agent, I want ResolveAI to recognize evidence of a platform-wide webhook outage so that it stops customer-level actions and prepares an escalation package for engineering.

**Category:** Webhook delivery failure  
**Expected outcome:** Escalation with customer impact, evidence, attempted checks, and unanswered questions

## 6. Allowed actions and risk rules

The investigation agent may propose an action, but it never executes a write tool directly.

| Action | Risk | Approval in first release | Reason |
|---|---|---|---|
| Retry a failed job | Medium | Required | A retry can duplicate work or consume resources. |
| Replay a webhook delivery | Medium | Required | A replay can trigger a duplicate downstream operation. |
| Refresh entitlement cache | Medium | Required | Incorrect use can temporarily affect customer access. |
| Append an internal ticket note | Low | Required | The note becomes part of the permanent support record. |
| Escalate a ticket | Low | Not required for `support_agent` | Escalation changes internal workflow but does not modify customer data. |

Every executable action must use validated arguments, an idempotency key, an audit record, and a post-action verification step.

## 7. Prohibited actions

ResolveAI must not:

- Issue refunds or credits.
- Delete customer data.
- Reset multi-factor authentication or API keys.
- Change a subscription.
- Modify production configuration.
- Run arbitrary SQL, shell commands, file paths, or URLs supplied by the model.

These actions must be handled by an authorized human process outside the investigation agent.

## 8. Non-goals for the first release

- A general customer-facing chatbot.
- Voice support.
- A multi-agent system.
- Automatic closure of every ticket.
- Support for every possible SaaS issue.
- Model fine-tuning.
- Kubernetes deployment.
- Simultaneous integrations with Zendesk, Salesforce, Slack, Jira, and Gmail.

## 9. Phase 0 acceptance criteria

Phase 0 is complete when:

- Every user story maps to one of the four supported categories.
- The stories cover explanation, approved action, and escalation outcomes.
- Every write action has a clear risk and approval rule.
- The investigation agent has no direct write permission.
- The three internal roles have distinct responsibilities.
- Out-of-scope and high-risk cases have a clear stopping path.
