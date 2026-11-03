import asyncio
import re
from pathlib import Path
from uuid import uuid7

import pytest
from assist_live_client import TimingEvent, TurnOpen
from assist_live_client.models import FALLBACK_REASONS

from tests.contract.test_event_store import service


@pytest.mark.parametrize("part", ["stt", "tts"])
def test_client_accepts_fallback_unavailable(part):
    data = {"reason": "fallback_unavailable", "part": part}
    event = TimingEvent(
        str(uuid7()),
        "satellite-a",
        "integration",
        "fallback_used",
        1,
        "measured",
        None,
        data=data,
    )
    assert event.data == data, "fallback_unavailable_client_data"


@pytest.mark.parametrize("part", ["stt", "tts"])
@pytest.mark.parametrize("reason", ["fallback_unavailable", "unknown_reason"])
def test_server_fallback_reason_round_trip(tmp_path, part, reason):
    async def run():
        async with service(tmp_path) as (client, session, store, _, _):
            turn = TurnOpen(str(uuid7()), "satellite-a", "voice", None, None)
            await client.open_turn(turn)
            payload = {
                "turn_id": turn.turn_id,
                "satellite": turn.satellite,
                "source": "integration",
                "stage": "fallback_used",
                "t_utc_ns": 1,
                "quality": "measured",
                "uncertainty_ms": None,
                "data": {"reason": reason, "part": part},
            }
            async with session.post(
                client.url + "/v1/event", headers=client._headers, json=payload
            ) as response:
                body = await response.text()
                if reason == "unknown_reason":
                    assert (response.status, body) == (400, "invalid request")
                else:
                    assert response.status == 200, (
                        "fallback_unavailable_server_accepted"
                    )
            rows = (await client.turn(turn.turn_id))["events"]
            if reason == "unknown_reason":
                assert rows == [], "unknown_fallback_reason_not_stored"
            else:
                assert rows[0]["data"] == payload["data"], (
                    "fallback_unavailable_returned"
                )
                assert (await store.get_turn(turn.turn_id))["events"] == rows

    asyncio.run(run())


def test_protocol_fallback_reason_count_and_lists():
    text = (Path(__file__).resolve().parents[2] / "docs/protocol.md").read_text()
    match = re.search(
        r"The (\d+) fallback `reason` values are (.*?)\.\nThe other values are (.*?)\.",
        text,
    )
    assert match is not None, "fallback_reason_documentation_found"
    values = re.findall(r"`([^`]+)`", match[2] + match[3])
    assert int(match[1]) == len(values) == len(FALLBACK_REASONS) == 14, (
        "fallback_reason_documented_count"
    )
    assert len(set(values)) == len(values), "fallback_reason_documented_duplicates"
    assert set(values) == FALLBACK_REASONS, "fallback_reason_documented_values"
