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
import datetime

import gspread
import requests
from google.oauth2.service_account import Credentials

# ── Sheet config ──────────────────────────────────────────────────────────────

SHEET_TAB = "Helpdesk Tickets"

# Column order in the spreadsheet. Must stay in sync with the row list built
# in build_row() below.
COLUMNS = [
    "issue_number",       # A  ┐ composite key —
    "repo",               # B  ┘ both columns together uniquely identify a row
    "title",              # C
    "url",                # D  written as =HYPERLINK() for clickability
    "opened_by",          # E
    "opened_at",          # F
    "requesting_org",     # G
    "category",           # H
    "impact",             # I
    "reproducibility",    # J
    "platform",           # K
    "assignees",          # L
    "status",             # M
    "closed_at",          # N
    "time_to_close_days", # O
    "story_points",            # P  parsed from GitHub Projects v2 Estimate field
    "labels",                  # Q
    "triage_category",         # R
    "root_cause",              # S
    "resolution_description",  # T
    "notes",                   # U  ← manually maintained; never overwritten by automation
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


def extract_checked_items(body: str, section_title: str) -> str:
    """
    Return a comma-separated string of checked checkbox labels under a section.

    Matches lines of the form '- [x] Label' (case-insensitive) that appear
    after '### Section Title' and before the next '###' header or end of body.
    """
    pattern = rf'^###\s+{re.escape(section_title)}\s*\n(.*?)(?=^###|\Z)'
    m = re.search(pattern, body or "", re.MULTILINE | re.DOTALL)
    if not m:
        return ""
    checked = re.findall(r'^\s*-\s*\[x\]\s*(.+)', m.group(1), re.MULTILINE | re.IGNORECASE)
    return ", ".join(item.strip() for item in checked)


# ── Org → assignee lookup ─────────────────────────────────────────────────────

def match_org(requesting_org: str, org_map: dict) -> str | None:
    """
    Case-insensitive substring match: org_map key appears in requesting_org,
    or requesting_org appears in the key.  Returns the GitHub username or None.
    """
    org_lower = requesting_org.lower()
    for key, assignee in org_map.items():
        if key.lower() in org_lower or org_lower in key.lower():
            return assignee
    return None


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
    a = datetime.datetime.fromisoformat(a_iso.replace("Z", "+00:00"))
    b = datetime.datetime.fromisoformat(b_iso.replace("Z", "+00:00"))
    return round((b - a).total_seconds() / 86400.0, 2)


# ── Google Sheets helpers ─────────────────────────────────────────────────────

def col_letter(n: int) -> str:
    """Convert 1-based column index to a spreadsheet letter (A, B, … Z, AA …)."""
    result = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


END_COL = col_letter(len(COLUMNS))   # e.g. "R" for 18 columns


def open_worksheet(sheet_id: str, creds_dict: dict) -> gspread.Worksheet:
    """Authenticate and return the helpdesk worksheet, creating it if needed."""
    client = gspread.service_account_from_dict(creds_dict, scopes=GOOGLE_SCOPES)
    sh = client.open_by_key(sheet_id)

    try:
        ws = sh.worksheet(SHEET_TAB)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=SHEET_TAB, rows=2000, cols=len(COLUMNS))

    # Create header row if the sheet is empty or was just created.
    first_row = ws.row_values(1)
    if not first_row or first_row[0] != "issue_number":
        ws.insert_row(COLUMNS, index=1)
        ws.freeze(rows=1)
        print("Created header row.")

    return ws


def find_issue_row(ws: gspread.Worksheet, repo: str, issue_number: int) -> int | None:
    """
    Return the 1-based row index matching (repo, issue_number), or None.
    Both columns are checked because the same issue number can appear in
    multiple repositories.
    """
    all_rows = ws.get_all_values()  # list of lists; row 0 is the header
    for i, row in enumerate(all_rows):
        if len(row) >= 2 and row[0] == str(issue_number) and row[1] == repo:
            return i + 1  # gspread rows are 1-indexed
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
    issue_closed_at  = issue.get("closed_at") or ""
    issue_state      = issue["state"]
    issue_body       = issue.get("body") or ""
    assignees        = [a["login"] for a in issue.get("assignees", [])]
    label_names      = [lb["name"] for lb in issue.get("labels", [])]
    repo             = f"{repo_owner}/{repo_name}"

    # Load org → assignee map
    script_dir = os.path.dirname(os.path.abspath(__file__))
    map_path   = os.path.join(script_dir, "..", "helpdesk", "org_assignee_map.json")
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
    resolution_description = extract_field(issue_body, "Resolution Description")
    story_points = get_estimate_from_project(repo_owner, repo_name, issue_number, token)

    # ── Auto-assign whenever the issue has no assignee yet ───────────────────
    # Not limited to "opened" because the labeled event often fires first and
    # cancels the opened run (GitHub applies template labels near-simultaneously).
    if not assignees:
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

    # ── Open the worksheet ────────────────────────────────────────────────────
    ws = open_worksheet(sheet_id, creds_dict)

    # ── Preserve manually-entered notes ──────────────────────────────────────
    existing_notes = ""
    row_idx = find_issue_row(ws, repo, issue_number)
    if row_idx:
        notes_col_idx = COLUMNS.index("notes") + 1  # 1-based
        existing_notes = ws.cell(row_idx, notes_col_idx).value or ""

    # ── Build row (order matches COLUMNS exactly) ─────────────────────────────
    # The url cell uses a HYPERLINK formula so it renders as a clickable link.
    url_cell = f'=HYPERLINK("{issue_url}", "#{issue_number}")'

    row = [
        str(issue_number),
        repo,
        issue_title,
        url_cell,
        issue_author,
        issue_created_at,
        requesting_org,
        category,
        impact,
        reproducibility,
        platform,
        ", ".join(assignees),
        status,
        issue_closed_at,
        time_to_close,
        story_points,
        ", ".join(label_names),
        triage_category,
        root_cause,
        resolution_description,
        existing_notes,         # preserved — never clobbered by automation
    ]

    # ── Write to sheet ────────────────────────────────────────────────────────
    if row_idx:
        range_notation = f"A{row_idx}:{END_COL}{row_idx}"
        # USER_ENTERED is required so the =HYPERLINK() formula is evaluated.
        ws.update(range_notation, [row], value_input_option="USER_ENTERED")
        print(f"Updated row {row_idx} for issue #{issue_number} in {repo} "
              f"(event: {event_action}, status: {status})")
    else:
        ws.append_row(row, value_input_option="USER_ENTERED")
        print(f"Appended new row for issue #{issue_number} in {repo} "
              f"(event: {event_action})")


if __name__ == "__main__":
    main()
