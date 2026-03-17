# AGENTS Guide

Use this file as the main handoff guide for coding agents working on this repo.

The goal is simple: an agent should be able to understand the project, run it locally, extend it safely, and keep the repo publishable without needing extra context from the original author.

## Project Purpose

This repo is a local-first Google Play subscription operations dashboard.

It exists to reduce manual Google Play Console work for:
- creating subscriptions
- creating base plans
- applying regional pricing
- creating offers
- repairing existing SKUs

The main operator surface is the Streamlit dashboard in `dashboard_android.py`.

## Core Principles

- Keep credentials local.
- Do not turn this into a hosted credential-storage product.
- Prefer upload-first settings, not manual path entry.
- Keep preview-first execution. Users should preview before execute.
- Treat live Google API responses as the source of truth when local heuristics disagree.
- Keep the repo publishable. Do not introduce machine-specific or private defaults into tracked files.

## Local Files That Must Stay Out Of Git

Never commit:
- `config.local.json`
- `dashboard_settings.local.json`
- `secrets/`
- `token.json`
- `logs/`

Public-safe files include:
- `config.template.json`
- `README.md`
- `AGENTS.md`
- the Python source files

## Main Files

- `dashboard_android.py`
  Main Streamlit UI, create flow, modify flow, preview logic, execute logic.

- `pricing_utils.py`
  Pricing import, normalization, region parsing, template generation, bulk parsing, Google region helpers.

- `auth_manager.py`
  Google authentication logic. Prefer service account auth. OAuth2 exists only as fallback.

- `subscription_client.py`
  Lower-level Google Play client operations from the older project.

- `subscription_manager.py`
  Older orchestration layer kept for reusable logic.

- `data_models.py`
  Shared structures and validations.

- `excel_processor.py`
  Spreadsheet parsing helpers.

## Expected User Workflow

### Create

1. Save service account JSON and package name.
2. Load Google region template.
3. Download pricing template.
4. Fill pricing file.
5. Preview payload.
6. Execute create.
7. Review diagnostics.

### Modify / Repair

1. Load Google region template.
2. Load an existing product ID and base plan ID.
3. Upload pricing input.
4. Preview modify payload.
5. Execute modify / repair.

## Important Product Behaviors

### Regions Version

- Create and modify depend on a live Google `regionsVersion`.
- Do not reintroduce silent fallback behavior.
- Preview and execute should stay tied to the currently loaded Google region template.

### Pricing

- The dashboard accepts CSV, Excel, and Google Sheet inputs.
- Bulk mode is SKU-aware.
- Automatic price rounding is intentional and should remain visible in preview.
- Region and currency mismatches should be surfaced clearly.

### Offers

- Offer creation is optional.
- `INTRO PRICE` is currently used for offer pricing input.
- Google offer payloads are strict. Change carefully.

### Legacy Compatibility

- The dashboard exposes legacy-compatible base plan settings because some apps still use deprecated Billing Library flows.
- Do not remove this unless the product intentionally drops support for those cases.

## Editing Guidance

When changing code:
- preserve the dashboard-first workflow
- preserve local-first credential handling
- avoid breaking bulk create
- avoid breaking modify / repair
- avoid hiding Google API errors too much; make them easier to understand instead

Be especially careful with:
- `regionsVersion`
- billable region filtering
- currency and region matching
- price rounding
- offer payload shape
- stale preview state

## If You Need To Add Features

Preferred approach:
1. extend preview first
2. add validation
3. then wire execute
4. then update README
5. keep AGENTS guidance aligned if the workflow changes

Do not add undocumented behavior that changes how operators are expected to use the dashboard.

## If You Need To Publish The Repo

Before publishing:
- confirm secrets are ignored
- confirm tracked files contain no local paths or private defaults
- confirm README reflects the current dashboard behavior
- run syntax validation

## Validation

Run this after Python edits:

```bash
python3 -m py_compile dashboard_android.py pricing_utils.py auth_manager.py subscription_client.py subscription_manager.py data_models.py excel_processor.py
```

## README Relationship

`README.md` is for human users of the repo.

`AGENTS.md` is for coding agents and maintainers.

If the workflow changes, both should be updated together.
