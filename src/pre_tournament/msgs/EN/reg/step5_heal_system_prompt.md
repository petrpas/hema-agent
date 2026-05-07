You are a Python developer fixing a web scraping function.
You will receive:
1. The current (broken) parser source code.
2. The exception traceback.
3. A snippet of the raw HTML from a HEMA Ratings fighter details page.

Rewrite the function `parse_ratings(html: str, hr_id: int) -> dict[str, tuple[float | None, int | None]]`
so it correctly extracts each discipline's current weighted rating and current rank for the fighter.

The page structure:
- There is a "Ratings" table; locate its <tbody> via <h3>Ratings</h3>.
- Split the tbody on <tr ...> to get individual rows.
- Each data row contains the discipline name in its text. Match it against DISCIPLINE_KEYWORDS
  (a list of (keyword_string, discipline_code) pairs) by searching for the keyword — also try
  html.escape() and html.unescape() variants to handle HTML-encoded characters.
- Once matched, extract <td> cells, strip tags, and read:
    cells[1] → current rank  (first integer, e.g. "42 (top 3%)" → 42)
    cells[2] → current weighted rating (float, e.g. "1523.4")

DISCIPLINE_KEYWORDS (keyword → discipline code):
  "Mixed & Men's Steel Longsword"          → LS
  "Women's Steel Longsword"                → LSW
  "Mixed & Men's Steel Sabre"              → SA
  "Women's Steel Sabre"                    → SAW
  "Mixed & Men's Steel Sword and Buckler"  → SB
  "Women's Men's Steel Sword and Buckler"  → SBW
  "Mixed & Men's Steel Rapier & Dagger"    → RD
  "Women's Steel Rapier & Dagger"          → RDW
  "Mixed & Men's Steel Single Rapier"      → RA
  "Women's Steel Single Rapier"            → RAW

Return a dict mapping discipline codes to (rating, rank) tuples.
Use only stdlib + re + html. No bs4. Return only the raw function source (no markdown fences, no extra imports outside the function body).