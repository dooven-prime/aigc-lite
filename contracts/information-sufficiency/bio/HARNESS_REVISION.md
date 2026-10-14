# Bio Case v1 test-harness revision

The published `case-lock.json` continues to identify the original candidate-only
Bio information-sufficiency Case and its historical helper-test bytes at commit
`91c38f7f375d43d35125e3912c18059610644c3c`. It has **not** been rewritten
to make later files appear to have existed at that commit.

The current helper tests add an administrator session solely to exercise the
new application-service authority boundary. Their exact new bytes are pinned
in `harness-revision-v2.json`, which names and hashes the parent lock and both
historical and current helper versions. The diagnostic source, L1 record-binding
Profile, Case regression, fixture, observation projection, possible-world
constraints, and target property remain byte-identical to the v1 lock.

The current regression replays the same unsigned fixture with fresh ephemeral
signatures, but is **not** a byte-identical replay of the old test harness.
The overlay records the claimed historical commit and lock digest; this local
test does not independently reconstruct that Git tree or authenticate its
publisher signature.
Neither harness revision establishes real laboratory lineage or grants L2
qualification, knowledge admission, or execution authority.
