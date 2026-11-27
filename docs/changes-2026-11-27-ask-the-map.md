# Changes 2026-11-27: Ask the map (free chat model)

A task pane beside the surveillance map (like an add-in pane in Word or Excel) that answers questions from RINDA's
live records and ends each answer with links that open the answer on the map.

The first version used the Claude API, which is billed per message. At the user's request it was rebuilt the same day
around free providers, with no paid service involved.

## How it works

- **The provider.** The model is reached through the OpenAI-style chat interface that all three supported providers
  offer:
  - **Groq** (default): free plan, `openai/gpt-oss-120b`. Groq's docs list limits for this model of 30 requests and
    8,000 tokens a minute, 1,000 requests a day, and say all its hosted models support tool calling. By default it
    keeps no request data.
  - **Google Gemini**: free tier, `gemini-flash-latest`. Google publishes no fixed limits; check them in AI Studio.
  - **Ollama**: a model on the same computer. Free, no key, nothing leaves the machine, but slow on this laptop and
    not possible on the hosted site.

  The provider and model are settings; no code change is needed to switch.
- **The tools.** The model answers only through ten read-only tools that call RINDA's own map functions as the
  signed-in user, so each user sees only what their role allows: `situation`, `daily_outlook`, `site_forecast`,
  `location`, `satellite_water`, `weather`, `field_and_operations`, `sensors`, `search`, `reliability`.
- **Staying inside free limits.** Tool results are compact (5 places by default, each result capped at 6,000
  characters). Only the last 4 questions and answers are resent, as plain text, with answers capped at 1,200 tokens.
  When the provider's limit is hit, the pane says so and asks the user to wait a minute.
- **The links.** Tool results carry ready-made map links. The map reads `layers`, `date`, `h`, `day`, `earth`,
  `insector`, `focus` and `highlight` from the address; `highlight` rings the named places and zooms to them. The pane
  renders only links that start with `#/`.

## Switching it on (off by default)

Questions and map data go to the chosen provider, so the operator opts in on the server.

1. `RINDA_ASSISTANT=on`
2. The provider: `RINDA_LLM_PROVIDER=groq` (or `gemini`, `ollama`), or the file `%USERPROFILE%\.rinda\llm_provider`.
3. The key: `RINDA_LLM_API_KEY`, or the file `%USERPROFILE%\.rinda\llm_api_key`. The file sits outside the repository
   and OneDrive, and the key is never in code, the browser or chat. Ollama needs no key.
4. Optional: `RINDA_LLM_MODEL` (overrides the default model) and `RINDA_ASSISTANT_HOURLY` (per-user hourly limit,
   default 40).

Only signed-in users can ask. Create the key on a free account **with no payment method**, so it cannot be charged.

## Files

| File | Change |
|---|---|
| `server/assistant.py` | Provider presets, streamed OpenAI-style chat with tool calls (`httpx`), tools, conversation store, rate limit, key file |
| `web/js/assistant.js` | The pane, streaming reader, safe Markdown with internal links only |
| `web/js/views/map.js` | Deep-link parameters, `highlight` rings, `focus` for outlook candidates, pane mount |
| `web/css/app.css`, `web/sw.js` | Pane styles; offline cache v8 |
| `requirements.txt` | `httpx==0.27.2` (was used locally but not listed); no Anthropic package |
| `tests/test_assistant.py` | 7 tests: off by default and key required; every tool on real data with internal links and compact output; tool call then streamed answer; plain-text follow-up; provider errors; a rate-limited model handing over to the next; parsing a real OpenAI-style stream with a tool call split across chunks; signed-in only and the rate limit. 85 tests pass |

## Tested with a real Groq key (27 November 2026)

**Limits on this key.** Each tool-capable model is limited to 8,000 tokens a minute and 1,000 requests a day, counted
per model, according to Groq's response headers. The models are `openai/gpt-oss-120b`, `openai/gpt-oss-20b` and
`qwen/qwen3.8-27b`; Llama models are not offered to this account.

**First build: two problems.**
- The free limit ran out after one question.
- Three answers had faults: a follow-up switched from the site forecast to the daily outlook; radar counts were
  misread (in-scope locations taken for wet ones); link parameters were invented.

**Fixes.**
- **Tokens:** shorter tool descriptions, leaner lookups, low reasoning on `gpt-oss`, answers capped at 900 tokens.
- **Limits:** a model at its per-minute limit hands over to the next one, and short waits are retried once.
- **Ready-made results:** links for the sites to inspect, and summary sentences that can be quoted as they are, with
  dates and ages computed on the server.
- **Instructions:** the two forecast products are told apart; links must be copied exactly; no tables.
- **Pane:** bare `#/...` addresses become clickable links, and tables render if the model still writes one.

**Result.** Six questions in a row answered in 3.6 to 7.8 seconds with no limit hit: what to inspect this week; which
of those are inside the sector; data age and the latest pass; reliability; one location; places in the sector over
the next 7 days. Counts, dates and links matched the map. In the browser, an answer's link opened the map with the
same 11 places, day +7, inside the sector, ringed.

## Still open

- **Lists are shortened.** An answer can name a count (11) while listing only the first 10 places, the lookup's
  default; the link shows them all.
- **Not hammered.** Under heavy use the free limits will still be reached; the pane then says so and asks the user to
  wait.
- **Not deployed.** The hosted site needs `RINDA_ASSISTANT=on` and the key set in Render.
