# Hema Squire

A fully competent AI agent to take over various tournament related tasks.

It helps the HEMA Tournament organiser with various administrative tasks

- **before the tournament**
  - Enriches Google Form registration data with HEMA Ratings scores and rankings
  - Detects duplicate registrations and merges them
  - Uploads enriched data to a Google Sheet (fencers list + per-discipline tabs)
  - Matches payments from bank export against registrations
- **during the tournament**
  - TBD
- **after the tournament**
  - Make tournament results sheets to be published on social media
  - Export of the results to HEMA Ratings

## Interface

- Each tournament gets its own Discord server and its own bot instance
- The organiser interacts with the bot via two channels:
  - `#setup` — configure the tournament (name, disciplines, sheet URLs) on first boot
  - `#reg-agent` — run and monitor the registration pipeline

## Registration pipeline

The reg-agent processes registrations in six steps:

| Step | What it does |
|---|---|
| 1. Download | Downloads the Google Form registration sheet as a versioned CSV |
| 2. Parse | LLM extracts structured fencer records from the raw CSV |
| 3. Match | LLM fuzzy-matches registrations to HEMA Ratings profiles |
| 4. Dedup | LLM merges duplicate registrations for the same fencer |
| 5. Ratings | Scrapes current ratings and rankings from hemaratings.com |
| 6. Upload | Writes enriched data back to the output Google Sheet |

## Deployment

See [`deploy/README.md`](deploy/README.md) for instructions on spinning up a bot instance for a new tournament.

