"""Regression tests for compact, topic-led research queries."""


import re
import studio_ultimate


def test_explicit_topic_is_preferred_over_long_production_objective():
    request = {
        "topic": "renewable energy",
        "title": "Canonical Runtime Smoke",
        "objective": "Create a useful 20-second explainer about another unrelated subject.",
    }
    assert studio_ultimate._research_query_from_request(request) == "renewable energy"


def test_descriptive_title_is_preferred_over_production_instructions():
    request = {
        "title": "AI Infinity launch",
        "objective": (
            "Create a professional 60-second AI Infinity launch video in English, "
            "16:9, cinematic, research-backed, with voiceover, background music, "
            "captions, thumbnail, article, SEO metadata, social campaign package "
            "and production/rights manifests."
        ),
    }
    assert studio_ultimate._research_query_from_request(request) == "AI Infinity launch"


def test_subject_is_extracted_when_title_and_topic_are_generic():
    request = {
        "title": "New project",
        "topic": "video",
        "objective": "Create a useful 20-second explainer about renewable energy.",
    }
    assert studio_ultimate._research_query_from_request(request) == "renewable energy"



def test_command_shaped_topic_does_not_override_a_clean_title():
    request = {
        "topic": (
            "Create a professional 60-second AI Infinity launch video in English, "
            "16:9, cinematic, research-backed, with voiceover, captions and SEO metadata."
        ),
        "title": "AI Infinity launch",
        "objective": "Create a professional launch video.",
    }
    assert studio_ultimate._research_query_from_request(request) == "AI Infinity launch"



def test_command_subject_before_video_is_extracted():
    request = {
        "title": "Create a professional 60-second AI Infinity launch video in English",
        "topic": "Create a professional 60-second AI Infinity launch video in English",
        "objective": "Create a professional 60-second AI Infinity launch video in English, 16:9, research-backed, with voiceover, captions and SEO metadata.",
    }
    assert studio_ultimate._research_query_from_request(request) == "AI Infinity launch"


def test_research_domain_for_product_launch_is_explicit_and_not_a_product_claim():
    query = studio_ultimate._research_search_topic(
        {"title": "AI Infinity launch", "objective": "Create an AI Infinity launch video"},
        "AI Infinity launch",
    )
    assert "video" in query and "caption" in query and "accessibility" in query
    assert "AI Infinity launch" not in query


def test_research_relevance_rejects_unrelated_scholarly_hits():
    topic = "video editing workflow content creation captions subtitles accessibility creator software production"
    unrelated = {
        "title": "Observation of a rare particle decay in high energy physics",
        "summary": "Measurements from proton proton collisions at the Large Hadron Collider.",
    }
    relevant = {
        "title": "Automatic video captioning for accessible digital media",
        "summary": "A workflow for caption production and accessibility in online video.",
    }
    assert studio_ultimate._research_source_relevant(topic, unrelated) is False
    assert studio_ultimate._research_source_relevant(topic, relevant) is True


def _upload_transform_request(**overrides):
    command = (
        "Turn the uploaded PDF into research-backed content: produce a podcast "
        "package, video, article and social campaign with evidence, captions "
        "and a final production package."
    )
    request = {
        "objective": command,
        "title": command,
        "topic": command,
        "source_file_names": ["acceptance-source.pdf"],
    }
    request.update(overrides)
    return request


def test_uploaded_pdf_title_isolated_from_flattened_metadata():
    request = _upload_transform_request()
    flattened_text = (
        "Title: AI Infinity Creator Operating System"
        "Brief: AI Infinity connects research writing visual selection and editing."
        "Source rights: Record creator item page license terms and file SHA-256."
        "Delivery: Render narration captions video article audio and social copy."
    )
    sources = [{
        "title": "acceptance-source.pdf",
        "source": "user upload",
        "text_extracted": True,
        "snippet": flattened_text,
    }]

    assert studio_ultimate._infer_uploaded_source_topic(request, sources) == (
        "AI Infinity Creator Operating System"
    )


def test_uploaded_pdf_title_isolated_with_newline_separated_metadata():
    request = _upload_transform_request()
    extracted_text = (
        "Title: AI Infinity Creator Operating System\n"
        "Brief: AI Infinity connects research writing visual selection and editing.\n"
        "Source rights: Record creator item page license terms and file SHA-256."
    )
    sources = [{
        "title": "acceptance-source.pdf",
        "source": "user upload",
        "text_extracted": True,
        "snippet": extracted_text,
    }]

    assert studio_ultimate._infer_uploaded_source_topic(request, sources) == (
        "AI Infinity Creator Operating System"
    )


def test_uploaded_pdf_does_not_override_explicit_user_topic():
    request = _upload_transform_request(
        title="Renewable Energy",
        topic="renewable energy",
    )
    sources = [{
        "title": "acceptance-source.pdf",
        "source": "user upload",
        "text_extracted": True,
        "snippet": (
            "Title: AI Infinity Creator Operating System"
            "Brief: AI Infinity connects research writing visual selection and editing."
        ),
    }]

    assert studio_ultimate._infer_uploaded_source_topic(request, sources) == ""


COMPOUND_DELIVERABLE_COMMAND = (
    "Create a 20-second research-backed video article package about "
    "Python's official website with evidence, captions, SEO and social copy."
)


def test_compound_deliverable_subject_does_not_become_quality_modifier():
    assert studio_ultimate._extract_brief_subject(COMPOUND_DELIVERABLE_COMMAND) == (
        "Python's official website"
    )


def test_research_query_uses_subject_not_brief_modifier():
    request = {
        "topic": COMPOUND_DELIVERABLE_COMMAND,
        "title": "Python's official website",
        "objective": COMPOUND_DELIVERABLE_COMMAND,
    }
    assert studio_ultimate._research_query_from_request(request) == (
        "Python's official website"
    )


def test_canonical_title_uses_subject_from_compound_deliverable_command():
    import ai_infinity_canonical

    assert ai_infinity_canonical._derive_title_from_command(
        COMPOUND_DELIVERABLE_COMMAND
    ) == "Python's official website"


def test_fallback_plan_uses_actual_subject_not_quality_modifier():
    command = COMPOUND_DELIVERABLE_COMMAND
    plan = studio_ultimate._fallback_creative_plan(
        "Python's official website",
        command,
        "short",
        20,
        "general audience",
        "clear and informative",
        {"sources": []},
        "English",
        topic=command,
    )
    assert plan["title"] == "Python's official website"
    assert plan["chapters"]
    for chapter in plan["chapters"]:
        visual_query = str(chapter.get("visual_query") or "").lower()
        assert "python" in visual_query, chapter
        assert "research-backed" not in visual_query, chapter
        narration = str(chapter.get("narration") or "").strip()
        assert re.search(r"[.!?…]$", narration), chapter
        assert len(re.findall(r"\b[\w'-]+\b", narration)) >= 6, chapter


def test_software_website_brief_uses_retrievable_topic_specific_visuals():
    command = (
        "Create a 20-second research-backed video about Python's official website "
        "with evidence, captions and a useful example."
    )
    plan = studio_ultimate._fallback_creative_plan(
        "Python's official website",
        command,
        "long",
        20,
        "general audience",
        "clear and informative",
        {"sources": []},
        "English",
        topic=command,
    )

    assert plan["title"] == "Python's official website"
    assert plan["chapters"]
    for chapter in plan["chapters"]:
        focus = str(chapter.get("visual_focus") or "").lower()
        assert any(term in focus for term in ("developer", "programmer", "source code", "website", "browser")), chapter
        queries = studio_ultimate._visual_search_query_variants(
            search_subject="Python's official website",
            focus=chapter["visual_focus"],
            topic="Python's official website",
            raw_query=chapter["visual_query"],
        )
        assert queries
        assert any(term in " ".join(queries).lower() for term in ("developer", "programmer", "code", "coding")), queries

def test_short_specific_subject_requires_title_context_and_all_subject_terms():
    topic = "Earth from space"
    exact = {
        "title": "Timeline of first images of Earth from space",
        "summary": "The first images of Earth from space came from early rockets and later spacecraft.",
    }
    one_term_only = {
        "title": "Earth in culture",
        "summary": "The cultural perspective on Earth varies across societies and historical periods.",
    }
    matching_words_but_unrelated_title = {
        "title": "Asteroid mining",
        "summary": "Asteroid material has been brought to Earth from space by several sample-return missions.",
    }
    broad_definition = {
        "title": "Outer space",
        "summary": "Outer space is the expanse beyond Earth's atmosphere and between celestial bodies.",
    }
    unrelated_space_object = {
        "title": "Artificial structures visible from space",
        "summary": "Artificial structures visible from space include buildings and walls; Earth has many human-made structures.",
    }
    unrelated_weapons = {
        "title": "Anti-satellite weapons tested in Earth orbit",
        "summary": "Anti-satellite weapons tested in Earth orbit can destroy spacecraft, even from space.",
    }

    assert studio_ultimate._research_source_relevant(topic, exact) is True
    assert studio_ultimate._research_source_relevant(topic, one_term_only) is False
    assert studio_ultimate._research_source_relevant(topic, matching_words_but_unrelated_title) is False
    assert studio_ultimate._research_source_relevant(topic, broad_definition) is False
    assert studio_ultimate._research_source_relevant(topic, unrelated_space_object) is False
    assert studio_ultimate._research_source_relevant(topic, unrelated_weapons) is False

def test_aspect_ratio_inference_does_not_read_integer_eleven_as_square():
    import ai_infinity_canonical

    intent = ai_infinity_canonical.compile_intent(
        "Create a 20-second cinematic video about Apollo 11 lunar landing.",
        {"duration": 20},
    )
    assert intent["constraints"]["aspect_ratio"] == "16:9"


def test_aspect_ratio_inference_requires_explicit_ratio_separator():
    import ai_infinity_canonical

    cases = [
        ("Create a 20-second video about Apollo 11.", "16:9"),
        ("Create a 20-second square video about coral reefs.", "1:1"),
        ("Create a 20-second 9:16 video about renewable energy.", "9:16"),
        ("Create a 20-second 4:5 video about Python.", "4:5"),
    ]
    for command, expected in cases:
        intent = ai_infinity_canonical.compile_intent(command, {"duration": 20})
        assert intent["constraints"]["aspect_ratio"] == expected, (command, intent["constraints"])



def test_research_evidence_profile_counts_distinct_urls_substantive_excerpts_domains_and_archetypes():
    archetype_words = [
        "overview fundamentals context",
        "historical timeline origin evolution",
        "mechanism process method workflow",
        "case study example implementation",
        "peer reviewed research experiment study",
        "quantitative benchmark statistics metrics",
        "practical application deployment guide",
        "impact outcome effect consequences",
        "risk limitation challenge safety",
        "debate critique counterargument alternative",
    ]
    sources = []
    for index in range(40):
        archetype = archetype_words[index % len(archetype_words)]
        words = (
            f"This evidence-backed source examines {archetype} in a specific real-world subject. "
            "It describes the scope, method, context, observations, limitations, supporting material, "
            "and implications. The excerpt is retained for source-aware writing and must not be used "
            "to claim facts beyond what the cited page actually supports. "
        ).split()
        summary = " ".join(words * 12)
        sources.append({
            "title": f"Independent research record {index + 1}: {archetype}",
            "url": f"https://research-{index}.example.org/record/{index + 1}?utm_source=collector",
            "summary": summary,
            "source": f"provider-{index % 5}",
            "retrieved_at": "2026-11-02T00:00:00Z",
        })

    # Same canonical source under a tracking URL must not inflate the research count.
    sources.append({
        **sources[0],
        "url": sources[0]["url"] + "&utm_campaign=duplicate#fragment",
        "source": "another-provider",
    })

    profile = studio_ultimate._research_evidence_profile(sources)

    assert profile["unique_source_url_count"] == 40
    assert profile["substantive_excerpt_count"] == 40
    assert profile["metadata_only_count"] == 0
    assert profile["provider_family_count"] == 5
    assert profile["unique_domain_count"] == 40
    assert profile["archetype_count"] == 10
    assert len(profile["archetypes"]) == 10
    assert profile["truthful"] is True


def test_research_evidence_profile_does_not_count_bibliographic_placeholders_as_substantive():
    sources = [{
        "title": "A scholarly work",
        "url": "https://openalex.example.org/work/1",
        "summary": "Scholarly work; abstract index available",
        "source": "OpenAlex",
    }, {
        "title": "A Crossref record",
        "url": "https://crossref.example.org/work/2",
        "summary": "Crossref bibliographic record",
        "source": "Crossref",
    }, {
        "title": "A short description",
        "url": "https://example.org/record/3",
        "summary": "A short metadata description.",
        "source": "Web",
    }]

    profile = studio_ultimate._research_evidence_profile(sources)

    assert profile["unique_source_url_count"] == 3
    assert profile["substantive_excerpt_count"] == 0
    assert profile["metadata_only_count"] == 3
    assert profile["truthful"] is True


def test_openalex_abstract_order():
    index = {"evidence": [4], "A": [0], "well-grounded": [2], "documentary": [1], "requires": [3], "sources.": [5]}
    assert studio_ultimate._reconstruct_openalex_abstract(index) == "A documentary well-grounded requires evidence sources."


def test_openalex_incomplete_abstract_is_rejected():
    assert studio_ultimate._reconstruct_openalex_abstract({"first": [0], "second": [2], "distant": [20]}) == ""
    assert studio_ultimate._reconstruct_openalex_abstract(None) == ""



def test_longform_research_expansion_targets_missing_evidence_without_weakening_gates(monkeypatch):
    calls = []
    archetype_evidence = (
        "overview fundamentals historical timeline methodology workflows case studies "
        "implementation peer-reviewed research experiments datasets quantitative "
        "measurement benchmarks applications practice adoption impacts outcomes "
        "limitations risks challenges failures safety debates critiques alternatives "
    )
    substantive_summary = (
        "This independent source provides an " + archetype_evidence
        + "analysis with documented context, scope, observations, methods, examples, "
        "supporting evidence, uncertainty, limitations, and implications. "
    ) * 5

    def fake_research_topic(query, limit=10):
        calls.append((query, limit))
        query_index = len(calls) - 1
        rows = []
        for index in range(10):
            rows.append({
                "title": f"Independent production research record {query_index}-{index}",
                "url": f"https://research-{index % 8}.example.org/record/{query_index}-{index}",
                "summary": substantive_summary,
                "source": f"provider-{index % 5}",
                "retrieved_at": "2026-11-02T00:00:00Z",
            })
        return {
            "sources": rows,
            "source_count": len(rows),
            "provider_count": 5,
            "status": "READY",
        }

    monkeypatch.setattr(studio_ultimate, "research_topic", fake_research_topic)
    result = studio_ultimate._expand_longform_research_evidence(
        {"topic": "video editing workflow", "sources": []},
        "video editing workflow",
        limit=25,
    )

    profile = result["evidence_profile"]
    assert len(calls) == 4
    assert all(limit == 18 for _, limit in calls)
    assert all(query.startswith("video editing workflow ") for query, _ in calls)
    assert profile["unique_source_url_count"] == 40
    assert profile["substantive_excerpt_count"] == 40
    assert profile["provider_family_count"] == 5
    assert profile["unique_domain_count"] == 8
    assert profile["archetype_count"] == 10
    assert result["research_expansion"]["requirements_met"] is True
    assert result["research_expansion"]["truthful"] is True


def test_longform_research_fetches_publisher_sources_for_url_shortfall_after_domain_gate_passes(monkeypatch):
    """Domain coverage must not suppress a real source-count top-up."""
    archetype_evidence = (
        "overview fundamentals context historical timeline origin evolution "
        "mechanism process methodology workflow case studies implementation "
        "peer-reviewed research experiments datasets quantitative measurement "
        "benchmarks applications practice adoption impacts outcomes limitations "
        "risks challenges failures safety debates critiques alternatives"
    )
    substantive_summary = (
        "This independently retrieved source documents " + archetype_evidence
        + " for evidence-based production. It includes scope, methods, examples, "
        "observations, supporting material, uncertainty, practical implications, "
        "and limitations for source-aware writing. "
    ) * 5
    initial_sources = [
        {
            "title": f"Independent research record {index + 1}: {archetype_evidence}",
            "url": f"https://research-{index % 8}.example.org/record/{index + 1}",
            "summary": substantive_summary,
            "source": f"provider-{index % 5}",
            "retrieved_at": "2026-11-07T00:00:00Z",
        }
        for index in range(39)
    ]
    initial_profile = studio_ultimate._research_evidence_profile(initial_sources)
    assert initial_profile["unique_source_url_count"] == 39
    assert initial_profile["substantive_excerpt_count"] == 39
    assert initial_profile["provider_family_count"] == 5
    assert initial_profile["unique_domain_count"] == 8
    assert initial_profile["archetype_count"] == 10

    facet_calls = []
    def empty_facet_search(query, limit=10):
        facet_calls.append((query, limit))
        return {"sources": [], "source_count": 0, "provider_count": 0, "status": "DEGRADED"}

    catalogue_calls = []
    def retrieved_publisher_sources(topic):
        catalogue_calls.append(topic)
        return [{
            "title": "W3C WAI: Audio and Video Media Accessibility Guidance",
            "url": "https://www.w3.org/WAI/media/av/",
            "summary": substantive_summary,
            "source": "official_media_reference_catalog",
            "source_publisher": "W3C WAI",
            "content_type": "retrieved_official_documentation",
            "retrieved_at": "2026-11-07T00:00:00Z",
        }]

    monkeypatch.setattr(studio_ultimate, "research_topic", empty_facet_search)
    monkeypatch.setattr(studio_ultimate, "_longform_media_reference_sources", retrieved_publisher_sources)

    topic = "video editing workflow content creation captions subtitles accessibility creator software production"
    result = studio_ultimate._expand_longform_research_evidence(
        {"topic": topic, "sources": initial_sources, "source_count": 39},
        topic,
        limit=25,
    )

    profile = result["evidence_profile"]
    assert len(facet_calls) == 4
    assert all(limit == 18 for _, limit in facet_calls)
    assert catalogue_calls == [topic]
    assert profile["unique_source_url_count"] == 40
    assert profile["substantive_excerpt_count"] == 40
    assert profile["provider_family_count"] == 6
    assert profile["unique_domain_count"] == 9
    assert profile["archetype_count"] == 10
    assert result["research_expansion"]["requirements_met"] is True
    assert result["research_expansion"]["additional_unique_source_urls"] == 1
    assert any(row.get("source") == "official_media_reference_catalog" for row in result["sources"])
