# care_filly

Self-hosted backend for
[`care_filly_fe`](https://github.com/ohcnetwork/care_filly_fe) — the CARE frontend
module that records clinician dictation and turns it into structured form-fill
data with **chunked, near-realtime transcription** (transcript ready ~1–2s after
the recording stops, structured JSON a few seconds later).

**`care_filly`** is a Django app plugged into [CARE](https://github.com/ohcnetwork/care)
via `plugs.manager.PlugManager`, mounted at `/api/care_filly/`. It reuses CARE's own
auth, adds per-facility/per-user quota enforcement, terms-and-conditions gating, and
persists Filly session history.

This plugin follows the structure of
[ohcnetwork/care_hello](https://github.com/ohcnetwork/care_hello), the CARE
plugin boilerplate.

## How it's fast

```
naive pipeline: record ──────────────┤stop├─ transcribe all ─ template LLM ─ done  (~30-47s)
this backend:   record ─ transcribe chunks as they upload ─┤stop├─ last chunk ─ LLM ─ done (~3-6s)
```

- Audio chunks (≤20s each) upload during recording; each is transcribed immediately
  (Sarvam AI by default, or an OpenAI-compatible Whisper endpoint).
- On stop, only the final chunk remains → transcript assembled almost instantly.
- One LLM call (OpenAI `gpt-4o-mini` JSON mode by default, or any OpenAI-compatible vendor)
  converts the transcript into form-fill JSON using the questionnaire schema the
  frontend sends per-session in `additional_data`.

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
in `care_filly/plugin_settings.py` can be overridden via `PLUGIN_CONFIGS` or
equivalent environment variables — see [Environment](#environment) below.

CARE's own JWT auth is used to authenticate requests — the frontend just sends
the logged-in user's access token, same as any other CARE API call.

No keys yet? Set `FILLY_MOCK=1` to run the full flow with fake
transcription/extraction (useful for wiring up the frontend without any
provider accounts).

### Frontend wiring

The frontend talks to the plugin at `/api/care_filly/` on the CARE API origin
(derived from `window.CARE_API_URL` — no extra configuration needed).

## Endpoints

Mounted at `/api/care_filly/v1/...`:

| Method | Path                                       | Purpose                                                         |
| ------ | ------------------------------------------ | --------------------------------------------------------------- |
| POST   | `/v1/sessions`                             | Create session                                                  |
| GET    | `/v1/sessions/{session_id}`                | Status poll (transcript appears before templates finish)        |
| POST   | `/v1/sessions/{session_id}/chunks`         | Chunk upload (`audio_N.mp3`) — transcription starts immediately |
| POST   | `/v1/sessions/{session_id}/end`            | End recording → assemble transcript, run extraction             |
| POST   | `/v1/sessions/{session_id}/process/template/{template_id}` | Re-run extraction                               |
| GET    | `/healthz`                                 | Health check                                                    |

The CARE-plugin mode additionally exposes quota and history management:

| Method           | Path                                      | Purpose                                          |
| ---------------- | ------------------------------------------ | -------------------------------------------------- |
| GET               | `/v1/quota/my`                            | Current user's quota + usage for a facility        |
| POST              | `/v1/quota/accept-tnc`                    | Accept the terms & conditions                      |
| GET/PUT           | `/v1/preferences/filly`                   | Get/set the user's per-user Filly opt-in           |
| GET/POST          | `/v1/quota`                               | List/create facility or user quotas (admin)        |
| GET/PATCH/DELETE  | `/v1/quota/{external_id}`                 | Manage a single quota row (admin)                  |
| GET               | `/v1/history`                             | List the current user's past Filly sessions        |
| GET/DELETE        | `/v1/history/{external_id}`               | Fetch/soft-delete a history entry                  |
| GET               | `/v1/history/{external_id}/audio`         | Download the recorded audio for a history entry    |
| POST              | `/v1/history/session/{session_id}/audio`  | Upload the recording once a session ends           |

## Environment

| Variable            | Default                   | Purpose                                                                                                     |
| ------------------- | ------------------------- | ----------------------------------------------------------------------------------------------------------- |
| `LLM_PROVIDER`      | `openai_compat`           | OpenAI-compatible LLM backend                                                                              |
| `ASR_PROVIDER`       | `sarvam`                  | `sarvam` (best for Indian languages) or `openai_compat` (Whisper)         |
| `ASR_API_KEY`        | —                         | Required (speech-to-text) for the active `ASR_PROVIDER`                   |
| `ASR_BASE_URL`       | `https://api.sarvam.ai`   | ASR vendor base URL (set to the OpenAI-compatible base for `openai_compat`) |
| `ASR_MODEL`          | `saaras:v3`               | `saaras:v3` / `saarika:v2.5` (Sarvam) or `whisper-1` (Whisper) |
| `SARVAM_ASR_MODE`    | `translate`               | `translate` (English output) or `transcribe` (original script)           |
| `LLM_API_KEY`        | —                         | Required (structured extraction)                                          |
| `LLM_BASE_URL`       | `https://api.openai.com/v1` | OpenAI-compatible LLM base URL                                     |
| `LLM_MODEL`         | `gpt-4o-mini` | Extraction model                                                                                            |
| `FILLY_AUTH_TOKEN` | —                         | Optional static bearer token accepted instead of a CARE JWT (testing)  |
| `FILLY_MOCK`       | `0`                       | `1` = fake ASR/LLM, no keys needed                                                                          |
| `FILLY_TNC`        | (built-in text)           | Terms & conditions shown before a user's first Filly session               |
