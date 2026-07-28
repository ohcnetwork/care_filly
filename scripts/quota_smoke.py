import uuid

from django.contrib.auth import get_user_model

from care_filly import quota as q
from care_filly.models import FillyQuota, FillyUsage, used_tokens

User = get_user_model()
user = User.objects.first()
fac = uuid.uuid4()

assert q.check_can_scribe(user, "not-a-uuid")["code"] == "facility_required"
assert q.check_can_scribe(user, fac)["code"] == "no_facility_quota"

fq = FillyQuota.objects.create(
    facility_external_id=fac, tokens=1000, tokens_per_user=100
)
assert q.check_can_scribe(user, fac)["code"] == "tnc_not_accepted"

_, h = q.current_tnc()
FillyQuota.objects.create(
    user=user, facility_external_id=fac, tokens=fq.tokens_per_user, tnc_hash=h
)
assert q.check_can_scribe(user, fac) is None, "expected allowed"

s = {
    "session_id": "smoke-1",
    "user_id": user.id,
    "facility_id": str(fac),
    "chunk_indexes": [0, 1, 2],
}
summary = q.record_usage(s, {"prompt_tokens": 80, "completion_tokens": 30})
assert summary == {
    "input_tokens": 80,
    "output_tokens": 30,
    "total_tokens": 110,
    "audio_seconds": 60,
}, summary
assert used_tokens(fac, user.id) == 110
assert used_tokens(fac) == 110
assert q.check_can_scribe(user, fac)["code"] == "user_quota_exceeded"

FillyUsage.objects.create(
    facility_external_id=fac, session_id="smoke-2", input_tokens=900, output_tokens=100
)
assert q.check_can_scribe(user, fac)["code"] == "facility_quota_exceeded"

fq.allow_scribe = False
fq.save()
assert q.check_can_scribe(user, fac)["code"] == "scribe_disabled"

FillyUsage.objects.filter(facility_external_id=fac).delete()
FillyQuota.objects.filter(facility_external_id=fac).delete()
print("ALL QUOTA SMOKE TESTS PASSED")
