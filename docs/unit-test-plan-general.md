# Test Plan: General Device Behavior

## Purpose

Validate device behavior that is common to both auto-discovery and manual configuration modes.

## Scope

These tests apply regardless of how devices are discovered or specified. The behavior is the same whether using `--discover` or `--device_type N`.

---

## Behavior Tests

### Device Stays Connected

**Given:** Device online for multiple cycles  
**Expected:**
- No state transition logs
- Metrics update every cycle
- Success counter increments each cycle
- Error counter is not incremented

---

### Connected Device Disconnects

**Given:** Device connected, then goes offline  
**Expected:**
- Log: `DISCONNECTED: device/0`
- Metric: `alpaca_device_connected=0`
- Metric created: `alpaca_error_total`, `alpaca_error_created`
- Error counter incremented for failed name query
- Other metrics keep last value (i.e. `alpaca_device_name`)

---

### Disconnected Device Reconnects

**Given:** Device was connected, disconnected, then connects again  
**Expected:**
- Log: `CONNECTED: device/0`
- Metric: `alpaca_device_connected=1`
- Success counter resumes incrementing
- All device metrics resume updating
- `alpaca_success_created` does not change (metric already exists)
- `alpaca_error_total` no longer incrementing
- `alpaca_error_created` does not change

---

### Not-Implemented Attribute (Error 1024)

**Given:** Telescope connected, query for `declinationrate` returns ErrorNumber 1024 (driver doesn't support tracking rates)
**Expected:**
- No error counter incremented
- Attribute added to skip list (will be skipped in future cycles)
- Next cycle: `declinationrate` not queried (skipped)
- Other attributes (altitude, azimuth, declination, etc.) continue to be queried normally

---

### Attribute Error (Non-1024)

**Given:** Camera connected, query for `ccdtemperature` returns ErrorNumber 1234 (temporary sensor read failure)
**Expected:**
- Error counter incremented for failed query
- Attribute NOT added to skip list
- Next cycle: `ccdtemperature` queried again (retry)
- Other attributes continue to be queried normally
- If subsequent query succeeds: metric created/updated, success counter incremented

---

### Skip List Reset on Connect

**Given:** Telescope connects with Driver A, then disconnects and reconnects with Driver B (different driver with different capabilities)
**Expected:**
1. Telescope connects with Driver A
2. Log: `CONNECTED: telescope/0`
3. Skip list reset to empty
4. Query `declinationrate` → returns ErrorNumber 1024 (not implemented in Driver A)
5. Attribute added to skip list
6. Next cycle: `declinationrate` not queried (skipped)
7. Telescope disconnects
8. Log: `DISCONNECTED: telescope/0`
9. Telescope reconnects with Driver B
10. Log: `CONNECTED: telescope/0`
11. Skip list reset to empty for telescope/0
12. Next cycle: `declinationrate` queried again (not skipped anymore)
13. Query returns valid value: `0.0` (implemented in Driver B)
14. Success counter incremented for `declinationrate` attribute
15. Metric created: `alpaca_telescope_declination_rate{...}=0.0`

---

### Multiple Devices with Independent State Transitions

**Purpose:** Verify that device state transitions are independent and don't interfere with each other.

**Given:** Three connected devices, then:
1. Rotator/0 stays connected throughout
2. Telescope/0 disconnects
3. Camera/0 disconnects
4. Telescope/0 reconnects

**Expected:**

**Initial State (all connected):**
- All three devices: `CONNECTED` logged, metrics created
- `alpaca_device_connected=1` for all three
- All three updating metrics normally

**Cycle 2: Telescope disconnects**
- Log: `DISCONNECTED: telescope/0`
- Metric: `alpaca_device_connected{telescope/0}=0`
- Counter: `alpaca_error_total{telescope/0}` increments
- Rotator and camera: Continue normally, no logs, no state change

**Cycle 3: Camera disconnects**
- Log: `DISCONNECTED: camera/0`
- Metric: `alpaca_device_connected{camera/0}=0`
- Counter: `alpaca_error_total{camera/0}` increments
- Rotator: Continues normally
- Telescope: Remains disconnected, error counter continues incrementing

**Cycle 4: Telescope reconnects**
- Log: `CONNECTED: telescope/0`
- Metric: `alpaca_device_connected{telescope/0}=1`
- Counter: `alpaca_success_total{telescope/0}` resumes, skip list reset
- Rotator: Continues normally
- Camera: Remains disconnected, error counter continues incrementing

**Key Validation:**
- Each device maintains its own independent state
- State transitions of one device don't affect others
- Error/success counters track correctly per device
- No cross-contamination of metrics or logs

---

### Multiple Devices of Same Type

**Given:** Two cameras configured/discovered: camera/0 and camera/1
**Expected:**
- Both devices monitored independently
- Separate metrics for each: `alpaca_device_connected{device_type="camera",device_number="0"}` and `{device_number="1"}`
- camera/0 disconnects: only camera/0 metrics affected, camera/1 continues normally
- camera/0 reconnects: only camera/0 transitions, camera/1 unaffected
- Skip lists maintained separately: camera/0 skips attribute X, camera/1 still queries it
- Success/error counters track independently per device_number

---

### Alpaca Server Unavailable at Startup

**Given:** Exporter starts, but Alpaca server at configured URL is not reachable
**Expected:**
- No devices discovered/queried
- No metrics created
- Exporter retries in loop (does not exit)
- Once server becomes available: Continue with normal operation

---

### Alpaca Server Becomes Unavailable During Runtime

**Given:** Exporter running with multiple connected devices, Alpaca server becomes unreachable
**Expected:**
- All devices transition to disconnected state
- Log: `DISCONNECTED: device/0` for each device
- Metric: `alpaca_device_connected=0` for all devices
- Error counters increment for all devices
- Exporter continues running and retrying
- Once server becomes available: Devices reconnect normally

---

**Generated By:** Cursor (Claude Sonnet 4.5)

