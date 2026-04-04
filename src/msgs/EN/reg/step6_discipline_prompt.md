## Worksheet "{{ discipline }}"

Synchronize the "{{ discipline }}" worksheet with the list of fencers registered for {{ discipline }},
preserving their registration order (fencer [1] goes to data row 2, fencer [2] to row 3, etc.).

Column layout (col index → field: type):
  1: No.              – table row number, do not modify: int
  2: Name             – full name of the fencer: str
  3: Nat.             – nationality code (CZ, SK, DE, …), blank if unknown: str
  4: Club             – club name, blank if unknown: str
  5: HR_ID            – hemaratings.com numeric ID, blank if unknown: int
  6: HRating          – current weighted rating in {{ discipline }}, blank if unavailable: float
  7: HRank            – current rank in {{ discipline }}, blank if unavailable: int

Rules:
1. Row 1 is the header — never overwrite it.
2. Column 1 is table index — never overwrite it, if missing fill it so it keeps the sequence.
3. Data rows start at row 2. Fencer [1] → row 2, fencer [2] → row 3, etc.
4. Row order = registration order. Never reorder or delete existing rows.
5. Always overwrite HRating (col 6) and HRank (col 7) with the values from your data —
   these are refreshed from HEMA Ratings on every run and must reflect the latest values.
6. For all other columns (Name, Nat., Club, HR_ID): if a cell is non-empty and differs from
   your data, treat it as a deliberate manual correction and leave it unchanged.
   Only write to those cells if they are blank or already match your data exactly.
7. To decide which fencers to append: find the last fencer in the sheet whose name matches
   your data — call their data index LAST. Append all data fencers with index > LAST.
   Data fencers with index ≤ LAST that are absent from the sheet were manually removed — skip them.