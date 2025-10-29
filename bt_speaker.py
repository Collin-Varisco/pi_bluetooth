#!/usr/bin/env python3
# Debian 13 (Trixie) / BlueZ / dbus-next
# Makes the Pi pairable as an A2DP sink and auto-trusts devices on connect.

import asyncio
import sys
from dbus_next.aio import MessageBus
from dbus_next import BusType, Variant, Message
from dbus_next.service import ServiceInterface, method

BLUEZ = "org.bluez"
OBJMGR_IFACE = "org.freedesktop.DBus.ObjectManager"
PROPS_IFACE = "org.freedesktop.DBus.Properties"
ADAPTER_IFACE = "org.bluez.Adapter1"
DEVICE_IFACE = "org.bluez.Device1"
AGENT_MGR_IFACE = "org.bluez.AgentManager1"
AGENT_IFACE = "org.bluez.Agent1"

AGENT_PATH = "/bt/agent"
AGENT_CAP = "NoInputNoOutput"  # “Just Works” pairing

class Agent(ServiceInterface):
    def __init__(self):
        super().__init__(AGENT_IFACE)

    @method()
    def Release(self):  # noqa: D401
        pass

    @method()
    def RequestPinCode(self, device: 'o') -> 's':  # noqa: N802
        return ""

    @method()
    def DisplayPinCode(self, device: 'o', pincode: 's'):  # noqa: N802
        pass

    @method()
    def RequestPasskey(self, device: 'o') -> 'u':  # noqa: N802
        return 0

    @method()
    def DisplayPasskey(self, device: 'o', passkey: 'u', entered: 'q'):  # noqa: N802
        pass

    @method()
    def RequestConfirmation(self, device: 'o', passkey: 'u'):  # noqa: N802
        return

    @method()
    def RequestAuthorization(self, device: 'o'):  # noqa: N802
        return

    @method()
    def AuthorizeService(self, device: 'o', uuid: 's'):  # noqa: N802
        return

    @method()
    def Cancel(self):  # noqa: D401
        pass


async def get_object_manager(bus: MessageBus):
    # Properly introspect root for ObjectManager
    intro = await bus.introspect(BLUEZ, "/")
    obj = bus.get_proxy_object(BLUEZ, "/", intro)
    return obj.get_interface(OBJMGR_IFACE)


async def get_adapter_path(bus: MessageBus, retries: int = 10, delay: float = 0.5):
    # Wait for BlueZ to export an adapter (hci0)
    for _ in range(retries):
        try:
            mngr = await get_object_manager(bus)
            objects = await mngr.call_get_managed_objects()
            for path, ifaces in objects.items():
                if ADAPTER_IFACE in ifaces:
                    return path
        except Exception:
            pass
        await asyncio.sleep(delay)
    return None


async def get_props_iface(bus: MessageBus, path: str):
    intro = await bus.introspect(BLUEZ, path)
    obj = bus.get_proxy_object(BLUEZ, path, intro)
    return obj.get_interface(PROPS_IFACE)


async def register_agent(bus: MessageBus):
    # Introspect /org/bluez for AgentManager1
    intro = await bus.introspect(BLUEZ, "/org/bluez")
    obj = bus.get_proxy_object(BLUEZ, "/org/bluez", intro)
    agent_mgr = obj.get_interface(AGENT_MGR_IFACE)

    agent = Agent()
    bus.export(AGENT_PATH, agent)

    try:
        await agent_mgr.call_register_agent(AGENT_PATH, AGENT_CAP)
    except Exception:
        # Ignore if already registered
        pass
    await agent_mgr.call_request_default_agent(AGENT_PATH)


async def on_props_changed(bus: MessageBus, message: Message, adapter_props):
    # Only care about Device1 Connected=true events
    if message.message_type != Message.SIGNAL:
        return
    if message.interface != PROPS_IFACE or message.member != "PropertiesChanged":
        return

    iface, changed, _invalid = message.body
    if iface != DEVICE_IFACE:
        return

    connected = changed.get("Connected")
    if isinstance(connected, Variant) and connected.signature == "b" and connected.value:
        dev_path = message.path
        try:
            dev_props = await get_props_iface(bus, dev_path)
            await dev_props.call_set(DEVICE_IFACE, "Trusted", Variant("b", True))
        except Exception as e:
            print(f"[WARN] Failed to trust {dev_path}: {e}", flush=True)

        # Keep adapter discoverable for the next phone
        try:
            await adapter_props.call_set(ADAPTER_IFACE, "Discoverable", Variant("b", True))
        except Exception:
            pass

        print(f"[INFO] Device connected and trusted: {dev_path}", flush=True)


async def main():
    # Connect to the system bus
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

    # Register pairing agent first
    await register_agent(bus)

    # Get adapter path with a short retry window
    adapter_path = await get_adapter_path(bus)
    if not adapter_path:
        print("[ERROR] No Bluetooth adapter found. Is bluetooth.service running?", file=sys.stderr)
        sys.exit(1)

    # Adapter properties interface
    adapter_props = await get_props_iface(bus, adapter_path)

    # Power on and make the adapter pairable and discoverable
    for key, val in [
        ("Powered", True),
        ("Pairable", True),
        ("Discoverable", True),
        ("Alias", "Pi Speaker"),
    ]:
        try:
            v = Variant("b", val) if isinstance(val, bool) else Variant("s", val)
            await adapter_props.call_set(ADAPTER_IFACE, key, v)
        except Exception as e:
            print(f"[WARN] Failed setting {key}: {e}", flush=True)

    # Subscribe to PropertiesChanged across BlueZ namespace
    async def handler(msg: Message):
        await on_props_changed(bus, msg, adapter_props)

    await bus.add_signal_receiver(
        handler,
        interface=PROPS_IFACE,
        signal="PropertiesChanged",
        path_namespace="/org/bluez",
    )

    print("[INFO] Ready. Pair your phone with 'Pi Speaker' and select it as audio output.", flush=True)
    # Sit forever
    await asyncio.get_running_loop().create_future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

