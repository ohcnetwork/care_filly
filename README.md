# care_filly

CARE backend plugin for [`care_filly_fe`](https://github.com/ohcnetwork/care_filly_fe) —
the frontend module that records clinician dictation and turns it into
structured form-fill data using the [Medispeak](https://medispeak.in) API.

The plugin has exactly one job: **it is the only place the Medispeak account
secret lives.** It authenticates the CARE user, checks they may use Filly,
creates a Medispeak session server-side, and hands the browser a short-lived,
session-scoped token. Audio, transcription and extraction go directly between
the browser and Medispeak — **this backend never sees the audio.**

```
browser ──[CARE JWT]──> care_filly ──[account secret]──> Medispeak   (create session, mint token)
browser ──────────────[scoped token mss_...]──────────> Medispeak   (audio, commit, results)
```

`care_filly` is a Django app plugged into [CARE](https://github.com/ohcnetwork/care)
via `plugs.manager.PlugManager` and mounted at `/api/care_filly/`. It reuses
CARE's own JWT auth — the frontend just sends the logged-in user's access
token, same as any other CARE API call.

## Installation

Register the plugin in CARE's `plug_config.py`:

```python
from plugs.plug import Plug

care_filly_plug = Plug(
    name="care_filly",
    package_name="care_filly",
    version="",
    configs={
        "MEDISPEAK_BASE_URL": "https://api.medispeak.example/api/v2",
        "MEDISPEAK_API_KEY": "msk_live_...",
    },
)

plugs = [care_filly_plug]
```

Then `pip install -e .` this repo into CARE's environment (or add it to
`plugs.txt` / your Docker build if installing from a git remote), and run
`python manage.py migrate care_filly`.

## Endpoints

| Method | Path                                       | Purpose                                        |
| ------ | ------------------------------------------ | ---------------------------------------------- |
| POST   | `/api/care_filly/v1/medispeak/sessions`    | Create a Medispeak session + mint first token  |
| POST   | `/api/care_filly/v1/medispeak/sessions/{id}/token` | Refresh the scoped token               |
| GET    | `/api/care_filly/healthz`                  | Health check                                   |

`{id}` is the plugin's own `MedispeakSession.external_id`, not Medispeak's
session id. Token minting is owner-scoped: requesting a token for another
user's session returns 404.

## Access control

Two gates on session creation, in order:

1. **`can_use_filly`** — a CARE facility permission granted to the doctor,
   nurse, staff, admin, facility-admin and administrator roles. **This is the
   real access control.**
2. **`user.preferences["filly"]["enabled"]`** — a per-user opt-in stored on
   CARE core's `User.preferences`, absent (off) by default. This is a
   UI-consistency check, *not* access control: CARE's `set_preferences` action
   is authenticated-only and always writes to `request.user`, so the flag is
   user-settable by definition.

The frontend reads the flag from `GET /api/v1/users/getcurrentuser/` and writes
it with `POST /api/v1/users/set_preferences/`:

```json
{ "preference": "filly", "version": "0.0.1", "value": { "enabled": true } }
```

No plugin endpoint is involved, and no `PREFERENCE_SCHEMA` entry is needed —
CARE only schema-validates preference keys it knows about.

## Environment

| Variable             | Purpose                                                             |
| -------------------- | ------------------------------------------------------------------- |
| `MEDISPEAK_BASE_URL` | Medispeak v2 API root, e.g. `https://api.medispeak.example/api/v2`   |
| `MEDISPEAK_API_KEY`  | Medispeak account secret (`msk_live_...`) — never expose to a browser |

Both can be set via `PLUGIN_CONFIGS` or as environment variables. Neither is a
*required* setting: the plugin loads without them so that `manage.py migrate`,
`manage.py check` and `collectstatic` still work on boxes without credentials
(CI, the release phase of a deploy). Instead you get a `care_filly.W001`
startup warning, and the session endpoints return 502 at call time.
