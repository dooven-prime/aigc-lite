from importlib.resources import files

from fastapi.testclient import TestClient

from app import main


def test_packaged_ui_is_current_react_build() -> None:
    static = files("app").joinpath("static")
    index = static.joinpath("index.html").read_text(encoding="utf-8")
    assert '<meta name="aigc-lite-ui" content="react-v0.4"' in index
    assert "type=\"module\"" in index
    assert "assets/" in index

    with TestClient(main.app) as client:
        response = client.get("/ui/")
        assert response.status_code == 200
        assert "react-v0.4" in response.text
