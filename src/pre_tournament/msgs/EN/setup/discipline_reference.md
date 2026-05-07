Discipline codes are composed of three parts:

Weapon (required):
  LS  — Longsword
  SA  — Sabre
  RA  — Rapier
  RD  — Rapier & Dagger
  SB  — Sword & Buckler

Gender suffix (optional, appended to weapon code):
  M   — Men only
  W   — Women only
  (none) — Open (default)

Material prefix (optional, prepended with a space):
  "Plastic " — plastic weapons
  (none)     — steel (default)

Examples:
  LS         → Steel Longsword Open
  LSW        → Steel Longsword Women
  LSM        → Steel Longsword Men
  SA         → Steel Sabre Open
  SAW        → Steel Sabre Women
  Plastic SA → Plastic Sabre Open
  RD         → Steel Rapier & Dagger Open

The config value for each code is a human-readable description, e.g.:
  {"LS": "Longsword Open", "LSW": "Longsword Women", "Plastic SA": "Plastic Sabre Open"}