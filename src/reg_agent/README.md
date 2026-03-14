# reg-agent

Enriches HEMA tournament registration data with HEMA Ratings scores.

Tournament organizers receive raw sign-ups from a Google Form and need each fencer's current rating and rank before they can set up competition pools. `reg-agent` automates the full process: it downloads the registration sheet, parses and normalises the data, matches every fencer to their HEMA Ratings profile, fetches their current scores, and writes the results back to an output Google Sheet — making only surgical, targeted edits so manual corrections are preserved.

## Pipeline

| Step | What it does |
|------|-------------|
| 1 — Download | Fetches the registration Google Sheet as a versioned local CSV |
| 2 — Parse | LLM extracts structured fencer records from the raw CSV rows |
| 3 — Match | LLM fuzzy-matches each fencer to their [hemaratings.com](https://hemaratings.com) profile (handles diacritics, nicknames, transliterations) |
| 4 — Dedup | LLM merges duplicate registrations sharing the same HEMA Ratings ID |
| 5 — Ratings | Scrapes current weighted rating and rank per discipline; self-heals the HTML parser via LLM if the page format changes |
| 6 — Upload | LLM agent syncs enriched data to the output Google Sheet, preserving manual edits |

All intermediate results are cached locally — re-runs skip already-completed work.

## Requirements

- Python 3.12+
- An [Anthropic API key](https://console.anthropic.com/)
- A Google service account with Sheets access (`creds.json`)

## Installation

```bash
pip install -e .
```

## Configuration

Create `src/reg_agent/config.json` (gitignored):

```json
{
  "tournament_name": "my_tournament_2025",
  "registration_sheet_url": "https://docs.google.com/spreadsheets/d/...",
  "output_sheet_url": "https://docs.google.com/spreadsheets/d/...",
  "creds_path": "creds.json",
  "data_root_dir": "data",
  "ai_models": {
    "default": "anthropic:claude-sonnet-4-6"
  },
  "disciplines": {
    "LS":  {"weapon": "LS"},
    "LSW": {"weapon": "LS", "gender": "W"},
    "SA":  {"weapon": "SA"},
    "RA":  {"weapon": "RA"},
    "SB":  {"weapon": "SB"},
    "RD":  {"weapon": "RD"}
  }
}
```

**`disciplines`** — include only the weapons used at your tournament. Each key becomes a worksheet name in the output sheet.

**`ai_models`** — optional per-step overrides. Keys: `parse`, `match`, `dedup`, `heal`, `upload`, and `default` (catch-all). Priority: step key > `default` > built-in step default.

**`upload_thinking_tokens`** — extended thinking budget for the upload agent (step 6), in tokens. Default `0` (disabled). Set to e.g. `2000` if the agent struggles with complex sheet states.

**`creds_path`** — path to the Google service account JSON key, relative to the working directory.

## Running

```bash
cd src/reg_agent
export ANTHROPIC_API_KEY=sk-ant-...
python main.py config.json
```

Intermediate data is written to `data/{tournament_name}/`. Re-running is safe — only new registrations trigger LLM calls.

## Output sheet structure

The output sheet must exist before the first run. See the [template](https://docs.google.com/spreadsheets/d/1UMUxRfHnk5nOLY5D4oie2fos_oGUGf70MOH0xqwMaxU/edit?usp=sharing). 

The agent expects:

- A **Fencers** worksheet: `Reg. | Name | Nat. | Club | HR_ID | Disciplines | Paid | Afterparty | Borrow weapons | Notes`
- One worksheet per discipline (named by discipline code, e.g. `LS`, `SA`): `No. | Name | Nat. | Club | HR_ID | HRating | HRank`

`Reg.` and `No.` are managed manually and never overwritten. `HRating`/`HRank` are always refreshed. All other cells are only written if blank or already matching — manual edits are preserved.
