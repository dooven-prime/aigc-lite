from pathlib import Path

from aigc_lite_ros2 import __version__ as ros2_bridge_version

from app import __version__
from app.main import app


def test_container_listens_on_all_interfaces() -> None:
    dockerfile = Path(__file__).resolve().parents[1] / "Dockerfile"

    assert "ENV AIGC_LITE_HOST=0.0.0.0" in dockerfile.read_text(encoding="utf-8")


def test_release_version_surfaces_are_aligned() -> None:
    root = Path(__file__).resolve().parents[1]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    package_json = (root / "frontend" / "package.json").read_text(encoding="utf-8")
    package_lock = (root / "frontend" / "package-lock.json").read_text(encoding="utf-8")
    ros2_pyproject = (root / "extensions" / "ros2-bridge" / "pyproject.toml").read_text(
        encoding="utf-8"
    )

    assert __version__ == "0.6.0"
    assert app.version == __version__
    assert 'version = "0.6.0"' in pyproject
    assert '"version": "0.6.0"' in package_json
    assert '"version": "0.6.0"' in package_lock
    assert ros2_bridge_version == __version__
    assert 'version = "0.6.0"' in ros2_pyproject
