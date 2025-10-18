import os
import re
import socket
import time
from threading import Thread

import prometheus_client

gauges = {}
counters = {}
filesWatched = []

DEBUG = False


def sorted_keys(data):
    if data is None or len(data.keys()) == 0:
        return None
    return sorted(data)


def sorted_values(data):
    keys = sorted_keys(data)
    if keys is None:
        return None
    values = []
    for key in keys:
        values.append(data[key])
    return values


def setDebug(value):
    global DEBUG
    DEBUG = value


def debug(message):
    if DEBUG == True:
        print(message)


def enrichLabels(labelDict):
    if labelDict is None:
        return labelDict
    if "host" in labelDict:
        return labelDict
    host = socket.gethostname().lower()
    labelDict.update(
        {
            "host": host,
        }
    )


def findNewestFile(directory, logfileregex):
    now = time.time()
    filemtimes = {}
    for root, _, files in os.walk(directory, topdown=False):
        for name in files:
            m = re.match(logfileregex, name)
            if m is not None:
                filename = os.path.join(root, name)
                mtime = os.path.getmtime(filename)
                if mtime >= now - 30 * 60:
                    # file was modified in the last 30 minutes.  consider it. else ignore it.
                    filemtimes[mtime] = filename
    s = sorted(filemtimes.items())
    if len(s) == 0:
        return None
    return s[-1][1]


def watchFile(filename, frequencySeconds, callback):
    try:
        if filename in filesWatched:
            print(f"Attempted create duplicate watchFile on: {filename}")

        print(f"Creating watchFile on: {filename}")

        filesWatched.append(filename)
        with open(filename) as f:
            while True:
                line = f.readline()
                if not line:
                    time.sleep(frequencySeconds)
                else:
                    callback(filename, line)
    except Exception as e:
        # print the error in case it helps debug and remove the file from being tracked
        print(e)
        if filename in filesWatched:
            filesWatched.remove(filename)


def watchDirectory(logdir, logfileregex, frequencySeconds, callback):
    while True:
        newestLogFile = findNewestFile(logdir, logfileregex)
        while newestLogFile is None or newestLogFile in filesWatched:
            time.sleep(frequencySeconds)
            newestLogFile = findNewestFile(logdir, logfileregex)

        # got a new log file, attempt to start watching it
        t = Thread(target=watchFile, args=(newestLogFile, frequencySeconds, callback), daemon=True)
        t.start()


def getGauge(name, description, labelDict):
    if name in gauges:
        gauge = gauges[name]
    else:
        print(f"Creating Gauge: {name}({labelDict})")
        gauge = prometheus_client.Gauge(name, description, labelDict)
        gauges[name] = gauge
    return gauge


def getCounter(name, description, labelDict):
    if name in counters:
        counter = counters[name]
    else:
        print(f"Creating Counter: {name}")
        counter = prometheus_client.Counter(name, description, labelDict)
        counters[name] = counter
    return counter


def set(name, value, labelDict):
    enrichLabels(labelDict)
    gauge = getGauge(name, "", sorted_keys(labelDict))
    debug(f"utility.set({name}, {value}, {labelDict})")
    if len(labelDict.keys()) > 0:
        if value is not None:
            gauge.labels(*sorted_values(labelDict)).set(value)
        else:
            gauge.remove(*sorted_values(labelDict))
    else:
        # cannot clear value if there is no label, just let the error propogate up
        gauge.set(value)


def add(name, value, labelDict):
    enrichLabels(labelDict)
    gauge = getGauge(name, "", sorted_keys(labelDict))
    debug(f"utility.add({name}, {value}, {labelDict})")
    if len(labelDict.keys()) > 0:
        if value is not None:
            gauge.labels(*sorted_values(labelDict)).inc(value)
        else:
            gauge.remove(*sorted_values(labelDict))
    else:
        gauge.inc(value)


def inc(name, labelDict):
    enrichLabels(labelDict)
    counter = getCounter(name, "", sorted_keys(labelDict))
    debug(f"utility.inc({name}, {labelDict})")
    if len(labelDict.keys()) > 0:
        counter.labels(*sorted_values(labelDict)).inc()
    else:
        counter.inc()


def dec(name, labelDict):
    enrichLabels(labelDict)
    counter = getCounter(name, "", sorted_keys(labelDict))
    debug(f"utility.dec({name}, {labelDict})")
    if len(labelDict.keys()) > 0:
        counter.labels(*sorted_values(labelDict)).dec()
    else:
        counter.dec()


def metrics(port):
    prometheus_client.start_http_server(port)
