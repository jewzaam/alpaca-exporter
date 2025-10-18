# Code Fixes Needed

**Status:** These fixes are required to make `src/alpaca-exporter.py` match the requirements and test plans.

**Priority:** High - These are critical bugs that affect production use.

---

## Critical Fixes

### 1. Startup Retry Loop (Both Modes)

**Problem:** Exporter exits if Alpaca server unavailable or no devices found at startup.

**Current Code (lines 204-233):**
```python
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
            done = True  # <-- BUG: Always exits after one try
        except Exception as e:
            print("EXCEPTION")
            print(e)
            pass
    
        time.sleep(int(refresh_rate))

if len(devices) == 0:
    print(f"ERROR: no devices configured, must supply at least one of: {device_types} or use --discover")
    os._exit(-1)  # <-- BUG: Should retry, not exit
```

**Issues:**
1. Discovery mode: Prints warning but then exits via `os._exit(-1)` at line 233
2. Manual mode: Sets `done=True` immediately (line 223), so only tries once
3. Both modes: `os._exit(-1)` kills the process instead of retrying

**Required Behavior (from requirements):**
- **Startup:** Retry in loop indefinitely, do not create metrics, wait for server
- **Discovery mode:** If no devices found, keep looping
- **Manual mode:** If specified devices offline, keep looping

**Fix:**
```python
# Startup loop - retry until we have valid connection
startup_complete = False
while not startup_complete:
    try:
        if use_discovery:
            print("Auto-discovering devices via Alpaca Management API...")
            devices = discoverDevices(alpaca_base_url)
            if len(devices) > 0:
                startup_complete = True
            else:
                print("WARNING: No devices discovered. Retrying in {refresh_rate} seconds...")
                time.sleep(refresh_rate)
        else:
            # Manual mode - verify devices exist
            devices = {}
            all_found = True
            for device_type in device_types:
                if args[device_type]:
                    devices[device_type] = args[device_type]
                    for device_number in devices[device_type]:
                        name = getValue(alpaca_base_url, device_type, device_number, "name", "", False)
                        if name:
                            print(f"CONNECTED: {device_type}/{device_number}")
                        else:
                            print(f"DISCONNECTED: {device_type}/{device_number}")
                            all_found = False
            # In manual mode, proceed even if some devices offline (they'll show as disconnected)
            if len(devices) > 0:
                startup_complete = True
            else:
                print(f"ERROR: No devices specified")
                time.sleep(refresh_rate)
    except Exception as e:
        print(f"ERROR connecting to Alpaca server: {e}")
        print(f"Retrying in {refresh_rate} seconds...")
        time.sleep(refresh_rate)
```

---

### 2. Manual Mode Startup Logging

**Problem:** Uses "SUCCESS: found" / "FAILURE: unable to find" instead of "CONNECTED" / "DISCONNECTED"

**Current Code (lines 220-222):**
```python
if name:
    print(f"SUCCESS: found {device_type}/{device_number}")
else:
    print(f"FAILURE: unable to find {device_type}/{device_number}")
```

**Required (from test plan):**
- Log: `CONNECTED: device/0` if device responds
- Log: `DISCONNECTED: device/0` if device doesn't respond

**Fix:** Change log messages to use CONNECTED/DISCONNECTED (see fix #1 above).

---

### 3. Manual Mode Metrics at Startup

**Problem:** Manual mode should create disconnected metrics immediately for all specified devices, even if they never connect.

**Current Code:** Only creates metrics after first successful connection (same as discovery mode).

**Required (from requirements):**
- **Manual mode:** Create `alpaca_device_connected=0` immediately for all specified devices
- **Discovery mode:** Only create metrics after first successful connection

**Fix:** Need to distinguish modes when creating initial metrics. After startup loop, for manual mode only:
```python
if not use_discovery:
    # Manual mode: Create initial disconnected metrics for all specified devices
    for device_type in devices.keys():
        for device_number in devices[device_type]:
            labels = {
                "device_type": device_type,
                "device_number": device_number,
            }
            device_key = f"{device_type}/{device_number}"
            if device_status.get(device_key) != True:
                # Device is disconnected, create metrics
                utility.set("alpaca_device_connected", 0, labels)
                device_status[device_key] = False
```

---

## Medium Priority Fixes

### 4. Discovery Mode Initial Device Tracking

**Problem:** Discovery mode doesn't initialize `all_known_devices` at startup, only in main loop.

**Current Code (lines 237-240):**
```python
if use_discovery:
    for device_type in devices.keys():
        all_known_devices[device_type] = devices[device_type].copy()
```

**Issue:** This happens after startup validation, but should happen during startup so devices are tracked from the beginning.

**Fix:** Move initialization into startup loop after successful discovery.

---

### 5. Manual Mode `all_known_devices` Initialization

**Problem:** Manual mode doesn't initialize `all_known_devices` at all.

**Current Code:** Only discovery mode initializes it.

**Required:** Manual mode should also populate `all_known_devices` so the main loop processing works correctly.

**Fix:**
```python
else:
    # Manual mode: all specified devices are "known"
    for device_type in devices.keys():
        all_known_devices[device_type] = devices[device_type].copy()
```

---

## Test When Fixed

Run these test scenarios after fixes:

1. **Alpaca server offline at startup** → Should retry indefinitely
2. **Discovery mode, no devices** → Should retry indefinitely
3. **Manual mode, all devices offline** → Should show DISCONNECTED, create metrics, continue
4. **Manual mode, some devices offline** → Should show mix of CONNECTED/DISCONNECTED
5. **Discovery mode startup** → Should only log DISCOVERED and CONNECTED (not SUCCESS/FAILURE)

---

## Summary of Changes Needed

**File:** `src/alpaca-exporter.py`

**Lines to modify:**
- Lines 204-233: Replace entire startup section with retry loop
- Add manual mode initial metrics creation after startup
- Fix `all_known_devices` initialization for both modes

**Estimated impact:** ~50 lines changed/added

**Testing:** Requires mock Alpaca server or test environment to validate all scenarios

---

**Generated By:** Cursor (Claude Sonnet 4.5)

