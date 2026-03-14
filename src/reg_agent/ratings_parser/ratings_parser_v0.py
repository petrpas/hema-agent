import re
from html import escape, unescape


# Maps substrings in hemaratings weapon-group headers → our codes (longest match first)
DISCIPLINE_KEYWORDS: list[tuple[str, str]] = [
    ("Mixed & Men's Steel Longsword", "LS"),
    ("Women's Steel Longsword", "LSW"),
    ("Mixed & Men's Steel Sabre", "SA"),
    ("Women's Steel Sabre", "SAW"),
    ("Mixed & Men's Steel Sword and Buckler", "SB"),
    ("Women's Men's Steel Sword and Buckler", "SBW"),
    ("Mixed & Men's Steel Rapier & Dagger", "RD"),
    ("Women's Steel Rapier & Dagger", "RDW"),
    ("Mixed & Men's Steel Single Rapier", "RA"),
    ("Women's Steel Single Rapier", "RAW"),
]

def _discipline_code(header: str) -> str | None:
    h = header.lower()
    for keyword, code in DISCIPLINE_KEYWORDS:
        if keyword in h:
            return code
    return None

def parse_ratings(html: str, hr_id: int) -> dict[str, tuple[float | None, int | None]]:
    """Parse fighter page HTML with pure regex. Returns {discipline_code: (rating, rank)}.

    The page is the fighter's own details page — every data row belongs to them.
    Structure: discipline group header row, then one data row per category in that group.
    Columns: Category | Rank (current) | Weighted Rating (current) | Rank (best) | Rating (best)
    """
    result: dict[str, tuple[float | None, int | None]] = {}

    table_match = re.search(
        r'<h3>Ratings</h3>.*?<tbody>(.*?)</tbody>',
        html, re.DOTALL,
    )
    if not table_match:
        return result

    tbody = table_match.group(1)

    rows = re.split(r'<tr[^>]*>', tbody)

    for row in rows:

        unescaped = unescape(row)

        for discipline in DISCIPLINE_KEYWORDS:
            escaped = escape(discipline[0])

            if re.search(discipline[0], row, re.DOTALL) or re.search(escaped, row, re.DOTALL) or re.search(discipline[0], unescaped, re.DOTALL):
                cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
                cells_text = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
                # cells_text: [category_name, rank_current, rating_current, ...]
                if len(cells_text) >= 3 and cells_text[0]:  # skip empty/spacer rows
                    rank_match = re.search(r'(\d+)', cells_text[1])
                    rank = int(rank_match.group(1)) if rank_match else None
                    try:
                        rating = float(cells_text[2])
                    except ValueError:
                        rating = None
                    result[discipline[1]] = (rating, rank)

    return result
