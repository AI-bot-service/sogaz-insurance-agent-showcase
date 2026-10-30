# AI Insurance Agent for SOGAZ

**English** · [Русский](README.md)

A messenger AI agent that collects a property insurance application from the client and
fills it into the insurer's portal by itself.

| | |
|---|---|
| **Client** | SOGAZ, one of Russia's largest insurers |
| **Type** | Commercial project under NDA |
| **Role** | Sole developer: from requirements to production support |
| **Status** | In production |
| **In this repo** | ~3,500 lines of the Python backend and the checklists, taken from the working repository as is |

**Contents:** [Problem](#1-the-problem) · [Architecture](#2-architecture) · [My role](#3-my-role) · [What was removed](#4-what-was-removed-and-why) · [Recent work](#5-recent-work-septemberoctober-2026)

> The full code is private (NDA, production infrastructure and personal data of the
> client's customers). What is published: the problem statement, the architecture, my
> contribution and the backend without secrets or internal details of the client's systems,
> with no edits "for the demo". The repository reads on its own: the decisions, their cost,
> the scope and the quality are visible without access to the private part.

---

## 1. The problem

A SOGAZ insurance agent issues a "Personal Solution+" policy (apartment or house) by hand:
calls the client, pulls several dozen property parameters out of them, then retypes them
into the AdInsure portal form: several tabs, reference lists, dependent blocks. One policy
means a conversation plus manual data entry, and a wrong field only shows up at the
pricing step.

What had to be built:

- move data collection into a messenger, where the client is comfortable: text, voice
  or a photo of a document;
- collect not a "free story" but exactly the fields the insurer's form needs, in the format
  it will accept;
- answer the client's questions about the product along the way, based on official SOGAZ
  documents rather than the model's general knowledge;
- take the collected application all the way to a draft policy in the portal without
  manual retyping.

The constraint that shaped half of the architecture: **the AdInsure portal is IP-restricted
and requires a second factor**. The server cannot reach it and does not store the portal
password, so the auto-fill has to run on the agent's own machine, inside the browser
session they have already signed into.

---

## 2. Architecture

```
client → Telegram / MAX → dialogue engine → application in DB → task queue
          (messenger)       (server)              │                (server)
                                                  │                    ↓
                                                  ↓     Windows agent on the insurance agent's machine
                                         deal in amoCRM                ↓
                                  + notifications to the manager   AdInsure form in their browser
```

Key decisions and why:

**The checklist is the single source of truth.** `checklists/{flat,house}.yaml`:
71 fields in 11 sections for an apartment, 68 fields in 9 sections for a house. Source:
official SOGAZ application forms. The same YAML is read by the dialogue engine, by value
normalisation and by the confirmation summary; the dialogue code contains no fields at all.
A product change is a YAML change. Conditional visibility (`depends_on`) and repeating
blocks are supported (buildings on a plot are a group, not a fixed set of fields).

**Tool-calling dialogue, not a scenario tree.** `bot/engine.py`: the model receives the
checklist state and the tools `set_object_type`, `set_fields`, `add_group_item`, `consult`.
A client can give five parameters in one sentence, drift off to ask about the deductible and
come back. The checklist sets the order, not a rigid script. Voice is transcribed with
Whisper, photos are parsed by a vision model.

**Values are coerced to the form field type, not to a "readable" format.**
`digitize/coerce.py`: dates to ISO, phone numbers to `+7XXXXXXXXXX`, a passport split into
series / number / issue date / issued by / unit code, because the insurer's party card has
separate fields for them. Addresses are normalised through DaData to the FIAS registry
format (`services/address.py`): the form picks an address from suggestions and does not
parse free text. A doubtful value is asked again, never filled in for the client.

**Product consultation is RAG over product documents.** pgvector; the knowledge base is
the insurance rules, key information document, customer memo and policy, separately for
apartment and house (`rag/indexer.py`, `rag/retriever.py`). Product answers come from the
client's documents, not from the model's head.

**Handing the application to the insurer is a swappable implementation.** `calc/base.py` +
`calc/providers.py`, switched by config: `rpa` (auto-fill in the browser), `sogaz_api`
(the agent API, once it is opened), `manual` (an operator). The product is chosen by the
client's business rule: the boxed product or "Personal Solution+" depending on sum insured
and region (`calc/product_choice.py`).

**Form filling runs on the agent's machine.** The server only puts a task in the queue;
an external Windows agent picks it up with its machine token, works inside the person's
signed-in browser session and reports the result. This gets around the IP filter and the
second factor, and the portal password is never stored on the server.

**Security.** Tokens are compared in constant time, logs get only a fingerprint (first 8
characters of sha256). An empty or too-short token never turns into open access: the app
answers 503. Endpoints with personal data are token-protected even on localhost.
Containers run as non-root, the API is exposed only through Traefik.

Full breakdown: [ARCHITECTURE.en.md](ARCHITECTURE.en.md).

**Stack.** Python 3.12, FastAPI, SQLAlchemy 2.0 (async) + PostgreSQL/pgvector, Redis,
aiogram 3 (Telegram), a custom Bot API client for the MAX messenger on httpx, OpenAI
(`gpt-4o-mini` for dialogue and vision, `whisper-1` for speech, `text-embedding-3-small` for
embeddings), Playwright (RPA), DaData, Docker Compose, Traefik.

### Where things are

```
src/
  main.py         FastAPI application assembly
  core/           configuration, DB, access token checks
  bot/            dialogue engine, Telegram and MAX channels, shared runtime
  checklists/     YAML checklists of application fields + loader
  calc/           application hand-off abstraction (RPA / API / operator), product choice
  digitize/       value coercion to field types, speech recognition
  rag/            embeddings, knowledge base indexing, search
  services/       consultation, verification, LLM wrapper, dialogue history
  models/         application, dialogue, agent task and knowledge base models
  schemas/        API schemas
```

---

## 3. My role

Sole developer on the project: requirements together with the client, architecture, all
the code, infrastructure and production support.

Designed and built:

- the tool-calling dialogue engine on top of OpenAI and two channels, Telegram and MAX
  (MAX has no ready SDK, so the Bot API client was written from scratch; the scenario is
  shared, the messenger only receives and sends messages);
- declarative checklists and their loader: conditional visibility, repeating groups,
  finding the next empty field, collection progress;
- input digitisation: voice via Whisper, photos via vision, value coercion to the insurer's
  form types, address normalisation via DaData, re-asking doubtful values;
- RAG consultation over product documents on pgvector;
- the data schema and the service API of the task queue for the external agent;
- the Windows agent that auto-fills the AdInsure form with Playwright (~13,600 lines):
  attaching to the person's browser, finding fields by label in re-rendering Angular
  markup, handling `ng-select` dropdowns, self-update, recording a pass over the form for
  debugging;
- the field map "checklist code → AdInsure form field" (~1,200 lines, about 70 fields) and
  per-tab fill plans extracted from the form metadata, with tools to build them and to check
  an application against the form before the browser starts;
- export of applications to amoCRM and notifications to the manager in MAX and Telegram;
- infrastructure: Docker Compose, Traefik, shared PostgreSQL and Redis, rollout and updates
  of the agent on the insurance agents' machines.

I also ran a security audit of the project and fixed what it found: the service API was
returning personal data without authorisation, because the token check was declared as an
endpoint parameter and fired only after the DB session had been opened.

---

## 4. What was removed and why

The backend is published. What belongs to the client or contains their customers' personal
data is not. In the private repository these paths are closed by explicit deny rules,
including against reads by the coding assistant during work sessions.

| Not published | Why |
|---|---|
| AdInsure form field map (`fieldmap.yaml`, `docs/adinsure_fields/`, `docs/asset_FL/`) and the HAR dumps it was extracted from | It is the structure of the client's internal portal form, obtained while working with their closed system |
| Form-filling Windows agent (`agent/`) | Useless and unsafe without the field map above; contains the logic of working inside a live portal session |
| Form pass recordings and snapshots of the live portal (`records/`, `dumps/`) | Personal data of the client's customers |
| Admin and agent-queue API routers (`api/v1/agent*.py`, `applications.py`, `checklists.py`) | They handle personal data and the task queue of external machines |
| Product documents: insurance rules, key information document, memo, application forms | Client materials provided for internal use; only the indexing and search pipeline is shown |
| amoCRM integration (`crm/`) | The mapping is tied to the client's pipeline, stages and CRM fields |
| Secrets: `.env`, OpenAI, DaData and amoCRM keys, agent machine tokens | For obvious reasons |

What this means when reading the code: `calc/rpa_runner.py` is a real file from the working
repository, but the `FLAT_FIELD_MAP` / `HOUSE_FIELD_MAP` selector maps in it remain an empty
skeleton with `TODO`. The same is true in the private repository: production selectors
live not in code but in the separate field map from the table above, and the agent
substitutes them on the insurance agent's machine. No execution branch was cut out here;
the file is shown in full.

Everything else (dialogue engine, checklists, digitisation, RAG, the pricing provider
abstraction, token security) is published in full and works the same as in production.

The full code, including the closed parts, is in the private repository
`AI-bot-service/sogaz-insurance-agent`. Access on request.

---

## 5. Recent work (September–October 2026)

The code of these parts is not in the showcase (see section 4); here is what was done and why.

**Export to amoCRM.** An application becomes a deal in the client's pipeline. The stage is
derived from the application state (object chosen → cost named → price known → data
collected → policy → invoice issued), deal fields are filled from the checklist, and the
conversation with the client goes in as notes. The stage only moves forward: a step back in
the pipeline reads to the manager as a lost client. The bot does not confirm payment; it
passes the client's words on to the manager. The integration does not create the pipeline or
fields; it finds them through a YAML map with fuzzy name matching. If the client renames a
stage, the YAML changes, not the code. If a field is missing, there is a warning at startup
and everything else keeps working.

**Hand-off to a human.** On every hand-off and every failure, the manager gets a notification
in MAX or Telegram: who the client is (profile link), at which step, what happened. The
manager can take over the conversation in the messenger.

**House data without asking the client.** The agent finds the house in GIS ZhKKh (the state
housing registry) by address and takes the construction year, number of floors, cadastral
number and wear from it, so the client does not have to remember them. The client's business
rule (the boxed product does not cover houses built before 1950, only "Personal Solution"
does) is a programme selection criterion, not a manual check.

**Summary with edits by number.** Before sending, the client sees a numbered summary and
fixes any field with one message, "5 — 72 sq m", instead of going through the questions
again. One client's turns are processed strictly in order, so quick button taps don't lose
answers.

**Bot questions come from the client's spreadsheet.** The client maintains the wording and
hints in their own spreadsheet; a snapshot of it is checked against the checklist, and
mismatches are visible before release.

**Agent console and form diagnostics.** The insurance agent's machine runs a console: the
application queue from the server, opening the portal, checking a single form field against
the spec in the task. The check tells "field missing from the map" from "field missing from
the form", measures the value twice (immediately and after the form recalculates), and
records a fingerprint of the field map version the agent received. A filling error is then
analysed from the report, without access to the live session.

**Visual scenario editor.** A separate project: the survey scenario on a canvas (nodes
"question", "write field", "bot reply", constants), with a draft run in a test bot that does
not affect the production one.
