#! /usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Regression checks for Occupatum, run against the stub indigo module in this directory.

    python3 tests/test_plugin.py

Exits non-zero if anything fails.  No dependencies, no test framework - the plugin itself has none, and
Indigo's embedded interpreter is not what runs this.

These cover the paths that are painful to exercise on a live server: device deletion, the props-edit restart,
the two-thread timer races, and plugin startup against a stale config.  What they cannot cover is Indigo
itself - the stub encodes assumptions about it (notably that replacePluginPropsOnServer synchronously stops
and restarts the device, and that deviceStopComm sees the post-edit props).  A pass here is evidence, not
proof; anything touching those two behaviours still wants a real server before release.
"""

import importlib.util
import logging
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
PLUGIN = HERE.parent / "Occupatum.indigoPlugin" / "Contents" / "Server Plugin" / "plugin.py"

sys.path.insert(0, str(HERE))  # so `import indigo` finds the stub
import indigo  # noqa: E402

logging.disable(logging.CRITICAL)  # the plugin logs heavily; silence it for readable output

spec = importlib.util.spec_from_file_location("occ_plugin", PLUGIN)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

PLUGIN_ID = "com.flyingdiver.indigoplugin.occupatum"
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))


def fresh(zone_props, zone_type="area", sensors=(100, 200), onState=False):
    """A plugin instance with one zone (id 1) and the given member sensors."""
    indigo.devices = indigo._Devices()
    mod.indigo.devices = indigo.devices
    indigo.trigger.executed = []
    for sid in sensors:
        indigo.devices.add(indigo.Device(sid, f"Sensor{sid}", "sensor", pluginId="other"))
    zone = indigo.devices.add(indigo.Device(1, "Zone", zone_type, props=zone_props, onState=onState))
    plugin = mod.Plugin(PLUGIN_ID, "Occupatum", "0", {"logLevel": 50})
    indigo.devices.plugin = plugin
    return plugin, zone


AREA = {"onAnyAll": "any", "onSensorsOnOff": "on", "onDelayValue": "0", "offDelayValue": "0", "forceOffValue": ""}


def area_props(sensors, **overrides):
    props = dict(AREA, sensorDevices=sensors)
    props.update(overrides)
    return props


# --- reconcile_zones: the only place that prunes the saved props ------------------------------------------

p, zone = fresh(area_props("100,999,abc"))
p.startup()
check("reconcile prunes deleted IDs and junk", zone.pluginProps["sensorDevices"] == "100",
      zone.pluginProps["sensorDevices"])

before = len(indigo.devices.restarts)
p.startup()
check("reconcile is idempotent", len(indigo.devices.restarts) == before,
      f"{before} -> {len(indigo.devices.restarts)}")

# --- watchList add/remove symmetry -------------------------------------------------------------------------

p, zone = fresh(area_props("100,200"))
p.startup()
p.deviceStartComm(zone)
props = zone.pluginProps
props["sensorDevices"] = "100"
zone.replacePluginPropsOnServer(props)  # a config-dialog edit: stops and restarts the device
check("sensor removed in the dialog leaves no watchList entry", 200 not in p.watchList, str(p.watchList))
check("surviving sensor stays registered", p.watchList.get(100) == [1], str(p.watchList))

# --- deleting a member sensor ------------------------------------------------------------------------------

p, zone = fresh(area_props("100,200"))
p.startup()
p.deviceStartComm(zone)
zone.updateStateOnServer(key="onOffState", value=True)  # zone is occupied when the sensor goes away
trg = type("T", (), {"id": 7, "name": "t", "pluginProps": {"zoneDevice": "1"}, "pluginTypeId": "zoneUnoccupied"})()
p.triggers[7] = trg
dead = indigo.devices[200]
indigo.devices.delete(200)
p.deviceDeleted(dead)
check("deleted sensor is pruned from the props", zone.pluginProps["sensorDevices"] == "100",
      zone.pluginProps["sensorDevices"])
p.runConcurrentThread()  # one tick, to complete the armed delay timer
check("zoneUnoccupied fires when an occupied zone loses a sensor",
      zone.onState is False and len(indigo.trigger.executed) == 1,
      f"onState={zone.onState} triggers={len(indigo.trigger.executed)}")

# --- activityZone history ----------------------------------------------------------------------------------

p, zone = fresh({"sensorDevices": "100,200", "activityCount": "3", "activityWindow": "600"},
                zone_type="activityZone")
p.startup()
p.deviceStartComm(zone)
p.activityZoneList[1] = [1.0, 2.0, 3.0]
dead = indigo.devices[200]
indigo.devices.delete(200)
p.deviceDeleted(dead)
check("surviving sensors keep their activation history", p.activityZoneList.get(1) == [1.0, 2.0, 3.0],
      str(p.activityZoneList.get(1)))

# --- deleting a zone device itself ---------------------------------------------------------------------------

p, zone = fresh(area_props("100,200", onDelayValue="5"))
p.startup()
p.deviceStartComm(zone)
p.delayTimers[1] = (9e9, True)
indigo.devices.delete(1)
p.deviceDeleted(zone)
check("deleting a zone leaves nothing behind",
      not p.zoneList and not p.watchList and not p.delayTimers and not p.activityZoneList,
      f"zoneList={p.zoneList} watchList={p.watchList} delayTimers={p.delayTimers}")

# --- the timer-dict race that used to kill runConcurrentThread ------------------------------------------------

p, zone = fresh(area_props("100"))
p.startup()
p.deviceStartComm(zone)


class Racy(dict):
    """Reports the key present, then loses it before the subscript - i.e. the main thread cancelled it."""

    def __contains__(self, key):
        return True


p.delayTimers = Racy()
p.forceTimers = Racy()
try:
    p.runConcurrentThread()
    survived, err = True, ""
except Exception as exc:  # noqa: BLE001 - any escape at all is the failure
    survived, err = False, f"{type(exc).__name__}: {exc}"
check("timer thread survives a cancelled-underneath-it timer", survived, err)

# --- props arriving from a script action ----------------------------------------------------------------------

p, zone = fresh(area_props("100", forceOffValue=300))  # an int, as a script would leave it
p.startup()
try:
    p.deviceStartComm(zone)
    ok, err = True, ""
except Exception as exc:  # noqa: BLE001
    ok, err = False, f"{type(exc).__name__}: {exc}"
check("non-string forceOffValue does not wedge the zone", ok, err)

# --- action methods honour the reply_dict convention ------------------------------------------------------------

p, zone = fresh({"sensorDevices": "100", "activityCount": "2", "activityWindow": "60"}, zone_type="activityZone")
p.startup()
p.deviceStartComm(zone)
action = type("A", (), {"props": {"activityCount": "2", "activityWindow": "60"}})()
reply = p.updateActivityZone(action, zone)
check("updateActivityZone returns its reply_dict", reply is not None and "status" in reply, repr(reply))

p, zone = fresh(area_props("100"))
p.startup()
p.deviceStartComm(zone)
action = type("A", (), {"props": {"onDelayValue": "1", "offDelayValue": "1", "forceOffValue": "0"}})()
reply = p.updateOccupancyZone(action, zone)
check("updateOccupancyZone returns its reply_dict", reply is not None and "status" in reply, repr(reply))

# --- all([]) is True ---------------------------------------------------------------------------------------------

p, zone = fresh(area_props("", onAnyAll="all"))
p.startup()
p.deviceStartComm(zone)
check("an empty 'all' zone is not occupied", zone.onState is False, f"onState={zone.onState}")


passed = sum(1 for _, ok, _ in results if ok)
print()
for name, ok, detail in results:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if not ok else ""))
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
