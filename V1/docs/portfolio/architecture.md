# ResolveAI Architecture Evidence

## System architecture

```mermaid
flowchart LR
    Customer[Customer View] --> API[FastAPI]
    Support[Support View] --> API
    API --> CustomerGraph[Customer Support Graph]
    CustomerGraph --> CustomerDocs[(Customer documents)]
    CustomerGraph --> ResolveLab[ResolveLab Simulator]
    CustomerGraph --> Ticket[(Ticket)]
    Ticket --> SupportGraph[Support Graph]
    SupportGraph --> InternalDocs[(Internal documents)]
    SupportGraph --> ResolveLab
    SupportGraph --> Approval[Human approval]
    Approval --> SupportGraph
    CustomerGraph --> Checkpoints[(PostgreSQL checkpoints)]
    SupportGraph --> Checkpoints
    Ticket --> BusinessData[(PostgreSQL business data)]
```

The browser only receives customer-safe fields in Customer View. Support View can read the saved handoff, evidence, diagnosis, approval, execution result, and escalation package.

## Customer Support Graph

```mermaid
stateDiagram-v2
    [*] --> SaveMessage
    SaveMessage --> UnderstandProblem
    UnderstandProblem --> ReadCustomerData
    ReadCustomerData --> AskOneQuestion: required information is still missing
    AskOneQuestion --> [*]
    ReadCustomerData --> ReadCustomerDocuments: enough information exists
    ReadCustomerDocuments --> OfferSteps: safe supported steps exist
    OfferSteps --> WaitForCustomer
    WaitForCustomer --> VerifyResult
    VerifyResult --> [*]: recovered
    VerifyResult --> BuildHandoff: unresolved
    ReadCustomerDocuments --> BuildHandoff: no safe customer step
    BuildHandoff --> CreateTicket
    CreateTicket --> [*]
```

## Support Graph

```mermaid
stateDiagram-v2
    [*] --> LoadHandoff
    LoadHandoff --> Investigate
    Investigate --> ValidateEvidence
    ValidateEvidence --> Finalize: resolution or engineer escalation
    ValidateEvidence --> CheckAction: safe action proposed
    CheckAction --> Finalize: rejected
    CheckAction --> SaveProposal: allowed
    SaveProposal --> WaitForApproval
    WaitForApproval --> Recheck
    Recheck --> Finalize: rejected or state changed
    Recheck --> ExecuteOnce: approved and still valid
    ExecuteOnce --> VerifyByReading
    VerifyByReading --> Finalize
    Finalize --> [*]
```

## Simplified data relationships

```mermaid
erDiagram
    SUPPORT_SESSION ||--o| TICKET : creates
    TICKET ||--o| ACTION_PROPOSAL : has
    ACTION_PROPOSAL ||--o| APPROVAL : receives
    ACTION_PROPOSAL ||--o| ACTION_EXECUTION : produces

    SUPPORT_SESSION {
        string session_id
        string customer_id
        string status
    }
    TICKET {
        int id
        json handoff
        json investigation_result
        string status
    }
    ACTION_PROPOSAL {
        int id
        int ticket_id
        json proposal
        string status
    }
    APPROVAL {
        int id
        int proposal_id
        string decision
    }
    ACTION_EXECUTION {
        int id
        int proposal_id
        string idempotency_key
        string status
    }
```

## Key decisions

1. The model can propose an action but cannot call a write tool.
2. Server code rebuilds action parameters from the current ticket, evidence, and ResolveLab state.
3. Approval pauses the saved Support Graph and resumes the same ticket after a decision.
4. A repeated write uses the same key, so ResolveLab changes state once.
5. A successful write response is not treated as recovery. The system reads the operation again before telling the customer it succeeded.
6. Customer and internal documents use separate filters and separate tool paths.

## Threat model

| Threat | Control | Evidence |
|---|---|---|
| Customer asks for another customer's data | Customer ID is bound by server code | Cross-customer evaluation cases and tool tests |
| Customer message tries to change system rules | Customer text is treated as untrusted data | Malicious customer cases |
| Document contains instructions for the model | Documents are treated as reference data and filtered by visibility, version, and feature | Malicious document case |
| Model cites a record that does not exist | Server validates evidence IDs and ownership | Evidence validation tests |
| Model tries to perform a write | No write tool is exposed to either model | Tool-list safety test |
| Action runs without approval | Execution checks the saved approval and fixed demo role | Approval bypass test |
| Retry runs more than once | Backend and ResolveLab use the same saved key | Repeated approval and retry tests |
| Service restarts during approval | Graph state is stored in PostgreSQL | Restart recovery test |

## Current limits

- The approval role is a fixed demo role, not a full sign-in and permission system.
- ResolveLab is a local simulator, not a production service.
- Only one approved write action is supported.
- Public cloud deployment is outside the current local delivery.
