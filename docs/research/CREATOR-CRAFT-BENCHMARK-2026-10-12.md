# AI Infinity creator-craft benchmark and implementation evidence

Research date: 2026-10-12. Additive benchmark; not exhaustive internet coverage and not human creative approval.

## Evidence-backed production principles

**Originality is substantive, not cosmetic.** Meta's March 2026 guidance says creator-produced material is original, while third-party content needs meaningful new information, analysis or substantial narrative improvement; cosmetic edits and reposts are not enough. This is platform guidance, not a reach guarantee: https://about.fb.com/news/2026/03/rewarding-original-creators-on-facebook/

YouTube's monetization/originality requirements also treat reused and repetitive/inauthentic content as a distinct policy concern from technical rendering: https://support.google.com/youtube/answer/1311392?hl=en

**Research, script and visual intent belong in one traceable workflow.** Kurzgesagt describes a process using primary sources/research papers, expert checks, source sheets, many script drafts, visual-metaphor sketches, voice-over, animation and purpose-built music/sound: https://kurzgesagt.org/what-we-do?visit=videos. This is one studio's documented workflow, not a universal formula.

**Platform-native creative and retention data have boundaries.** TikTok Creative Codes describe principles for advertising, including platform-native work, structure, production fundamentals and sound: https://ads.tiktok.com/business/en-US/creative-codes. YouTube retention reports represent observed audience behavior once data exist; they are not truthful pre-publication predictions: https://support.google.com/youtube/answer/9314415?hl=en

**Editable means more than MP4.** Canva AI 2.0 describes conversational creation and editable output: https://www.canva.com/newsroom/news/canva-create-2026-ai/. OpenTimelineIO represents clips, tracks, timing, transitions and metadata, but references external media instead of embedding the underlying audio/video: https://opentimelineio.readthedocs.io/en/latest/

**Captioning and sound need independent validation.** WCAG 2.2 criterion 1.2.2 requires synchronized captions for prerecorded audio: https://www.w3.org/WAI/WCAG22/Understanding/captions-prerecorded. Adobe documents loudness measurement and presets for different contexts including YouTube and podcasts: https://helpx.adobe.com/premiere/desktop/add-audio-effects/apply-audio-effects/about-loudness-meter.html. FFprobe independently inspects media streams and metadata: https://ffmpeg.org/ffprobe.html

**Rights depend on the asset and restrictions.** Pexels permits use/modification subject to terms, and restricts standalone redistribution, implied endorsement and some other uses: https://www.pexels.com/license/. Pixabay permits reuse/modification subject to prohibited uses and warns about trademarks and third-party rights: https://pixabay.com/service/license-summary/. Creative Commons licenses differ in attribution, ShareAlike, NonCommercial and NoDerivatives obligations: https://creativecommons.org/share-your-work/cclicenses/

Implementation rule: source-specific page, ID/URL, license/terms URL, attribution, hash and known compatible commercial-use status are required. Provider name or license label alone is not clearance. Unknown/incompatible terms, unsupported ShareAlike obligations and lost provenance remain REVIEW_REQUIRED.

## Representative 44-example creator/publisher cohort

This structured public-work cohort represents distinct creative archetypes; public channels are comparisons, not proof of private workflows or guaranteed-success formulas.

| Archetype | Public examples | Evaluation questions |
|---|---|---|
| Short-form narrative/comedy | Gohar Khan; Jordan Howlett; Khaby Lame; Zach King | Is the premise quickly legible? Is the payoff honest? Are captions/crops mobile-safe? |
| Long-form entertainment/challenge | MrBeast; Ryan Trahan; Airrack; Ludwig | Is the central question sustained? Are stakes and payoffs clear without misleading packaging? |
| Education/science | Veritasium; CGP Grey; MinuteEarth; NileRed | Are claims sourced? Do visuals explain concepts rather than decorate narration? |
| Technology/product review | Marques Brownlee (MKBHD); Dave2D; The Tech Chap; Linus Tech Tips | Do demonstrations support claims? Are comparisons fair and limits clear? |
| Documentary/journalism | Johnny Harris; Wendover Productions; ColdFusion; RealLifeLore | Are sources and uncertainty visible? Does compression preserve context? |
| Animation/motion design | Kurzgesagt; Jaiden Animations; The Infographics Show; Domics | Does every scene have a deliberate visual idea and coherent style? |
| Podcast/interview | Colin and Samir; Lex Fridman; The Diary Of A CEO; First We Feast / Hot Ones | Are speech, speakers and chapters intelligible? Do clips retain context? |
| Tutorials/learning | Ali Abdaal; Thomas Frank; freeCodeCamp.org; Fireship | Does learning progress from objective to demo, example and usable outcome? |
| Commercial/campaign | Apple; Nike; Google; LEGO | Is product proof concrete? Are rights and brand claims checked? |
| Lifestyle/food/travel | Mark Wiens; Binging with Babish; Drew Binsky; Kara and Nate | Are visuals authentic or licensed? Is cultural context treated respectfully? |
| Written/data storytelling | James Clear; Morning Brew; Our World in Data; The Pudding | Are claims traceable, labels legible and charts non-misleading? |

Sampling limitation: this is a reviewable 44-example cohort, not a claim that every creator's private process was independently audited. Process claims above use cited sources. Popularity is not treated as causal evidence; no scripts, signature style, logos or copyrighted footage are copied.

## Requirements-to-code/evidence matrix

| Requirement | Existing code area | Required verification |
|---|---|---|
| Exact visual rights/provenance | studio_ultimate.py; production_closure_3624.py | Fail-closed rights tests; source/license manifest; missing data never clears |
| Relevant distinct visuals without metadata loss | studio_ultimate.py acquire_scene_asset | Candidate source record, reuse test, scene hashes and relevance |
| Research/claim evidence | studio_ultimate.py provenance/claim ledger | Real-provider test separate from fixtures; inspect sourced claims |
| Ratio/resolution/duration | production_closure_3624.py | Independent ffprobe; no silent downgrade |
| Captions and sound | studio_ultimate.py/production_closure_3624.py | Timed cues/coverage, decoding, peak/loudness |
| Human editorial/language proof | editorial_review_3625.py | Actual reviewer records and native-speaker review |
| Editable reconstruction | timeline modules; future OTIO interchange | Round-trip edit/re-render; include referenced source media |
| Durable state | ai_infinity/persistence_truth.py; project state backend | Real PUT/HEAD/GET/hash and clean-instance restore |
| Primary intent preservation | canonical brief/parser | Explicit selector regression; collateral shorts never rewrite master format |
| Release truth | CI/deployment verification | Same live SHA creates/downloads/verifies a real project |

## Implementation conclusions

1. Retain FFmpeg until measured MLT comparison proves benefit; OTIO is interchange, not renderer.
2. Source-metadata loss and false rights clearance are correctness bugs, not documentation gaps.
3. Separate automated technical checks from human editorial and native-language approval.
4. Provider failure remains a truthful degraded/blocked state; illustration is a draft, not source footage.
5. Authorized post-publication analytics can inform revisions but cannot replace pre-release review.
