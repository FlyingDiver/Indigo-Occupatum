# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Occupatum is a plugin for the [Indigo](https://www.indigodomo.com) home automation server. It creates virtual
occupancy sensor devices whose on/off state is derived from the states of other Indigo sensor devices.

There is no build step, no dependency manifest, and no test suite. The repo *is* the deliverable: the
`Occupatum.indigoPlugin/` directory is a macOS bundle that Indigo loads directly.

## Development workflow

- Indigo runs the plugin under its own embedded Python 3 interpreter. `import indigo` only resolves inside
  that runtime, so the code cannot be executed, imported, or unit-tested outside the Indigo server.
- To test a change: install the bundle into Indigo (double-click `Occupatum.indigoPlugin` in Finder, or copy it
  to `/Library/Application Support/Perceptive Automation/Indigo <ver>/Plugins/`), then reload the plugin from
  Indigo's Plugins menu. Verify via the Indigo Event Log and the plugin's own log file.
- Set log level to Debug in the plugin's config dialog (`PluginConfig.xml` → `logLevel`) to see the extensive
  `self.logger.debug` tracing that most methods already emit.
- `Info.plist` holds `PluginVersion`. See the parent `Indigo PlugIns/CLAUDE.md` for the tag-on-version-bump rule.
- Releases are distributed as `Occupatum.indigoPlugin.zip` (the zipped bundle), one directory up from this repo.

## Architecture

All logic lives in one file: `Occupatum.indigoPlugin/Contents/Server Plugin/plugin.py`, a subclass of
`indigo.PluginBase`. The XML files beside it declare the UI and are wired to Python methods by name:

| File | Declares | Bound to |
|---|---|---|
| `Devices.xml` | the two device types + their ConfigUI + custom states | `sensorDevices`, `sensorDeviceList`, `addDevice`, `deleteDevices` callbacks |
| `Actions.xml` | plugin actions | `cancelTimer`, `forceZoneOff`, `updateActivityZone`, `updateOccupancyZone` |
| `Events.xml` | trigger types `zoneOccupied` / `zoneUnoccupied` | dispatched by `checkTriggers` |
| `PluginConfig.xml` | plugin prefs (log level) | `closedPrefsConfigUi` |

Adding a UI element means editing XML *and* the matching Python callback; the `id`/`CallbackMethod` strings are
the only linkage, and a mismatch fails silently at runtime rather than at load.

### The two device types

Both are Indigo `sensor` devices with `SupportsOnState`, and both store their member sensors in a single hidden
`sensorDevices` prop as a **comma-separated string of Indigo device IDs** (not a list). Every read of it does
`[int(x) for x in props.get("sensorDevices","").split(",")]`.

- **`area` ("Occupancy Zone")** — boolean combination of member sensors. `onAnyAll` (all/any) × `onSensorsOnOff`
  (on/off/change) decides occupancy, then `onDelayValue` / `offDelayValue` delay the transition and an optional
  `forceOffValue` forces the zone off after N seconds. Exposes the `delay_timer` and `force_off_timer` states.
- **`activityZone`** — occupied when at least `activityCount` sensor activations occurred within the trailing
  `activityWindow` seconds. Activations are kept as a list of timestamps ("time hacks") in `activityZoneList`.

### Runtime state (all in-memory, rebuilt on plugin reload)

- `zoneList` — zone device ID → list of member sensor device IDs
- `watchList` — member sensor device ID → list of zone device IDs that reference it (the reverse index)
- `activityZoneList` — activityZone device ID → list of activation timestamps
- `delayTimers` — zone device ID → `(deadline, occupied)`; `forceTimers` — zone device ID → `deadline`
- `triggers` — trigger ID → trigger

`deviceStartComm` populates `zoneList`/`watchList`; `deviceStopComm` tears them down. Several of these use bare
`assert` for invariants, so a stale entry surfaces as an AssertionError in the log at device start/stop.

### Event flow

1. `startup()` calls `indigo.devices.subscribeToChanges()`.
2. `deviceUpdated` fires for *every* Indigo device change; it filters to devices in `watchList` whose `onState`
   actually changed, then calls `checkSensors(zone, newState)` for each dependent zone.
3. `checkSensors` computes the new occupancy and, for `area` zones, arms a delay timer rather than changing the
   state immediately. For `activityZone` it appends a timestamp and updates state directly.
4. `runConcurrentThread` ticks once per second: counts down and displays `delay_timer` / `force_off_timer`,
   expires them via `delayTimerComplete` / `forceOffTimerComplete`, and pops timestamps that fell out of the
   activity window (re-calling `checkSensors`).
5. State transitions call `checkTriggers`, which executes matching `zoneOccupied` / `zoneUnoccupied` triggers.

Note the per-second `updateStateOnServer` calls on the timer states — `deviceStartComm` sets
`sqlLoggerIgnoreStates = "delay_timer,force_off_timer"` in sharedProps so they don't flood the SQL logger.

### Zones as sensors for other zones

Zone devices are themselves `indigo.sensor` devices, so a zone can be a member sensor of another zone.
`isRecursive` walks the membership graph in `validateDeviceConfigUi` to reject cycles.

### Action validation convention

Each action has a `validate_<action>` helper returning `(is_valid, indigo.Dict of errors)`. It is called from
both `validateActionConfigUi` (dialog-time) and the action method itself (execution-time, since actions can be
invoked from scripts/HTTP API bypassing the dialog). Action methods return a `reply_dict` with `status` and
optional `errors` for `caller_waiting_for_result`.
