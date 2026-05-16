**HEMA Squire — Příkazy**

Všechny příkazy vyžadují roli **Admin**.

**Nastavení serveru** *(jednorázové — po prvním spuštění zmizí)*
`/setup` — vytvoří role, kanály, pozvánky a QR kódy; automaticky přiřadí Admin vlastníkovi serveru

**Konfigurace** *(pouze v #setup)*
`/configure` — nastaví název turnaje, jazyk a disciplíny (otevře formulář; po potvrzení smaže historii kanálů)
`/create_pool_sheets` — vytvoří jeden Google Sheet pro každou disciplínu
`/validate_pools [disc]` — zkontroluje skupiny oproti seznamu účastníků (výchozí: všechny disciplíny)
`/render_pools [disc]` — vykreslí skupiny jako PDF do vlákna **<disc> Pool Tables** v #setup
`/calc_pools <disc>` — vypočítá výsledky skupin z ověřeného listu a zapíše je do listu Pool Results; problémy při validaci se zobrazí ve vlákně **<disc> Pool Results** v #setup
`/pub_pool_res <disc>` — vykreslí list Pool Results jako PDF+PNG a zveřejní ho do **<disc> Pool Results** v #setup a **<disc> Pool Results** v #results

**Publikování** *(#setup nebo #bot-commands)*
`/publish_pools <disc>` — zveřejní skupiny pro šermíře do vlákna **<disc> Pools** v #announcements

**Výsledky**
Nahrajte fotografie vyplněných výsledkových listů skupin do **#org-results-upload** — bot je zpracuje a zapíše zápasy do Google Sheetu.
`/refresh` — okamžitě zkontroluje ověřené zápasy a zveřejní dokončené skupiny (jinak probíhá automaticky každých 30 s)
`/repub_pool_matches <disc> <pool_no>` — ručně zveřejní zápasy konkrétní skupiny z ověřeného listu (např. `/repub_pool_matches LS 3`)

**Moderování**
`/clear` — smaže všechny zprávy v tomto kanálu kromě první

Admini mohou také psát volně v **#setup** a pracovat s AI asistentem při nastavení.
