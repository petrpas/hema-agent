
# hr_agent

## Purpose

The agent automates enrichment of HEMA tournament registration data. Tournament organizers receive raw sign-up data from a Google Form and need to enrich it with HEMA Ratings scores before they can set up competition pools. This process is currently manual and tedious — the agent handles it end-to-end.

## Input

A Google Sheet produced by a Google Form registration. Each row is one fencer with fields like:
- Name, club
- Which weapons they want to compete in
- Tournament-specific extras (e.g. joining afterparty, borrowing a weapon)

## Output

A filled-in Google Sheet template with:
- A **Fencers** worksheet: full enriched data for every registered fencer
- One worksheet per weapon (**LS**, **SA**, **RA**, **SB**, **RD**) listing only fencers competing in that weapon, with their HEMA Ratings ID, current rating, and current rank

## Weapon codes

| Code | Weapon            |
|------|-------------------|
| LS   | Long Sword        |
| SA   | Sabre             |
| RA   | Rapier            |
| SB   | Sword and Buckler |
| RD   | Rapier and Dagger |

---

## Pipeline

### Step 1 — Download registration sheet (no LLM)
- Open the registration Google Sheet by its configured URL
- Export it as a local CSV file
- Auth: `pygsheets` service account (`creds.json`)

### Step 2 — Parse registrations into structured data (LLM)
- Read the CSV and extract a clean, normalized list of fencers
- LLM is used to interpret the weapon selection field (which may be free text or ambiguous)
- Output: a structured record per fencer with their name, club, and a yes/no flag for each weapon

### Step 3 — Match fencers to HEMA Ratings profiles (LLM)
- Download the full fighter list from `https://hemaratings.com/fighters/`
- Also load `fencers.csv` — a local cache of previously matched fencers (name → hr_id), updated after every run
- LLM does fuzzy matching to find the right HEMA Ratings profile for each fencer
  - Handles name variants: e.g. "Jan Blazek" ↔ "Honza Blažek"
  - Uses club and nationality as secondary signals
- Fencers not present in the cache are matched fresh; already-cached matches are reused without an LLM call
- `fencers.csv` is updated with any newly matched fencers after each run
- Output CSV columns: `name, nationality, club, hr_id, LS, SA, RA, SB, RD` (weapon columns: Yes/No)

### Step 4 — Fetch current HEMA ratings and ranks (LLM)
- For each fencer with a known `hr_id`, scrape `https://hemaratings.com/fighters/details/{hr_id}/`
- A prepared HTML parser extracts the weighted rating and rank for each weapon category
- If the parser fails (the page format changes occasionally), the LLM receives the error and the raw HTML and rewrites the parser until it works
- Only fetch pages that are not already cached locally

### Step 5 — Update the Google Sheet template (LLM with tools)
- The LLM is given pygsheets functions as tools (read cell, write cell, find row, etc.)
- It reads the current state of the sheet and identifies what is missing or outdated
- It then makes only the necessary targeted updates — individual cells or rows — leaving everything else untouched
- The goal is to simulate a human manually filling in an already partially-processed spreadsheet: minimal, surgical edits rather than overwriting the whole sheet
- Row order is determined by registration order: the first fencer to register is in the first row, new fencers are always appended at the bottom — no sorting or reordering
- Worksheets to update: **Fencers** (full fencer data) and one per weapon (**LS**, **SA**, etc.) with `hr_id`, rating, and rank for registered fencers

---

## Non-functional requirements

1. **Google Sheets**: `pygsheets` library, service account auth via `creds.json` (path in `config.json`)
2. **Configuration**: Pydantic `Config` class, serialized to/from `config.json` — includes sheet URLs, file paths, credentials path
3. **Incremental runs**: The agent runs multiple times as registrations grow. It must reuse all already-inferred data (cached matches, downloaded pages, parsed ratings) and only make LLM calls for genuinely new or missing information
4. **LLM framework**: Pydantic AI, with structured Pydantic types for all LLM inputs and outputs, and tool calls where needed
5. **Model**: Claude (Anthropic) — `claude-sonnet-4-6`
