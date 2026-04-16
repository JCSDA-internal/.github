# Helpdesk Sheet Sync — Setup & Test Guide

## Overview

This guide walks through setting up and testing the Google Sheets integration
for the JCSDA helpdesk system. On each helpdesk issue event (open, edit, close,
etc.) a GitHub Actions workflow syncs issue data to a shared Google Sheet.

---

## Step 1 — Google Cloud: create the service account (~15 min)

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project (e.g., `jcsda-helpdesk`) or select an existing one
3. **Enable APIs**: Navigation menu → APIs & Services → Library
   - Search and enable **Google Sheets API**
   - Search and enable **Google Drive API**
4. **Create service account**: IAM & Admin → Service Accounts → **+ Create Service Account**
   - Name: `jcsda-helpdesk-sheets`
   - Skip project role (click through) — permissions come from the Sheet itself
   - Click **Done**
5. Click the new service account → **Keys** tab → **Add Key → Create new key → JSON**
   - Save the downloaded `.json` file somewhere temporarily (you will paste it into GitHub next)
   - Note the service account **email address**
     (e.g., `jcsda-helpdesk-sheets@your-project.iam.gserviceaccount.com`)

---

## Step 2 — Create the Google Sheet (~5 min)

1. Go to [sheets.google.com](https://sheets.google.com) → create a **Blank** spreadsheet
2. Name it `JCSDA Helpdesk Tracker`
3. **Share it with the service account**: Share button → paste the service account
   email → set to **Editor** → Send
4. Copy the **Sheet ID** from the URL bar:
   ```
   https://docs.google.com/spreadsheets/d/THIS_LONG_STRING_IS_THE_ID/edit
   ```

---

## Step 3 — Add GitHub Secrets (~5 min)

Go to `github.com/JCSDA-internal/.github/settings/secrets/actions` and add two secrets:

| Name | Value |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Paste the **entire contents** of the downloaded `.json` key file |
| `HELPDESK_SHEET_ID` | Paste just the Sheet ID string from Step 2 |

---

## Step 4 — Add a test entry to the org map

In [org_assignee_map.json](org_assignee_map.json), add at least one real
org/username pair so you can verify auto-assignment works. Leave the rest as
placeholders for now.

```json
{
  "NOAA/NWS/EMC": "real-github-username",
  ...
}
```

---

## Step 5 — Open the PR

Push the branch containing the following files and open a PR in `JCSDA-internal/.github`:

```
.github/workflows/helpdesk-sheet-sync.yml      ← new
.github/workflows/helpdesk-triage-labels.yml   ← bug fix
.github/scripts/helpdesk_sheet_sync.py         ← new
.github/helpdesk/org_assignee_map.json         ← new
.github/helpdesk/SETUP.md                      ← this file
.github/ISSUE_TEMPLATE/config.yaml             ← updated
```

> **Note:** `issues` event workflows only run from the **default branch**.
> The Actions tab will show the workflow files during PR review, but the
> triggers will not fire until the PR is merged. This is expected behavior.

---

## Step 6 — Merge and create the `helpdesk` label

After merging, confirm the `helpdesk` label exists in the repo (the issue
template references it). Check `github.com/JCSDA-internal/.github/labels` —
if it is not there, create it manually with any color.

---

## Step 7 — Open a test issue

Go to `github.com/JCSDA-internal/.github/issues/new/choose` → select
**Helpdesk Request** and fill in every required field. Use a **Requesting
Organization** value that matches your test entry in `org_assignee_map.json`
to verify auto-assignment.

---

## Step 8 — Verify

**GitHub Actions** (`github.com/JCSDA-internal/.github/actions`):
- `Helpdesk → Google Sheet sync` should show a completed run
  - Log should contain: `Appended new row for issue #N in JCSDA-internal/.github`
- `Helpdesk triage → labels` should also show a completed run

**GitHub issue:**
- The configured liaison should appear as assignee
- Labels `helpdesk` and any `triage:*` labels should be applied

**Google Sheet:**
- Row 1 should be a frozen header row (auto-created on first run)
- Row 2 should have all issue fields populated
- Column D (`url`) should display `#N` as a clickable hyperlink

**Close the test issue** and re-check the sheet:
- `status` → `Closed`
- `closed_at` → timestamp
- `time_to_close_days` → a decimal number

---

## Credential security notes

- The service account has no GCP project roles — its only access is the single
  Sheet it was shared on
- The `drive.file` OAuth scope used by the script limits it to files the service
  account created or was explicitly shared on; it cannot browse Drive
- Rotate the service account key periodically via GCP IAM & Admin → Service Accounts
- For production use, replace the JSON key with **Workload Identity Federation**
  (no long-lived credentials) — see Google's
  [GitHub Actions OIDC guide](https://cloud.google.com/blog/products/identity-security/enabling-keyless-authentication-from-github-actions)

---

## Expanding to org-wide coverage (Option B)

Currently all helpdesk tickets must be filed in `JCSDA-internal/.github`
because GitHub Actions workflows only fire for events in the repo they live in.
Partners in other repos are directed here via the `contact_links` entry in
`ISSUE_TEMPLATE/config.yaml`.

When moving to full operational use, the plan is to add an **org-level
webhook** routing `issues` events to an AWS Lambda (following the existing
pattern in `github-admin/webhooks/`). This will allow tickets to be filed in
any JCSDA-Internal repo while still syncing to the same sheet. The `repo`
column already in the spreadsheet schema handles the multi-repo case without
any schema changes.
