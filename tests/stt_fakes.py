from assist_live_server.turn.detector import TurnDetector


class FirstBlockCommandDetector(TurnDetector):
    def __init__(self):
        super().__init__(vad_probability=lambda _: 0, turn_probability=lambda _: 0)

    def push(self, pcm, partial):
        self.samples += len(pcm) // 2
        if pcm and self.speech_started_at_ms is None:
            self.speech_started_at_ms = 0.0
        return []

    def flush(self, partial):
        return []
