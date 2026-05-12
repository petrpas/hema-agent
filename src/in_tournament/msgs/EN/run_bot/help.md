**HEMA Squire — Commands**

**Server**
`/setup` — provision roles, channels, permissions and invite links
`/configure` — set tournament name, language, and disciplines (opens a form; also wipes all channel history)

**Pool sheets** *(manage_guild)*
`/create_pool_sheets` — create one Google Sheet per discipline to be filled with fencers
`/validate_pools [disc]` — check pool sheets against the tournament roster (default: all disciplines)
`/render_pools [disc]` — render pool tables as PDFs into **<disc>_pool_tables** thread in #setup
`/publish_pools <disc>` — publish pool tables for fencers into **<disc>_pools** thread in #announcements

**Moderation**
`/clear` — delete all messages in this channel except the first *(manage_messages)*

You can also type freely in **#setup** to work through the remaining steps with the AI assistant.