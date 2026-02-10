"""Test the linknlink config flow."""
import errno
import socket
import sys
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import linknlink as llk
from linknlink.exceptions import (
    AuthenticationError,
    LinknLinkException,
    NetworkTimeoutError,
)
import pytest

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_TIMEOUT, CONF_TYPE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

# Mock the dhcp module if it doesn't exist
try:
    from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
except ImportError:
    # Create a mock module
    from types import ModuleType
    mock_dhcp = ModuleType("dhcp")
    mock_dhcp.DhcpServiceInfo = type("DhcpServiceInfo", (), {})
    sys.modules["homeassistant.helpers.service_info.dhcp"] = mock_dhcp
    sys.modules["homeassistant.helpers.service_info"] = ModuleType("service_info")

from custom_components.linknlink.const import DEFAULT_TIMEOUT, DEVICE_TYPES, DOMAIN


def create_mock_device():
    """Create a mock linknlink device."""
    device = Mock(spec=llk.Device)
    device.type = "EHUB"
    device.devtype = 0x1234
    device.mac = bytes.fromhex("aabbccddeeff")
    device.name = "Test Device"
    device.model = "Test Model"
    device.host = ("192.168.1.100", 80)
    device.timeout = DEFAULT_TIMEOUT
    device.is_locked = False
    device.auth = Mock()
    device.set_lock = Mock()
    return device


@pytest.mark.asyncio
async def test_init(hass) -> None:
    """Test initialization of flow handler."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"


@pytest.mark.asyncio
async def test_async_step_user_no_input(hass) -> None:
    """Test user step with no input shows form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}
    assert CONF_HOST in result["data_schema"].schema
    assert CONF_TIMEOUT in result["data_schema"].schema


@pytest.mark.asyncio
async def test_async_step_user_success(hass) -> None:
    """Test user step with successful device discovery."""
    mock_device = create_mock_device()

    with patch("linknlink.hello", return_value=mock_device):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_HOST: "192.168.1.100", CONF_TIMEOUT: 10},
        )

    # Should redirect to auth step, then finish
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == f"{DOMAIN}-{mock_device.mac.hex()}"
    assert result["data"][CONF_HOST] == "192.168.1.100"
    assert result["data"][CONF_MAC] == mock_device.mac.hex()
    assert result["data"][CONF_TYPE] == mock_device.devtype
    assert result["data"][CONF_TIMEOUT] == 10


@pytest.mark.asyncio
async def test_async_step_user_network_timeout(hass) -> None:
    """Test user step with network timeout error."""
    with patch("linknlink.hello", side_effect=NetworkTimeoutError()):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_HOST: "192.168.1.100"},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "cannot_connect"


@pytest.mark.asyncio
async def test_async_step_user_invalid_host(hass) -> None:
    """Test user step with invalid host error."""
    error = OSError()
    error.errno = errno.EINVAL

    with patch("linknlink.hello", side_effect=error):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_HOST: "invalid_host"},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_host"


@pytest.mark.asyncio
async def test_async_step_user_eai_noname(hass) -> None:
    """Test user step with EAI_NONAME error."""
    error = OSError()
    error.errno = socket.EAI_NONAME

    with patch("linknlink.hello", side_effect=error):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_HOST: "invalid_host"},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_host"


@pytest.mark.asyncio
async def test_async_step_user_network_unreachable(hass) -> None:
    """Test user step with network unreachable error."""
    error = OSError()
    error.errno = errno.ENETUNREACH

    with patch("linknlink.hello", side_effect=error):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_HOST: "192.168.1.100"},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "cannot_connect"


@pytest.mark.asyncio
async def test_async_step_user_unknown_error(hass) -> None:
    """Test user step with unknown OSError."""
    error = OSError("Unknown error")
    error.errno = 999

    with patch("linknlink.hello", side_effect=error):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_HOST: "192.168.1.100"},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "unknown"


@pytest.mark.asyncio
async def test_async_step_user_unsupported_device(hass) -> None:
    """Test user step with unsupported device type."""
    mock_device = create_mock_device()
    mock_device.type = "UNSUPPORTED"

    with patch("linknlink.hello", return_value=mock_device):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_HOST: "192.168.1.100"},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "not_supported"


@pytest.mark.asyncio
async def test_async_step_dhcp_success(hass) -> None:
    """Test DHCP discovery step with successful device setup."""
    mock_device = create_mock_device()

    discovery_info = Mock()
    discovery_info.ip = "192.168.1.100"
    discovery_info.macaddress = "AA:BB:CC:DD:EE:FF"
    discovery_info.hostname = "linknlink_test"

    with patch("linknlink.hello", return_value=mock_device):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_DHCP},
            data=discovery_info,
        )

    # Should redirect to auth step, then finish
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == f"{DOMAIN}-{mock_device.mac.hex()}"
    assert result["data"][CONF_HOST] == "192.168.1.100"


@pytest.mark.asyncio
async def test_async_step_dhcp_timeout(hass) -> None:
    """Test DHCP discovery with timeout."""
    discovery_info = Mock()
    discovery_info.ip = "192.168.1.100"
    discovery_info.macaddress = "AA:BB:CC:DD:EE:FF"
    discovery_info.hostname = "linknlink_test"

    with patch("linknlink.hello", side_effect=NetworkTimeoutError()):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_DHCP},
            data=discovery_info,
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


@pytest.mark.asyncio
async def test_async_step_dhcp_network_unreachable(hass) -> None:
    """Test DHCP discovery with network unreachable."""
    error = OSError()
    error.errno = errno.ENETUNREACH

    discovery_info = Mock()
    discovery_info.ip = "192.168.1.100"
    discovery_info.macaddress = "AA:BB:CC:DD:EE:FF"
    discovery_info.hostname = "linknlink_test"

    with patch("linknlink.hello", side_effect=error):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_DHCP},
            data=discovery_info,
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


@pytest.mark.asyncio
async def test_async_step_dhcp_unknown_error(hass) -> None:
    """Test DHCP discovery with unknown OSError."""
    error = OSError("Unknown error")
    error.errno = 999

    discovery_info = Mock()
    discovery_info.ip = "192.168.1.100"
    discovery_info.macaddress = "AA:BB:CC:DD:EE:FF"
    discovery_info.hostname = "linknlink_test"

    with patch("linknlink.hello", side_effect=error):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_DHCP},
            data=discovery_info,
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "unknown"


@pytest.mark.asyncio
async def test_async_step_dhcp_unsupported_device(hass) -> None:
    """Test DHCP discovery with unsupported device type."""
    mock_device = create_mock_device()
    mock_device.type = "UNSUPPORTED"

    discovery_info = Mock()
    discovery_info.ip = "192.168.1.100"
    discovery_info.macaddress = "AA:BB:CC:DD:EE:FF"
    discovery_info.hostname = "linknlink_test"

    with patch("linknlink.hello", return_value=mock_device):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_DHCP},
            data=discovery_info,
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "not_supported"


@pytest.mark.asyncio
async def test_async_step_auth_authentication_error(hass) -> None:
    """Test authentication step with authentication error."""
    mock_device = create_mock_device()
    mock_device.auth.side_effect = AuthenticationError()

    with patch("linknlink.hello", return_value=mock_device):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_HOST: "192.168.1.100"},
        )

    # Should show reset form
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reset"
    assert result["errors"]["base"] == "invalid_auth"


@pytest.mark.asyncio
async def test_async_step_auth_network_timeout(hass) -> None:
    """Test authentication step with network timeout."""
    mock_device = create_mock_device()
    mock_device.auth.side_effect = NetworkTimeoutError("Timeout")

    with patch("linknlink.hello", return_value=mock_device):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_HOST: "192.168.1.100"},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "auth"
    assert result["errors"]["base"] == "cannot_connect"


@pytest.mark.asyncio
async def test_async_step_auth_linknlink_exception(hass) -> None:
    """Test authentication step with LinknLink exception."""
    mock_device = create_mock_device()
    mock_device.auth.side_effect = LinknLinkException("Error")

    with patch("linknlink.hello", return_value=mock_device):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_HOST: "192.168.1.100"},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "auth"
    assert result["errors"]["base"] == "unknown"


@pytest.mark.asyncio
async def test_async_step_auth_network_unreachable(hass) -> None:
    """Test authentication step with network unreachable."""
    mock_device = create_mock_device()
    error = OSError("Network unreachable")
    error.errno = errno.ENETUNREACH
    mock_device.auth.side_effect = error

    with patch("linknlink.hello", return_value=mock_device):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_HOST: "192.168.1.100"},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "auth"
    assert result["errors"]["base"] == "cannot_connect"


@pytest.mark.asyncio
async def test_async_step_auth_os_error_unknown(hass) -> None:
    """Test authentication step with unknown OSError."""
    mock_device = create_mock_device()
    error = OSError("Unknown error")
    error.errno = 999
    mock_device.auth.side_effect = error

    with patch("linknlink.hello", return_value=mock_device):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_HOST: "192.168.1.100"},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "auth"
    assert result["errors"]["base"] == "unknown"


@pytest.mark.asyncio
async def test_async_step_auth_locked_device(hass) -> None:
    """Test authentication step with locked device."""
    mock_device = create_mock_device()
    mock_device.is_locked = True

    with patch("linknlink.hello", return_value=mock_device):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_HOST: "192.168.1.100"},
        )

    # Should show unlock form
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "unlock"


@pytest.mark.asyncio
async def test_async_step_unlock_success(hass) -> None:
    """Test unlock step with successful unlock."""
    mock_device = create_mock_device()
    mock_device.is_locked = True

    with patch("linknlink.hello", return_value=mock_device):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_HOST: "192.168.1.100"},
        )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "unlock"

        # Now unlock
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"unlock": True}
        )

    assert result2["type"] == FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
async def test_async_step_unlock_declined(hass) -> None:
    """Test unlock step when user declines to unlock."""
    mock_device = create_mock_device()
    mock_device.is_locked = True

    with patch("linknlink.hello", return_value=mock_device):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_HOST: "192.168.1.100"},
        )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "unlock"

        # Decline unlock
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"unlock": False}
        )

    assert result2["type"] == FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
async def test_async_step_unlock_network_timeout(hass) -> None:
    """Test unlock step with network timeout."""
    mock_device = create_mock_device()
    mock_device.is_locked = True
    mock_device.set_lock.side_effect = NetworkTimeoutError("Timeout")

    with patch("linknlink.hello", return_value=mock_device):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_HOST: "192.168.1.100"},
        )

        # Try to unlock
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"unlock": True}
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "unlock"
    assert result2["errors"]["base"] == "cannot_connect"


@pytest.mark.asyncio
async def test_async_step_unlock_linknlink_exception(hass) -> None:
    """Test unlock step with LinknLink exception."""
    mock_device = create_mock_device()
    mock_device.is_locked = True
    mock_device.set_lock.side_effect = LinknLinkException("Error")

    with patch("linknlink.hello", return_value=mock_device):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_HOST: "192.168.1.100"},
        )

        # Try to unlock
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"unlock": True}
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "unlock"
    assert result2["errors"]["base"] == "unknown"


@pytest.mark.asyncio
async def test_async_step_unlock_network_unreachable(hass) -> None:
    """Test unlock step with network unreachable."""
    mock_device = create_mock_device()
    mock_device.is_locked = True
    error = OSError("Network unreachable")
    error.errno = errno.ENETUNREACH
    mock_device.set_lock.side_effect = error

    with patch("linknlink.hello", return_value=mock_device):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_HOST: "192.168.1.100"},
        )

        # Try to unlock
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"unlock": True}
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "unlock"
    assert result2["errors"]["base"] == "cannot_connect"


@pytest.mark.asyncio
async def test_async_step_unlock_os_error_unknown(hass) -> None:
    """Test unlock step with unknown OSError."""
    mock_device = create_mock_device()
    mock_device.is_locked = True
    error = OSError("Unknown error")
    error.errno = 999
    mock_device.set_lock.side_effect = error

    with patch("linknlink.hello", return_value=mock_device):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_HOST: "192.168.1.100"},
        )

        # Try to unlock
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"unlock": True}
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "unlock"
    assert result2["errors"]["base"] == "unknown"


@pytest.mark.asyncio
async def test_async_step_reset_with_input(hass) -> None:
    """Test reset step with user input."""
    mock_device = create_mock_device()
    mock_device.auth.side_effect = AuthenticationError()

    with patch("linknlink.hello", return_value=mock_device):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_HOST: "192.168.1.100"},
        )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reset"

        # Continue from reset
        mock_device.auth.side_effect = None  # Remove the error
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )

    # Should go back to user step
    assert result2["type"] == FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
async def test_async_step_user_with_custom_timeout(hass) -> None:
    """Test user step with custom timeout."""
    mock_device = create_mock_device()

    with patch("linknlink.hello", return_value=mock_device):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_HOST: "192.168.1.100", CONF_TIMEOUT: 20},
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TIMEOUT] == 20


@pytest.mark.asyncio
async def test_async_step_user_with_default_timeout(hass) -> None:
    """Test user step uses default timeout when not specified."""
    mock_device = create_mock_device()

    with patch("linknlink.hello", return_value=mock_device):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_HOST: "192.168.1.100"},
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TIMEOUT] == DEFAULT_TIMEOUT


@pytest.mark.asyncio
async def test_dhcp_mac_address_formatting(hass) -> None:
    """Test DHCP MAC address is correctly formatted to lowercase without colons."""
    mock_device = create_mock_device()

    discovery_info = Mock()
    discovery_info.ip = "192.168.1.100"
    discovery_info.macaddress = "AA:BB:CC:DD:EE:FF"
    discovery_info.hostname = "linknlink_test"

    with patch("linknlink.hello", return_value=mock_device):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_DHCP},
            data=discovery_info,
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    # Verify MAC address was converted correctly
    assert result["data"][CONF_MAC] == "aabbccddeeff"


@pytest.mark.asyncio
async def test_multiple_supported_device_types(hass) -> None:
    """Test that all supported device types are accepted."""
    for device_type in DEVICE_TYPES:
        mock_device = create_mock_device()
        mock_device.type = device_type
        mock_device.mac = bytes.fromhex(f"aabbccddee{device_type[:2].lower()}")

        with patch("linknlink.hello", return_value=mock_device):
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_USER},
                data={CONF_HOST: "192.168.1.100"},
            )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_MAC] == mock_device.mac.hex()


@pytest.mark.asyncio
async def test_device_already_configured(hass) -> None:
    """Test device is already configured."""
    mock_device = create_mock_device()

    with patch("linknlink.hello", return_value=mock_device):
        # Configure device first time
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_HOST: "192.168.1.100"},
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY

        # Try to configure same device again
        result2 = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_HOST: "192.168.1.101"},  # Different host, same MAC
        )

    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "already_configured"