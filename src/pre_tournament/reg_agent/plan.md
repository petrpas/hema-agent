# Implementation Plan: reg_agent — Interactive Discord Agent

## Context

The reg_agent is the agentic backend for the HEMA tournament Discord bot. Instead of a
batch pipeline triggered once, it is an interactive pydantic-ai agent that guides the
tournament organizer through the registration enrichment process step-by-step, with
human-in-the-loop approval after each step.

The existing pipeline step functions (step1–step6) are **not replaced** — they become
**tools** that the agent calls on behalf of the user.

---

## Architecture overview

```
Discord #registration channel
        │
        │  on_message / /run / /status
        ▼
RegistrationCog (bot.py)
        │
        │  channel history + new message
        ▼
registration_agent (agent.py)          ← pydantic-ai Agent
        │
        ├── tool: download_registrations   → step1_download.py
        ├── tool: parse_registrations      → step2_parse.py
        ├── tool: match_fencers            → step3_match.py
        ├── tool: deduplicate_fencers      → step4_dedup.py
        ├── tool: fetch_ratings            → step5_ratings.py
        ├── tool: upload_results           → step6_upload.py
        ├── tool: inform                   → posts message to Discord channel
        ├── tool: store_memory             → appends to data/{name}/memory.md
        └── tool: read_memory              → reads data/{name}/memory.md
```

---

## Persistence model

**The Discord channel history IS the agent's persistent state.** There is no separate
database or session file. On every invocation the bot fetches the full message history
of the channel and passes it as conversation context to the agent.

- `/clear` in Discord wipes the history → effectively resets the agent session.
- The agent can always reconstruct "where it was" by reading the history.
- Structured intermediate data (fencer lists, ratings) live in `data/{tournament}/`
  as before and are referenced by the agent when needed.

### Memory file

User-provided context that is not captured in the conversation (e.g. "the fencer Jan
Novak is the same person as John Smith", "ignore the SA discipline this year") is
persisted to `data/{tournament_name}/memory.md` via the `store_memory` tool. The file
is injected into the system prompt on every agent run so all tools receive this context.

---

## Agent (agent.py)

### Entry point

```python
async def run_agent(
    channel: discord.TextChannel,
    new_message: discord.Message,
    config: Config,
) -> None
```

Called by `RegistrationCog` for every non-bot message in `#registration` and on `/run`.

### Conversation context construction

1. Fetch last N messages from the channel (oldest first).
2. Map each message to an alternating user/assistant turn:
   - Bot messages → `assistant` role
   - Other messages → `user` role
3. Append the new message as the final `user` turn.
4. Prepend the system prompt (see below).

### System prompt

```
You are the HEMA Tournament Registration Agent. You help tournament organisers
enrich fencer registration data with HEMA Ratings scores.

You run the enrichment pipeline interactively, one step at a time. After each
step you call `inform` to post a short summary to the Discord channel and then
STOP and wait for the organiser to approve before proceeding.

The organiser may also give you additional instructions at any time — store
relevant facts with `store_memory` and take them into account.

Current tournament: {config.tournament_name}
Memory:
{memory_file_contents}
```

### Agent behaviour

- On `/run` or "start"/"go"/"proceed" → call next pending step tool.
- On approval keywords ("ok", "looks good", "proceed", "yes", "continue") → advance.
- On rejection or correction → ask the user to clarify; do not advance automatically.
- On a free-form fact ("remember that…", "the organiser wants…") → call `store_memory`.
- On `/status` → describe the current state based on channel history without running anything.
- The agent never runs more than one pipeline step per invocation.

---

## Tools

### Pipeline step tools

Each wraps the corresponding existing function. All are `async`.

| Tool | Wraps | Returns posted to Discord |
|---|---|---|
| `download_registrations()` | step1 | number of new registrations vs previous version |
| `parse_registrations()` | step2 | fencer count, weapon breakdown, unresolved hr_ids |
| `match_fencers()` | step3 | matched/unmatched counts, list of unmatched names |
| `deduplicate_fencers()` | step4 | merged duplicate count, final fencer count |
| `fetch_ratings()` | step5 | ratings fetched, fencers with/without ratings |
| `upload_results()` | step6 | rows written to each worksheet |

After calling a step tool the agent **must** call `inform` with a human-readable
summary, then stop and await approval.

### `inform(message: str) -> None`

Posts `message` to the Discord channel. Used to notify the organiser of step
completion, errors, or questions. The message is a short markdown string (no raw data
dumps). Called via the `discord.TextChannel` reference held by the agent runner.

### `store_memory(fact: str) -> None`

Appends `fact` as a bullet point to `data/{tournament_name}/memory.md` (with
timestamp). Acknowledges to the user via `inform`.

### `read_memory() -> str`

Returns the full contents of `memory.md` (or empty string if missing). Used internally
before constructing the system prompt; also callable by the agent if it needs to
re-read during a run.

---

## File structure changes

```
src/reg_agent/
├── agent.py          ← NEW: pydantic-ai agent + tool definitions
├── main.py           ← kept for standalone CLI use (unchanged)
├── config.py
├── models.py
├── utils.py
├── step1_download.py
├── step2_parse.py
├── step3_match.py
├── step4_dedup.py
├── step5_ratings.py
└── step6_upload.py

data/{tournament_name}/
├── memory.md         ← NEW: user-provided context, appended by store_memory
├── registrations_v*/
├── fencers_parsed.json
├── fencers_matched.json
├── fencers_cache.json
├── fencers_deduped.json
└── ratings_*/
```

---

## Discord bot integration (bot.py changes)

`RegistrationCog` is updated to:

1. On `/run` — call `run_agent(channel, synthetic_message("/run"), config)`.
2. On `/status` — call `run_agent(channel, synthetic_message("/status"), config)`.
3. On `on_message` in `#registration` — call `run_agent(channel, message, config)`.
4. Hold a `Config` instance loaded at startup (or on first use).
5. `/clear` requires no change — it already deletes history, which resets agent state.

The `RegistrationCog` passes the `discord.TextChannel` to the agent so the `inform`
tool can post back to the same channel.

---

## Interaction example

```
organiser: /run
bot:       Downloading registrations… ✓ 47 registrations (3 new since last run).
           Ready to parse? (reply "ok" to continue)
organiser: ok
bot:       Parsing registrations… ✓ 47 fencers parsed.
           Weapons: LS×32 SA×18 RA×12 SB×5 RD×3.
           6 fencers have no hr_id and will be matched in the next step.
           Proceed to matching?
organiser: wait — Jan Novak and John Smith are the same person, hr_id 1234
bot:       Got it, I'll remember that.  ← store_memory called
organiser: ok proceed
bot:       Matching fencers… ✓ 41 matched, 5 unmatched.
           Unmatched: Alice B., Bob C., Carol D., Dave E., Eve F.
           These will be left with hr_id=None. Proceed to dedup?
…
```

---

## Implementation order

1. `agent.py` — agent skeleton, `inform` + `store_memory` + `read_memory` tools, conversation context builder.
2. Wire `RegistrationCog` in `bot.py` to call `run_agent`.
3. Add pipeline step tools one by one, test each interactively in Discord.
4. Tune system prompt and approval detection.