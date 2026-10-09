from importlib.resources import files

from fastapi.testclient import TestClient

from app import main


def test_packaged_ui_is_current_react_build() -> None:
    static = files("app").joinpath("static")
    index = static.joinpath("index.html").read_text(encoding="utf-8")
    assert '<meta name="aigc-lite-ui" content="react-v0.6"' in index
    assert "type=\"module\"" in index
    assert "assets/" in index
    bundles = [
        asset.read_text(encoding="utf-8")
        for asset in static.joinpath("assets").iterdir()
        if asset.name.endswith(".js")
    ]
    assert any("Claim authority flow" in bundle for bundle in bundles)
    assert any("Research Explorer" in bundle for bundle in bundles)
    assert any("Research imports" in bundle for bundle in bundles)

    with TestClient(main.app) as client:
        response = client.get("/ui/")
        assert response.status_code == 200
        assert "react-v0.6" in response.text
