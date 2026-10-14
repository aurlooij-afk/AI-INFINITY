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
