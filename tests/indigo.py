"""Minimal stub of the Indigo runtime, enough to exercise plugin.py off-server."""


class Dict(dict):
    pass


class List(list):
    pass


class kStateImageSel:
    MotionSensor = "motion"
    MotionSensorTripped = "tripped"


class Device:
    def __init__(self, dev_id, name, deviceTypeId, props=None, onState=False, pluginId="com.flyingdiver.indigoplugin.occupatum"):
        self.id = dev_id
        self.name = name
        self.deviceTypeId = deviceTypeId
        self.pluginProps = Dict(props or {})
        self.sharedProps = Dict()
        self.onState = onState
        self.supportsOnState = True
        self.pluginId = pluginId
        self.states = {}
        self.state_writes = []

    def updateStateOnServer(self, key, value, uiValue=None):
        self.states[key] = value
        self.state_writes.append((key, value, uiValue))
        if key == "onOffState":
            self.onState = bool(value)

    def updateStateImageOnServer(self, image):
        self.image = image

    def stateListOrDisplayStateIdChanged(self):
        pass

    def replaceSharedPropsOnServer(self, props):
        self.sharedProps = Dict(props)

    def replacePluginPropsOnServer(self, props):
        self.pluginProps = Dict(props)
        devices._restart(self)


class _Devices:
    def __init__(self):
        self._devs = {}
        self.plugin = None
        self.restarts = []

    def add(self, dev):
        self._devs[dev.id] = dev
        return dev

    def delete(self, dev_id):
        self._devs.pop(dev_id, None)

    def __contains__(self, key):
        return key in self._devs

    def __getitem__(self, key):
        if key not in self._devs:
            raise KeyError(f"key id {key} not found in database")
        return self._devs[key]

    def iter(self, filt=None):
        for dev in list(self._devs.values()):
            if filt == "self" and dev.pluginId != "com.flyingdiver.indigoplugin.occupatum":
                continue
            if filt == "indigo.sensor" and dev.deviceTypeId not in ("area", "activityZone", "sensor"):
                continue
            yield dev

    def subscribeToChanges(self):
        pass

    def _restart(self, dev):
        # Indigo stops and restarts a device whose props were replaced
        self.restarts.append(dev.id)
        if self.plugin is not None:
            self.plugin.deviceStopComm(dev)
            self.plugin.deviceStartComm(dev)


devices = _Devices()


class trigger:
    executed = []

    @staticmethod
    def execute(trg):
        trigger.executed.append(trg)


class PluginBase:
    class StopThread(Exception):
        pass

    def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):
        self.pluginId = pluginId
        self.pluginPrefs = pluginPrefs or {}
        import logging
        self.logger = logging.getLogger("occupatum")
        if not hasattr(self.logger, "threaddebug"):
            self.logger.threaddebug = self.logger.debug
        self.plugin_file_handler = logging.NullHandler()
        self.plugin_file_handler.setFormatter = lambda *a, **k: None
        self.indigo_log_handler = logging.NullHandler()
        self.indigo_log_handler.setLevel = lambda *a, **k: None

    def deviceDeleted(self, dev):
        pass

    def deviceUpdated(self, old, new):
        pass

    def sleep(self, secs):
        raise PluginBase.StopThread()
