# Architecture

## Backend
Python 3.12, FastAPI (service API) + Telegram (aiogram 3) and MAX bots (a custom Bot API
client on httpx, `bot/max_api.py`). Both channels run in one process on long polling
(`bot/runner.py`); the dialogue scenario is shared (`bot/dialog.py`, `bot/engine.py`),
and the messengers only receive messages and send replies. A channel is enabled by the
presence of its token in settings (`TELEGRAM_BOT_TOKEN`, `MAX_BOT_TOKEN`).

## LLM
A single provider, OpenAI: chat + vision (`gpt-4o-mini`), speech recognition
(`whisper-1`), embeddings (`text-embedding-3-small`). It can be switched to OpenRouter
(`LLM_PROVIDER=openrouter`) without code changes.

## RAG
pgvector; the knowledge base is the official SOGAZ product documents (insurance rules,
key information document, customer memo, policy, separately for apartment and house).
The documents themselves are not published (see README), but the indexing and search
pipeline, `rag/indexer.py` and `rag/retriever.py`, is shown in full.

## Core: declarative checklists
`checklists/{flat,house}.yaml` is the single source of truth for application fields
(source: official SOGAZ application forms). It is used by the dialogue engine, by
digitisation and by verification. Reading logic is in `checklists/loader.py`
(conditional field visibility via `depends_on`, finding the next empty field, collection
progress). Fields change only in YAML, never hardcoded in the dialogue code.

## Application flow
1. The client writes to the bot (Telegram or MAX) → the object type is determined
   (apartment / house).
2. The engine (`bot/engine.py`) runs the dialogue over the checklist with OpenAI
   tool-calling: `set_object_type`, `set_fields`, `add_group_item`, `consult` (RAG).
3. Voice is transcribed with Whisper (`digitize/stt.py`), photos are parsed by the vision
   model directly in the engine. Values are coerced to the field type
   (`digitize/coerce.py`); doubtful ones are asked again.
4. `address` fields are normalised through DaData suggestions (`services/address.py`):
   in the insurer's form an address is picked from a registry, not typed as free text.
5. When all required fields are collected, the client sees a summary to confirm
   (`services/verify.py`).
6. After confirmation, the product is chosen by the client's business rule
   (`calc/product_choice.py`: the boxed product or "Personal Solution+", depending on sum
   insured and region) and the application is handed on through `CalcProvider`.

## Handing the application to the insurer: the CalcProvider abstraction
`calc/base.py` + `calc/providers.py` define a common interface; implementations are
switched by the `CALC_PROVIDER` config:

- `sogaz_api`: the SOGAZ agent API (when available);
- `rpa`: auto-filling the insurer's portal with Playwright (`calc/rpa_runner.py`). The
  portal is closed to the server by IP, so in production the auto-fill is done by an
  external Windows agent on the insurance agent's machine, not by the server itself;
- `manual`: fallback, the application goes to an operator.

The map of portal form fields and selectors (extracted from HAR dumps of the live form)
is not published; see README, section "What was removed and why".

## Security
- Non-root in the Dockerfile, API only on `127.0.0.1` behind Traefik.
- Tokens are compared in constant time (`core/security.py`); logs get only fingerprints
  (first 8 characters of sha256), never the tokens themselves.
- An empty or too-short token in settings never turns into open access: the app answers
  503 instead of letting the request through.
- Endpoints with clients' personal data are token-protected even when running only on
  localhost.
- `.env` is never committed, only `.env.example`.

## Infrastructure
Docker Compose, shared PostgreSQL/Redis, Traefik as the reverse proxy. The bots run on
polling, so they don't need to be exposed; only the service API for the external
Windows agent is published through Traefik.
