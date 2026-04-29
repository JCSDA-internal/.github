#!/usr/bin/env python3
"""
helpdesk_sheet_sync.py
======================
Syncs a GitHub helpdesk issue to a Google Sheet row.

Triggered by helpdesk-sheet-sync.yml on issue open / edit / close / reopen /
assign / label events.  Each issue occupies exactly one row, keyed by the
composite (repo, issue_number) because the same number can exist in multiple
repos.  On every event the row is overwritten with current state except for
the 'notes' column, which is manually maintained by the team and is always
preserved.

On the 'opened' event the script also auto-assigns the issue to the JCSDA
liaison for the partner organisation (looked up via org_assignee_map.json).

Required env vars (all set by the workflow):
  GH_TOKEN                    GitHub token with issues:write
  GOOGLE_SERVICE_ACCOUNT_JSON Service account JSON key (full file contents)
  HELPDESK_SHEET_ID           Google Sheet ID from its URL
  ISSUE_JSON                  toJSON(github.event.issue) payload
  EVENT_ACTION                github.event.action
  REPO_OWNER                  github.repository_owner
  REPO_NAME                   github.event.repository.name
"""

import json
import os
import re
import time
import random
import datetime

import yaml

import gspread
import requests

# ── Retry helpers ────────────────────────────────────────────────────────────

_RETRY_ATTEMPTS = 5
_RETRY_BASE     = 2.0   # seconds; doubles each attempt + jitter


def _sheet_write_with_retry(fn, *args, **kwargs):
    """
    Call fn(*args, **kwargs) with exponential-backoff retry on gspread API errors.

    Needed because multiple repos can push helpdesk events simultaneously to the
    same Google Sheet, and the Sheets API returns 429 / 503 under write contention.
    """
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return fn(*args, **kwargs)
        except gspread.exceptions.APIError as exc:
            status = getattr(exc.response, "status_code", None)
            if attempt == _RETRY_ATTEMPTS - 1 or status not in (429, 500, 503):
                raise
            delay = _RETRY_BASE * (2 ** attempt) + random.uniform(0, 1)
            print(f"Sheet API error {status} on attempt {attempt + 1}; "
                  f"retrying in {delay:.1f}s …")
            time.sleep(delay)


# ── Sheet config ──────────────────────────────────────────────────────────────

SHEET_TAB = "Helpdesk Tickets"

# Column order in the spreadsheet. Must stay in sync with the row list built
# in main() below.
COLUMNS = [
    # ── Ticket Information ────────────────────────────────────────────────────
    "issue_number",           # A  ┐ composite key —
    "repo",                   # B  ┘ both columns together uniquely identify a row
    "title",                  # C
    "url",                    # D  written as =HYPERLINK() for clickability
    "labels",                 # E
    # ── Requester Information ─────────────────────────────────────────────────
    "opened_by",              # F
    "opened_at",              # G
    "requesting_org",         # H
    "category",               # I
    "impact",                 # J
    "reproducibility",        # K
    "platform",               # L
    # ── Work Tracking ─────────────────────────────────────────────────────────
    "assignees",              # M
    "status",                 # N
    "closed_at",              # O
    "time_to_close_days",     # P
    "story_points",           # Q  parsed from GitHub Projects v2 Estimate field
    # ── Maintainer Notes ──────────────────────────────────────────────────────
    "triage_category",        # R
    "root_cause",             # S
    "resolution_description", # T
    "notes",                  # U  ← manually maintained; never overwritten by automation
]

# Row-1 section headers: (label, first_col_1based, last_col_1based inclusive)
_SECTIONS = [
    ("Ticket Information",     1,  5),  # A–E
    ("Requester Information",  6, 12),  # F–L
    ("Work Tracking",         13, 17),  # M–Q
    ("Maintainer Notes",      18, 21),  # R–U
]

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

# ── Form-field parsing ────────────────────────────────────────────────────────

def extract_field(body: str, section_title: str) -> str:
    """
    Pull the first non-blank line after a GitHub issue form section header.

    GitHub renders form fields as:
        ### Section Title
        <blank line>
        Value text
    """
    pattern = rf'^###\s+{re.escape(section_title)}\s*\n+([^\n]+)'
    m = re.search(pattern, body or "", re.MULTILINE)
    return m.group(1).strip() if m else ""


def _section_body(body: str, section_title: str) -> str | None:
    m = re.search(rf'^###\s+{re.escape(section_title)}\s*\n(.*?)(?=^###|\Z)',
                  body or "", re.MULTILINE | re.DOTALL)
    return m.group(1) if m else None


def extract_section(body: str, section_title: str) -> str:
    """
    Return all text under a GitHub issue form section header, up to the next
    '###' header or end of body, with leading/trailing whitespace stripped.
    """
    text = _section_body(body, section_title)
    return text.strip() if text is not None else ""


def extract_checked_items(body: str, section_title: str) -> str:
    """
    Return a comma-separated string of checked checkbox labels under a section.

    Matches lines of the form '- [x] Label' (case-insensitive) that appear
    after '### Section Title' and before the next '###' header or end of body.
    """
    text = _section_body(body, section_title)
    if text is None:
        return ""
    checked = re.findall(r'^\s*-\s*\[x\]\s*(.+)', text, re.MULTILINE | re.IGNORECASE)
    return ", ".join(item.strip() for item in checked)


# ── Issue template helpers ────────────────────────────────────────────────────

def load_field_placeholder(template_path: str, field_id: str) -> str:
    """
    Return the stripped default `value` for a textarea field in a GitHub issue
    form YAML template, identified by its `id`.  Returns '' if not found.
    """
    with open(template_path) as f:
        template = yaml.safe_load(f)
    for field in template.get("body", []):
        if field.get("id") == field_id:
            return field.get("attributes", {}).get("value", "").strip()
    return ""


# ── Org → assignee lookup ─────────────────────────────────────────────────────

_PLACEHOLDER_ORGS = {"na", "n/a", "none", "unknown", "n.a.", "not applicable", ""}

def match_org(requesting_org: str, org_map: dict) -> str | None:
    """
    Case-insensitive substring match: org_map key appears in requesting_org,
    or requesting_org appears in the key.  Falls back to org_map['default_assignee']
    when no specific match is found (including placeholder/unknown org values).
    Returns the GitHub username or None.
    """
    default = org_map.get("default_assignee")
    org_lower = requesting_org.lower().strip()
    if org_lower in _PLACEHOLDER_ORGS:
        return default
    for key, assignee in org_map.items():
        if key.startswith("_") or key == "default_assignee":
            continue
        if key.lower() in org_lower or org_lower in key.lower():
            return assignee
    return default


# ── GitHub API helpers ────────────────────────────────────────────────────────

def gh_assign(owner: str, repo: str, issue_number: int,
              assignees: list, token: str) -> None:
    """Add assignees to a GitHub issue."""
    url = (f"https://api.github.com/repos/{owner}/{repo}"
           f"/issues/{issue_number}/assignees")
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={"assignees": assignees},
        timeout=30,
    )
    resp.raise_for_status()


# ── Date helpers ──────────────────────────────────────────────────────────────

def days_between(a_iso: str, b_iso: str) -> float:
    a = datetime.datetime.fromisoformat(a_iso)
    b = datetime.datetime.fromisoformat(b_iso)
    return round((b - a).total_seconds() / 86400.0, 2)


# ── Google Sheets helpers ─────────────────────────────────────────────────────

def col_letter(n: int) -> str:
    """Convert 1-based column index to a spreadsheet letter (A, B, … Z, AA …)."""
    result = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


END_COL = col_letter(len(COLUMNS))   # "U" for 21 columns


def _remove_bold(ws: "gspread.Worksheet", range_notation: str) -> None:
    """Remove bold using a narrow fields mask so the HYPERLINK formula is preserved."""
    body = {
        "requests": [
            {
                "repeatCell": {
                    "range": gspread.utils.a1_range_to_grid_range(range_notation, ws.id),
                    "cell": {"userEnteredFormat": {"textFormat": {"bold": False}}},
                    "fields": "userEnteredFormat.textFormat.bold",
                }
            }
        ]
    }
    ws.spreadsheet.batch_update(body)


def _setup_header_rows(ws: "gspread.Worksheet", sa_email: str = "") -> None:
    """
    Build the two locked header rows on a fresh (or just-inserted) sheet.

    Row 1 — merged section banners: Ticket Information | Requester Information |
             Work Tracking | Maintainer Notes
    Row 2 — individual column names (COLUMNS list)

    Both rows are frozen and protected; the service-account email (sa_email)
    is added as an editor so automation can still reinitialise if needed.
    """
    sh = ws.spreadsheet
    requests = []

    # Merge row-1 cells within each section
    for _, start_col, end_col in _SECTIONS:
        requests.append({
            "mergeCells": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 0, "endRowIndex": 1,
                    "startColumnIndex": start_col - 1, "endColumnIndex": end_col,
                },
                "mergeType": "MERGE_ALL",
            }
        })

    # Format row 1: bold, centred, light-blue background
    requests.append({
        "repeatCell": {
            "range": {
                "sheetId": ws.id,
                "startRowIndex": 0, "endRowIndex": 1,
                "startColumnIndex": 0, "endColumnIndex": len(COLUMNS),
            },
            "cell": {"userEnteredFormat": {
                "textFormat": {"bold": True},
                "horizontalAlignment": "CENTER",
                "backgroundColor": {"red": 0.78, "green": 0.87, "blue": 0.95},
            }},
            "fields": "userEnteredFormat(textFormat.bold,horizontalAlignment,backgroundColor)",
        }
    })

    # Format row 2: bold, light-grey background
    requests.append({
        "repeatCell": {
            "range": {
                "sheetId": ws.id,
                "startRowIndex": 1, "endRowIndex": 2,
                "startColumnIndex": 0, "endColumnIndex": len(COLUMNS),
            },
            "cell": {"userEnteredFormat": {
                "textFormat": {"bold": True},
                "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9},
            }},
            "fields": "userEnteredFormat(textFormat.bold,backgroundColor)",
        }
    })

    # Lock rows 1–2; service account retains edit rights, everyone else sees a hard lock
    editors_payload = {"users": [sa_email]} if sa_email else {}
    requests.append({
        "addProtectedRange": {
            "protectedRange": {
                "range": {"sheetId": ws.id, "startRowIndex": 0, "endRowIndex": 2},
                "description": "Header rows — do not edit",
                "warningOnly": False,
                "editors": editors_payload,
            }
        }
    })

    sh.batch_update({"requests": requests})

    # Write section labels after merging so they land in the first cell of each region
    for label, start_col, _ in _SECTIONS:
        ws.update_cell(1, start_col, label)
    ws.update(f"A2:{END_COL}2", [COLUMNS])
    ws.freeze(rows=2)
    print("Created section and column header rows.")


def open_worksheet(sheet_id: str, creds_dict: dict) -> gspread.Worksheet:
    """Authenticate and return the helpdesk worksheet, creating it if needed."""
    client = gspread.service_account_from_dict(creds_dict, scopes=GOOGLE_SCOPES)
    sh = client.open_by_key(sheet_id)

    try:
        ws = sh.worksheet(SHEET_TAB)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=SHEET_TAB, rows=2000, cols=len(COLUMNS))

    # Row 2 holds the column headers in the new two-row layout.
    second_row = ws.row_values(2)
    if not second_row or second_row[0] != "issue_number":
        # Handle sheets that were initialised with the old single-header format:
        # insert a blank row above so existing column headers shift to row 2.
        first_row = ws.row_values(1)
        if first_row and first_row[0] == "issue_number":
            ws.insert_row([""] * len(COLUMNS), index=1)
        _setup_header_rows(ws, creds_dict.get("client_email", ""))

    return ws


def find_issue_row(
    ws: gspread.Worksheet,
    repo: str,
    issue_number: int,
    all_rows: list[list[str]] | None = None,
) -> int | None:
    """
    Return the 1-based row index matching (repo, issue_number), or None.
    Both columns are checked because the same issue number can appear in
    multiple repositories.  Data starts at row 3 (rows 1–2 are headers).

    Pass a pre-fetched all_rows to avoid a redundant get_all_values() call.
    """
    if all_rows is None:
        all_rows = ws.get_all_values()
    for i, row in enumerate(all_rows[2:], start=3):
        if len(row) >= 2 and row[0] == str(issue_number) and row[1] == repo:
            return i
    return None


# ── GitHub Projects helpers ───────────────────────────────────────────────────

def get_estimate_from_project(owner: str, repo: str, issue_number: int, token: str) -> str:
    """Return the Estimate field value (as a string) from GitHub Projects v2, or ''."""
    query = """
    query($owner: String!, $repo: String!, $number: Int!) {
      repository(owner: $owner, name: $repo) {
        issue(number: $number) {
          projectItems(first: 10) {
            nodes {
              fieldValues(first: 20) {
                nodes {
                  ... on ProjectV2ItemFieldNumberValue {
                    number
                    field { ... on ProjectV2FieldCommon { name } }
                  }
                }
              }
            }
          }
        }
      }
    }
    """
    resp = requests.post(
        "https://api.github.com/graphql",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        json={"query": query, "variables": {"owner": owner, "repo": repo, "number": issue_number}},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    items = (data.get("data", {})
                 .get("repository", {})
                 .get("issue", {})
                 .get("projectItems", {})
                 .get("nodes", []))

    for item in items:
        for fv in item.get("fieldValues", {}).get("nodes", []):
            if (fv.get("field", {}).get("name", "").lower() == "estimate"
                    and fv.get("number") is not None):
                val = fv["number"]
                return str(int(val)) if val == int(val) else str(val)
    return ""


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    # Load env
    token        = os.environ["GH_TOKEN"]
    sheet_id     = os.environ["HELPDESK_SHEET_ID"]
    creds_dict   = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    event_action = os.environ["EVENT_ACTION"]
    repo_owner   = os.environ["REPO_OWNER"]
    repo_name    = os.environ["REPO_NAME"]
    issue        = json.loads(os.environ["ISSUE_JSON"])

    # Unpack issue fields
    issue_number     = issue["number"]
    issue_title      = issue["title"]
    issue_url        = issue["html_url"]
    issue_author     = issue["user"]["login"]
    issue_created_at = issue["created_at"]

    # Refresh state/closed_at from the live API: the event payload is a
    # snapshot from when the event fired, so a 'labeled' event generated
    # while the issue was still open can arrive after the 'closed' event and
    # (via cancel-in-progress) overwrite the sheet with stale "Open" state.
    try:
        live = requests.get(
            f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues/{issue_number}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30,
        )
        live.raise_for_status()
        live_data = live.json()
        issue["state"]     = live_data["state"]
        issue["closed_at"] = live_data.get("closed_at")
    except Exception as exc:
        print(f"Warning: could not refresh issue state from API, using event payload: {exc}")

    issue_closed_at  = issue.get("closed_at") or ""
    issue_state      = issue["state"]
    issue_body       = issue.get("body") or ""
    assignees        = [a["login"] for a in issue.get("assignees", [])]
    label_names      = [lb["name"] for lb in issue.get("labels", [])]
    repo             = f"{repo_owner}/{repo_name}"

    # Load org → assignee map
    script_dir    = os.path.dirname(os.path.abspath(__file__))
    map_path      = os.path.join(script_dir, "..", "helpdesk", "org_assignee_map.json")
    template_path = os.path.join(script_dir, "..", "ISSUE_TEMPLATE", "Helpdesk.yml")
    with open(map_path) as f:
        org_map = json.load(f)

    # Parse structured form fields from the issue body
    requesting_org  = extract_field(issue_body, "Requesting Organization")
    category        = extract_field(issue_body, "Issue category (required for stats)")
    impact          = extract_field(issue_body, "Impact / priority")
    reproducibility = extract_field(issue_body, "Reproducibility")
    platform        = extract_field(issue_body, "Platform / system (select all that apply)")

    # Extract checkbox fields from the maintainer closure section of the issue body
    triage_category      = extract_checked_items(issue_body, "Triage Category / Maintainer Classification")
    root_cause           = extract_checked_items(issue_body, "Root Cause")
    resolution_description = extract_section(issue_body, "Resolution Description")
    resolution_placeholder = load_field_placeholder(template_path, "resolution")
    if resolution_placeholder and resolution_description.strip() == resolution_placeholder:
        resolution_description = ""
    # ── Auto-assign on open/label/assign events when no assignee is set ──────
    # Includes "labeled" because GitHub applies template labels near-simultaneously
    # with "opened", and the labeled event can fire first and cancel the opened run.
    # Excluding other events (edit, reopen, etc.) prevents silently undoing manual unassignment.
    if not assignees and event_action in {"opened", "labeled"}:
        liaison = match_org(requesting_org, org_map)
        if liaison:
            try:
                gh_assign(repo_owner, repo_name, issue_number, [liaison], token)
                assignees = [liaison]
                print(f"Auto-assigned issue #{issue_number} to {liaison} "
                      f"(matched org: {requesting_org!r})")
            except Exception as exc:
                # Non-fatal: sheet sync continues even if assignment fails.
                print(f"Warning: could not auto-assign issue #{issue_number}: {exc}")
        else:
            print(f"No org match found for {requesting_org!r}; skipping auto-assign.")

    # ── Computed fields ───────────────────────────────────────────────────────
    status         = "Closed" if issue_state == "closed" else "Open"
    time_to_close  = (str(days_between(issue_created_at, issue_closed_at))
                      if issue_closed_at else "")

    # ── Open the worksheet and locate any existing row ────────────────────────
    ws       = open_worksheet(sheet_id, creds_dict)
    all_rows = ws.get_all_values()
    row_idx  = find_issue_row(ws, repo, issue_number, all_rows)

    # ── Preserve manually-maintained columns from the existing row ────────────
    existing_notes = ""
    if row_idx is not None:
        notes_col_idx = COLUMNS.index("notes") + 1  # 1-based
        existing_notes = ws.cell(row_idx, notes_col_idx).value or ""

    # Only fetch story points on open/close; the Projects Estimate field doesn't
    # change on label/assign/edit events and the GraphQL call costs quota.
    if event_action in {"opened", "closed", "edited"}:
        story_points = get_estimate_from_project(repo_owner, repo_name, issue_number, token)
    elif row_idx is not None:
        sp_col_idx = COLUMNS.index("story_points") + 1  # 1-based
        story_points = ws.cell(row_idx, sp_col_idx).value or ""
    else:
        story_points = ""

    # ── Build row (order matches COLUMNS exactly) ─────────────────────────────
    # The url cell uses a HYPERLINK formula so it renders as a clickable link.
    url_cell = f'=HYPERLINK("{issue_url}", "#{issue_number}")'

    row = [
        # Ticket Information (A–E)
        str(issue_number),
        repo,
        issue_title,
        url_cell,
        ", ".join(label_names),
        # Requester Information (F–L)
        issue_author,
        issue_created_at,
        requesting_org,
        category,
        impact,
        reproducibility,
        platform,
        # Work Tracking (M–Q)
        ", ".join(assignees),
        status,
        issue_closed_at,
        time_to_close,
        story_points,
        # Maintainer Notes (R–U)
        triage_category,
        root_cause,
        resolution_description,
        existing_notes,         # preserved — never clobbered by automation
    ]

    # ── Write to sheet ────────────────────────────────────────────────────────
    if row_idx is not None:
        range_notation = f"A{row_idx}:{END_COL}{row_idx}"
        # USER_ENTERED is required so the =HYPERLINK() formula is evaluated.
        _sheet_write_with_retry(
            ws.update, range_notation, [row], value_input_option="USER_ENTERED"
        )
        _remove_bold(ws, range_notation)
        print(f"Updated row {row_idx} for issue #{issue_number} in {repo} "
              f"(event: {event_action}, status: {status})")
    else:
        _sheet_write_with_retry(ws.append_row, row, value_input_option="USER_ENTERED")
        new_row_idx = find_issue_row(ws, repo, issue_number)
        if new_row_idx is not None:
            _remove_bold(ws, f"A{new_row_idx}:{END_COL}{new_row_idx}")
        print(f"Appended new row for issue #{issue_number} in {repo} "
              f"(event: {event_action})")


if __name__ == "__main__":
    main()
