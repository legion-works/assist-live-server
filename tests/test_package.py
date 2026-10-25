def test_client_is_separate():
    from pathlib import Path

    import assist_live_client

    root = Path(__file__).resolve().parents[1]

    assert assist_live_client is not None
    assert (root / "server/pyproject.toml").exists()
    assert (root / "client/pyproject.toml").exists()
    assert "homeassistant_api" not in (root / "ha-app/repository.yaml").read_text()


def test_client_dependencies_are_compatible_ranges():
    import tomllib
    from pathlib import Path

    from packaging.requirements import Requirement

    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "client/pyproject.toml").read_text())["project"]
    requirements = [Requirement(value) for value in project["dependencies"]]

    assert all(
        specifier.operator not in {"==", "==="}
        for requirement in requirements
        for specifier in requirement.specifier
    )
    aiohttp = next(
        requirement for requirement in requirements if requirement.name == "aiohttp"
    )
    assert aiohttp.specifier.contains("3.14.4")
    assert aiohttp.specifier.contains("3.14.5")
