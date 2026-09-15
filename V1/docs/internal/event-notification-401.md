---
title: Event notification HTTP 401 runbook
visibility: INTERNAL
product: ResolveLab
feature: order notifications
version: 2026.8
source_uri: docs/internal/event-notification-401.md
effective_from: 2026-08-01
effective_to:
---

# Event notification HTTP 401 runbook

When recent delivery records consistently show response status 401 while the event notification platform is operational, the receiving endpoint rejected ResolveLab authentication.

Confirm that the records belong to the current customer and problem time. The safe resolution is for the customer to check the receiving endpoint access settings and then send one test notification. Do not claim that an operational platform snapshot proves delivery success.
