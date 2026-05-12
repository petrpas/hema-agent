**HEMA Squire — Příkazy**

**Server**
`/setup` — vytvoří role, kanály, oprávnění a pozvánky
`/configure` — nastaví název turnaje, jazyk a disciplíny (otevře formulář; smaže také historii kanálů)

**Skupiny** *(manage_guild)*
`/create_pool_sheets` — vytvoří jeden Google Sheet pro každou disciplínu k vyplnění jmen šermířů
`/validate_pools [disc]` — zkontroluje skupiny oproti seznamu účastníků (výchozí: všechny disciplíny)
`/render_pools [disc]` — vykreslí skupiny jako PDF do vlákna **<disc>_pool_tables** v #setup
`/publish_pools <disc>` — zveřejní skupiny pro šermíře do vlákna **<disc>_pools** v #announcements

**Moderování**
`/clear` — smaže všechny zprávy v tomto kanálu kromě první *(manage_messages)*

V kanálu **#setup** můžete také psát volně a pokračovat v nastavení s pomocí AI asistenta.