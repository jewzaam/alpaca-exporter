import argparse
import yaml
import json
import os
import time
import requests
import copy
from cachetools import cached, TTLCache

import utility

device_types = [
    "camera",
    "covercalibrator",
    "dome",
    "filterwheel",
    "focuser",
    "observingconditions",
    "rotator",
    "safetymonitor",
    "switch",
    "telescope",
]

# general configuration, key is 'device type' (i.e. telescope)
configurations = {}

# devices we are monitoring, key is 'device type', value is array of device numbers
devices = {}

# cache metadata for metrics.  it's an array of tuples, each tuple being [string,dictionary] representing metric name and labels (no value)
metric_metadata_cache = {}

DEBUG = False

def debug(message):
    # simply so I can easily control debug messages
    if DEBUG:
        print(message)

def loadConfigurations(path):
    for _, _, filenames in os.walk(path):
        for filename in filenames:
            with open(os.path.join(path, filename), 'r') as file:
                c = yaml.load(file, Loader=yaml.FullLoader)
                t = filename.split(".")[0]
                configurations[t] = c

@cached(cache=TTLCache(maxsize=1024, ttl=60))
def getValueCached(alpaca_base_url, device_type, device_number, attribute, querystr=""):
    debug(f"getValueCached(_, {device_type}, {device_number}, {attribute}, {querystr})")
    return getValue(alpaca_base_url, device_type, device_number, attribute, querystr)

def getValue(alpaca_base_url, device_type, device_number, attribute, querystr=""):
    debug(f"getValue(_, {device_type}, {device_number}, {attribute}, {querystr})")

    request_url = f"{alpaca_base_url}/{device_type}/{device_number}/{attribute}?{querystr}"
    debug(f"request_url = {request_url}")
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
            errNo = data["ErrorNumber"]
            if errNo == 1024:
                # indicates something is not implemented.  return None, do nothing.
                # NOTE do not log any warning, it will just spam output as we don't disable / remove the attribute.
                return None
            utility.inc("alpaca_error_total",labels)
            return None
        value = data["Value"]
        # convert boolean to int
        if isinstance(value, (bool)):
            value = int(value == True)
        utility.inc("alpaca_success_total",labels)
        debug(f"==> {value}")
        return value

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Export logs as prometheus metrics.")
    parser.add_argument("--port", type=int, help="port to expose metrics on, default: 9876")
    parser.add_argument("--alpaca_base_url", type=str, help="base alpaca v1 api with trailing slash, default: http://127.0.0.1:11111/api/v1/")
    parser.add_argument("--refresh_rate", type=int, help="seconds between refreshing metrics, default: 5")

    # add args for each supported device type
    for device_type in device_types:
        parser.add_argument(f"--{device_type}", type=int, action='append', help=f"{device_type} device number")

    # treat args parsed as a dictionary
    args = vars(parser.parse_args())

    alpaca_base_url = "http://127.0.0.1:11111/api/v1/"
    if "alpaca_base_url" in args and args["alpaca_base_url"]:
        alpaca_base_url = args["alpaca_base_url"]

    refresh_rate = 5
    if "refresh_rate" in args and args["refresh_rate"]:
        refresh_rate = args["refresh_rate"]

    port = 9876
    if "port" in args and args["port"]:
        port = args["port"]

    # build array of device numbers for each supported device
    # and verify user device numbers provided
    done = False
    while not done:
        try:
            for device_type in device_types:
                if args[device_type]:
                    devices[device_type] = args[device_type]
                    for device_number in devices[device_type]:
                        name = getValue(alpaca_base_url, device_type, device_number, "name", "")
                        if name:
                            print(f"SUCCESS: found {device_type}/{device_number}")
                        else:
                            print(f"FAILURE: unable to find {device_type}/{device_number}")
            done = True
        except Exception as e:
            print("EXCEPTION")
            print(e)
            pass
    
        time.sleep(int(refresh_rate))

    if len(devices) == 0:
        print(f"ERROR: no devices configured, must supply at least one of: {device_types}")
        os._exit(-1)

    loadConfigurations("config/")

    # verify user input for devices

    # Start up the server to expose the metrics.
    utility.metrics(port)

    # collect metric name and array of labels from this and previous iterations (metrics_previous, metrics_current)
    # will compare w/ the previous iteration to see if we need to wipe any state
    metrics_previous = []
    while True:
        try:
            metrics_current = []

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
                    metrics_current.append(["alpaca_device_connected", copy.deepcopy(labels)])
                    if not name:
                        utility.set("alpaca_device_connected", 0, labels)
                        continue
                    else:
                        utility.set("alpaca_device_connected", 1, labels)
                        labels.update({"name": name})
                        utility.set("alpaca_device_name", 1, labels)
                        metrics_current.append(["alpaca_device_name",copy.deepcopy(labels)])

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

                            # if metric_value is None we'll try to clear it
                            # if it's none but there is no prior value it will fail, ignore this
                            try:
                                utility.set(metric_name, metric_value, labels)
                                metrics_current.append([metric_name,copy.deepcopy(labels)])
                            except:
                                pass

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

        except Exception as e:
            print("EXCEPTION")
            print(e)
            pass

        # handle cleanup of metrics.  this is the case when something stops reporting and we do not want stale metric.
        try:
            for m in metrics_previous:
                # if the cache has a value we didn't just collect we must remove the metric
                if m not in metrics_current:
                    metric_name=m[0]
                    labels=m[1]
                    debug(f"DEBUG: removing metric.  metric_name={metric_name}, labels={labels}")
                    # wipe the metric
                    utility.set(metric_name, None, labels)
            metrics_previous = metrics_current
        except Exception as e:
            print("EXCEPTION")
            print(e)
            pass

        time.sleep(int(refresh_rate))