**HEMA Squire — Příkazy**

Všechny příkazy vyžadují roli **Admin**.

**Nastavení serveru** *(jednorázové — po prvním spuštění zmizí)*
`/setup` — vytvoří role, kanály, pozvánky a QR kódy; automaticky přiřadí Admin vlastníkovi serveru

**Konfigurace** *(pouze v #setup)*
`/configure` — nastaví název turnaje, jazyk a disciplíny (otevře formulář; po potvrzení smaže historii kanálů)
`/create_pool_sheets` — vytvoří jeden Google Sheet pro každou disciplínu
`/validate_pools [disc]` — zkontroluje skupiny oproti seznamu účastníků (výchozí: všechny disciplíny)
`/render_pools [disc]` — vykreslí skupiny jako PDF do vlákna **<disc>_pool_tables** v #setup

**Publikování** *(#setup nebo #bot-commands)*
`/publish_pools <disc>` — zveřejní skupiny pro šermíře do vlákna **<disc>_pools** v #announcements

**Moderování**
`/clear` — smaže všechny zprávy v tomto kanálu kromě první

Admini mohou také psát volně v **#setup** a pracovat s AI asistentem při nastavení.
