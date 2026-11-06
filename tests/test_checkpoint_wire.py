import asyncio

import numpy as np
import pytest
from assist_live_client.protocol import encode_audio, encode_control
from assist_live_server.audio_gateway import AudioGateway
from assist_live_server.providers.parakeet import ParakeetSession
from assist_live_server.turn.detector import TurnDetector

from tests.contract.test_streaming_stt import connect, service


@pytest.mark.parametrize("end_rule", ["partial", "checkpoint"])
@pytest.mark.parametrize("command", [False, True])
def test_end_rules_keep_wire_and_export_wait_metric(tmp_path, end_rule, command):
    async def run():
        gateway = AudioGateway(
            lambda _: ParakeetSession(lambda _: "complete checkpoint"),
            detector_factory=lambda: TurnDetector(
                vad_probability=lambda a: float(np.any(a)),
                turn_probability=lambda _: 0,
            ),
            end_rule=end_rule,
        )
        async with service(tmp_path, gateway=gateway) as (client, session, turn, _):

            async def audio():
                yield (
                    np.ones(9003, dtype="<i2").tobytes() if command else bytes(18006)
                )

            replies = [reply async for reply in client.stt(turn.turn_id, audio())]
            assert next(r for r in replies if r["type"] == "final") == encode_control(
                "final", turn.turn_id, text="complete checkpoint" if command else ""
            )
            async with session.get(
                "http://service.invalid/metrics",
                headers={"Authorization": "Bearer synthetic-token"},
            ) as response:
                metrics = await response.text()
            metric = "assist_live_end_decision_wait_seconds_count"
            assert f'{metric}{{end_rule="{end_rule}"}} 1' in metrics
            assert "assist_live_checkpoint_deadline_failures_total 0" in metrics
            assert "assist_live_checkpoint_wall_limit_failures_total 0" in metrics

    asyncio.run(asyncio.wait_for(run(), 4))


def test_checkpoint_deadline_uses_existing_failure_wire_and_metric(tmp_path):
    async def run():
        class Session(ParakeetSession):
            async def checkpoint(self, prefix):
                await asyncio.Event().wait()

        gateway = AudioGateway(
            lambda _: Session(lambda _: ""),
            detector_factory=lambda: TurnDetector(
                vad_probability=lambda a: float(np.any(a)),
                turn_probability=lambda _: 1,
            ),
            end_rule="checkpoint",
        )
        async with service(tmp_path, gateway=gateway) as (_, session, turn, store):
            ws = await connect(session, turn)
            await ws.send_bytes(
                encode_audio(turn.turn_id, 0, np.ones(14000, dtype="<i2").tobytes())
            )
            await ws.send_json(encode_control("end", turn.turn_id))
            replies = [message.json() async for message in ws]
            terminal = [r for r in replies if r["type"] != "heartbeat"]
            assert terminal == [
                encode_control("error", turn.turn_id, code="stt_failed"),
                encode_control("end", turn.turn_id),
            ]
            assert not any(
                e["stage"] == "stt_final"
                for e in (await store.get_turn(turn.turn_id))["events"]
            )
            async with session.get(
                "http://service.invalid/metrics",
                headers={"Authorization": "Bearer synthetic-token"},
            ) as response:
                metrics = await response.text()
            assert "assist_live_checkpoint_deadline_failures_total 1" in metrics
            assert (
                'assist_live_end_decision_wait_seconds_count{end_rule="checkpoint"} 0'
                in metrics
            )

    asyncio.run(asyncio.wait_for(run(), 4))
