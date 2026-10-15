# ADR-0002: Fail-closed rights evidence and provenance-preserving asset reuse

Status: accepted for implementation; CI/live release gates must be rerun. Date: 2026-10-12.

## Failing invariant

Production truth must not classify an asset as rights-cleared merely because a license/status label exists. Reusing media bytes must preserve their original provider/license metadata.

## Evidence and root cause

The PR #37 acceptance artifact produced a 60-second H.264/AAC MP4 with SHA-256 8fc266f90a4bfcf054f6cbae3e607f821dbd55d91589a8fd9a863fcd1017983a and truth state VERIFIED. Its rights manifest recorded three verified-source-reuse assets without source URL/creator and with UNKNOWN_REVIEW_REQUIRED commercial-use state; a fourth Commons asset remained review-required. The reviewer packet had human review pending.

In production_closure_3624.py, a license/status string made a visual rights record evidence_present, and the gate counted only the exact state unknown. In studio_ultimate.py acquire_scene_asset, the distinct-source reuse path rebuilt a generic record with null source URL/creator and an inherited license label, erasing provenance.

## Decision

- Clear an asset only when an asset-specific URL, specific license/terms URL, license identifier, identifying title, applicable attribution, and compatible known commercial-use state exist.
- NC, ND, ambiguous provider/model terms, unsupported ShareAlike obligations, inherited metadata and missing evidence remain review-required.
- Cache the original candidate metadata by exact downloaded path and preserve it through reuse. Reuse only when metadata exists, media is usable and relevance passes.
- Include source ID/URL, license URL, creator/attribution, restrictions, size and SHA-256 in manifests when available.
- Human editorial and native-language review remain separate requirements. Automated technical checks do not certify artistry.

## Regression coverage

tests/test_visual_diversity_truth.py covers complete CC BY evidence, inherited metadata, missing license URL, NC/ND, generic provider homepage URLs, fallback illustrations and license classification.

## Rollback

Revert if unrelated regressions appear, but preserve fail-closed rights and metadata integrity.

## Known limitation

Generic metadata cannot resolve every third-party publicity, trademark, privacy or provider-policy issue. This is not legal advice or universal clearance.


## Follow-up: visual relevance and publication status

The acceptance artifact on 9fce34a603462e5b1714dd930f65a2663bef2dd7 correctly remained REVIEW_REQUIRED because three scenes used local illustrations and the remaining visual-rights evidence was not complete. Source review traced overly literal word matching in `studio_ultimate.py::acquire_scene_asset`: a relevant item titled “video editor at a computer workstation” could be rejected because the plan said “content creator working at a video editing workstation”, and the Pexels adapter manufactured a source title from provider plus ID. The new scene-focus matcher requires independent metadata concepts and never treats the search query alone as proof. Pexels keeps a missing source title missing. The truth response now distinguishes gate configuration from quality-gate permission and reports canonical state as REVIEW_REQUIRED until verification actually passes. Tests cover semantically relevant, unrelated and placeholder-only candidate metadata.
