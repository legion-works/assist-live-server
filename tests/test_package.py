def test_client_is_separate():
    from pathlib import Path

    import assist_live_client

    root = Path(__file__).resolve().parents[1]

    assert assist_live_client is not None
    assert (root / "server/pyproject.toml").exists()
    assert (root / "client/pyproject.toml").exists()
    assert "homeassistant_api" not in (root / "ha-app/repository.yaml").read_text()
