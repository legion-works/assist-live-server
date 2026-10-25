from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def detector():
    from assist_live_server.turn.detector import TurnDetector

    return TurnDetector(
        vad_probability=lambda audio: 0.0, turn_probability=lambda audio: 0.99
    )
