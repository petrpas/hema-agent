# Hema Agent

A tool to automate various tournament related tasks.

## Modules

**[reg-agent](src/reg_agent/README.md)** — Automates enrichment of HEMA tournament registration data with HEMA Ratings scores.
- From raw Google Form responses to a fully populated output spreadsheet, guided interactively through Discord.
  - downloads registrations
  - matches fencers to HEMA Ratings profiles
  - fetches current ratings
  - syncs results back to a Google Sheet.
    - see the [template](https://docs.google.com/spreadsheets/d/1UMUxRfHnk5nOLY5D4oie2fos_oGUGf70MOH0xqwMaxU/edit?usp=sharing)
