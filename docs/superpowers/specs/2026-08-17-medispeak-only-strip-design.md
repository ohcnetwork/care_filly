# Strip `care_filly` to a Medispeak-only plugin

**Date:** 2026-08-17
**Repos:** `ohcnetwork/care_filly` (branch `medi` → `medi-strip`), `ohcnetwork/care_filly_fe` (branch `medi` → `medi-strip`)
**Status:** approved design, ready for implementation planning

## Problem

`care_filly` is a CARE backend plugin that today carries two complete scribe
pipelines:

- an **in-house pipeline** — chunked audio upload into CARE, Sarvam/Whisper ASR,
  an OpenAI-compatible LLM extraction pass, Celery tasks, S3 recordings, quota
  accounting and per-user history; and
- a **Medispeak seam** — the backend mints a scoped token and the browser talks
  to the Medispeak API directly, never routing audio through CARE.

The Medispeak seam is now the real path. Everything else is dead weight that
still has to be read, maintained and reasoned about. This strips the plugin to
the minimum required to run against the Medispeak API.

Non-goal: changing how the Medispeak seam itself works. The session/token flow
and `medispeak_client.py` stay as they are.

## Decisions

| Question | Decision |
|---|---|
| In-house pipeline | Delete. Medispeak-only. |
| Frontend fallback branch | Delete — `REACT_MEDISPEAK_API_URL` becomes required. |
| History (past sessions + audio replay) | Delete entirely, backend and frontend. |
| Quota, usage metering, terms & conditions | Delete entirely. |
| Per-user opt-in | Keep, default off — but move it onto CARE core's `User.preferences` rather than keeping a plugin model. |
| Migrations | Fresh single `0001_initial`. |

Four accepted consequences, all confirmed against the code:

1. **CARE loses all usage visibility and spend ceilings.** `check_can_filly`
   enforces a facility opt-in flag, a facility token ceiling, a per-user
   ceiling and a per-user disable. All go. Medispeak's own dashboard becomes
   the only place usage is metered — against a paid, metered external API.
2. **Admins lose the per-user kill switch** (`FillyQuota.allow_filly`). After
   this, the only administrative lever is the `can_use_filly` RBAC permission.
3. **No keyless local dev.** `FILLY_MOCK=1` disappears; contributors need a
   real Medispeak key.
4. **Backend and frontend must deploy together** — routes vanish, so a lagging
   frontend would 404.

## Architecture after the strip

Three routes, all under `/api/care_filly/`:

```
POST /v1/medispeak/sessions            → create Medispeak session + mint first token
POST /v1/medispeak/sessions/{id}/token → refresh the scoped token
GET  /healthz                          → {"ok": true}
```

The plugin holds exactly one responsibility: **it is the only place the
Medispeak account secret lives.** It authenticates the CARE user, checks they
may use Filly, creates a session server-side, and hands the browser a
short-lived session-scoped token. Audio, transcription, extraction and results
never touch CARE.

One model survives: `MedispeakSession` — a thin pointer row that exists so the
token-mint endpoint can verify the caller owns the session.

### Authorization contract

Two gates on `create_medispeak_session`, in order:

1. `AuthorizationController.call("can_use_filly", user, facility)` → 403
   `forbidden`. **This is the real access control.**
2. `user.preferences.get("filly", {}).get("enabled")` → 403 `filly_not_enabled`.

Gate 2 is a **UI-consistency check, not access control**. `set_preferences` on
CARE core has no permission class beyond `IsAuthenticated` and always writes to
`request.user`, so the flag is user-settable by definition. This must be stated
in the code comment so nobody later mistakes it for a security boundary.

## Backend changes

### Delete outright

| Path | Reason |
|---|---|
| `api/viewsets/filly.py` | in-house session pipeline |
| `api/viewsets/scribe.py` | already unreachable — `urls.py` never imported it |
| `api/viewsets/history.py` | history removed |
| `api/viewsets/quota.py` | quota + TnC removed |
| `api/serializers/` (whole package) | empty, zero importers |
| `chunk_store.py` | in-house chunk hot path |
| `quota.py` | quota + TnC |
| `models/session.py`, `models/quota.py`, `models/preference.py` | models removed |
| `providers/` (whole package, 8 files) | no local ASR/LLM |
| `tasks/` (whole package, 5 files) | **drops the Celery dependency** |
| `migrations/0001`–`0005` | regenerated |
| `scripts/smoke.py`, `scripts/quota_smoke.py`, `scripts/history_smoke.py` | exercise deleted paths |
| `tests/test_care_filly.py` | stub for deleted code |

Celery note: `tasks/__init__.py` registers a beat entry
`care_filly_expire_stale_sessions` (every 15 min) via
`@current_app.on_after_finalize.connect`. It only ever loaded because
`api/viewsets/filly.py` imported `care_filly.tasks` at URLconf import time.
Both go, so the periodic task disappears cleanly — **but a worker still running
the old schedule will raise until it is redeployed.** Sequence the worker
redeploy with the web redeploy.

### New file: `care_filly/http.py`

`medispeak_client.py` needs `ProviderError` (from `providers/base.py`) and the
`get`/`post` helpers (from `providers/http.py`). Keeping a package called
`providers` with no providers in it would be misleading, so both collapse into
one small module. `ProviderError` is renamed `MedispeakError` — it now has
exactly one source. Update `medispeak_client.py:10-11` and
`api/viewsets/medispeak.py:26`.

### Rewrite: `api/viewsets/medispeak.py`

- Drop `from care_filly.quota import check_can_filly` (line 27) and the gate at
  lines 48-49; replace with the preference check returning the file's own
  `error()` envelope — `error("filly_not_enabled", …, 403)`. Note the current
  call returns a bare dict rather than the envelope; the replacement
  normalises this.
- Line 25 becomes `from care_filly.models import MedispeakSession` (drop
  `FillyUsage`).
- Line 26 `ProviderError` → `MedispeakError` from `care_filly.http`.
- **Delete `finalize_medispeak_session` entirely.** Its whole body writes a
  `FillyUsage` row. With it go the now-unused `from django.db import
  transaction` (line 11) and `from django.utils import timezone` (line 13).
- **Add `healthz`**, relocated from the deleted `filly.py:66-67`:

  ```python
  @require_http_methods(["GET"])
  def healthz(request: HttpRequest) -> JsonResponse:
      return JsonResponse({"ok": True})
  ```

  The original returned `{"ok": True, "mock": mock_mode()}`. The `mock` field is
  **dropped** — `mock_mode()` reads `plugin_settings.FILLY_MOCK`, and
  `PluginSettings.__getattr__` (`settings.py:43-45`) raises `AttributeError` for
  any key absent from `DEFAULTS`.

### Rewrite: `models/medispeak.py`

Drop `usage_recorded_at` — it existed solely to guard against double-charging
quota via `finalize`, which is gone.

`related_name` is safe to change: a case-insensitive grep for "filly" across
CARE core returns zero hits, and no code anywhere traverses
`filly_sessions` / `filly_quotas` / `filly_usages` / `filly_preference` /
`medispeak_sessions`.

### Rewrite: `admin.py`

Not a trim — **every** registration in the file targets a deleted model, and
nothing in it references `MedispeakSession`. This matters more than it looks:
`django.contrib.admin` is in CARE's `INSTALLED_APPS` as the autodiscovering
`AdminConfig`, and `autodiscover_modules` re-raises any exception from an admin
module that exists. A stale import raises `ImportError` inside `django.setup()`,
killing web, worker and every `manage.py` command before URLs load.

End state: a single `@admin.register(MedispeakSession)` with
`list_display = ("external_id", "user", "facility", "medispeak_session_id", "created_date")`
and `search_fields = ("user__username", "medispeak_session_id")`. Note
`BaseManager` filters `deleted=False`, so soft-deleted rows won't list.

### Rewrite: `settings.py`

`DEFAULTS` drops to two keys: `MEDISPEAK_BASE_URL`, `MEDISPEAK_API_KEY`.

Removed: `ASR_PROVIDER`, `LLM_PROVIDER`, `FILLY_MOCK`, `ASR_BASE_URL`,
`ASR_API_KEY`, `ASR_MODEL`, `SARVAM_ASR_MODE`, `LLM_BASE_URL`, `LLM_API_KEY`,
`LLM_MODEL`, `CHUNK_SECONDS`, `SESSION_TTL_SECONDS`, `FINALIZE_TIMEOUT_SECONDS`,
`MAX_AUDIO_UPLOAD_MB`, `FILLY_TNC` (a 4 KB block of legal text), plus the
`terms_and_conditions()` and `mock_mode()` helpers.

**`REQUIRED_SETTINGS` stays `set()`.** This reverses an earlier proposal and the
reason is load-bearing: `PluginSettings.__init__` calls `self.validate()`
(`settings.py:41`) and the singleton is constructed at module scope
(`settings.py:148-150`). CARE's root URLconf imports `care_filly.urls` eagerly
(`care/config/urls.py:141-142`), so a required-but-unset key would raise
`ImproperlyConfigured` at **import time** — breaking `manage.py migrate`,
`manage.py check` and `collectstatic` on any keyless box, including CI and the
release phase of a deploy.

Enforcement stays lazy and scoped, where it already is: the guards in
`medispeak_client.py:15-26` raise `MedispeakError`, converted to a 502 at
`api/viewsets/medispeak.py:63-65`.

For a startup signal, register a Django system check from
`CareFillyConfig.ready()` returning `checks.Warning("care_filly.W001", …)` when
either key is unset. **Warning, never Error** — an Error-level check raises
`SystemCheckError` and reintroduces exactly the failure we just avoided.

### Rewrite: `urls.py`

Import becomes `from care_filly.api.viewsets import medispeak`. Three paths:
`v1/medispeak/sessions`, `v1/medispeak/sessions/<str:session_id>/token`,
`healthz`. **Keep the `csrf_exempt` loop verbatim** — auth is JWT-only
(`api/common.py:3-7`) and `medispeak.py` has no per-view decorator.

### Trim: `security/`

Keep `can_use_filly` only. Delete `can_view_filly_history`,
`can_manage_filly_quota`, `can_view_filly_session` and `get_filly_history` from
`authorization.py`, and the two corresponding `FillyPermissions` enum members.

### Remaining files

- `apps.py` — one addition: register the system check above.
- `models/__init__.py` — rewritten to export only `MedispeakSession`.
- `medispeak_client.py` — import swap only (`care_filly.http`).
- `api/common.py` — genuinely unchanged. Verified: `authenticate`, `body`,
  `error`, `parse_uuid` and `resolve_facility` depend on nothing being deleted.
- `README.md` — the route, environment and "How it's fast" tables all document
  deleted behaviour and must be rewritten alongside the code.
- `setup.py` / `setup.cfg` / `MANIFEST.in` / `Makefile` / `requirements_dev.txt`
  — check for references to deleted packages before landing.

## Preference: reuse CARE core, add no model

`FillyUserPreference` is deleted. The opt-in moves to CARE core's
`User.preferences` JSONField at key `filly` → `{"enabled": bool}`. Absent key
means off, which gives default-off for free.

Contract, traced through the router rather than guessed:

**Read** — `GET /api/v1/users/getcurrentuser/`

`preferences: dict` is declared **only** on `CurrentUserRetrieveSpec`
(`care/emr/resources/user/spec.py:162`), served only by the `getcurrentuser`
action (`care/emr/api/viewsets/user.py:174-177`). It is *not* on
`UserRetrieveSpec`, so `GET /api/v1/users/{username}/` will not carry the flag.
There is no `users/me` endpoint.

**Write** — `POST /api/v1/users/set_preferences/` (trailing slash required)

```json
{ "preference": "filly", "version": "0.0.1", "value": { "enabled": true } }
```

All three fields are **required** (`UserPreferenceRequest`, `user.py:67-70`).
`value` must be a JSON object — a bare boolean is rejected. Response is
**201 with an empty body**; core's own frontend types it as `CurrentUserRead`,
which is wrong — do not copy that typing.

**Server-side read** needs no core change: `api/common.py:14-33` delegates to
`CustomJWTAuthentication`, whose `get_user` is untouched, so the returned object
is a fully-loaded `User` with `preferences` as a plain dict and zero extra
queries.

**No `PREFERENCE_SCHEMA` entry needed** — `validate_preference` only validates
keys present in the schema (`user.py:75`), and `filly` won't be. Worth stating
honestly: `facility_quick_links` is the only key in use today and it is
core-owned, so `filly` is the first plugin-owned key on a path with no test
coverage in core. Writes are per-key merges, so `filly` cannot clobber
`facility_quick_links`; but `set_preferences` is a read-modify-write on a
JSONField with no locking, so concurrent writes to *different* keys can clobber
each other. Acceptable for a single toggle, worth knowing.

No plugin endpoint is added. `/v1/preferences/filly` is deleted.

## Frontend changes

### Delete

`src/lib/filly-api.ts`, `src/lib/filly-history.ts`, `src/lib/quota-api.ts`,
`src/hooks/useQuota.ts`, `src/components/filly/HistoryPanel.tsx`,
`src/components/filly/TncDialog.tsx`, all four `src/components/admin/*.tsx`,
`src/state/user-atom.ts`.

Newly orphaned UI primitives, all verified to lose their last importer:
`src/components/ui/{badge,checkbox,command,select,field,label,separator,dialog,sheet}.tsx`.
Only `button.tsx` and `switch.tsx` retain live importers.

### The riskiest edit: `src/hooks/useFilly.ts`

Collapsing the `isMedispeakConfigured()` branches is not mechanical. Three
hazards the compiler will not catch:

1. **Teardown.** When `capturedAudioRef` and `flushHistoryAudio` are removed,
   lines 479-480 must become `await recorder.stop(); recorderRef.current = null;`
   — **never delete the statement.** `recorder.stop()` is the only path that
   destroys the VAD, stops the `MediaRecorder` and stops the `MediaStream`
   tracks (`filly-engine.ts:125-130`). Deleting it compiles cleanly and leaves
   the microphone recording indefinitely.
2. **Ordering.** `await Promise.allSettled(pendingUploadsRef.current)` (:481-482)
   must stay **before** the final silence-marker chunk (:486-491), or Medispeak
   commits before the last utterances land.
3. **History reprocess.** `flushHistoryAudio` is the only writer of live-session
   audio. Since history is being deleted, remove the whole surface in one move:
   `useFilly.ts:126-130` (interface member), `520-626` (`processAudioFile`),
   `:669` (return), the `segmentAudioBlob` import at `:13`, plus
   `FillyController.tsx:564-580` (`handleReprocessFromHistory`) and its
   `HistoryPanel` wiring at `:617`.

### Symbol-level edits

These files are shared between both pipelines — they survive, but shed the
legacy-only half:

- `src/lib/template-builder.ts` — keep `extractFormFields`, `FormField`,
  `MedispeakFieldSpec`, `buildFieldSpecs`. Delete `buildTemplateDescription`
  (:98-166) and its now-unused imports.
- `src/lib/structured/fill.ts` — keep `collectFieldsToFill` /
  `applyFieldToState`. Delete `getStructuredFieldDescription` (:486-491) and
  `getStructuredFieldExample` (:493-497); drop
  `STRUCTURED_TEMPLATE_DESCRIPTIONS` / `STRUCTURED_TEMPLATE_EXAMPLES` from the
  import at :6-11.
- `src/lib/structured/index.ts` — delete `STRUCTURED_TEMPLATE_EXAMPLES`
  (:31-137) and `STRUCTURED_TEMPLATE_DESCRIPTIONS` (:139-155); they lose their
  only consumers.
- `src/lib/filly-engine.ts` — keep `VadRecorder` + `encodeSilenceMarker`.
  Delete the offline path (`segmentAudioBlob` :253-282, `decodeToMono16k`
  :212-231, `capSegments` :234-243, the `NonRealTimeVAD` import) and the entire
  full-session `MediaRecorder` path (`RECORDER_MIME_CANDIDATES` :41-45,
  `this.recorder` + `historyChunks` :58-59, `startHistoryRecorder` :146-167,
  `finishHistoryRecorder` :169-191, and the recorder handling inside `pause()`
  :111, `resume()` :118, `cancel()` :135-140). `stop()` (:125-130) becomes
  `Promise<void>` doing VAD teardown only.

### Other edits

- `src/components/filly/index.ts` — delete the `HistoryPanel` re-export
  (line 2). **Mandatory:** `src/index.tsx:5` imports `FillyController` through
  this barrel and `vite.config.mts:39` makes `src/index.tsx` the rollup entry,
  so a dangling re-export fails the federated build.
- `src/routes.tsx` — delete the two admin routes
  (`/admin/filly/quotas`, `/admin/filly/quotas/:id`), their lazy imports and the
  local `AdminPage` wrapper. Note: four admin files, but only two routed pages —
  `QuotaSheet`/`UserQuotaSheet` are child components.
- `src/manifest.ts` — remove the "Filly Quotas" entry from `adminNavItems`
  (:22-28). Leaving the key as `[]` or deleting it are both safe; care_fe types
  it optional (`pluginTypes.ts:209`) and guards it (`admin-nav.tsx:85-87`).
- `src/hooks/useFillyPreference.ts` — rewritten (below).
- `src/lib/medispeak-api.ts` — drop `isMedispeakConfigured` and
  `finalizeMedispeakSession`; `REACT_MEDISPEAK_API_URL` becomes required.
- `src/components/filly/FillyController.tsx` — remove history button/panel
  wiring, and the handling of the deleted error codes
  `facility_quota_exceeded` / `user_quota_exceeded` (:659-679).
- `src/components/filly/FillyProfileSection.tsx` — drop TnC and quota UI, keep
  the toggle; update the `filly_not_enabled` handling at :99.
- `src/components/filly/FillySettingsPage.tsx` — trim to the surviving toggle.
- `package.json` + `vite.config.mts` — paired edit. Dead deps that fall out:
  `recharts`, `cmdk`, `@radix-ui/react-{checkbox,dialog,label,select,separator}`,
  and `raviger` (only used by the deleted admin routes).
- Locale files — remove keys for history, quota and TnC.

### `useFillyPreference` rewrite

Do **not** read through `user-atom.ts`. Nothing reads that atom today and a
`sessionStorage`-backed jotai atom cannot observe a same-tab write. Delete it.

Use `@tanstack/react-query`, which is shared and deduped between host and
plugin:

- **Read** — `useQuery` with a queryKey whose first element is `"currentUser"`
  (e.g. `["currentUser", "filly"]`) against `GET /api/v1/users/getcurrentuser/`,
  selecting `preferences?.filly?.enabled`. Use the existing `src/lib/request.ts`
  helper with no `baseUrl` — `request.ts:97` already resolves against
  `window.CARE_API_URL`. **Do not** use `FILLY_BASE_URL`; `filly-be.ts:10`
  appends `/api/care_filly/v1`, the wrong base for core endpoints.
- **Write** — `useMutation` POSTing `/api/v1/users/set_preferences/` with
  `onSuccess: () => queryClient.invalidateQueries({ queryKey: ["currentUser"] })`.
  That prefix matches the host's `["currentUser", accessToken]`, so one
  invalidation refreshes both the plugin's copy and the host's — the same
  pattern core uses at `care_fe/src/hooks/useUserPreferences.ts:163-165`. The
  mutation must not read the response body; there isn't one.

## Migrations and data

Delete `0001`–`0005`; generate one fresh `0001_initial` containing only
`MedispeakSession`.

**Verified:** no environment has ever run these migrations. The running Docker
`care-db-1` CARE database — fully migrated on core (574 facility, 128 users, 88
emr migrations) — returns zero rows for
`SELECT app, name FROM django_migrations WHERE app='care_filly'` and has no
`%filly%` or `%medispeak%` tables. CARE core's `plug_config.py` is `plugs = []`,
neither repo has CI workflows, `care_filly` has no releases or tags, and
`setup.py` declares Pre-Alpha 0.1.0.

The known risk of reusing the `0001_initial` name — Django keys applied state on
`(app, name)`, so a rewritten `0001_initial` is silently skipped on a DB that
already ran it, leaving legacy tables forever — is therefore not live. But
because we cannot inspect every environment, the reset below is a **required
pre-deploy step** for any instance that ever installed the plugin. Confirmed
table names (app label `care_filly`, no `db_table` overrides):

```sql
BEGIN;
DROP TABLE IF EXISTS
  care_filly_medispeaksession,
  care_filly_fillysession,
  care_filly_fillyquota,
  care_filly_fillyusage,
  care_filly_fillyuserpreference
CASCADE;
DELETE FROM django_migrations WHERE app = 'care_filly';
COMMIT;
```

`CASCADE` is not strictly required — the only intra-app FK is
`FillyUsage.session → FillySession` and both are in the drop list; every other
FK points outward to `users_user` / `facility_facility`. It is kept as a
harmless guard. Do **not** attempt `django_content_type` cleanup: the
`auth_permission → django_content_type` FK is `DEFERRABLE INITIALLY DEFERRED`
with no `ON DELETE`, so a naive delete fails.

### S3 purge — required, and ordered before the table drop

`FillySession` rows carry `internal_name`, pointing at clinical consultation
recordings in the `BucketType.PATIENT` bucket under the `filly_session/` prefix.
**Dropping the table destroys the only index of those objects.** Purge first:

1. Iterate `FillySession._base_manager.filter(internal_name__isnull=False).exclude(internal_name="")`
   — **`_base_manager`, not `objects`.** `objects` filters `deleted=False` and
   would skip every session a user cleared from history, whose audio is still
   live in S3.
2. Call `files_manager.delete_object(session, quiet=True)` per row.
3. Only once the purge reports zero remaining keys, drop the table.

This follows core's own precedent in
`care/emr/tasks/cleanup_incomplete_file_uploads.py` — delete the object first,
collect ids, then delete rows. Verify with
`aws s3 ls --recursive s3://<patient-bucket>/filly_session/` (expect empty), with
`aws s3 rm --recursive` as the fallback for keys whose rows were already lost.

On the verified state above there is nothing to purge, but the step is
unconditional: it costs one query and protects patient audio.

## Testing

The existing `tests/test_care_filly.py` is a stub for deleted code and goes. The
strip should land with tests that did not exist before:

1. **Route surface** — exactly three routes resolve; the deleted paths 404.
2. **Authorization** — `create_medispeak_session` returns 403 `forbidden`
   without `can_use_filly`, and 403 `filly_not_enabled` when
   `user.preferences["filly"]["enabled"]` is absent or false; 201 when both pass.
3. **Preference default-off** — a user with no `preferences` key is denied.
4. **Session ownership** — minting a token for another user's `MedispeakSession`
   returns 404.
5. **Unconfigured keys** — with `MEDISPEAK_BASE_URL`/`MEDISPEAK_API_KEY` unset,
   `manage.py check` and `manage.py migrate` still succeed (the regression the
   `REQUIRED_SETTINGS` reversal exists to prevent), and the session endpoint
   returns 502.
6. **Import safety** — `django.setup()` succeeds with the plugin installed
   (catches a stale `admin.py`).

Frontend: the teardown hazard deserves an explicit check that `recorder.stop()`
is called on the stop path and the `MediaStream` tracks end.

## Alternatives considered

- **Keep both pipelines**, deleting only provably-dead code. Rejected: leaves
  the maintenance burden the strip exists to remove.
- **Re-point history at `MedispeakSession`.** Rejected: history is deleted.
- **Additive `0006_strip_inhouse` migration** instead of a fresh `0001_initial`.
  Safer in the abstract — it needs no manual reset and cannot silently no-op —
  but the fresh migration was chosen deliberately for a clean end state, and the
  evidence says no database has ever run the old chain. Recorded here because if
  an unknown environment surfaces, this is the fallback.
- **`REQUIRED_SETTINGS` for the Medispeak keys.** Rejected on evidence — it
  breaks `migrate`/`check`/`collectstatic` at import time. Replaced by a
  `Warning`-level system check.
