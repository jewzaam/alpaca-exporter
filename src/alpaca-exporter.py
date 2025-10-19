import argparse
import json
import os
import time

import requests
import yaml
from cachetools import TTLCache, cached

import constants
import exporter_core
import utility

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
            if device_type in constants.DEVICE_TYPES:
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

    labels = {
        "device_type": device_type,
        "device_number": device_number,
        "attribute": attribute,
    }

    try:
        response = requests.get(request_url)
    except Exception as e:
        # Network error, connection refused, timeout, etc.
        debug(f"Connection error: {e}")
        if record_metrics:
            utility.inc("alpaca_error_total", labels)
        return None

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
        value = int(value)
    if record_metrics:
        utility.inc("alpaca_success_total", labels)
    debug(f"==> {value}")
    return value


def main():
    """Main entry point for the exporter application."""
    parser = argparse.ArgumentParser(description="Export logs as prometheus metrics.")
    parser.add_argument("--port", type=int, help=f"port to expose metrics on, default: {constants.DEFAULT_PORT}")
    parser.add_argument("--alpaca_base_url", type=str, help=f"base alpaca v1 api, default: {constants.DEFAULT_ALPACA_BASE_URL}")
    parser.add_argument("--refresh_rate", type=int, help=f"seconds between refreshing metrics, default: {constants.DEFAULT_REFRESH_RATE}")
    parser.add_argument("--discover", action="store_true", help="automatically discover all configured devices via Alpaca Management API")

    # add args for each supported device type
    for device_type in constants.DEVICE_TYPES:
        parser.add_argument(f"--{device_type}", type=int, action="append", help=f"{device_type} device number")

    # treat args parsed as a dictionary
    args = vars(parser.parse_args())

    # Parse configuration with defaults
    alpaca_base_url, refresh_rate, port = exporter_core.parse_config_defaults(args)

    # Check if using discovery mode
    try:
        use_discovery = exporter_core.is_discover_mode(args)
    except ValueError as e:
        print(f"ERROR: {e}")
        exit(1)

    # Load device configurations
    loadConfigurations("config/")

    # Start Prometheus HTTP server
    utility.metrics(port)

    # Initialize state tracking
    all_known_devices = {}  # Tracks all devices ever seen (for discovery mode)
    device_status = {}  # Tracks connection status: "device_type/device_number" -> True/False/None
    metrics_previous = []

    # Main execution loop - handles both startup and runtime uniformly
    while True:
        try:
            # Get current device list based on mode
            if use_discovery:
                # Discovery mode: query Alpaca Management API
                devices = discoverDevices(alpaca_base_url, verbose=False)

                # Track newly discovered devices
                for device_type in devices.keys():
                    if device_type not in all_known_devices:
                        all_known_devices[device_type] = []
                    for device_number in devices[device_type]:
                        if device_number not in all_known_devices[device_type]:
                            all_known_devices[device_type].append(device_number)
                            print(f"NEW DEVICE: {device_type}/{device_number} added to monitoring")
            else:
                # Manual mode: use configured device list
                devices = exporter_core.get_manual_device_list(args)

                # In manual mode, all configured devices are "known"
                if not all_known_devices:
                    all_known_devices = {dt: devices[dt].copy() for dt in devices.keys()}

            metrics_current = []

            # Process devices based on mode
            device_list_to_process = all_known_devices if use_discovery else devices

            for device_type in device_list_to_process.keys():
                device_numbers = all_known_devices[device_type] if use_discovery else devices[device_type]

                for device_number in device_numbers:
                    # Process this device and collect metrics
                    device_metrics = exporter_core.process_device(
                        device_type,
                        device_number,
                        configurations,
                        alpaca_base_url,
                        use_discovery,
                        devices,
                        device_status,
                        skip_device_attribute,
                        getValue,
                        getValueCached,
                    )
                    metrics_current.extend(device_metrics)

        except Exception as e:
            print(f"EXCEPTION: {e}")

        # Clean up stale metrics
        try:
            exporter_core.cleanup_stale_metrics(metrics_previous, metrics_current)
            metrics_previous = metrics_current
        except Exception as e:
            print(f"EXCEPTION: {e}")

        time.sleep(int(refresh_rate))


if __name__ == "__main__":
    main()
