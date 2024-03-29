#! /usr/bin/env python
# -*- coding: utf-8 -*-
####################

import logging
import indigo
import time


################################################################################
class Plugin(indigo.PluginBase):

    ########################################
    # Main Plugin methods
    ########################################
    def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):
        indigo.PluginBase.__init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs)

        pfmt = logging.Formatter('%(asctime)s.%(msecs)03d\t[%(levelname)8s] %(name)20s.%(funcName)-25s%(msg)s', datefmt='%Y-%m-%d %H:%M:%S')
        self.plugin_file_handler.setFormatter(pfmt)
        self.logLevel = int(self.pluginPrefs.get("logLevel", logging.INFO))
        self.indigo_log_handler.setLevel(self.logLevel)
        self.logger.debug(f"logLevel = {self.logLevel}")

        self.zoneList = {}
        self.activityZoneList = {}
        self.watchList = {}
        self.delayTimers = {}
        self.forceTimers = {}
        self.triggers = {}

    def startup(self):
        self.logger.info("Starting Occupatum")
        indigo.devices.subscribeToChanges()

    def shutdown(self):
        self.logger.info("Stopping Occupatum")

    ################################################################################
    #
    # delegate methods for indigo.devices.subscribeToChanges()
    #
    ################################################################################

    def deviceDeleted(self, delDevice):
        indigo.PluginBase.deviceDeleted(self, delDevice)
        if delDevice.id in self.watchList:
            self.logger.debug(f"Watched Device deleted: {delDevice.name}")
            del self.watchList[delDevice.id]

    def deviceUpdated(self, oldDevice, newDevice):
        indigo.PluginBase.deviceUpdated(self, oldDevice, newDevice)
        if newDevice.id in self.watchList and oldDevice.onState != newDevice.onState:  # only care about onState changes
            self.logger.debug(f"Watched Device updated: {newDevice.name} is now {newDevice.onState}")
            for zone in self.watchList[newDevice.id]:
                self.checkSensors(indigo.devices[zone], newDevice.onState)

    def runConcurrentThread(self):
        try:
            while True:
                for zoneDevID in self.zoneList:
                    zoneDevice = indigo.devices[zoneDevID]

                    if zoneDevID in self.delayTimers:
                        timerEnd, occupied = self.delayTimers[zoneDevID]
                        duration = timerEnd - time.time()
                        zoneDevice.updateStateOnServer(key='delay_timer', value=duration)
                        zoneDevice.updateStateOnServer(key='onOffState', value=zoneDevice.onState, uiValue=f"Delay {duration:.1f}")
                        if timerEnd <= time.time():
                            self.delayTimerComplete(zoneDevice, occupied)

                    if zoneDevID in self.forceTimers:
                        timerEnd = self.forceTimers[zoneDevID]
                        duration = timerEnd - time.time()
                        zoneDevice.updateStateOnServer(key='force_off_timer', value=duration)
                        zoneDevice.updateStateOnServer(key='onOffState', value=zoneDevice.onState, uiValue=f"Force Off {duration:.1f}")
                        if timerEnd <= time.time():
                            self.forceOffTimerComplete(zoneDevice)

                    expired = time.time() - float(zoneDevice.pluginProps.get("activityWindow", 0))
                    if zoneDevID in self.activityZoneList:  # remove expired time hacks
                        if len(self.activityZoneList[zoneDevID]) and (self.activityZoneList[zoneDevID][0] < expired):
                            self.activityZoneList[zoneDevID].pop(0)
                            self.logger.debug(f"{zoneDevice.name}: checkSensors activityZone, deleted time hack")
                            self.checkSensors(zoneDevice, False)

                self.sleep(1.0)
        except self.StopThread:
            pass

    def deviceStartComm(self, device):
        self.logger.info(f"{device.name}: Starting Device")

        if device.deviceTypeId == 'area':

            sharedProps = device.sharedProps
            sharedProps["sqlLoggerIgnoreStates"] = "delay_timer,force_off_timer"
            device.replaceSharedPropsOnServer(sharedProps)

            device.updateStateOnServer(key='onOffState', value=False)
            device.updateStateImageOnServer(indigo.kStateImageSel.MotionSensor)

            device.stateListOrDisplayStateIdChanged()

            sensorsInZone = [int(x) for x in device.pluginProps.get("sensorDevices", "").split(",")]
            self.logger.debug(f"{device.name}: Zone {device.id} uses sensor devices: {sensorsInZone}")

            for sensor in sensorsInZone:
                if sensor not in self.watchList:
                    self.watchList[sensor] = list()
                self.watchList[sensor].append(device.id)

            self.logger.debug(f"{device.name}: watchList updated: {self.watchList}")

            assert device.id not in self.zoneList
            self.zoneList[device.id] = sensorsInZone

        elif device.deviceTypeId == 'activityZone':

            device.updateStateOnServer(key='onOffState', value=False)
            device.updateStateImageOnServer(indigo.kStateImageSel.MotionSensor)

            sensorsInZone = [int(x) for x in device.pluginProps.get("sensorDevices", "").split(",")]
            self.logger.debug(f"{device.name}: Zone {device.id} uses sensor devices: {sensorsInZone}")

            for sensor in sensorsInZone:
                if sensor not in self.watchList:
                    self.watchList[sensor] = list()
                self.watchList[sensor].append(device.id)

            self.logger.debug(f"{device.name}: watchList updated: {self.watchList}")

            assert device.id not in self.zoneList
            self.zoneList[device.id] = sensorsInZone  # list of indigo device IDs that are sensors for this zone
            self.activityZoneList[device.id] = []  # list of time hacks that sensor on updates occurred

        else:
            self.logger.warning(f"{device.name}: deviceStartComm: Invalid device type: {device.deviceTypeId}")
            return

        # update the state
        self.checkSensors(device, False)

    def deviceStopComm(self, device):
        self.logger.info(f"{device.name}: Stopping Device")

        # need to remove this sensor from the watch lists
        sensorsInZone = [int(x) for x in device.pluginProps.get("sensorDevices", "").split(",")]
        for sensor in sensorsInZone:
            try:
                self.watchList[sensor].remove(device.id)
            except (Exception,):
                pass

        # if there are existing timers for this sensor, cancel them

        if self.delayTimers.get(device.id, None):
            del self.delayTimers[device.id]

        if self.forceTimers.get(device.id, None):
            del self.forceTimers[device.id]

        assert device.id in self.zoneList
        del self.zoneList[device.id]

    def checkSensors(self, zoneDevice, sensorState):

        if zoneDevice.deviceTypeId == 'area':

            onSensorsOnOff = zoneDevice.pluginProps.get("onSensorsOnOff", "on")
            if onSensorsOnOff == 'change':
                occupiedList = [True for x in self.zoneList[zoneDevice.id]]
            elif onSensorsOnOff == 'on':
                occupiedList = [indigo.devices[x].onState for x in self.zoneList[zoneDevice.id]]
            else:
                occupiedList = [not indigo.devices[x].onState for x in self.zoneList[zoneDevice.id]]

            onAnyAll = zoneDevice.pluginProps.get("onAnyAll", "all")
            if onAnyAll == 'all':
                occupied = all(occupiedList)
            else:
                occupied = any(occupiedList)

            previous = zoneDevice.onState

            self.logger.debug(
                f"{zoneDevice.name}: checkSensors, onSensorsOnOff = {onSensorsOnOff}, onAnyAll = {onAnyAll}, sensors: {self.zoneList[zoneDevice.id]}")

            if occupied:
                delay = float(zoneDevice.pluginProps.get("onDelayValue", "0"))
            else:
                delay = float(zoneDevice.pluginProps.get("offDelayValue", "0"))

            self.logger.debug(f"{zoneDevice.name}: checkSensors, occupied = {occupied}, previous = {previous}, delay = {delay}")

            # start a timer with the specified delay
            self.delayTimers[zoneDevice.id] = ((time.time() + delay), occupied)
            self.logger.debug(f"{zoneDevice.name}: checkSensors, adding delay timer with value = {delay}, occupied = {occupied}")
            zoneDevice.updateStateOnServer(key='onOffState', value=previous, uiValue=f"Delay {delay:.1f}")
            zoneDevice.updateStateOnServer(key='delay_timer', value=delay)

            forceOff = zoneDevice.pluginProps.get("forceOffValue", "0")
            if forceOff.isdigit() and float(forceOff) > 0.0:
                self.forceTimers[zoneDevice.id] = time.time() + float(forceOff)
                self.logger.debug(f"{zoneDevice.name}: checkSensors, starting force timer with value = {forceOff}")
                zoneDevice.updateStateOnServer(key='onOffState', value=previous, uiValue=f"Force Off  {float(forceOff):.1f}")
                zoneDevice.updateStateOnServer(key='force_off_timer', value=float(forceOff))

        elif zoneDevice.deviceTypeId == 'activityZone':

            if sensorState:
                # add another time hack to list
                self.activityZoneList[zoneDevice.id].append(time.time())
                self.logger.debug(f"{zoneDevice.name}: checkSensors activityZone, added time hack. {len(self.activityZoneList[zoneDevice.id])} total")

            previous = zoneDevice.onState
            occupied = len(self.activityZoneList[zoneDevice.id]) >= int(zoneDevice.pluginProps.get("activityCount", 0))
            self.logger.debug(f"{zoneDevice.name}: checkSensors activityZone, occupied = {occupied}")
            if previous != occupied:
                zoneDevice.updateStateOnServer(key='onOffState', value=occupied, uiValue=("on" if occupied else "off"))
                zoneDevice.updateStateImageOnServer(indigo.kStateImageSel.MotionSensorTripped if occupied else indigo.kStateImageSel.MotionSensor)
                self.checkTriggers(zoneDevice, occupied)

    def delayTimerComplete(self, device, occupied):
        self.logger.debug(f"{device.name}: delayTimerComplete, occupied = {occupied}")

        if device.id in self.delayTimers:
            del self.delayTimers[device.id]
        else:
            self.logger.warning(f"{zoneDevice.name}: delayTimerComplete, no timer found")

        previous = device.onState

        device.updateStateOnServer(key='delay_timer', value=0.0)
        device.updateStateOnServer(key='onOffState', value=occupied, uiValue=("on" if occupied else "off"))
        device.updateStateImageOnServer(indigo.kStateImageSel.MotionSensorTripped if occupied else indigo.kStateImageSel.MotionSensor)
        if previous != occupied:
            self.checkTriggers(device, occupied)

    def forceOffTimerComplete(self, device):
        self.logger.debug(f"{device.name}: forceOffTimerComplete")

        if device.id in self.forceTimers:
            del self.forceTimers[device.id]
        else:
            self.logger.warning(f"{zoneDevice.name}: forceOffTimerComplete, no timer found")

        previous = device.onState

        device.updateStateOnServer(key='force_off_timer', value=0.0)
        device.updateStateOnServer(key='onOffState', value=False, uiValue="")
        device.updateStateImageOnServer(indigo.kStateImageSel.MotionSensor)
        if previous:
            self.checkTriggers(device, False)

    ########################################
    # Trigger (Event) handling
    ########################################

    def triggerStartProcessing(self, trigger):
        self.logger.debug(f"{trigger.name}: Adding Trigger")
        assert trigger.id not in self.triggers
        self.triggers[trigger.id] = trigger

    def triggerStopProcessing(self, trigger):
        self.logger.debug(f"{trigger.name}: Removing Trigger")
        assert trigger.id in self.triggers
        del self.triggers[trigger.id]

    def checkTriggers(self, device, occupied):

        for trigger in self.triggers.values():
            self.logger.debug(f"{trigger.name}: Testing Event Trigger")

            if trigger.pluginProps["zoneDevice"] == str(device.id):
                self.logger.debug(f"{trigger.name}: Match on Zone {device.name}")

                if trigger.pluginTypeId == "zoneOccupied":
                    if occupied:
                        indigo.trigger.execute(trigger)
                elif trigger.pluginTypeId == "zoneUnoccupied":
                    if not occupied:
                        indigo.trigger.execute(trigger)
                else:
                    self.logger.error(f"{trigger.name}: Unknown Trigger Type {trigger.pluginTypeId}")

    ########################################
    # Action methods
    ########################################

    def getActionConfigUiValues(self, action_props, type_id, dev_id):
        self.logger.debug(f"getActionConfigUiValues, actionProps = {action_props}, type_id = {type_id}, dev_id = {dev_id}")
        device = indigo.devices[dev_id]
        if type_id == "cancelTimer":
            pass

        elif type_id == "updateActivityZone":
            if 'activityCount' not in action_props:
                action_props["activityCount"] = device.pluginProps['activityCount']
            if 'activityWindow' not in action_props:
                action_props["activityWindow"] = device.pluginProps['activityWindow']

        elif type_id == "updateOccupancyZone":
            if 'onDelayValue' not in action_props:
                action_props["onDelayValue"] = device.pluginProps['onDelayValue']
            if 'offDelayValue' not in action_props:
                action_props["offDelayValue"] = device.pluginProps['offDelayValue']
            if 'forceOffValue' not in action_props:
                action_props["forceOffValue"] = device.pluginProps['forceOffValue']
        return action_props

    def validateActionConfigUi(self, values_dict, type_id, dev_id):
        self.logger.debug(f"validateActionConfigUi, values_dict = {values_dict}, type_id = {type_id}, dev_id = {dev_id}")
        device = indigo.devices[dev_id]
        if type_id == "cancelTimer":
            is_valid, errors = self.validate_cancel_timer_action(dev_id, values_dict)
            return is_valid, values_dict, errors
        elif type_id == "updateActivityZone":
            is_valid, errors = self.validate_update_activity_zone_action(dev_id, values_dict)
            return is_valid, values_dict, errors
        elif type_id == "updateOccupancyZone":
            is_valid, errors = self.validate_update_occupancy_zone_action(dev_id, values_dict)
            return is_valid, values_dict, errors
        else:
            return True, values_dict

    def validate_cancel_timer_action(self, dev_id, props):
        errors = indigo.Dict()
        if dev_id not in indigo.devices:
            errors["device"] = "'deviceId' must be included and must represent an existing device"

        if "state" not in props:
            errors["state"] = "'state' parameter is missing"
        elif props["state"] not in ["on", "off", "unchanged"]:
            errors["state"] = f"{props['state']} must be one of: 'on', 'off', 'unchanged'"

        self.logger.debug(f"validate_update_occupancy_zone_action, errors={errors}")
        return (not bool(errors)), errors  # bool(errors) will return False if empty, True if not)

    def validate_force_zone_off_action(self, dev_id):
        errors = indigo.Dict()
        if dev_id not in indigo.devices:
            errors["device"] = "'deviceId' must be included and must represent an existing device"

        self.logger.debug(f"validate_force_zone_off_action, errors={errors}")
        return (not bool(errors)), errors  # bool(errors) will return False if empty, True if not)

    def validate_update_activity_zone_action(self, dev_id, props):
        errors = indigo.Dict()
        if dev_id not in indigo.devices:
            errors["device"] = "'deviceId' must be included and must represent an existing device"

        try:
            if int(props["activityWindow"]) <= 0:
                errors["activityWindow"] = f"{props['activityWindow']} must be a positive integer"
        except ValueError:
            errors["activityWindow"] = f"{props['activityWindow']} must be a positive integer"

        try:
            if int(props["activityCount"]) <= 0:
                errors["activityCount"] = f"{props['activityCount']} must be a positive integer"
        except ValueError:
            errors["activityWindow"] = f"{props['activityWindow']} must be a positive integer"

        self.logger.debug(f"validate_update_activity_zone_action, errors={errors}")
        return (not bool(errors)), errors  # bool(errors) will return False if empty, True if not)

    def validate_update_occupancy_zone_action(self, dev_id, props):
        errors = indigo.Dict()
        if dev_id not in indigo.devices:
            errors["device"] = "'deviceId' must be included and must represent an existing device"

        try:
            if int(props["onDelayValue"]) < 0:
                errors["onDelayValue"] = f"{props['onDelayValue']} must be a positive integer"
        except ValueError as err:
            errors["onDelayValue"] = f"ValueError: {props['onDelayValue']}: {err}"

        try:
            if int(props["offDelayValue"]) < 0:
                errors["offDelayValue"] = f"{props['offDelayValue']} must be a positive integer"
        except ValueError as err:
            errors["offDelayValue"] = f"ValueError: {props['offDelayValue']}: {err}"

        if props.get("forceOffValue", None):
            try:
                if int(props["forceOffValue"]) < 0:
                    errors["forceOffValue"] = f"{props['forceOffValue']} must be a positive integer"
            except ValueError as err:
                errors["forceOffValue"] = f"ValueError: {props['forceOffValue']}: {err}"

        self.logger.debug(f"validate_update_occupancy_zone_action, errors={errors}")
        return (not bool(errors)), errors  # bool(errors) will return False if empty, True if not)

    def cancelTimer(self, action, device, caller_waiting_for_result=None):
        self.logger.debug(f"cancelTimer, zoneDevice={device.id}, pluginAction={action}")
        reply_dict = indigo.Dict()  # This will hold the status and errors or device details in the appropriate format
        is_valid, errors = self.validate_cancel_timer_action(device.id, action.props)
        reply_dict["status"] = is_valid
        if not is_valid:
            self.logger.error(f"Couldn't complete 'cancelTimer' action because of errors:\n{dict(errors)}")
            reply_dict["errors"] = errors
        elif device.id not in self.delayTimers:
            self.logger.warning(f"{device.name}: cancelTimer, no timer found")
            reply_dict["errors"] = {"forceOffValue": f"delayTimerComplete, no timer found for device {device.id}"}
        else:
            del self.delayTimers[device.id]
            device.updateStateOnServer(key='delay_timer', value=0.0)
            state = action.props["state"]
            if state == "on":
                device.updateStateOnServer(key='onOffState', value=True, uiValue="On")
                device.updateStateImageOnServer(indigo.kStateImageSel.MotionSensorTripped)
            elif state == "off":
                device.updateStateOnServer(key='onOffState', value=False, uiValue="Off")
                device.updateStateImageOnServer(indigo.kStateImageSel.MotionSensor)
        return reply_dict

    def forceZoneOff(self, action, device, caller_waiting_for_result=None):
        self.logger.debug(f"forceZoneOff, zoneDevice={device.id}")
        reply_dict = indigo.Dict()  # This will hold the status and errors or device details in the appropriate format
        is_valid, errors = self.validate_force_zone_off_action(device.id)
        reply_dict["status"] = is_valid
        if not is_valid:
            self.logger.error(f"Couldn't complete 'forceZoneOff' action because of errors:\n{dict(errors)}")
            reply_dict["errors"] = errors
        else:
            device.updateStateOnServer(key='onOffState', value=False, uiValue="Off")
            device.updateStateImageOnServer(indigo.kStateImageSel.MotionSensor)
        return reply_dict

    def updateActivityZone(self, plugin_action, zone_device, caller_waiting_for_result=None):
        self.logger.debug(f"updateActivityZone, zoneDevice={zone_device.id}, pluginAction={plugin_action}")
        reply_dict = indigo.Dict()  # This will hold the status and errors or device details in the appropriate format
        is_valid, errors = self.validate_update_activity_zone_action(zone_device.id, plugin_action.props)
        reply_dict["status"] = is_valid
        if not is_valid:
            self.logger.error(f"Couldn't complete 'updateActivityZone' action because of errors:\n{dict(errors)}")
            reply_dict["errors"] = errors
        elif zone_device.id not in indigo.devices:
            self.logger.warning(f"{zone_device.name}: updateActivityZone, device not found")
            reply_dict["errors"] = {"forceOffValue": f"updateActivityZone, device not found: {zone_device.id}"}
        else:
            props = zone_device.pluginProps
            props["activityCount"] = plugin_action.props["activityCount"]
            props["activityWindow"] = plugin_action.props["activityWindow"]
            zone_device.replacePluginPropsOnServer(props)

    def updateOccupancyZone(self, plugin_action, zone_device, caller_waiting_for_result=None):
        self.logger.debug(f"updateOccupancyZone, zoneDevice={zone_device.id}, pluginAction={plugin_action}")
        reply_dict = indigo.Dict()  # This will hold the status and errors or device details in the appropriate format
        is_valid, errors = self.validate_update_occupancy_zone_action(zone_device.id, plugin_action.props)
        reply_dict["status"] = is_valid
        if not is_valid:
            self.logger.error(f"Couldn't complete 'updateOccupancyZone' action because of errors:\n{dict(errors)}")
            reply_dict["errors"] = errors
        elif zone_device.id not in indigo.devices:
            self.logger.warning(f"{device.name}: updateOccupancyZone, device not found")
            reply_dict["errors"] = {"forceOffValue": f"updateOccupancyZone, device not found: {device.id}"}
        else:
            props = zone_device.pluginProps
            props["onDelayValue"] = plugin_action.props["onDelayValue"]
            props["offDelayValue"] = plugin_action.props["offDelayValue"]
            props["forceOffValue"] = plugin_action.props["forceOffValue"]
            zone_device.replacePluginPropsOnServer(props)

    ########################################
    # ConfigUI methods
    ########################################

    def closedPrefsConfigUi(self, valuesDict, userCancelled):
        if not userCancelled:
            self.logLevel = int(valuesDict.get("logLevel", logging.INFO))
            self.indigo_log_handler.setLevel(self.logLevel)
            self.logger.debug(f"logLevel = {self.logLevel}")

    ########################################
    # This routine will validate the device configuration dialog when the user attempts to save the data
    ########################################

    def validateDeviceConfigUi(self, valuesDict, typeId, devId):
        self.logger.debug(f"validateDeviceConfigUi, devId={devId}, typeId={typeId}, valuesDict = {valuesDict}")
        errorMsgDict = indigo.Dict()

        sensorDevices = valuesDict.get('sensorDevices', None)
        if len(sensorDevices) == 0:
            self.logger.error("Configuration Error: No sensors specified.")
            errorMsgDict["sensorDevices"] = "Empty Sensor List"
            return False, valuesDict, errorMsgDict

        if self.isRecursive(devId, indigo.devices[devId].name, sensorDevices):
            self.logger.error("Configuration Error: Sensor Recursion Detected")
            errorMsgDict["sensorDevices"] = "Sensor Recursion Detected"
            return False, valuesDict, errorMsgDict

        if typeId == 'area':

            if valuesDict.get("onSensorsOnOff", None) == "change":
                if not str(valuesDict.get("forceOffValue", "").isdigit()):
                    self.logger.error("Configuration Error: Force Off valid number required for 'Either (Any Change)' sensors")
                    errorMsgDict["forceOffValue"] = "Force Off valid number required for 'Either (Any Change)' sensors"
                    return False, valuesDict, errorMsgDict

            if not str(valuesDict.get("onDelayValue", "")).isdigit():
                self.logger.error("Configuration Error: A number for time in seconds is required")
                errorMsgDict["onDelayValue"] = "Please enter a valid number"
                return False, valuesDict, errorMsgDict

            if not str(valuesDict.get("offDelayValue", "")).isdigit():
                self.logger.error("Configuration Error: A number for time in seconds is required")
                errorMsgDict["offDelayValue"] = "Please enter a valid number"
                return False, valuesDict, errorMsgDict

        elif typeId == 'activityZone':

            if not str(valuesDict.get("activityWindow", "")).isdigit():
                self.logger.error("Configuration Error: A number for time in seconds is required")
                errorMsgDict["activityWindow"] = "Please enter a valid number"
                return False, valuesDict, errorMsgDict

            if not str(valuesDict.get("activityCount", "")).isdigit():
                self.logger.error("Configuration Error: A number for time in seconds is required")
                errorMsgDict["activityCount"] = "Please enter a valid number"
                return False, valuesDict, errorMsgDict

        return True, valuesDict

    def isRecursive(self, devId, devName, sensorDevices):
        self.logger.debug(f"isRecursive, devId = {devId}, devName = {devName}, sensorDevices = {sensorDevices}")

        sensorList = sensorDevices.split(",")
        if str(devId) in sensorList:
            self.logger.error(f"{devName}: Recursion Error - Sensor {devId} found in sensor list: {sensorDevices}")
            return True

        for sensorID in sensorList:
            try:
                sensorDev = indigo.devices[int(sensorID)]
            except (Exception,):
                continue
            if sensorDev.pluginId == self.pluginId and sensorDev.deviceTypeId == 'area':
                return self.isRecursive(devId, sensorDev.name, sensorDev.pluginProps.get('sensorDevices', None))

        return False

    ################################################################################
    #
    # UI List methods
    #
    ################################################################################

    ########################################
    # This is the method that's called to build the source device list. 
    ########################################
    def sensorDevices(self, filter="", valuesDict=None, typeId="", targetId=0):
        self.logger.threaddebug(f"sensorDevices, targetId={targetId}, typeId={typeId}, filter={filter}, valuesDict = {valuesDict}")
        returnList = list()

        if not valuesDict:
            valuesDict = {}

        deviceList = valuesDict.get("sensorDevices", "").split(",")
        for device in indigo.devices.iter("indigo.sensor"):
            if (str(device.id) not in deviceList) and device.supportsOnState:
                returnList.append((str(device.id), device.name))
        return returnList

    ########################################
    # This is the method that's called by the Add Device button in the config dialog.
    ########################################
    def addDevice(self, valuesDict, typeId, devId):
        self.logger.debug(f"addDevice called, devId={devId}, typeId={typeId}, valuesDict = {valuesDict}")

        if "sensorDeviceMenu" in valuesDict:
            deviceId = valuesDict["sensorDeviceMenu"]
            if deviceId == "":
                return

            selectedDevicesString = valuesDict.get("sensorDevices", "")
            self.logger.debug(f"adding device: {deviceId} to {selectedDevicesString}")

            if selectedDevicesString == "":
                selectedDevicesString = deviceId
            else:
                selectedDevicesString += "," + str(deviceId)

            valuesDict["sensorDevices"] = selectedDevicesString
            self.logger.debug(f"valuesDict = {valuesDict}")

            if "sensorDeviceList" in valuesDict:
                del valuesDict["sensorDeviceList"]
            if "sensorDeviceMenu" in valuesDict:
                del valuesDict["sensorDeviceMenu"]

            return valuesDict

    ########################################
    # This is the method that's called by the Device button in the scene device config UI.
    ########################################
    def deleteDevices(self, valuesDict, typeId, devId):
        self.logger.debug(f"deleteDevices called, devId={devId}, typeId={typeId}, valuesDict = {valuesDict}")

        if "sensorDevices" in valuesDict:
            devicesInZone = valuesDict.get("sensorDevices", "").split(",")
            selectedDevices = valuesDict.get("sensorDeviceList", [])

            for deviceId in selectedDevices:
                self.logger.debug(f"remove deviceId: {deviceId}")
                if deviceId in devicesInZone:
                    devicesInZone.remove(deviceId)
            valuesDict["sensorDevices"] = ",".join(devicesInZone)

            if "sensorDeviceList" in valuesDict:
                del valuesDict["sensorDeviceList"]
            if "sensorDeviceMenu" in valuesDict:
                del valuesDict["sensorDeviceMenu"]
            return valuesDict

    ########################################
    # This is the method that's called to build the sensor list for this device. Note that valuesDict is read-only.
    ########################################
    def sensorDeviceList(self, filter="", valuesDict=None, typeId="", targetId=0):
        self.logger.threaddebug(f"sensorDeviceList called, targetId={targetId}, typeId={typeId}, filter={filter}, valuesDict = {valuesDict}")

        returnList = list()

        if valuesDict and "sensorDevices" in valuesDict:
            deviceListString = valuesDict["sensorDevices"]
            self.logger.debug(f"deviceListString: {deviceListString}")
            deviceList = deviceListString.split(",")

            for devId in deviceList:
                try:
                    if int(devId) in indigo.devices:
                        returnList.append((devId, indigo.devices[int(devId)].name))
                except (Exception,):
                    continue
        return returnList
