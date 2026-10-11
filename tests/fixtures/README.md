# Recovery fixture policy

Fixture ID: cc0-recovery-illustration-v1

The acceptance harness generates deterministic 1280x720 PNG illustrations with Pillow in studio_ultimate._ci_test_visual. No provider, model, network call or downloaded art is involved. Every image visibly says "CC0 TEST ILLUSTRATION" and "NOT REAL FOOTAGE".

The repository-generated art is dedicated under the CC0 1.0 Universal Public Domain Dedication:
https://creativecommons.org/publicdomain/zero/1.0/

This fixture is only for deterministic retry/fault-injection tests. It is recorded with fallback=true, quality_tier=original_vector_editorial_fallback and asset_kind=illustrative_graphic_not_photograph. A fixture-backed render must remain REVIEW_REQUIRED and publishing must be blocked. It is not proof of real-provider or professional creative quality.

All three controls are required: CI=true, AI_INFINITY_TEST_MEDIA=1, and the exact allow-listed fixture ID supplied in the request. Without them the helper returns no fixture and production uses the ordinary provider ladder.
