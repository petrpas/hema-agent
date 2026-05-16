**HEMA Squire — Commands**

All commands require the **Admin** role.

**Setup** *(one-time — removed after first run)*
`/setup` — provision roles, channels, invite links and QR codes; auto-assigns Admin to the server owner

**Configuration** *(#setup only)*
`/configure` — set tournament name, language and disciplines (opens a form; wipes all channel history on confirm)
`/create_pool_sheets` — create one Google Sheet per discipline
`/validate_pools [disc]` — check pool sheets against the tournament roster (default: all disciplines)
`/render_pools [disc]` — render pool tables as PDFs into **<disc>_pool_tables** thread in #setup

**Publishing** *(#setup or #bot-commands)*
`/publish_pools <disc>` — publish pool assignments for fencers into **<disc>_pools** thread in #announcements

**Results**
Upload photos of completed pool score sheets to **#org-results-upload** — the bot will parse them and write bouts to the Google Sheet.
`/refresh` — check the verified sheets immediately and publish any newly-complete pools (normally happens automatically every 30 s)
`/repub_pool_res <disc> <pool_no>` — manually publish a specific pool from the verified sheet (e.g. `/republish_pool LS 3`)

**Moderation**
`/clear` — delete all messages in this channel except the first

Admins can also type freely in **#setup** to work with the AI setup assistant.
