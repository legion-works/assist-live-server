def test_clock_uses_minimum_offset():
    from assist_live_server.clock import ClockWindow

    window = ClockWindow()
    window.observe(1, 1000, 1_010_000_000)
    host_ns, uncertainty_ms = window.observe(1, 1100, 1_130_000_000)
    assert host_ns == 1_110_000_000
    assert uncertainty_ms == 10


def test_clock_event_limit_time_limit_and_boot_reset():
    from assist_live_server.clock import ClockWindow

    window = ClockWindow()
    window.observe(1, 0, 10_000_000)
    for i in range(1, 51):
        stamp, uncertainty = window.observe(1, i * 100, i * 100_000_000 + 30_000_000)
    assert stamp == 5_030_000_000 and uncertainty == 0
    assert window.observe(1, 64_000, 64_050_000_000) == (64_030_000_000, 0)
    assert window.observe(1, 127_000, 127_070_000_000) == (127_070_000_000, 0)
    assert window.observe(2, 1, 128_000_000_000) == (128_000_000_000, 0)


def test_clock_reordered_device_time_does_not_reset_window():
    from assist_live_server.clock import ClockWindow

    window = ClockWindow()
    window.observe(1, 1000, 1_010_000_000)
    assert window.observe(1, 900, 1_030_000_000) == (910_000_000, 60)


def test_clock_large_utc_offset_keeps_nanosecond_precision():
    from assist_live_server.clock import ClockWindow

    window = ClockWindow()
    window.observe(1, 1000, 1_800_000_001_000_000_001)
    assert window.observe(1, 1100, 1_800_000_001_100_000_003) == (
        1_800_000_001_100_000_001,
        0.000001,
    )
