import argparse
import yaml
import json
import os
import time
import requests
from cachetools import cached, TTLCache

import httpimport

with httpimport.github_repo('jewzaam', 'metrics-utility', 'utility', 'main'):
    import utility

device_types = [
    "camera",
    "covercalibrator",
    "dome",
    "filterwheel",
    "focuser",
    "observingcondition",
    "rotator",
    "safetymonitor",
    "switch",
    "telescope",
]

# general configuration, key is 'device type' (i.e. telescope)
configurations = {}

# devices we are monitoring, key is 'device type', value is array of device numbers
devices = {}

def loadConfigurations(path):
    for _, _, filenames in os.walk(path):
        for filename in filenames:
            with open(os.path.join(path, filename), 'r') as file:
                c = yaml.load(file, Loader=yaml.FullLoader)
                t = filename.split(".")[0]
                configurations[t] = c

@cached(cache=TTLCache(maxsize=1024, ttl=60))
def getValueCached(alpaca_base_url, device_type, device_number, attribute, querystr=""):
    print(f"getValueCached(_, {device_type}, {device_number}, {attribute}, {querystr})")
    return getValue(alpaca_base_url, device_type, device_number, attribute, querystr)

def getValue(alpaca_base_url, device_type, device_number, attribute, querystr=""):
    print(f"getValue(_, {device_type}, {device_number}, {attribute}, {querystr})")

    request_url = f"{alpaca_base_url}/{device_type}/{device_number}/{attribute}?{querystr}"
    print(f"request_url = {request_url}")
    response = requests.get(request_url)

    labels = {
        "device_type": device_type,
        "device_number": device_number,
        "attribute": attribute,
    }

    if response.status_code != 200 or response.text is None or response.text == '':
        utility.inc("alpaca_error_total",labels)
        return None
    else:
        data = json.loads(response.text)
        if "ErrorNumber" in data and data["ErrorNumber"] > 0:
            utility.inc("alpaca_error_total",labels)
            return None
        value = data["Value"]
        # convert boolean to int
        if isinstance(value, (bool)):
            value = int(value == True)
        utility.inc("alpaca_success_total",labels)
        return value

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Export logs as prometheus metrics.")
    parser.add_argument("--port", type=int, help="port to expose metrics on")
    parser.add_argument("--alpaca_base_url", type=str, help="base alpaca v1 api with trailing slash, i.e. http://127.0.0.1:11111/api/v1/")
    parser.add_argument("--refresh_rate", type=int, help="seconds between refreshing metrics")

    # add args for each supported device type
    for device_type in device_types:
        parser.add_argument(f"--{device_type}", type=int, action='append', help=f"{device_type} device number")

    # treat args parsed as a dictionary
    args = vars(parser.parse_args())

    alpaca_base_url = args["alpaca_base_url"]
    refresh_rate = args["refresh_rate"]

    # build array of device numbers for each supported device
    # and verify user device numbers provided
    for device_type in device_types:
        if args[device_type]:
            devices[device_type] = args[device_type]
            for device_number in devices[device_type]:
                name = getValue(alpaca_base_url, device_type, device_number, "name", None)
                if name:
                    print(f"SUCCESS: found {device_type}/{device_number}")
                else:
                    print(f"FAILURE: unable to find {device_type}/{device_number}")

    loadConfigurations("config/")

    # verify user input for devices

    # Start up the server to expose the metrics.
    utility.metrics(args["port"])

    while True:
        for device_type in devices.keys():
            for device_number in devices[device_type]:
                c = configurations[device_type]

                if "metrics" not in c:
                    # no metrics, no point processing anything, go to next device
                    continue
                
                # collect labels for device this iteration
                labels = {
                    "device_type": device_type,
                    "device_number": device_number,
                }

                # verify this is a valid device.
                # if it cannot be found, skip it.. it might come online later
                name = getValue(alpaca_base_url, device_type, device_number, "name", None)
                if not name:
                    utility.set("alpaca_device_connected", 0, labels)
                    continue
                else:
                    utility.set("alpaca_device_connected", 1, labels)

                metric_prefix = ""
                if "metric_prefix" in c:
                    metric_prefix = c["metric_prefix"]

                # use inner functions to encapsulate scope and eliminate variable scope issues

                def global_labels():
                    if "global" in configurations and "labels" in configurations["global"]:
                        for l in configurations["global"]["labels"]:
                            alpaca_name = l["alpaca_name"]
                            label_name = alpaca_name
                            if "label_name" in l:
                                label_name = l["label_name"]
                            
                            if alpaca_name == "name":
                                # already pulled this early on
                                label_value = name
                            elif "cached" in l and l["cached"] > 0:
                                label_value = getValueCached(alpaca_base_url, device_type, device_number, alpaca_name)
                            else:
                                label_value = getValue(alpaca_base_url, device_type, device_number, alpaca_name)

                            if label_name and label_value:
                                labels[label_name] = label_value

                # device specific labels
                def device_labels(querystr=""):
                    if "labels" in c:
                        for l in c["labels"]:
                            alpaca_name = l["alpaca_name"]
                            label_name = alpaca_name
                            if "label_name" in l:
                                label_name = l["label_name"]

                            if alpaca_name == "name":
                                # already pulled this early on
                                label_value = name
                            if "cached" in l and l["cached"] > 0:
                                label_value = getValueCached(alpaca_base_url, device_type, device_number, alpaca_name, querystr)
                            else:
                                label_value = getValue(alpaca_base_url, device_type, device_number, alpaca_name, querystr)

                            if label_name and label_value:
                                labels[label_name] = label_value

                # process each metric
                def device_metrcis(querystr=""):
                    for m in c["metrics"]:
                        alpaca_name = m["alpaca_name"]
                        if "metric_name" not in m:
                            metric_name = f"{metric_prefix}{alpaca_name}"
                        else:
                            metric_name = f"{metric_prefix}{m['metric_name']}"

                        if "cached" in m and m["cached"] > 0:
                            metric_value = getValueCached(alpaca_base_url, device_type, device_number, alpaca_name, querystr)
                        else:
                            metric_value = getValue(alpaca_base_url, device_type, device_number, alpaca_name, querystr)

                        print(f"utility.set({metric_name}, {metric_value}, {labels})")
                        utility.set(metric_name, metric_value, labels)

                # get global labels
                global_labels()


                # SWITCH is a special device with an "id" query param.
                if "switch" == device_type:
                    # ids will be number of switch devices.
                    # id is 0 based, but range excludes the upper boundary.
                    # so using ids as is and upper bounds excluded is perfect.
                    ids = getValueCached(alpaca_base_url, device_type, device_number, "maxswitch")
                    for id in range(0, ids):
                        # also set 'id' on labels so it creates a unique key for metrics
                        labels['id'] = id
                        device_labels(f"id={id}")
                        device_metrcis(f"id={id}")
                else:
                    # all other devices do not have query params
                    device_labels()
                    device_metrcis()


        time.sleep(int(refresh_rate))