"""Infrared platform for LinknLink remotes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from linknlink.exceptions import LinknLinkException

from homeassistant.components.infrared import InfraredCommand, InfraredEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import LinknLinkEntity

if TYPE_CHECKING:
    from .coordinator import LinknLinkCoordinator

PARALLEL_UPDATES = 1

_TICK_US = 32.84  # microseconds per hardware tick


def _timings_to_packet(timings: list[int]) -> bytes:
    """Convert signed microsecond timings to a LinknLink IR packet.

    Positive values are pulse (high) durations; negative are space (low).
    Uses the same 32.84 µs tick resolution as Broadlink-compatible devices.
    """
    if not timings:
        raise ValueError("IR timings cannot be empty")

    result = bytearray(4)
    result[0] = 0x26

    for timing in timings:
        ticks = max(1, int(abs(timing) // _TICK_US))
        if ticks > 0xFFFF:
            raise ValueError(f"IR timing out of range: {timing}us")
        div, mod = divmod(ticks, 256)
        if div:
            result.append(0)
            result.append(div)
        result.append(mod)

    data_len = len(result) - 4
    result[2] = data_len & 0xFF
    result[3] = data_len >> 8

    return bytes(result)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up LinknLink infrared entity."""
    coordinator: LinknLinkCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities([LinknLinkInfraredEntity(coordinator)])


class LinknLinkInfraredEntity(LinknLinkEntity, InfraredEntity):
    """LinknLink infrared transmitter entity."""

    _attr_has_entity_name = True
    _attr_translation_key = "infrared_emitter"

    def __init__(self, coordinator: LinknLinkCoordinator) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.api.mac.hex()}-emitter"

    async def async_send_command(self, command: InfraredCommand) -> None:
        """Send an IR command via the LinknLink device."""
        try:
            packet = _timings_to_packet(command.get_raw_timings())
            await self.coordinator.async_request(
                self.coordinator.api.send_data, packet
            )
        except (LinknLinkException, OSError, ValueError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="send_command_failed",
                translation_placeholders={"error": str(err)},
            ) from err
