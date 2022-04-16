#! /usr/bin/env python
# -*- coding: utf-8 -*-
####################

import logging
import indigo
import threading

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

        self.ZoneList = {}
        self.watchList = {}
        self.delayTimers = {}
        self.forceTimers = {}
        self.triggers = {}

    def startup(self):
        self.logger.info("Starting Occupatum")
        indigo.devices.subscribeToChanges()

    def shutdown(self):
        self.logger.info("Stopping Occupatum")

    def deviceStartComm(self, device):
        self.logger.info(f"{device.name}: Starting Device")

        sensorsInZone = [int(x) for x in device.pluginProps.get("sensorDevices","").split(",")]
        self.logger.debug(f"{device.name}: Zone {device.id} uses sensor devices: {sensorsInZone}")

        for sensor in sensorsInZone:
            if sensor not in self.watchList:
                self.watchList[sensor] = list()
            self.watchList[sensor].append(device.id)
                
        self.logger.debug(f"{device.name}: watchList updated: {self.watchList}")
        
        assert device.id not in self.ZoneList
        self.ZoneList[device.id] = sensorsInZone
        
        # update the state
        self.checkSensors(device)

    def deviceStopComm(self, device):
        self.logger.info(f"{device.name}: Stopping Device")

        # need to remove this sensor from the watch lists
        sensorsInZone = [int(x) for x in device.pluginProps.get("sensorDevices","").split(",")]
        for sensor in sensorsInZone:
            try:
                self.watchList[sensor].remove(device.id)
            except (Exception,):
                pass

        # if there are existing timers for this sensor, cancel them
        
        timer = self.delayTimers.get(device.id, None)
        if timer:
            timer.cancel()
            
        timer = self.forceTimers.get(device.id, None)
        if timer:
            timer.cancel()
                
        assert device.id in self.ZoneList
        del self.ZoneList[device.id]

    def checkSensors(self, zoneDevice):
                
        onSensorsOnOff = zoneDevice.pluginProps.get("onSensorsOnOff","on")
        if onSensorsOnOff == 'change':
            occupiedList = [True for x in self.ZoneList[zoneDevice.id]]            
        elif onSensorsOnOff == 'on':
            occupiedList = [indigo.devices[x].onState for x in self.ZoneList[zoneDevice.id]]
        else:
            occupiedList = [not indigo.devices[x].onState for x in self.ZoneList[zoneDevice.id]]
        
        onAnyAll = zoneDevice.pluginProps.get("onAnyAll","all")
        if onAnyAll == 'all':
            occupied = all(occupiedList)
        else:
            occupied = any(occupiedList)
        
        previous = zoneDevice.onState
                     
        self.logger.debug(f"{zoneDevice.name}: checkSensors, onSensorsOnOff = {onSensorsOnOff}, onAnyAll = {onAnyAll}, sensors: {self.ZoneList[zoneDevice.id]}")

        if occupied:
            delay = float(zoneDevice.pluginProps.get("onDelayValue","0"))
        else:
            delay = float(zoneDevice.pluginProps.get("offDelayValue","0"))

        self.logger.debug(f"{zoneDevice.name}: checkSensors, occupied = {occupied}, previous = {previous}, delay = {delay}")

        # if there's an existing timer for this sensor, cancel it before starting a new one
        
        timer = self.delayTimers.get(zoneDevice.id, None)
        if timer:
            self.logger.debug(f"{zoneDevice.name}: checkSensors, cancelling existing delay timer")
            timer.cancel()
            
        # start a timer with the specified delay
        
        timer = threading.Timer(delay, lambda: self.delayTimerComplete(zoneDevice, occupied))
        self.delayTimers[zoneDevice.id] = timer
        self.logger.debug(f"{zoneDevice.name}: checkSensors, starting delay timer {timer} with delay = {delay}")
        timer.start()        

        if onSensorsOnOff == 'change':
            # if there's an existing timer for this sensor, cancel it before starting a new one
        
            timer = self.forceTimers.get(zoneDevice.id, None)
            if timer:
                self.logger.debug(f"{zoneDevice.name}: checkSensors, cancelling existing force timer")
                timer.cancel()
                
            delay = float(zoneDevice.pluginProps.get("forceOffValue"))
            
            # start a timer with the specified delay
        
            timer = threading.Timer(delay, lambda: self.forceTimerComplete(zoneDevice, False))
            self.forceTimers[zoneDevice.id] = timer
            self.logger.debug(f"{zoneDevice.name}: checkSensors, starting force timer {timer} with delay = {delay}")
            timer.start()        

    def delayTimerComplete(self, device, occupied):
        self.logger.debug(f"{device.name}: delayTimerComplete, occupied = {occupied}")
    
        if device.id in self.delayTimers:
            del self.delayTimers[device.id]
        else:
            self.logger.warning(f"{zoneDevice.name}: delayTimerComplete, no timer found")

        if device.onState != occupied:            
            device.updateStateOnServer(key='onOffState', value=occupied)
            self.checkTriggers(device, occupied)
        
    def forceTimerComplete(self, device, occupied):
        self.logger.debug(f"{device.name}: forceTimerComplete, occupied = {occupied}")
    
        if device.id in self.forceTimers:
            del self.forceTimers[device.id]
        else:
            self.logger.warning(f"{zoneDevice.name}: forceTimerComplete, no timer found")

        if device.onState != occupied:            
            device.updateStateOnServer(key='onOffState', value=occupied)
            self.checkTriggers(device, occupied)

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
   
    def cancelTimer(self, pluginAction, zoneDevice):
        timer = self.delayTimers.get(zoneDevice.id, None)
        if timer:
            self.logger.debug(f"{zoneDevice.name}: cancelTimer: Timer Cancelled")
            timer.cancel()
            del self.delayTimers[zoneDevice.id]
        else:
            self.logger.debug(f"{zoneDevice.name}: cancelTimer: No Timer Active")

        state = pluginAction.props["state"]
        if state == "on":
            zoneDevice.updateStateOnServer(key='onOffState', value=True)
        elif state == "off":
            zoneDevice.updateStateOnServer(key='onOffState', value=False)

    ########################################
    # ConfigUI methods
    ########################################

    def closedPrefsConfigUi(self, valuesDict, userCancelled):
        if not userCancelled:
            self.logLevel = int(valuesDict.get(u"logLevel", logging.INFO))
            self.indigo_log_handler.setLevel(self.logLevel)
            self.logger.debug(f"logLevel = {self.logLevel}")

    ########################################
    # This routine will validate the device configuration dialog when the user attempts to save the data
    ########################################

    def validateDeviceConfigUi(self, valuesDict, typeId, devId):
        self.logger.debug(u"validateDeviceConfigUi, devId={}, typeId={}, valuesDict = {}".format(devId, typeId, valuesDict))
        errorMsgDict = indigo.Dict()
        
        sensorDevices = valuesDict.get('sensorDevices', None)
        if len(sensorDevices) == 0:
            self.logger.error("Configuration Error: No sensors specified.")
            errorMsgDict[u"sensorDevices"] = u"Empty Sensor List"
            return False, valuesDict, errorMsgDict
            
        elif self.isRecursive(devId,  indigo.devices[devId].name, sensorDevices):
            self.logger.error("Configuration Error: Sensor Recursion Detected")
            errorMsgDict[u"sensorDevices"] = "Sensor Recursion Detected"
            return False, valuesDict, errorMsgDict
        elif valuesDict.get("onSensorsOnOff", None) == "change" and (valuesDict.get("forceOffValue", "") == "" or int(valuesDict.get("forceOffValue", "0")) == 0):
            self.logger.error("Configuration Error: Force Off required for 'Any Change' sensors")
            errorMsgDict["forceOffValue"] = u"Force Off required for 'Any Change' sensors"
            return False, valuesDict, errorMsgDict
        else:
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
        if newDevice.id in self.watchList and oldDevice.onState != newDevice.onState:   # only care about onState changes
            self.logger.debug(f"Watched Device updated: {newDevice.name} is now {newDevice.onState}")
            for zone in self.watchList[newDevice.id]:
                self.checkSensors(indigo.devices[zone])

    ################################################################################
    #
    # UI List methods
    #
    ################################################################################

    ########################################
    # This is the method that's called to build the source device list. 
    ########################################
    def sensorDevices(self, filter="", valuesDict=None, typeId="", targetId=0):
        self.logger.debug(f"sensorDevices, targetId={targetId}, typeId={typeId}, filter={filter}, valuesDict = {valuesDict}")
        returnList = list()

        if not valuesDict:
            valuesDict = {}

        deviceList = valuesDict.get("sensorDevices","").split(",")
        for device in indigo.devices.iter("indigo.sensor"):
            if (unicode(device.id) not in deviceList) and device.supportsOnState:
                returnList.append((unicode(device.id), device.name))
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
                
            selectedDevicesString = valuesDict.get("sensorDevices","")
            self.logger.debug(f"adding device: {deviceId} to {selectedDevicesString}")
            
            if selectedDevicesString == "":
                selectedDevicesString = deviceId
            else:
                selectedDevicesString += "," + str(deviceId)

            valuesDict["sensorDevices"] = selectedDevicesString
            self.logger.debug(u"valuesDict = {}".format(valuesDict))

            if "sensorDeviceList" in valuesDict:
                del valuesDict["sensorDeviceList"]
            if "sensorDeviceMenu" in valuesDict:
                del valuesDict["sensorDeviceMenu"]

            return valuesDict

    ########################################
    # This is the method that's called by the Delete Device button in the scene device config UI.
    ########################################
    def deleteDevices(self, valuesDict, typeId, devId):
        self.logger.debug(f"deleteDevices called, devId={devId}, typeId={typeId}, valuesDict = {valuesDict}")
        
        if "sensorDevices" in valuesDict:
            devicesInZone = valuesDict.get("sensorDevices","").split(",")
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
        self.logger.debug(u"sensorDeviceList called, targetId={}, typeId={}, filter={}, valuesDict = {}".format(targetId, typeId, filter, valuesDict))

        returnList = list()

        if valuesDict and "sensorDevices" in valuesDict:
            deviceListString = valuesDict["sensorDevices"]
            self.logger.debug(u"deviceListString: {}".format(deviceListString))
            deviceList = deviceListString.split(",")

            for devId in deviceList:
                if int(devId) in indigo.devices:
                    returnList.append((devId, indigo.devices[int(devId)].name))
        return returnList
