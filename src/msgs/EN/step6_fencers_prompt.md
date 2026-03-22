## Worksheet "Fencers"

Synchronize the "Fencers" worksheet with the provided list of registered fencers,
preserving their registration order (fencer [1] goes to data row 2, fencer [2] to row 3, etc.).

Column layout (col index → field: type):
  1: Reg.             – registration order number: int
  2: Name             – full name of the fencer: str
  3: Nat.             – nationality code (CZ, SK, DE, …), blank if unknown: str
  4: Club             – club name, blank if unknown: str
  5: HR_ID            – hemaratings.com numeric ID, blank if unknown: int
  6: Disciplines      – comma-separated disciplines from {{ disciplines }}: str
  7: Paid             – leave blank (do not touch): str
  8: Afterparty       – Yes / No / Other, blank if not provided: str
  9: Borrow weapons   – comma-separated weapon codes the fencer wants to borrow, blank if none: str
  10: Notes           – free-text notes from the fencer: str

Rules:
1. Row 1 is the header — never overwrite it.
2. Data rows start at row 2. Fencer [1] → row 2, fencer [2] → row 3, etc.
3. Column 1 (Reg.) is managed manually — never write to it. Always use col_offset=1 (the default)
   so writes start from col 2 (Name) onwards.
4. Row order = registration order. Never reorder or delete existing rows.
5. Trust what is already in the sheet: if a cell is non-empty and differs from your data,
   the difference is likely a deliberate manual correction — leave it unchanged.
   Only write to cells that are blank or already match your data exactly.
6. To decide which fencers to append: find the last fencer in the sheet whose name matches
   your data — call their data index LAST. Append all data fencers with index > LAST.
   Data fencers with index ≤ LAST that are absent from the sheet were manually removed — skip them.