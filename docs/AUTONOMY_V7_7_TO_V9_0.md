# QLDA Automation & AI Autonomy Roadmap — V7.7 → V9.0

This document defines the production architecture for progressively automating QLDA without allowing AI to bypass business rules, RBAC, approvals or auditability.

## V7.7 — Data Integrity & Evidence Layer

Goals:
- preserve every source row;
- reconcile source rows vs persisted rows;
- compute deterministic checksums and Evidence IDs;
- block write-capable AI actions when data integrity is not valid;
- distinguish raw rows, normalized rows and official source totals.

Core: `qlda.autonomy.integrity.DataIntegrityGate`.

Mandatory invariant: **AI_DATA_VALID=False means no autonomous business-state change.**

## V7.8 — Unified Service / AI Tool Layer

All AI actions cross `ToolRegistry`. AI must never write database tables directly.

Canonical tools include:
- `get_project_status`
- `sync_google_data`
- `check_data_integrity`
- `create_work_task`
- `draft_rfi`
- `draft_ncr`
- `update_schedule_progress`
- `approve_document`
- `approve_ipc`
- `approve_vo`
- `close_ncr`
- `generate_report`
- `send_notification`

Each tool owns risk level, execution mode, allowed roles, approval requirements, idempotency and audit metadata.

## V7.9 — Event & Background Automation

Canonical events:
- `GOOGLE_SHEET_UPDATED`
- `DOCUMENT_UPLOADED`
- `BOQ_IMPORTED`
- `IPC_IMPORTED`
- `RFI_OVERDUE`
- `NCR_OVERDUE`
- `INSPECTION_REJECTED`
- `SCHEDULE_DELAYED`
- `CONTRACT_EXPIRING`
- `PRODUCTION_CHANGED`
- `PAYMENT_OVERDUE`
- `NEW_DRAWING_REVISION`
- `DATA_INTEGRITY_FAILED`
- `APPROVAL_GRANTED`

Events are replay-safe through deterministic IDs. Durable storage uses `qlda_ai_events`.

## V8.0 — AI Orchestrator

Pipeline:

`request/event → planner → execution plan → tool registry → approval gate → executor → audit`

The deterministic `HeuristicPlanner` is the safe fallback. A model-backed planner may be plugged in later but must produce the same `ExecutionPlan` and cannot bypass tool policy.

## V8.1 — AI Project Supervisor

The supervisor calculates transparent, testable findings before AI explains them. Current checks include:
- data integrity;
- schedule delay;
- stalled daily production;
- overdue NCR/RFI;
- rejected inspections;
- expiring contracts;
- overdue payments.

Output: `ProjectHealthReport` + proposed actions.

Recommended scheduler:
- 02:00 backup;
- 06:00 source synchronization;
- 06:15 integrity checks;
- 06:20 supervisor evaluation;
- 06:30 daily project-health report;
- 07:00 notifications/dashboard.

## V8.2 — Semi-Autonomous QLDA

Policy tiers:
- read-only: automatic;
- draft: automatic;
- low-risk operational action: automatic when data is valid;
- medium/high/critical actions: approval according to role and policy.

Always protected:
- document approval;
- IPC approval;
- VO approval;
- NCR closure;
- schedule progress state change.

Human approval is a feature, not a limitation: the system may prepare and validate everything before presenting the final decision.

## V9.0 — Autonomous Construction Management + Digital Twin

`ProjectDigitalTwin` represents current schedule, production, cost, quality, safety, cash exposure and data integrity. The scenario engine can evaluate what-if assumptions such as productivity, time horizon and added risk.

The default engine is deterministic and explainable. ML/PINN/optimization models can be injected through the predictor interface without changing business controls.

### Target V9 loop

1. ingest project data;
2. reconcile and issue evidence IDs;
3. update digital-twin state;
4. detect changes/events;
5. supervisor finds anomalies;
6. orchestrator creates a multi-step plan;
7. low-risk steps execute automatically;
8. protected steps wait for approval;
9. results are audited;
10. twin is updated and the loop repeats.

## Persistence

The autonomy repository defines:
- `qlda_ai_events`
- `qlda_ai_action_audit`
- `qlda_ai_approvals`
- `qlda_ai_project_snapshots`

## Production integration rule

Existing QLDA business logic remains the source of truth. To enable a tool in production, register an adapter that calls the existing service/use-case. Never implement an AI-only duplicate of BOQ, IPC, VO, schedule, document or contractor logic.

## Release activation

The codebase can contain later-stage capability while the active production release remains earlier. A stage is considered activated only after:
1. adapter wiring is complete;
2. RBAC and approval tests pass;
3. migration/reconciliation is validated on production-like data;
4. both native and Docker CI pass;
5. rollback procedure is documented.
