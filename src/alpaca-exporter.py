import argparse
import copy
import json
import os
import time

import requests
import yaml
from cachetools import TTLCache, cached

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

# atributes not implemented for a device, could be device and driver specific.  so skip for that specific device.
# structure is {device_type: {device_number: [attributes]}}
skip_device_attribute = {}

DEBUG = False


def debug(message):
    # simply so I can easily control debug messages
    if DEBUG:
        print(message)


def loadConfigurations(path):
    for _, _, filenames in os.walk(path):
        for filename in filenames:
            with open(os.path.join(path, filename)) as file:
                c = yaml.load(file, Loader=yaml.FullLoader)
                t = filename.split(".")[0]
                configurations[t] = c


@cached(cache=TTLCache(maxsize=1024, ttl=60))
def getValueCached(alpaca_base_url, device_type, device_number, attribute, querystr="", record_metrics=True):
    debug(f"getValueCached(_, {device_type}, {device_number}, {attribute}, {querystr})")
    return getValue(alpaca_base_url, device_type, device_number, attribute, querystr, record_metrics)


def discoverDevices(alpaca_base_url, verbose=True):
    """
    Discover all configured devices via the Alpaca Management API.
    Returns a dictionary with device_type as key and list of device numbers as value.

    Args:
        alpaca_base_url: Base URL for Alpaca API
        verbose: If True, print discovery messages for all devices found
    """
    debug("discoverDevices(_)")
    discovered = {}

    # Extract base URL (without /api/v1) for management API
    # alpaca_base_url is like "http://127.0.0.1:11111/api/v1"
    # management API is at "http://127.0.0.1:11111/management/v1/configureddevices"
    base = alpaca_base_url.rsplit("/api/", 1)[0]
    management_url = f"{base}/management/v1/configureddevices"

    try:
        debug(f"management_url = {management_url}")
        response = requests.get(management_url)

        if response.status_code != 200:
            print(f"WARNING: Failed to discover devices via management API (status {response.status_code})")
            return discovered

        data = json.loads(response.text)

        if "Value" not in data:
            print("WARNING: Management API response missing 'Value' field")
            return discovered

        for device in data["Value"]:
            device_type = device["DeviceType"].lower()
            device_number = device["DeviceNumber"]
            device_name = device.get("DeviceName", "Unknown")
            unique_id = device.get("UniqueID", "")

            # Only add devices of supported types
            if device_type in device_types:
                if device_type not in discovered:
                    discovered[device_type] = []
                if device_number not in discovered[device_type]:
                    discovered[device_type].append(device_number)
                    if verbose:
                        print(f"DISCOVERED: {device_type}/{device_number} - {device_name}")
            elif verbose:
                print(f"SKIPPED: {device_type}/{device_number} - {device_name} (unsupported device type)")

    except Exception as e:
        print(f"ERROR: Failed to discover devices: {e}")

    return discovered


def getValue(alpaca_base_url, device_type, device_number, attribute, querystr="", record_metrics=True):
    debug(f"getValue(_, {device_type}, {device_number}, {attribute}, {querystr})")

    # check if we need to skip
    if device_type in skip_device_attribute and str(device_number) in skip_device_attribute[device_type] and attribute in skip_device_attribute[device_type][str(device_number)]:
        # yup, skip it
        debug(f"skipping attribute={attribute} for {device_type}/{device_number}")
        return None

    request_url = f"{alpaca_base_url}/{device_type}/{device_number}/{attribute}?{querystr}"
    debug(f"request_url = {request_url}")
    response = requests.get(request_url)

    labels = {
        "device_type": device_type,
        "device_number": device_number,
        "attribute": attribute,
    }

    if response.status_code != 200 or response.text is None or response.text == "":
        if record_metrics:
            utility.inc("alpaca_error_total", labels)
        return None
    data = json.loads(response.text)
    if "ErrorNumber" in data and data["ErrorNumber"] > 0:
        errNo = data["ErrorNumber"]
        if errNo == 1024:
            # indicates something is not implemented.  return None, do nothing.
            # NOTE do not log any warning, it will just spam output as we don't disable / remove the attribute.
            if device_type not in skip_device_attribute:
                skip_device_attribute[device_type] = {}
            if str(device_number) not in skip_device_attribute[device_type]:
                skip_device_attribute[device_type][str(device_number)] = []
            # add this attribute to be skipped
            skip_device_attribute[device_type][str(device_number)].append(attribute)
            return None
        if record_metrics:
            utility.inc("alpaca_error_total", labels)
        return None
    value = data["Value"]
    # convert boolean to int
    if isinstance(value, (bool)):
        value = int(value == True)
    if record_metrics:
        utility.inc("alpaca_success_total", labels)
    debug(f"==> {value}")
    return value


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export logs as prometheus metrics.")
    parser.add_argument("--port", type=int, help="port to expose metrics on, default: 9876")
    parser.add_argument("--alpaca_base_url", type=str, help="base alpaca v1 api (trailing slash will be stripped), default: http://127.0.0.1:11111/api/v1")
    parser.add_argument("--refresh_rate", type=int, help="seconds between refreshing metrics, default: 5")
    parser.add_argument("--discover", action="store_true", help="automatically discover all configured devices via Alpaca Management API")

    # add args for each supported device type
    for device_type in device_types:
        parser.add_argument(f"--{device_type}", type=int, action="append", help=f"{device_type} device number")

    # treat args parsed as a dictionary
    args = vars(parser.parse_args())

    alpaca_base_url = "http://127.0.0.1:11111/api/v1"
    if args.get("alpaca_base_url"):
        alpaca_base_url = args["alpaca_base_url"].rstrip("/")

    refresh_rate = 5
    if args.get("refresh_rate"):
        refresh_rate = args["refresh_rate"]

    port = 9876
    if args.get("port"):
        port = args["port"]

    # build array of device numbers for each supported device
    # either via discovery or from user-provided arguments
    use_discovery = args.get("discover")
    has_explicit_devices = any(args.get(dt) for dt in device_types)

    # Validate mutually exclusive modes
    if use_discovery and has_explicit_devices:
        print("ERROR: Cannot use --discover with explicit device specifications")
        print("Usage: Either use '--discover' OR specify devices (e.g., '--telescope 0 --camera 0')")
        exit(1)

    if not use_discovery and not has_explicit_devices:
        print("ERROR: Must specify either --discover or at least one device")
        print("Usage: '--discover' OR explicit devices (e.g., '--telescope 0 --camera 0')")
        exit(1)

    if use_discovery:
        print("Auto-discovering devices via Alpaca Management API...")
        devices = discoverDevices(alpaca_base_url)
        if len(devices) == 0:
            print("WARNING: No devices discovered. Retrying...")
    else:
        # verify user device numbers provided
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

            time.sleep(int(refresh_rate))

    if len(devices) == 0:
        print(f"ERROR: no devices configured, must supply at least one of: {device_types} or use --discover")
        os._exit(-1)

    # Track all devices we've ever seen (for marking as disconnected when they disappear)
    # Initialize with the devices we found at startup
    all_known_devices = {}
    if use_discovery:
        for device_type in devices.keys():
            all_known_devices[device_type] = devices[device_type].copy()

    # Track device connection status to detect state changes
    # Key format: "device_type/device_number", Value: True (connected) or False (disconnected)
    device_status = {}

    loadConfigurations("config/")

    # verify user input for devices

    # Start up the server to expose the metrics.
    utility.metrics(port)

    # collect metric name and array of labels from this and previous iterations (metrics_previous, metrics_current)
    # will compare w/ the previous iteration to see if we need to wipe any state
    metrics_previous = []
    while True:
        try:
            # Re-run discovery if in discovery mode to detect new/removed devices
            if use_discovery:
                discovered_devices = discoverDevices(alpaca_base_url, verbose=False)

                # Update all_known_devices with newly discovered devices
                for device_type in discovered_devices.keys():
                    if device_type not in all_known_devices:
                        all_known_devices[device_type] = []
                    for device_number in discovered_devices[device_type]:
                        if device_number not in all_known_devices[device_type]:
                            all_known_devices[device_type].append(device_number)
                            print(f"NEW DEVICE: {device_type}/{device_number} added to monitoring")

                # Update devices to currently discovered ones
                devices = discovered_devices

            metrics_current = []

            # First, handle all known devices (including ones that might be disconnected)
            for device_type in all_known_devices.keys() if use_discovery else devices.keys():
                # Get the list of device numbers to check
                device_numbers_to_check = all_known_devices[device_type] if use_discovery else devices[device_type]

                for device_number in device_numbers_to_check:
                    c = configurations[device_type]

                    if "metrics" not in c:
                        # no metrics, no point processing anything, go to next device
                        continue

                    # collect labels for device this iteration
                    labels = {
                        "device_type": device_type,
                        "device_number": device_number,
                    }

                    # Track device status for state change detection
                    device_key = f"{device_type}/{device_number}"
                    previous_status = device_status.get(device_key)

                    # Check if device is in currently discovered devices (for discovery mode)
                    # or verify it's reachable (for manual mode or as fallback)
                    is_currently_discovered = True
                    if use_discovery:
                        is_currently_discovered = device_type in devices and device_number in devices[device_type]

                    # If not discovered, mark as disconnected (only if previously connected)
                    if not is_currently_discovered:
                        if previous_status is True:
                            print(f"DISCONNECTED: {device_type}/{device_number} no longer discovered")
                            # Set to 0 so alerts can fire, and keep in metrics_current for tracking
                            utility.set("alpaca_device_connected", 0, labels)
                            metrics_current.append(["alpaca_device_connected", copy.deepcopy(labels)])
                        device_status[device_key] = False
                        continue

                    # Verify this is a valid device by getting its name
                    # Only record metrics if device has been connected before
                    should_record = previous_status is True
                    name = getValue(alpaca_base_url, device_type, device_number, "name", "", should_record)
                    if not name:
                        if previous_status is True:
                            print(f"DISCONNECTED: {device_type}/{device_number} not responding")
                            # Set to 0 so alerts can fire, and keep in metrics_current for tracking
                            utility.set("alpaca_device_connected", 0, labels)
                            metrics_current.append(["alpaca_device_connected", copy.deepcopy(labels)])
                        device_status[device_key] = False
                        continue
                    else:
                        # Device is connected - create/update metrics
                        utility.set("alpaca_device_connected", 1, labels)
                        metrics_current.append(["alpaca_device_connected", copy.deepcopy(labels)])

                        # Print CONNECTED when device becomes available (transitioning from any non-connected state)
                        if previous_status is not True:
                            print(f"CONNECTED: {device_type}/{device_number}")
                            # Reset skip list on connect (new connection may have different driver/capabilities)
                            skip_device_attribute.setdefault(device_type, {})[str(device_number)] = []
                        device_status[device_key] = True
                        labels.update({"name": name})
                        utility.set("alpaca_device_name", 1, labels)
                        metrics_current.append(["alpaca_device_name", copy.deepcopy(labels)])

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
                                metrics_current.append([metric_name, copy.deepcopy(labels)])
                            except:
                                pass

                    # get global labels
                    global_labels()

                    # SWITCH is a special device with an "id" query param.
                    if device_type == "switch":
                        # ids will be number of switch devices.
                        # id is 0 based, but range excludes the upper boundary.
                        # so using ids as is and upper bounds excluded is perfect.
                        ids = getValueCached(alpaca_base_url, device_type, device_number, "maxswitch")
                        for id in range(ids):
                            # also set 'id' on labels so it creates a unique key for metrics
                            labels["id"] = id
                            device_labels(f"id={id}")
                            device_metrcis(f"id={id}")
                    else:
                        # all other devices do not have query params
                        device_labels()
                        device_metrcis()

        except Exception as e:
            print("EXCEPTION")
            print(e)

        # handle cleanup of metrics.  this is the case when something stops reporting and we do not want stale metric.
        try:
            for m in metrics_previous:
                # if the cache has a value we didn't just collect we must remove the metric
                if m not in metrics_current:
                    metric_name = m[0]
                    labels = m[1]  # type: ignore[assignment]
                    debug(f"DEBUG: removing metric.  metric_name={metric_name}, labels={labels}")
                    # wipe the metric
                    utility.set(metric_name, None, labels)
            metrics_previous = metrics_current
        except Exception as e:
            print("EXCEPTION")
            print(e)

        time.sleep(int(refresh_rate))
