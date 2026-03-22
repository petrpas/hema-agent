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

## Fencer registration pipeline

See the **[reg-agent](src/reg_agent/README.md)** docs

## Deployment

See [`deploy/README.md`](deploy/README.md) for instructions on spinning up a bot instance for a new tournament.

