"""Tests for the linknlink infrared platform."""
from unittest.mock import AsyncMock, MagicMock, patch

from linknlink.exceptions import LinknLinkException
import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.linknlink.infrared import (
    LinknLinkInfraredEntity,
    _timings_to_packet,
)


# ---------------------------------------------------------------------------
# _timings_to_packet unit tests
# ---------------------------------------------------------------------------


class TestTimingsToPacket:
    """Tests for the _timings_to_packet encoder."""

    def test_header_byte_is_0x26(self):
        """Packet must start with 0x26 (IR magic byte)."""
        packet = _timings_to_packet([500])
        assert packet[0] == 0x26

    def test_data_len_field(self):
        """Bytes 2–3 must hold the little-endian length of encoded payload."""
        # One timing < 256 ticks encodes as a single byte.
        packet = _timings_to_packet([500])
        data_len = packet[2] | (packet[3] << 8)
        assert data_len == len(packet) - 4

    def test_single_small_timing_encodes_as_one_byte(self):
        """A timing that maps to < 256 ticks should produce a single payload byte."""
        # 500 µs / 32.84 ≈ 15 ticks → 1 byte
        packet = _timings_to_packet([500])
        assert len(packet) == 5  # 4-byte header + 1 payload byte

    def test_negative_timing_treated_as_positive(self):
        """Negative (space) timings must encode identically to their absolute value."""
        assert _timings_to_packet([500]) == _timings_to_packet([-500])

    def test_large_timing_encodes_as_three_bytes(self):
        """A timing mapping to ≥ 256 ticks must produce a 3-byte encoding (0x00, div, mod)."""
        # 9000 µs / 32.84 ≈ 273 ticks → 3-byte encoding
        packet = _timings_to_packet([9000])
        assert len(packet) == 7  # 4-byte header + 3 payload bytes
        assert packet[4] == 0x00  # leading zero signals wide encoding

    def test_multiple_timings_concatenated(self):
        """Multiple timings should each be encoded and concatenated."""
        single_a = _timings_to_packet([500])
        single_b = _timings_to_packet([500])
        combined = _timings_to_packet([500, 500])
        # Header 4 bytes; each 500µs → 1 byte, so combined payload is 2 bytes
        assert len(combined) == 4 + (len(single_a) - 4) + (len(single_b) - 4)

    def test_sub_tick_timing_clamped_to_one(self):
        """A timing shorter than one tick (< 32.84 µs) must encode as 1 tick, not 0."""
        packet = _timings_to_packet([10])  # 10µs → 0 ticks before clamp → clamped to 1
        assert packet[4] == 1

    def test_zero_timing_clamped_to_one(self):
        """Zero-duration timing must encode as 1 tick."""
        packet = _timings_to_packet([0])
        assert packet[4] == 1

    def test_empty_timings_raises(self):
        """An empty timing list must raise ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            _timings_to_packet([])

    def test_oversized_timing_raises(self):
        """A timing mapping to > 0xFFFF ticks must raise ValueError."""
        # 0xFFFF ticks * 32.84 µs ≈ 2_152_698 µs; use something well above that
        with pytest.raises(ValueError, match="out of range"):
            _timings_to_packet([3_000_000])

    def test_exactly_256_ticks_encodes_as_three_bytes(self):
        """A timing that maps to exactly 256 ticks should use the wide encoding."""
        # 256 * 32.84 = 8407.04 µs; use 8500 to reliably hit ≥ 256 ticks
        timing = 8500
        packet = _timings_to_packet([timing])
        assert packet[4] == 0x00, "Wide encoding must begin with 0x00"

    def test_nec_agc_burst_encodes_correctly(self):
        """NEC AGC burst (9000 µs pulse, 4500 µs space) encodes at correct tick counts."""
        # 9000 µs / 32.84 ≈ 273 ticks → div=1, mod=17 → [0x00, 0x01, 0x11]
        # 4500 µs / 32.84 ≈ 137 ticks → single byte [0x89]
        packet = _timings_to_packet([9000, -4500])
        payload = packet[4:]
        # First encoding: 0x00 prefix means 3 bytes
        assert payload[0] == 0x00
        reconstructed_ticks = payload[1] * 256 + payload[2]
        assert reconstructed_ticks == int(9000 // 32.84)
        # Second encoding: single byte
        assert payload[3] == int(4500 // 32.84)

    def test_maximum_valid_timing(self):
        """0xFFFF ticks must encode without raising."""
        max_us = int(0xFFFF * 32.84)
        packet = _timings_to_packet([max_us])
        assert packet[4] == 0x00  # wide encoding
        reconstructed = packet[5] * 256 + packet[6]
        assert reconstructed == 0xFF  # 0xFFFF // 256 = 255

    def test_byte_1_is_zero(self):
        """Byte 1 (repeat count) is always 0x00."""
        packet = _timings_to_packet([500])
        assert packet[1] == 0x00


# ---------------------------------------------------------------------------
# LinknLinkInfraredEntity unit tests
# ---------------------------------------------------------------------------


def _make_coordinator(mac_hex: str = "aabbccddeeff") -> MagicMock:
    """Return a mock coordinator that mimics LinknLinkCoordinator."""
    coordinator = MagicMock()
    coordinator.api.mac = bytes.fromhex(mac_hex)
    coordinator.api.send_data = MagicMock()
    coordinator.async_request = AsyncMock()
    return coordinator


def _make_entity(mac_hex: str = "aabbccddeeff") -> LinknLinkInfraredEntity:
    """Construct a LinknLinkInfraredEntity backed by a mock coordinator."""
    coordinator = _make_coordinator(mac_hex)
    # Bypass __init_subclass__ / entity framework by calling __new__ + manual init
    entity = LinknLinkInfraredEntity.__new__(LinknLinkInfraredEntity)
    # Directly set the attributes LinknLinkEntity.__init__ would set
    entity.coordinator = coordinator
    entity.api = coordinator.api
    entity._attr_device_info = MagicMock()
    entity._attr_unique_id = f"{mac_hex}-emitter"
    entity._attr_translation_key = "infrared_emitter"
    return entity


def _make_command(timings: list[int]) -> MagicMock:
    """Return a mock InfraredCommand with fixed timings."""
    cmd = MagicMock()
    cmd.get_raw_timings.return_value = timings
    return cmd


class TestLinknLinkInfraredEntity:
    """Tests for LinknLinkInfraredEntity."""

    def test_unique_id(self):
        """unique_id must be '<mac>-emitter'."""
        entity = _make_entity("aabbccddeeff")
        assert entity._attr_unique_id == "aabbccddeeff-emitter"

    def test_translation_key(self):
        """translation_key must be 'infrared_emitter'."""
        entity = _make_entity()
        assert entity._attr_translation_key == "infrared_emitter"

    @pytest.mark.asyncio
    async def test_send_command_calls_send_data(self):
        """async_send_command must encode timings and call coordinator.async_request."""
        entity = _make_entity()
        timings = [9000, -4500, 562, -562]
        cmd = _make_command(timings)

        await entity.async_send_command(cmd)

        entity.coordinator.async_request.assert_awaited_once()
        call_args = entity.coordinator.async_request.call_args
        # First positional arg is the api method
        assert call_args[0][0] is entity.coordinator.api.send_data
        # Second is the bytes packet
        packet = call_args[0][1]
        assert isinstance(packet, bytes)
        assert packet[0] == 0x26

    @pytest.mark.asyncio
    async def test_send_command_raises_on_linknlink_exception(self):
        """LinknLinkException from send_data must surface as HomeAssistantError."""
        entity = _make_entity()
        entity.coordinator.async_request.side_effect = LinknLinkException("boom")

        with pytest.raises(HomeAssistantError):
            await entity.async_send_command(_make_command([500, -500]))

    @pytest.mark.asyncio
    async def test_send_command_raises_on_os_error(self):
        """OSError from send_data must surface as HomeAssistantError."""
        entity = _make_entity()
        entity.coordinator.async_request.side_effect = OSError("network gone")

        with pytest.raises(HomeAssistantError):
            await entity.async_send_command(_make_command([500, -500]))

    @pytest.mark.asyncio
    async def test_send_command_raises_on_empty_timings(self):
        """An empty timing list must surface as HomeAssistantError."""
        entity = _make_entity()

        with pytest.raises(HomeAssistantError):
            await entity.async_send_command(_make_command([]))

    @pytest.mark.asyncio
    async def test_send_command_raises_on_oversized_timing(self):
        """A timing too large for the packet format must surface as HomeAssistantError."""
        entity = _make_entity()

        with pytest.raises(HomeAssistantError):
            await entity.async_send_command(_make_command([3_000_000]))

    @pytest.mark.asyncio
    async def test_send_command_packet_content(self):
        """The packet passed to send_data must match the expected encoding."""
        entity = _make_entity()
        timings = [500, -500]
        await entity.async_send_command(_make_command(timings))

        expected = _timings_to_packet(timings)
        actual = entity.coordinator.async_request.call_args[0][1]
        assert actual == expected
