#! /usr/bin/env python
# -*- coding: utf-8 -*-
####################

import logging
import indigo

################################################################################
class Plugin(indigo.PluginBase):

    ########################################
    # Main Plugin methods
    ########################################
    def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):
        indigo.PluginBase.__init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs)

        pfmt = logging.Formatter('%(asctime)s.%(msecs)03d\t[%(levelname)8s] %(name)20s.%(funcName)-25s%(msg)s', datefmt='%Y-%m-%d %H:%M:%S')
        self.plugin_file_handler.setFormatter(pfmt)

        try:
            self.logLevel = int(self.pluginPrefs[u"logLevel"])
        except:
            self.logLevel = logging.INFO
        self.indigo_log_handler.setLevel(self.logLevel)
        self.logger.debug(u"logLevel = {}".format(self.logLevel))


    def startup(self):
        self.logger.info(u"Starting Where Am I")
        
        self.areaList = {}
        self.watchList = {}
        
        indigo.devices.subscribeToChanges()

    def shutdown(self):
        self.logger.info(u"Shutting down Where Am I")


    def deviceStartComm(self, device):
        self.logger.info(u"{}: Starting Device".format(device.name))

        devicesInArea = device.pluginProps.get("sensorDevices","").split(",")
        self.logger.debug(u"{}: sensor devices: {}".format(device.name, devicesInArea))

        for sensor in devicesInArea:
            self.watchList[sensor] = device.id
        self.logger.debug(u"{}: watchList: {}".format(device.name, self.watchList))

        device.updateStateOnServer(key='onOffState', value=self.checkSensors(device))
        
        assert device.id not in self.areaList
        self.areaList[device.id] = devicesInArea


    def deviceStopComm(self, device):
        self.logger.info(u"{}: Stopping Device".format(device.name))

        assert device.id in self.areaList
        del self.areaList[device.id]


    def checkSensors(self, device):
        
        return False
        
    
    ########################################
    # Menu Methods
    ########################################



    ########################################
    # ConfigUI methods
    ########################################

    def closedPrefsConfigUi(self, valuesDict, userCancelled):
        if not userCancelled:
            try:
                self.logLevel = int(valuesDict[u"logLevel"])
            except:
                self.logLevel = logging.INFO
            self.indigo_log_handler.setLevel(self.logLevel)
            self.logger.debug(u"logLevel = {}".format(self.logLevel))
 

    ################################################################################
    #
    # delegate methods for indigo.devices.subscribeToChanges()
    #
    ################################################################################

    def deviceDeleted(self, delDevice):
        indigo.PluginBase.deviceDeleted(self, delDevice)
        if unicode(delDevice.id) in self.watchList:
            self.logger.debug(u"Watched Device deleted: {}".format(delDevice.name))

    def deviceUpdated(self, oldDevice, newDevice):
        indigo.PluginBase.deviceUpdated(self, oldDevice, newDevice)
        if unicode(newDevice.id) in self.watchList:
            self.logger.debug(u"Watched Device updated: {}".format(newDevice.name))
        

    ################################################################################
    #
    # UI List methods
    #
    ################################################################################

    ####################
    # This is the method that's called to build the source device list. 
    ####################
    def sensorDevices(self, filter="", valuesDict=None, typeId="", targetId=0):
        self.logger.debug(u"sensorDevices called, targetId={}, typeId={}, filter={}, valuesDict = {}".format(targetId, typeId, filter, valuesDict))
        returnList = list()

        if not valuesDict:
            valuesDict = {}

        deviceList = valuesDict.get("sensorDevices","").split(",")
        for device in indigo.devices.iter("indigo.sensor"):
            if (unicode(device.id) not in deviceList) and device.supportsOnState and (device.pluginId != self.pluginId):
                returnList.append((unicode(device.id), device.name))
        return returnList


    ########################################
    # This is the method that's called by the Add Device button in the config dialog.
    ########################################
    def addDevice(self, valuesDict, typeId, devId):
        self.logger.debug(u"addDevice called, devId={}, typeId={}, valuesDict = {}".format(devId, typeId, valuesDict))
        
        if "sensorDeviceMenu" in valuesDict:
            deviceId = valuesDict["sensorDeviceMenu"]
            if deviceId == "":
                return
                
            selectedDevicesString = valuesDict.get("sensorDevices","")
            self.logger.debug(u"adding device: {} to {}".format(deviceId, selectedDevicesString))
            
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
        self.logger.debug(u"deleteDevices called, devId={}, typeId={}, valuesDict = {}".format(devId, typeId, valuesDict))
        
        if "sensorDevices" in valuesDict:
            devicesInArea = valuesDict.get("sensorDevices","").split(",")
            selectedDevices = valuesDict.get("sensorDeviceList", [])

            for deviceId in selectedDevices:
                self.logger.debug(u"remove deviceId: {}".format(deviceId))
                if deviceId in devicesInArea:
                    devicesInArea.remove(deviceId)
            valuesDict["sensorDevices"] = ",".join(devicesInArea)

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

