import pytest
from assist_live_server.providers.stt import provider_factory, provider_names


def test_no_streaming_default_or_rejected_adapter():
    assert provider_names(mode="local_streaming") == ()
    for name in (None, "sherpa-zipformer", "fastconformer"):
        with pytest.raises(ValueError, match="no local streaming"):
            provider_factory(name, mode="local_streaming")


def test_compatible_default_and_unknown_provider():
    from assist_live_server.providers.parakeet import ParakeetProvider

    assert provider_names(mode="local_compatible") == ("parakeet",)
    assert provider_factory(mode="local_compatible") == ParakeetProvider.load
    with pytest.raises(ValueError, match="unknown STT provider"):
        provider_factory("unknown")
