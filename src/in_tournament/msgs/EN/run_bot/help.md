**HEMA Squire — Commands**

All commands require the **Admin** role.

**Setup** *(one-time — removed after first run)*
`/setup` — provision roles, channels, invite links and QR codes; auto-assigns Admin to the server owner

**Configuration** *(#setup only)*
`/configure` — set tournament name, language and disciplines (opens a form; wipes all channel history on confirm)
`/create_pool_sheets` — create one Google Sheet per discipline
`/validate_pools [disc]` — check pool sheets against the tournament roster (default: all disciplines)
`/render_pools [disc]` — render pool tables as PDFs into **<disc> Pool Tables** thread in #setup
`/calc_pools <disc>` — calculate pool-stage results from the verified sheet and write to Pool Results sheet; validation issues go to **<disc> Pool Results** thread in #setup
`/pub_pool_res <disc>` — render Pool Results sheet as PDF+PNG and post to **<disc> Pool Results** in #setup and **<disc> Pool Results** in #results

**Publishing** *(#setup or #bot-commands)*
`/publish_pools <disc>` — publish pool assignments for fencers into **<disc> Pools** thread in #announcements

**Results**
Upload photos of completed pool score sheets to **#org-results-upload** — the bot will parse them and write bouts to the Google Sheet.
`/refresh` — check the verified sheets immediately and publish any newly-complete pools (normally happens automatically every 30 s)
`/repub_pool_matches <disc> <pool_no>` — manually publish matches for a specific pool from the verified sheet (e.g. `/repub_pool_matches LS 3`)

**Moderation**
`/clear` — delete all messages in this channel except the first

Admins can also type freely in **#setup** to work with the AI setup assistant.
