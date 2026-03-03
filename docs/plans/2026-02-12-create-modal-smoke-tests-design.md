# Create Modal Smoke Tests Design

**Date**: 2026-02-12
**Status**: Approved

## Problem

The existing Playwright tests verify that Create modals _open_ (DOM visible) but don't check whether they render content. This allowed a bug where `/api/v1/status` returning 404 caused modals to appear blank — the modal container was visible but contained zero form fields.

## Solution

Add a data-driven test block to `web/tests/e2e/web-ui.spec.ts` that iterates over all ~24 pages with Create/Add buttons.

### Per-page test assertions

1. Navigate to page
2. Click Create/Add button
3. Wait for modal to appear
4. **Not blank**: modal contains at least 1 visible `input`, `select`, or `textarea`
5. **No errors**: no console errors fired during modal open
6. Close modal

### Test matrix

Array of `{ route, name, button }` objects for all pages:
- Entities, Software, Services, Compute, Certificates, Issues, etc.
- Each gets a named test case for clear failure output

### Skip vs fail logic

- **Skip**: page 404s, or no Create button found (not deployed)
- **Hard fail**: button exists, modal opens, but content is blank or console errors occur

### Resilience

- `domcontentloaded` wait strategy (not networkidle)
- 10s timeout for modal content to appear
- Independent page loads per test (no shared state)
- Console error filtering: 404s and network errors excluded from pre-modal baseline, but errors _during_ modal open are caught

### Estimated execution

~45-90s for all 24 pages (2-4s each)
