# AI Infinity — Creator Resource Registry and Enablement Plan

Research date: 2026-10-15. This register is additive. It does not replace the protected creator benchmark, ten-method research report, or master command.

## Operating rule: resources are not evidence until they work

Track every source through these separate states: discovered → terms reviewed → adapter implemented → credentials validated (when required) → real query succeeded → actual bytes downloaded and decoded → source metadata and SHA-256 preserved → rights cleared or review required → incorporated into a real render → output inspected → persistence/recovery verified → human approval where applicable → publish API state reconciled.

Do not collapse these stages into one connected badge. A provider name, token, result count, generated MP4, contact sheet, green syntax check, or a CI fixture is not evidence of production quality. Unknown rights stay REVIEW_REQUIRED. Fallback illustrations are useful drafts but do not count as professional source footage.

## Resource inventory

| Resource | Access / capability | Current or proposed use | Gate and caveat |
|---|---|---|---|
| Openverse API | Free/open API for openly licensed images and other media; filters include license and commercial/modification compatibility | Existing image adapter; use CC0/CC BY as the currently supported baseline | Preserve per-item title, ID, original landing page, creator, exact license/version/deed and downloaded-byte hash; validate locally. Do not assume a result is cleared merely because it was indexed. |
| Wikimedia Commons API | Searchable media with item-level source and extmetadata; no API key | Existing images and some video | Terms vary by file. Preserve exact file page, creator, license URL and attribution. NC/ND, unsupported ShareAlike obligations and missing evidence must remain review-required. |
| NASA Image and Video Library | Search and download space/science media; no API key | Existing image adapter | NASA media-use terms are not universal clearance for every item. Keep the item record and use/right notices; independently review non-NASA work and restrictions. |
| Pexels API | Free API key for stock video and photos | Existing video adapter; extend to the documented photo-search endpoint | Keep photographer, item page, source ID, and Pexels terms. Respect standalone redistribution, implied endorsement and third-party-right restrictions. API availability and rate limits can change. |
| Pixabay API | Free API key for stock video and images | Existing video adapter; extend to the documented image-search endpoint | Preserve username, item page, ID and content-license URL. Check trademarks, privacy and other rights separately. |
| Library of Congress JSON/YAML API | Public search and item metadata; no API key | Add as a supplementary discovery source, not an automatically cleared source | Viewing/downloading is not universal permission to reuse. Retain the item-specific Rights and Access/Rights Advisory; default to REVIEW_REQUIRED unless exact rights pass the normal policy. |
| Local FFmpeg and FFprobe | Free, local rendering and independent media inspection | Existing baseline renderer/probe | Bound threads, duration, pixel counts and downloads for low-memory hosting. Validate codec, dimensions, aspect ratio, duration, full decode, black/frozen frames, and audio from actual bytes. |
| Pillow | Local image parsing, normalization and thumbnails | Existing dependency | Decode untrusted media defensively, cap pixel dimensions and sizes, and reject corrupt or malformed assets. |
| eSpeak / eSpeak NG | Local/offline TTS where installed | Existing free fallback | Identify it truthfully as offline voice; validate pronunciation, voice quality and requested language instead of calling it neural or provider-connected. |
| Hugging Face Hub / Inference Providers | Optional model and inference ecosystem; token, model, provider policy and availability vary | Existing optional image/video path | A token is not successful inference or free credits. Review each model card/license and provider terms; never require paid inference for baseline workflows. |
| Freesound API v2 | Searchable sound effects and audio; account/API credential required | Candidate audio source | Rights vary per sound. Preserve sound ID, creator, item URL, exact license/version, attribution and bytes hash. Reject incompatible NC/ND rights for commercial/editing purposes. |
| Internet Archive APIs | Broad historical audio, video, texts and metadata | Candidate discovery source | Collection membership or accessible download does not establish a reusable license. Use per-item rights and terms; do not bulk-assume clearance. |
| Smithsonian Open Access API | Open-access metadata and media; API key required | Candidate source | Only treat specific records as CC0 where the item says so. Keep item ID, attribution and third-party-right warnings. |
| Europeana API | Aggregated cultural-heritage records; API key required | Candidate source | Inspect each record's rights statement and media availability; no generic Europeana-wide license. |
| OpenTimelineIO (OTIO) | Open timeline interchange for clips, tracks, timing, transitions and metadata | Candidate for editable project exchange; not a renderer | Media is referenced externally rather than embedded. Prove package paths resolve, export/import round-trip works and the project can be re-rendered after moving workspaces. |
| MLT Framework | Open-source multitrack authoring/rendering framework | Bounded comparison only | Keep FFmpeg as reference until measured 20s/60s/180s tests show a useful quality or capability benefit within real memory/time limits. |
| Google Fonts / Noto fonts | Open-license typefaces and broad multilingual-script support | Candidate for consistent multilingual rendering | Preserve each font's license when bundling. Urdu, Pashto, Dari and Arabic need shaping/RTL checks and native-speaker review; Latin-only tests do not close this gate. |
| YouTube Data and Analytics APIs | Authorized publishing and actual channel metrics; OAuth scopes required | Existing product connection surface, not assumed live-connected | Require explicit destination authorization, human approval for the exact current artifact fingerprint, and real returned upload/resource IDs. Analytics exist only after authorized channel activity. |
| TikTok Content Posting API / Meta Instagram Graph API | Platform-specific publishing and permitted insights; account/app permissions may be required | Future adapter candidates | Treat app review/scopes as prerequisites; never simulate a connected account or upload. Implement one provider at a time and reconcile actual remote status. |

## Resource expansion order

### First: broaden useful inputs without new paid dependencies

1. Extend the existing Pexels adapter from video to photos and the existing Pixabay adapter from video to images. Reuse current optional free API keys only when supplied by the owner. No secret goes into repository source, manifests, browser output or logs.
2. Add Library of Congress as a supplementary discovery rung. It must keep item-level rights advisory and default to REVIEW_REQUIRED; it cannot clear a project on its own or be used to bypass current professional gates.
3. Keep Openverse, Commons and NASA as core keyless search resources. Keep local original illustration as an honest degraded draft fallback only.
4. Keep model-driven image/video generation optional. A model response must be decoded and validated, and terms must be recorded. No credit card or paid inference becomes a baseline requirement.

### Second: professional media acceptance

Each scene must map to its requested story beat, and media metadata must substantively match the scene focus. Distinct database rows cannot manufacture visual diversity: hash actual source bytes and compare against rendered scene masters. Preserve source records through acquisition, selection, edit, manifest, export and recovery. Rights are per item, not per provider. Human inspection of a real contact sheet/reviewer packet remains separate from machine checks.

The primary deliverable selector must be immutable through social/article add-ons. Preserve the requested aspect ratio, resolution and duration, and use FFprobe plus full decode to verify the final video. SRT must be checked against the measured speech timeline, not falsely required to cover a deliberate silent visual tail. Captions must remain sequential, readable and within media bounds.

### Third: audio, languages and editable timelines

Add Freesound only with per-item license controls. Evaluate OTIO on a representative project containing multiple video clips, narration, music and subtitles, then import/export and re-render. Benchmark MLT versus FFmpeg on identical test material under the deployment memory limit before adopting it. Generate native review packets for Urdu, Pashto, Dari and Arabic; validate glyph shaping, RTL layout, punctuation, pronunciation and meaning with qualified reviewers.

### Fourth: publishing and actual analytics

Use YouTube first because the product already models it. Verify OAuth scopes, actual channel identity, approved destination, artifact fingerprint, human review decision, API response identifier and subsequent status reconciliation. A disconnected account should simply remain not connected while download/export remain available. Treat TikTok and Meta as credential/permission-gated candidates, not UI-only checkboxes.

## Test contract for every provider adapter

Test missing credentials, wrong credentials, 401/403/404/429/5xx, network timeout, malformed/empty metadata, missing item URL/license/creator, unsafe redirects, private-network URL attempts, oversized/corrupt/wrong-type media, hash duplicates, irrelevant title/description/tags, incompatible license classes, successful bounded download and correct secret redaction. Add mocked deterministic adapter tests and a separate opt-in live network probe; never depend on outside API stability for ordinary unit tests.

Expose statuses separately: implemented, credentials-configured, reachable, bytes-verified, rights-cleared, production-verified, human-approved and published. Each state needs its own evidence and timestamp. Never report these as a single 100% score.

## Official resource references

- Openverse API: https://api.openverse.org/v1/ and https://docs.openverse.org/meta/media_properties/api.html
- Wikimedia Commons API: https://www.mediawiki.org/wiki/API:Main_page
- NASA Image and Video Library API: https://images.nasa.gov/docs/images.nasa.gov_api_docs.pdf
- Pexels API / license: https://www.pexels.com/api/documentation/ and https://www.pexels.com/license/
- Pixabay API / content license: https://pixabay.com/api/docs/ and https://pixabay.com/service/license-summary/
- Library of Congress API / rights guidance: https://www.loc.gov/apis/json-and-yaml/ and https://www.loc.gov/legal/security-copyright-and-privacy/understanding-copyright/
- Freesound API v2: https://freesound.org/docs/api/
- Internet Archive APIs: https://archive.org/developers/
- Smithsonian Open Access API: https://www.si.edu/openaccess/devtools
- Europeana APIs: https://pro.europeana.eu/page/apis
- Hugging Face model cards: https://huggingface.co/docs/hub/en/model-cards
- Creative Commons license chooser: https://creativecommons.org/chooser/
- FFprobe: https://ffmpeg.org/ffprobe.html
- OpenTimelineIO: https://opentimelineio.readthedocs.io/en/latest/
- MLT: https://mltframework.org/docs/
- Google Fonts: https://developers.google.com/fonts
- YouTube Data / Analytics APIs: https://developers.google.com/youtube/v3 and https://developers.google.com/youtube/analytics
- TikTok Content Posting API: https://developers.tiktok.com/products/content-posting-api/
- Instagram Graph API: https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/content-publishing/

## Current known acceptance boundary — 2026-10-15

At PR #37 head 8f2ff2259569f4d3ad790293c5b9bb561bf43b3a, the Visual Diversity Truth Regression passed, but Creator Studio Build Guard and Final Complete Acceptance Suite failed. The build guard caught a real video-smoke caption QC failure. The acceptance run correctly left a 60-second project REVIEW_REQUIRED because one scene used an original illustration and one asset lacked cleared source-specific rights. Fix these defects and prove them; do not weaken the truth gate, merge, or deploy on the strength of this inventory.

Preserve the separately versioned benchmark and master research files unchanged.