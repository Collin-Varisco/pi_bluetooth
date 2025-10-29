#!/usr/bin/env python3
import asyncio
from dbus_next.aio import MessageBus
from dbus_next import BusType, Variant, Message
from dbus_next.constants import PropertyAccess
from dbus_next.service import (ServiceInterface, method, dbus_property)

BLUEZ = "org.bluez"
ADAPTER_IFACE = "org.bluez.Adapter1"
DEVICE_IFACE = "org.bluez.Device1"
AGENT_MANAGER_IFACE = "org.bluez.AgentManager1"
AGENT_PATH = "/bt/agent"
AGENT_CAP = "NoInputNoOutput"

class Agent(ServiceInterface):
    def __init__(self):
        super().__init__("org.bluez.Agent1")

    @method()
    def Release(self):
        pass

    @method()
    def RequestPinCode(self, device: 'o') -> 's':
        # No keypad. Return empty or fixed code if needed.
        return ""

    @method()
    def DisplayPinCode(self, device: 'o', pincode: 's'):
        pass

    @method()
    def RequestPasskey(self, device: 'o') -> 'u':
        return 0

    @method()
    def DisplayPasskey(self, device: 'o', passkey: 'u', entered: 'q'):
        pass

    @method()
    def RequestConfirmation(self, device: 'o', passkey: 'u'):
        # Auto-accept matching codes
        return

    @method()
    def RequestAuthorization(self, device: 'o'):
        return

    @method()
    def AuthorizeService(self, device: 'o', uuid: 's'):
        return

    @method()
    def Cancel(self):
        pass

async def get_managed_objects(bus):
    obj = await bus.get_proxy_object(BLUEZ, "/", None)
    mngr = obj.get_interface("org.freedesktop.DBus.ObjectManager")
    return await mngr.call_get_managed_objects()

async def main():
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

    # 1) Register Agent
    agent = Agent()
    bus.export(AGENT_PATH, agent)
    agent_mgr_obj = await bus.get_proxy_object(BLUEZ, "/org/bluez", [
        "org.bluez.AgentManager1"
    ])
    agent_mgr = agent_mgr_obj.get_interface(AGENT_MANAGER_IFACE)
    try:
        await agent_mgr.call_register_agent(AGENT_PATH, AGENT_CAP)
    except Exception:
        # Ignore if already registered
        pass
    await agent_mgr.call_request_default_agent(AGENT_PATH)

    # 2) Get adapter (hci0)
    objects = await get_managed_objects(bus)
    adapter_path = None
    for path, ifaces in objects.items():
        if ADAPTER_IFACE in ifaces:
            adapter_path = path
            break
    if not adapter_path:
        raise RuntimeError("Bluetooth adapter not found")

    adapter_obj = await bus.get_proxy_object(BLUEZ, adapter_path, [ADAPTER_IFACE, "org.freedesktop.DBus.Properties"])
    adapter_props = adapter_obj.get_interface("org.freedesktop.DBus.Properties")

    # Power on, discoverable, pairable, nice alias
    await adapter_props.call_set(ADAPTER_IFACE, "Powered", Variant("b", True))
    await adapter_props.call_set(ADAPTER_IFACE, "Discoverable", Variant("b", True))
    await adapter_props.call_set(ADAPTER_IFACE, "Pairable", Variant("b", True))
    await adapter_props.call_set(ADAPTER_IFACE, "Alias", Variant("s", "Pi Speaker"))

    # 3) Auto-trust and log connects
    obj_manager = await bus.get_proxy_object(BLUEZ, "/", ["org.freedesktop.DBus.ObjectManager"])
    props_changed_match = await bus.add_message_handler(
        lambda msg: False  # placeholder to keep a reference; we use add_signal_receiver below
    )

    async def on_props_changed(message: Message):
        if message.message_type != Message.SIGNAL:
            return
        if message.interface != "org.freedesktop.DBus.Properties":
            return
        if message.member != "PropertiesChanged":
            return
        iface, changed, invalidated = message.body
        if iface != DEVICE_IFACE:
            return
        path = message.path
        connected = changed.get("Connected")
        if isinstance(connected, Variant) and connected.signature == "b" and connected.value:
            # Device connected: trust it
            dev_obj = await bus.get_proxy_object(BLUEZ, path, ["org.freedesktop.DBus.Properties"])
            dev_props = dev_obj.get_interface("org.freedesktop.DBus.Properties")
            try:
                await dev_props.call_set(DEVICE_IFACE, "Trusted", Variant("b", True))
            except Exception:
                pass
            # Keep discoverable for next devices too
            try:
                await adapter_props.call_set(ADAPTER_IFACE, "Discoverable", Variant("b", True))
            except Exception:
                pass
            print(f"[INFO] Device connected and trusted: {path}")

    # Subscribe to PropertiesChanged
    await bus.add_signal_receiver(
        on_props_changed,
        interface="org.freedesktop.DBus.Properties",
        signal="PropertiesChanged",
        path_namespace="/org/bluez"
    )

    print("[INFO] Pi is now a discoverable Bluetooth A2DP sink named 'Pi Speaker'.")
    print("[INFO] On your phone: Bluetooth → pair with 'Pi Speaker' → set it as audio output.")
    # Just sit forever
    await asyncio.get_running_loop().create_future()

if __name__ == "__main__":
    asyncio.run(main())

