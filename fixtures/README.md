# Lethe v0.1 frozen fixtures

Everything here is deterministic demo material. No value is a production secret.

- `documents/canary_source.md` is original source used to prove ghost knowledge.
- `documents/corrected_source.md` is immutable replacement branch.
- `documents/policy_parent.md` plus `documents/multi_parent_summary.md` exercise ACL
  intersection and earliest-parent expiry.
- `probes/prohibited.json` and `probes/unrelated.json` contain exactly 100 fixed queries each.
- `keys/demo_keys.json` contains intentionally public, deterministic Ed25519 private seeds.
- `keys/enrollments.json` binds corresponding public keys to demo scope and roles.
- `demo-events.json` contains pre-signed commands for dashboard/demo use.
- `manifest.json` pins settings, labels, denominators, and SHA-256 asset digests.

Editing any hashed asset requires an explicit fixture-version change and refreshed manifest hashes.
Receipt signatures prove fixture signer and record integrity only; they do not prove complete deletion.
