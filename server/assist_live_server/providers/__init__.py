"""Local speech recognition interfaces."""

from .stt import STTSession, provider_factory, provider_names

__all__ = ["STTSession", "provider_factory", "provider_names"]
