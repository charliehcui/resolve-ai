---
title: Report export dependency timeout runbook
visibility: INTERNAL
product: ResolveLab
feature: report exports
version: 2026.8
source_uri: docs/internal/report-export-timeout.md
effective_from: 2026-08-01
effective_to:
---

# Report export dependency timeout runbook

A report export with status failed, latest run status failed, failure code dependency_timeout, and retry_allowed true is eligible for a human-controlled retry when the report exports platform is operational.

This finding only supports an action proposal. It does not authorize or execute a retry, and it does not prove recovery.
