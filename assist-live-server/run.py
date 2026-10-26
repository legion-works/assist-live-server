import json
import os
from pathlib import Path

OPTIONS = {
    "stt_provider": "ASSIST_LIVE_STT_PROVIDER",
    "tts_provider": "ASSIST_LIVE_TTS_PROVIDER",
    "local_only": "ASSIST_LIVE_LOCAL_ONLY",
    "record_transcripts": "ASSIST_LIVE_RECORD_TRANSCRIPTS",
    "speaker_url": "ASSIST_LIVE_SPEAKER_URL",
    "download_models": "ASSIST_LIVE_DOWNLOAD_MODELS",
    "deepgram_api_key": "DEEPGRAM_API_KEY",
    "elevenlabs_api_key": "ELEVENLABS_API_KEY",
}


def main(path: Path = Path("/data/options.json")) -> None:
    options = json.loads(path.read_text())
    environment = dict(os.environ)
    for name, variable in OPTIONS.items():
        if name in options:
            value = options[name]
            environment[variable] = (
                str(value).lower() if isinstance(value, bool) else str(value)
            )
    os.execvpe("assist-live-server", ["assist-live-server"], environment)


if __name__ == "__main__":
    main()
