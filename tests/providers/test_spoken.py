import asyncio
import base64
import sys
from types import SimpleNamespace
from uuid import uuid7

import pytest
from aiohttp import web
from assist_live_client.models import TTSFormat, TTSSentenceEnd
from assist_live_client.protocol import encode_control
from assist_live_server.providers.elevenlabs import ElevenLabsProvider
from assist_live_server.providers.piper import PiperProvider
from assist_live_server.providers.tts import SentenceAssembler
from assist_live_server.tts_gateway import TTSGateway


class Voice:
    config = SimpleNamespace(sample_rate=22050)

    def __init__(self):
        self.calls = []

    def synthesize(self, text):
        self.calls.append(text)
        yield SimpleNamespace(audio_int16_bytes=b"\x01\0")


async def parts(*values):
    for value in values:
        yield value


def speak(*chunks):
    async def run():
        voice = Voice()
        items = [
            item
            async for item in PiperProvider(voice).stream(
                parts(*chunks), "en_US-lessac-medium"
            )
        ]
        return voice.calls, [
            item.text_end for item in items if isinstance(item, TTSSentenceEnd)
        ]

    return asyncio.run(run())


@pytest.mark.parametrize(
    "raw,spoken",
    [
        ("# Heading\nTail.", ["Heading", "Tail."]),
        ("> Quote.", ["Quote."]),
        (
            "- First\n* Second\n+ Third\n1. Fourth\n1) Fifth",
            ["First", "Second", "Third", "Fourth", "Fifth"],
        ),
        ("---\n***\n___\nWords.", ["Words."]),
        ("```python\nprint()\n```\nDone.", ["print()\n\nDone."]),
        (
            "**Bold** *soft* __wide__ _low_ ~~old~~ `code`.",
            ["Bold soft wide low old code."],
        ),
        ("[Read](https://example.org/a.b) ![Image](image.png).", ["Read Image."]),
        ("<b>Read</b> <span>this</span>.", ["Read this."]),
        ('<span title="a>b">Read</span>.', ["Read."]),
        (
            '<span title="' + "x" * 2048 + '">Read</span>.',
            ['<span title="' + "x" * 2048 + '">Read.'],
        ),
        ("**cafe\u0301**.", ["cafe\u0301."]),
        ("[Read](https://example.org/\nmap).", ["Read."]),
        ("# Heading\r\nTail.", ["Heading", "Tail."]),
        ("x < 5 and y > 3", ["x < 5 and y > 3"]),
        ("Read\n--- | :---:\nmore.", ["Read\n\nmore."]),
        ("| A | B |\n| --- | :---: |\n| C | D |", ["A , B \n\nC , D"]),
        (
            "2 * 3; snake_case; a_b_c; 5 - 3; mid # mark; C++; "
            "https://example.org/a_b; 2 < 3 > 1",
            [
                "2 * 3; snake_case; a_b_c; 5 - 3; mid # mark; C++; "
                "https://example.org/a_b; 2 < 3 > 1"
            ],
        ),
    ],
    ids=[
        "heading",
        "quote",
        "lists",
        "rules",
        "fences",
        "emphasis",
        "links",
        "html",
        "html-attribute",
        "long-html-tag",
        "combining-word",
        "multiline-link",
        "crlf-heading",
        "comparison-signs",
        "table-without-edge",
        "tables",
        "negative",
    ],
)
def test_spoken_rule(raw, spoken):
    calls, ends = speak(*raw)
    assert calls == spoken, "spoken rule output"
    assert ends[-1] == len(raw), "request endpoint"


@pytest.mark.parametrize("mark", ["-", "*", "+", "1.", "1)"])
@pytest.mark.parametrize("ending", ["\n", "  \n", "\r\n", " \t\r\n", ""])
@pytest.mark.parametrize("streamed", [False, True])
def test_empty_list_lines_never_reach_tts_and_keep_request_positions(
    mark, ending, streamed
):
    line = mark + ending
    chunks = list(line) if streamed else [line]
    assert speak(*chunks) == ([], []), "empty list line must not reach TTS"
    if ending:
        raw = "First.\n" + line + "Next."
        expected = (["First.", "Next."], [6, len(raw)])
    else:
        raw = "First\n" + line
        expected = (["First"], [len(raw)])
    chunks = list(raw) if streamed else [raw]
    assert speak(*chunks) == expected, "empty list marks removed at request positions"


@pytest.mark.parametrize("digits", ["1990", "5"])
@pytest.mark.parametrize("ending", ["", "\n", "\r\n", "  \r\n"])
def test_digit_only_line_without_list_suffix_stays_spoken(digits, ending):
    raw = digits + ending
    assert speak(*raw) == ([digits], [len(raw)]), "bare digits are not list marks"


STORY = (
    "**The copper bird**.\n# Dawn\n- It woke\n1. It flew\n"
    "Read [the map](https://example.org/map). Use `north`.\n"
    "```text\nFly.\n```\n| Sky | Sea |\n| --- | --- |\n"
)


def test_story_real_piper_path_keeps_request_endpoints():
    calls, ends = speak(STORY)
    assert calls == [
        "The copper bird.",
        "Dawn",
        "It woke",
        "It flew",
        "Read the map.",
        "Use north.",
        "Fly.",
        "Sky , Sea",
    ], "story provider text"
    assert ends == [20, 27, 37, 48, 89, 102, 115, len(STORY)], "story request endpoints"


def test_stream_splits_do_not_change_text_or_positions():
    sample = "**Bird**.\n# Dawn\n- [the map](https://example.org/a.b) `north`. Tail."
    expected = speak(sample)
    assert expected[0] == ["Bird.", "Dawn", "the map north.", "Tail."], (
        "split sample text"
    )
    for split in range(len(sample) + 1):
        assert speak(sample[:split], sample[split:]) == expected, f"split {split}"
    assert speak(*sample) == expected, "character stream"


@pytest.mark.parametrize("length", [2047, 2048, 2049])
def test_link_hold_limit(length):
    raw = "[label](" + "a" * (length - 9) + ")"
    assert len(raw) == length
    calls, _ = speak(*raw)
    assert calls == (["label"] if length <= 2048 else [raw]), "bounded link hold"


def test_link_hold_releases_before_input_end():
    async def run():
        assembler = SentenceAssembler()
        held = "[label](" + "a" * 2040
        assembler.feed_positions(held)
        assert held in "".join(assembler.buffer), "link released at hold limit"

    asyncio.run(run())


@pytest.mark.parametrize("opening", ["<", "<unfinished"])
def test_unclosed_tag_in_large_reply_does_not_delay_first_audio(opening):
    async def run():
        released = asyncio.Event()
        paused = asyncio.Event()
        received = 0
        raw = ("Start " + opening + " word. " * 15000)[:100000]
        assert len(raw) == 100000

        async def text():
            nonlocal received
            for offset in range(0, len(raw), 128):
                if offset >= 4096:
                    paused.set()
                    await released.wait()
                part = raw[offset : offset + 128]
                received += len(part)
                yield part

        voice = Voice()
        stream = PiperProvider(voice).synthesize(text(), "en_US-lessac-medium")
        audio = asyncio.create_task(anext(stream))
        waiting = asyncio.create_task(paused.wait())
        try:
            done, _ = await asyncio.wait(
                [audio, waiting], timeout=5, return_when=asyncio.FIRST_COMPLETED
            )
            assert audio in done, "bounded tag hold permits early audio"
            assert await audio == b"\x01\0"
            assert received < 100000, "first audio before reply end"
            assert voice.calls[0] == "Start " + opening + " word.", (
                "unclosed tag stays unchanged"
            )
        finally:
            released.set()
            for task in (audio, waiting):
                task.cancel()
            await asyncio.gather(audio, waiting, return_exceptions=True)
            await stream.aclose()

    asyncio.run(run())


def test_marks_only_skipped_and_next_marker_covers_request():
    raw = "***. Next."
    assert speak(*raw) == (["Next."], [10]), "marks-only request coverage"


def test_emoji_only_sentence_is_skipped_with_request_coverage():
    assert speak("🙂. Hi 👋.") == (["Hi 👋."], [8]), "emoji-only request coverage"


def test_crlf_heading_endpoint_excludes_line_ending():
    assert speak(*"# Heading\r\nTail.") == (["Heading", "Tail."], [9, 16]), (
        "CRLF request endpoints"
    )


@pytest.mark.parametrize("mode", ["links", "line", "marks"])
def test_normalizer_work_is_linear(mode):
    from assist_live_server.spoken import SpokenText

    def operations(size):
        count = 0

        def trace(frame, event, arg):
            nonlocal count
            if event == "line" and frame.f_code.co_filename.endswith("/spoken.py"):
                count += 1
            return trace

        text = "[x](" + "a" * 2100 + " **word**.\n| --- | --- |\n"
        text = (text * (size // len(text) + 1))[:size]
        if mode == "line":
            text = "_" * size
        elif mode == "marks":
            text = "x" + "*" * size + "x"
        normalizer = SpokenText()
        previous = sys.gettrace()
        try:
            sys.settrace(trace)
            for char in text:
                list(normalizer.feed(char))
            list(normalizer.finish())
        finally:
            sys.settrace(previous)
        return count

    small, large = operations(8192), operations(16384)
    assert small < large <= small * 2.2, "linear normalizer operation count"


def test_marks_only_reply_keeps_no_audio_fallback():
    async def run():
        voice = Voice()
        gateway = TTSGateway(lambda _: PiperProvider(voice))
        output = [
            item
            async for item in gateway.stream(
                str(uuid7()), parts("***"), "en_US-lessac-medium"
            )
        ]
        assert voice.calls == [], "marks-only provider suppression"
        assert output[-1]["type"] == "error", "no-audio fallback"
        assert output[-1]["code"] == "tts_failed"

    asyncio.run(run())


def test_gateway_sentence_events_use_request_offsets():
    async def run():
        voice = Voice()
        gateway = TTSGateway(lambda _: PiperProvider(voice))
        events = []

        async def record(kind, value):
            events.append((kind, value))

        items = [
            item
            async for item in gateway.stream(
                str(uuid7()),
                parts(*STORY),
                "en_US-lessac-medium",
                text_positions=True,
                record=record,
            )
        ]
        expected = [20, 27, 37, 48, 89, 102, 115, 148]
        assert [
            item["text_end"]
            for item in items
            if isinstance(item, dict) and item["type"] == "sentence_end"
        ] == expected, "wire request offsets"
        notes = [value for kind, value in events if kind == "tts_sentence"]
        assert [note["text_end"] for note in notes] == expected, (
            "timing request offsets"
        )
        assert [note["index"] for note in notes] == list(range(8)), (
            "timing sentence order"
        )
        assert all(note["text_ms"] <= note["first_byte_ms"] for note in notes), (
            "sentence text readiness"
        )

    asyncio.run(run())


@pytest.mark.parametrize("marker", [0, 1, None])
def test_legacy_backend_gets_spoken_text_and_keeps_marker_validation(marker):
    async def run():
        received = []

        class Backend:
            audio_format = TTSFormat("pcm_s16le", 16000, 1, 2)

            async def tts(self, turn_id, text, voice):
                received.extend([part async for part in text])
                yield b"\x01\0"
                if marker is not None:
                    yield encode_control("sentence_end", turn_id, sentence=marker)

        gateway = TTSGateway(backend=Backend())
        output = [
            item
            async for item in gateway.stream(
                str(uuid7()), parts(*"# **Bird**\n[Map](https://example.org)."), ""
            )
        ]
        assert "".join(received).strip() == "Bird\nMap.", "legacy spoken text"
        controls = [item for item in output if isinstance(item, dict)]
        if marker == 0:
            assert controls[-1]["type"] == "sentence_end", "legacy valid marker"
            assert controls[-1]["sentence"] == 0
            assert "text_end" not in controls[-1]
        else:
            assert controls[-1]["type"] == "error", "legacy invalid marker"
            assert controls[-1]["code"] == "tts_failed"

    asyncio.run(run())


@pytest.mark.parametrize("split", [False, True])
def test_cloud_alignment_matches_changed_sentence_and_maps_request(
    cloud_ws_server, split
):
    async def run():
        received = []

        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for message in ws:
                value = message.json()
                if "generation_config" not in value and not value.get("flush"):
                    received.append(value["text"])
                if value == {"text": ""}:
                    alignment = "Bird. Dawn the map north. Tail."
                    for frame in list(alignment) if split else [alignment]:
                        await ws.send_json(
                            {
                                "audio": base64.b64encode(b"\x01\0").decode(),
                                "alignment": {
                                    "chars": list(frame),
                                    "charStartTimesMs": [0] * len(frame),
                                    "charDurationsMs": [1] * len(frame),
                                },
                            }
                        )
                    await ws.send_json({"isFinal": True})
                    await ws.close()
            return ws

        raw = (
            "***. **Bird**.\n# Dawn\n"
            "- [the map](https://example.org/a.b) `north`. Tail."
        )
        async with cloud_ws_server(handler) as fake:
            provider = ElevenLabsProvider("cloud-test-credential", endpoint=fake.url)
            items = [item async for item in provider.stream(parts(*raw), "test-voice")]
        assert "".join(received).strip() == "Bird.\n Dawn\nthe map north. Tail.", (
            "cloud changed text"
        )
        assert [
            item.text_end for item in items if isinstance(item, TTSSentenceEnd)
        ] == [14, 21, 67, 73], "cloud request offsets"

    asyncio.run(run())
