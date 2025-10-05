# AGENTS.md — (PriceBot/app) Path Guide

Scope: This file governs the `app/` path (FastAPI API surface, ingestion orchestrators, services, templates, UI).

Read this first if you’re contributing, reviewing, or acting as an automated coding agent within `app/`.

## Reading Order

README.md (product overview and feature tour)

docs/ingestion_playbook.md (ingestion architecture and CLI usage)

docs/PROJECT_PLAN.md (roadmap, priorities, and status)

## Intent & Principles

- SOLID, KISS, YAGNI; lean modules that expose small, composable interfaces.

- Ingestion-first domain design: isolate ingestion, normalization, and API/chat delivery steps.

- End-to-end traceability: every response must link back to source documents, upload metadata, and storage artefacts.

- Security by default: encryption at rest/in transit, least-privilege dependencies, sanitized user uploads.

- Testability: deterministic boundaries, fast unit tests, targeted integration flows for ingestion + retrieval.

- Clarity: idiomatic Python (PEP 8), type hints, only necessary explanatory comments.

## Expectations for Agents/Contributors

- Skim README.md, docs/ingestion_playbook.md, docs/PROJECT_PLAN.md before coding.

- Keep work scoped to `app/` and coordinate cross-path changes via issues/PRs.

- Plan via GitHub Issues (no ad-hoc trackers). Document assumptions about uploads/chat flows in issue notes.

- Add or update unit tests for ingestion processors, services, and API/chat endpoints you touch.

- Provide integration coverage for the user interaction surfaces: upload → document ingestion → product answer payloads.

- Structured logging only; never rely on `print`.

## Session Handoff Protocol (GitHub Issues)

- Start: pick a ready P0 issue under the chat/ingestion backlog, self-assign, post a “Session Start” plan.

- During: drop concise updates at milestones; adjust labels as needed (blocked, ready for review, etc.).

- End: post “What landed” + “Next steps”, update project boards, link merged PRs.

- If behavior/architecture changes, update docs/ingestion_playbook.md or README.md within the same PR.

### Task Tooling (GitHub)

- Windows PowerShell (preferred on Windows):

- Pick a ready P0 task and mark it in-progress:
  `gh issue list -l P0 -s open`
  `gh issue edit <#> --add-label in-progress --assignee @me`

- Update status/comment:
  `gh issue comment <#> -b "Status: in-progress — <brief update>"`
  `gh issue edit <#> --remove-label in-progress --add-label blocked|done`

- Quickly show the top ready P0:
  `gh issue list -l P0 -s open --limit 1`

- Bash (macOS/Linux):

- `gh issue list -l P0 -s open`
- `gh issue edit <#> --add-label in-progress --assignee @me`
- `gh issue comment <#> -b "Status: <ready|in-progress|blocked|done> — <note>"`

- Note: If CRLF line-endings cause issues, prefer the GitHub CLI (`gh`) which is cross-platform.

All tools read `GITHUB_TOKEN` (environment variable or `gh auth login`). On CI, ensure the token has repo scope.

## Code Organization

`app/main.py` — FastAPI bootstrap, lifespan hooks, dependency wiring.

`app/api/routes/` — REST endpoints for documents, offers, products, health; extend with chat/composite answer route.

`app/services/` — Business logic orchestrators (ingestion pipelines, product lookups, aggregation for chat answers).

`app/ingestion/` — Processors for spreadsheets, WhatsApp transcripts, OCR/PDF; emit normalized offers + metadata.

`app/ui/` & `app/templates/` — Operator/admin experiences and future chat UI scaffolding.

`app/core/` & `app/db/` — Configuration, settings, session management, models, migrations.

Tests live in `tests/app/...` mirroring the vertical slices above.

### File Layout Rules (Vertical Slice)

- One domain concept per module; filenames reflect the concept (`document.py`, `offers.py`, `chat_service.py`).

- Shared abstractions stay in `app/ingestion/base.py`, `app/services/interfaces.py`, etc.

- Feature-specific contracts co-locate with implementations inside their feature package.

- Organize by vertical slice first (api/, services/, ingestion/, ui/); keep contracts vs implementations separate when useful.

- Avoid mixing unrelated features in the same folder.

## Workflow & Quality

- User flow baseline: upload artefact → ingestion persists document/offers → chat/API surfaces expose aggregated answers.

- Responses for a product query must include latest price, available price history summary, specs, photo URL (if present), upload timestamp, and uploader identity.

- Store artefacts under `storage/` and record provenance inside `app/db/models.py` entities.

- Feature toggles/configuration via `app/core/config.py`; default to disabled for experimental chat surfaces.

- APIs/services must stay async-friendly; avoid blocking IO in request paths.

- Validate payloads with Pydantic models; return structured errors.

## Roadmap & Priorities

- Current Sprint: unify ingestion outputs so chat answers can hydrate price/spec metadata.

- Next: expose chat-friendly endpoints and WhatsApp-compatible webhook payloads.

- Later: analytics layer (market trends, derived metrics, manipulations) built atop normalized offers.

- Keep GitHub issues atomic, label by P0/P1/P2, and link to roadmap milestones.

## Coding Standards

- Target Python 3.11+, Ruff for linting (~100 char lines), pytest for testing.

- Async-first; use FastAPI dependencies for sessions/services.

- Prefer context managers (`with` / `async with`) for resource management.

- SQLModel/SQLAlchemy: entities in `app/db/models.py`, session helpers in `app/db/session.py`, migrations per feature.

- Modern typing everywhere; remove dead code.

- Logging via `logging` with structured extras; no console prints in production flows.

## Documentation Rules

- Keep README.md, docs/ingestion_playbook.md, docs/PROJECT_PLAN.md updated when flows change.

- Document new chat endpoints or services in docs/API_REFERENCE.md or inline docstrings.

- Link GitHub issues/PRs when adjusting ingestion processors or response payloads.

## Ambiguity

- Default to the simplest design that satisfies current requirements.

- When multiple viable options exist, note the rationale in the PR/issue and update docs as needed.
