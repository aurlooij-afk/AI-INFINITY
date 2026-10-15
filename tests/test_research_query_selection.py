"""Regression tests for compact, topic-led research queries."""


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

