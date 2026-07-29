# care_filly

Self-hosted, MedScribe Alliance protocol compatible scribe backend for
[`care_filly_fe`](https://github.com/ohcnetwork/care_filly_fe) — the CARE frontend
module that records clinician dictation and turns it into structured form-fill
data with **chunked, near-realtime transcription** (transcript ready ~1–2s after
the recording stops, structured JSON a few seconds later).

**`care_filly`** is a Django app plugged into [CARE](https://github.com/ohcnetwork/care)
via `plugs.manager.PlugManager`, mounted at `/api/care_filly/`. It reuses CARE's own
auth, adds per-facility/per-user quota enforcement, terms-and-conditions gating, and
persists scribe session history.

This plugin follows the structure of
[ohcnetwork/care_hello](https://github.com/ohcnetwork/care_hello), the CARE
plugin boilerplate.

## How it's fast

```
naive pipeline: record ──────────────┤stop├─ transcribe all ─ template LLM ─ done  (~30-47s)
this backend:   record ─ transcribe chunks as they upload ─┤stop├─ last chunk ─ LLM ─ done (~3-6s)
```

- Audio chunks (≤20s each) upload during recording; each is transcribed immediately
  by the configured ASR provider (Sarvam AI by default; any OpenAI-compatible
  Whisper endpoint works too).
- On stop, only the final chunk remains → transcript assembled almost instantly.
- One JSON-mode LLM call (any OpenAI-compatible chat-completions endpoint —
  Groq `llama-3.3-70b-versatile` by default) converts the transcript into
  form-fill JSON using the questionnaire schema the frontend sends per-session
  in `additional_data`.

## Running as a CARE plugin (primary mode)

Register the plugin in CARE's `plug_config.py`:

```python
from plugs.plug import Plug

care_filly_plug = Plug(
    name="care_filly",
    package_name="care_filly",
    version="",
    configs={
        "ASR_API_KEY": "...",
        "LLM_API_KEY": "...",
    },
)

plugs = [care_filly_plug]
```

Then `pip install -e .` this repo into CARE's environment (or add it to
`plugs.txt` / your Docker build if installing from a git remote). All settings
in `care_filly/settings.py` can be overridden via `PLUGIN_CONFIGS` or
equivalent environment variables — see [Environment](#environment) below.

CARE's own JWT auth is used to authenticate requests — the frontend just sends
the logged-in user's access token, same as any other CARE API call.

No keys yet? Set `FILLY_MOCK=1` to run the full flow with fake
transcription/extraction (useful for wiring up the frontend without any
provider accounts).

### Frontend wiring

The frontend talks to the plugin at `/api/care_filly/` on the CARE API origin
(no extra configuration needed — leave `REACT_SCRIBE_BE_URL` unset).

## Protocol endpoints (MedScribe Alliance v0.1)

Mounted at `/api/care_filly/v1/...`:

| Method | Path                                       | Purpose                                                         |
| ------ | ------------------------------------------ | --------------------------------------------------------------- |
| GET    | `/v1/.well-known/medscribealliance`        | Discovery document                                              |
| POST   | `/v1/sessions`                             | Create session                                                  |
| POST   | `/v1/upload/{session_id}/{filename}`       | Chunk upload (`audio_N.mp3`) — transcription starts immediately |
| POST   | `/v1/sessions/{id}/end`                    | End recording → assemble transcript, run extraction             |
| GET    | `/v1/sessions/{id}`                        | Status poll (transcript appears before templates finish)        |
| PATCH  | `/v1/sessions/{id}`                        | Update session metadata                                         |
| POST   | `/v1/sessions/{id}/process/template/{tid}` | Re-run extraction                                               |

The CARE-plugin mode additionally exposes quota and history management:

| Method           | Path                                      | Purpose                                          |
| ---------------- | ------------------------------------------ | -------------------------------------------------- |
| GET               | `/v1/quota/my`                            | Current user's quota + usage for a facility        |
| POST              | `/v1/quota/accept-tnc`                    | Accept the terms & conditions                      |
| GET               | `/v1/preferences/scribe`                  | Get/set the user's per-user scribe opt-in          |
| GET/POST          | `/v1/quota`                               | List/create facility or user quotas (admin)        |
| GET/PATCH/DELETE  | `/v1/quota/{external_id}`                 | Manage a single quota row (admin)                  |
| GET               | `/v1/history`                             | List the current user's past scribe sessions       |
| GET/DELETE        | `/v1/history/{external_id}`               | Fetch/soft-delete a history entry                  |
| GET               | `/v1/history/{external_id}/audio`         | Download the recorded audio for a history entry    |
| POST              | `/v1/history/session/{session_id}/audio`  | Upload the recording once a session ends           |

## Environment

AI providers are fully generic — no vendor is hardcoded. `ASR_PROVIDER` /
`LLM_PROVIDER` accept a built-in adapter name (`sarvam`, `openai`) or a dotted
class path to a custom adapter (see `care_filly/providers/`). The `openai`
adapter works with any OpenAI-compatible API (OpenAI, Groq, Together, vLLM,
LiteLLM, ...).

| Variable           | Default                          | Purpose                                                                  |
| ------------------ | -------------------------------- | ------------------------------------------------------------------------ |
| `ASR_PROVIDER`     | `sarvam`                         | Speech-to-text adapter: `sarvam`, `openai`, or a dotted class path        |
| `ASR_BASE_URL`     | `https://api.sarvam.ai`          | ASR API origin                                                            |
| `ASR_API_KEY`      | —                                | ASR credential                                                            |
| `ASR_MODEL`        | `saaras:v3`                      | ASR model identifier                                                      |
| `ASR_OPTIONS`      | `{"mode": "translate"}`          | Provider extras (e.g. sarvam `mode`: `translate` / `transcribe`)          |
| `LLM_PROVIDER`     | `openai`                         | Extraction adapter: `openai` or a dotted class path                       |
| `LLM_BASE_URL`     | `https://api.groq.com/openai/v1` | Chat-completions API origin                                               |
| `LLM_API_KEY`      | —                                | LLM credential                                                            |
| `LLM_MODEL`        | `llama-3.3-70b-versatile`        | Extraction model                                                          |
| `LLM_OPTIONS`      | `{}`                             | Extra JSON body fields for the completion request                         |
| `FILLY_AUTH_TOKEN` | —                                | Optional static bearer token accepted instead of a CARE JWT (testing)     |
| `FILLY_MOCK`       | `0`                              | `1` = fake ASR/LLM, no keys needed                                        |
| `FILLY_TNC`        | (built-in text)                  | Terms & conditions shown before a user's first scribe session             |
