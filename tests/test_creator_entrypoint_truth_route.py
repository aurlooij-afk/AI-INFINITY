"""Regression tests for the creator dispatcher's professional truth endpoint."""


def test_creator_dispatcher_registers_professional_truth_endpoint():
    import creator_entrypoint

    expected = "/infinity/studio/project/{project_id}/truth"
    paths = {getattr(route, "path", "") for route in creator_entrypoint.creator_app.routes}
    assert expected in paths
