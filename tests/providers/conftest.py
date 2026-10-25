import struct

import pytest


@pytest.fixture
def first_word_clip():
    return [struct.pack("<h", value) * 6400 for value in (100, 200, 300)]


@pytest.fixture
def local_stt():
    from assist_live_server.providers.parakeet import ParakeetSession

    def decode(pcm):
        values = struct.unpack(f"<{len(pcm) // 2}h", pcm)
        return " ".join(
            {100: "turn", 200: "on", 300: "lights"}[v] for v in values[::6400]
        )

    return ParakeetSession(decode)
