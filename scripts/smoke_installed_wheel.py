"""Smoke the UI from an installed wheel, outside the source checkout."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from app.main import app


def main() -> None:
    with TestClient(app) as client:
        readiness = client.get("/ready")
        assert readiness.status_code == 200
        assert readiness.json()["status"] == "ready"
        index = client.get("/ui/")
        assert index.status_code == 200
        assert '<meta name="aigc-lite-ui" content="react-v0.4"' in index.text
        match = re.search(r'src="(/ui/assets/[^"]+\.js)"', index.text)
        assert match is not None
        asset = client.get(match.group(1))
        assert asset.status_code == 200
        assert len(asset.content) > 100_000


if __name__ == "__main__":
    main()
