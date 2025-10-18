# Implementation Summary: Dynamic Device Discovery and Metric Management

## Changes Made

This document summarizes the implementation of dynamic device discovery with intelligent metric creation and connection tracking.

## Key Features Implemented

### 1. Continuous Discovery (`--discover` flag)

**What it does:**
- Runs device discovery on every collection cycle
- Detects new devices added during runtime
- Detects devices removed from configuration
- No restart required for configuration changes

**Implementation:**
- `discoverDevices()` called at startup (verbose) and every cycle (silent)
- `all_known_devices` tracks all devices ever seen
- `devices` tracks currently discovered devices
- Comparison determines new/removed devices

### 2. Intelligent Metric Creation

**Rule:** Metrics only created on first successful connection

**Why:** Prevents false alerts for:
- Devices configured but never used
- Devices offline at startup
- Test/placeholder configurations

**Implementation:**
```python
# Only create metrics when device successfully connects
if not name:
    if previous_status is True:
        # Was connected, now disconnected - keep metrics
        utility.set("alpaca_device_connected", 0, labels)
    # else: never connected - don't create metrics
else:
    # Connected - create/update all metrics
    utility.set("alpaca_device_connected", 1, labels)
```

### 3. State Tracking

**Device States:**
- `None`: Never connected
- `True`: Connected
- `False`: Was connected, now disconnected

**Implementation:**
```python
device_status = {}  # Key: "device_type/device_number", Value: True/False/None
```

### 4. Conditional Metrics Recording

**Rule:** Only record `alpaca_success_total`/`alpaca_error_total` for devices that have connected at least once

**Implementation:**
```python
def getValue(..., record_metrics=True):
    if response.status_code != 200:
        if record_metrics:
            utility.inc("alpaca_error_total", labels)
    # ...

# Usage for connectivity check
should_record = (previous_status is True)
name = getValue(..., should_record)

# Usage for metric collection (after connection)
# Uses default record_metrics=True
value = getValue(...)
```

### 5. Connection State Logging

**Messages:**
- `DISCOVERED`: Device found at startup
- `NEW DEVICE`: New device found during runtime
- `CONNECTED`: Device successfully responds (first time or after disconnect)
- `DISCONNECTED`: Device stops responding or removed from config

**Rules:**
- No spam: Only log state transitions
- Silent checks: Don't log when state unchanged

**Implementation:**
```python
if previous_status is not True:
    print(f"CONNECTED: {device_type}/{device_number}")
if previous_status is True:
    print(f"DISCONNECTED: {device_type}/{device_number} not responding")
```

## File Changes

### `src/alpaca-exporter.py`

**New/Modified Functions:**
- `discoverDevices(alpaca_base_url, verbose=True)` - Added verbose parameter
- `getValue(..., record_metrics=True)` - Added conditional metric recording
- `getValueCached(..., record_metrics=True)` - Passes through record_metrics flag

**New Variables:**
- `use_discovery` - Boolean flag for discovery mode
- `all_known_devices` - Tracks all devices ever seen
- `device_status` - Tracks connection state per device

**Logic Changes:**
- Discovery runs every cycle when `--discover` flag set
- Metric creation only on first successful connection
- State transitions tracked and logged appropriately

### `README.md`

Updated Auto-Discovery section to document:
- Continuous discovery behavior
- Dynamic device detection
- Connection state tracking
- Metric lifecycle (only created on first connection)

### `docs/requirements.md`

Comprehensive documentation of:
- Device states and transitions
- Logging rules for each transition
- Metric creation rules
- Success/error recording rules
- Implementation requirements
- Alert design guidelines

## Testing Scenarios

### Scenario 1: Device Online at Startup
```
1. Start exporter
2. DISCOVERED: device/0
3. CONNECTED: device/0
4. Metrics created
5. alpaca_success_total recorded
```

### Scenario 2: Device Offline at Startup
```
1. Start exporter
2. DISCOVERED: device/0
3. (no CONNECTED message)
4. No metrics created
5. No alpaca_success_total recorded
```

### Scenario 3: Device Connects After Startup
```
1. (device offline, no metrics)
2. Device comes online
3. CONNECTED: device/0
4. Metrics created
5. alpaca_success_total recorded
```

### Scenario 4: Device Disconnects
```
1. (device online, metrics exist)
2. Device goes offline
3. DISCONNECTED: device/0 not responding
4. alpaca_device_connected set to 0
5. alpaca_error_total recorded
6. Alert fires
```

### Scenario 5: Device Reconnects
```
1. (device offline, metrics exist at 0)
2. Device comes back
3. CONNECTED: device/0
4. alpaca_device_connected set to 1
5. alpaca_success_total recorded
6. Alert resolves
```

### Scenario 6: Hot-Plug New Device
```
1. (exporter running)
2. New device added to NINA
3. NEW DEVICE: device/1 added to monitoring
4. CONNECTED: device/1
5. Metrics created for device/1
```

## Benefits

1. **No False Alerts**: Offline/unused devices don't trigger alerts
2. **No Restart Required**: Hot-plug and config changes detected automatically
3. **Clean Metrics**: Only devices that have connected create metrics
4. **Accurate Tracking**: Success/error counters only for active devices
5. **Clear Logging**: State transitions clearly indicated in logs

## Monitoring Best Practices

### Alert on Disconnection
```promql
alpaca_device_connected{device_type="telescope"} == 0
```

This only fires for devices that were connected and went offline (intended behavior).

### Track API Reliability
```promql
rate(alpaca_error_total[5m]) / rate(alpaca_success_total[5m])
```

This tracks error rate only for devices that are being monitored (have connected).

### Device Inventory
```promql
count by (device_type) (alpaca_device_connected)
```

This counts devices that have connected at least once (actual inventory).

---

**Generated By:** Cursor (Claude Sonnet 4.5)


