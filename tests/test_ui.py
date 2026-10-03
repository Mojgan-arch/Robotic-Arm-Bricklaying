import pytest
from masonmind.ui import prepare_design, build_design


def test_ui_requires_reviewed_design():
    with pytest.raises(ValueError, match="review"):
        build_design(None, "clean", False)
    with pytest.raises(ValueError, match="Describe"):
        prepare_design("  ", "B3", 7)


def test_optional_ui_constructs_without_server():
    pytest.importorskip("gradio")
    from masonmind.ui import create_app
    app = create_app()
    assert app.title == "MasonMind"
    assert app.analytics_enabled is False


def test_ui_serves_local_review_page():
    gr = pytest.importorskip("gradio")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from masonmind.ui import create_app
    app = gr.mount_gradio_app(FastAPI(), create_app(), path="/")
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "MasonMind" in response.text
