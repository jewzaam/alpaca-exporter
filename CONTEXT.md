# Project Context: ASCOM Alpaca Exporter

## Overview

The Alpaca Exporter is a Python-based Prometheus exporter that monitors ASCOM astronomical devices via the Alpaca API and exposes their telemetry as Prometheus metrics. It bridges the gap between amateur astronomy hardware (mounts, cameras, focusers, etc.) and modern observability infrastructure.

### Primary Use Case

Monitor observatory equipment health and status in real-time:
- Track device connectivity (connected vs disconnected vs never-connected)
- Collect telemetry (temperature, position, state)
- Enable alerting on device failures without false alarms
- Support dynamic device addition/removal in long-running observatory sessions

## Core Concepts

### ASCOM Alpaca

**ASCOM** (Astronomy Common Object Model) is a standard for controlling astronomical devices. **Alpaca** is the HTTP/JSON variant of ASCOM that enables network-based device control and monitoring.

Key characteristics:
- RESTful API at `http://host:port/api/v1/{device_type}/{device_number}/{attribute}`
- Management API for device discovery at `http://host:port/management/v1/configureddevices`
- Responses include `Value` (the data) and `ErrorNumber` (0 = success, >0 = error)
- ErrorNumber 1024 specifically means "not implemented" (driver doesn't support that attribute)

### Device Types

Supported ASCOM device types:
- `camera` - Imaging cameras (CCD/CMOS)
- `telescope` - Mounts and telescope control
- `rotator` - Camera rotators
- `focuser` - Electronic focusers
- `filterwheel` - Filter wheels
- `dome` - Observatory dome control
- `covercalibrator` - Dust covers and flat field calibrators
- `observingconditions` - Weather stations
- `safetymonitor` - Safety monitoring devices
- `switch` - Controllable switches/relays

Each device type has a configuration file in `config/{device_type}.yaml` that defines which attributes to monitor.

### Device Identification

Devices are uniquely identified by two labels:
- `device_type` - The ASCOM device type (e.g., "camera", "telescope")
- `device_number` - Zero-based index for multiple devices of same type (e.g., camera/0, camera/1)

This allows multiple devices of the same type to be monitored independently.

## Architecture

### Data Flow

```
┌──────────────────────────────────────────────────────────────────┐
│                         Alpaca Exporter                          │
│                                                                  │
│  ┌────────────────┐    ┌──────────────┐    ┌─────────────────┐ │
│  │   Discovery    │───▶│    Device    │───▶│    Metric       │ │
│  │   (optional)   │    │   Polling    │    │   Collection    │ │
│  └────────────────┘    └──────────────┘    └─────────────────┘ │
│         │                      │                     │          │
│         ▼                      ▼                     ▼          │
│  Management API         Device API              Prometheus      │
└──────────────────────────────────────────────────────────────────┘
         │                      │                     │
         ▼                      ▼                     ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Alpaca API Server                            │
│                  (ASCOM Remote / AlpycaDevice)                  │
└─────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│                      ASCOM Devices                              │
│        (Telescopes, Cameras, Focusers, etc.)                   │
└─────────────────────────────────────────────────────────────────┘
```

### Collection Cycle

The exporter runs in an infinite loop with configurable refresh rate (default: 5 seconds):

1. **Discovery Phase** (auto-discovery mode only)
   - Query management API for configured devices
   - Compare with known devices, detect new additions
   - Log "DISCOVERED" for new devices

2. **Polling Phase** (all devices)
   - Query `name` attribute to verify connectivity
   - If successful: device is CONNECTED
   - If failed: device is DISCONNECTED

3. **Metric Collection** (connected devices only)
   - Query each configured attribute
   - Update Prometheus metrics
   - Track success/error counts

4. **State Transition Logging**
   - Log only when state changes
   - DISCOVERED → CONNECTED → DISCONNECTED → CONNECTED...

5. **Metric Cleanup**
   - Remove stale metrics from previous cycle
   - Keep disconnected device metrics at last value

## Operating Modes

The exporter supports two mutually exclusive modes, selected at startup via CLI flags.

### Permissive Mode (Auto-Discovery)

**Flag:** `--discover`

**Behavior:**
- Queries Alpaca Management API every cycle for configured devices
- Only creates metrics for devices that successfully connect
- Devices that never connect are silently ignored (no metrics, no alerts)
- New devices added during runtime are automatically discovered
- **Philosophy:** "Monitor what's working, ignore what's not there"

**Use Case:** Dynamic environments where devices may be added/removed, or where some configured devices are intentionally offline.

**Example:**
```bash
python src/alpaca-exporter.py --discover --port 8001 --refresh_rate 10
```

### Strict Mode (Manual Configuration)

**Flags:** `--{device_type} N` (e.g., `--telescope 0 --camera 0`)

**Behavior:**
- Monitors only explicitly specified devices
- Creates metrics immediately for all specified devices (even if offline)
- Devices that never connect are treated as DISCONNECTED (metrics show 0)
- Offline devices increment error counters
- **Philosophy:** "Monitor these specific devices, alert if any fail"

**Use Case:** Production environments where specific devices are expected to be present and should alert if unavailable.

**Example:**
```bash
python src/alpaca-exporter.py --telescope 0 --camera 0 --rotator 0 --port 8001
```

### Mode Selection Enforcement

The exporter validates CLI arguments at startup:
- **Error if both:** `--discover` + explicit devices (e.g., `--telescope 0`)
- **Error if neither:** Must specify either `--discover` OR explicit devices
- This prevents ambiguous monitoring configurations

## Device State Management

### State Definitions

**Never Connected**
- Device exists (discovered or specified) but has never successfully responded
- Permissive mode: No metrics created
- Strict mode: `alpaca_device_connected=0`, no attribute metrics

**Connected**
- Device currently responding to `name` attribute query
- All metrics actively updating
- Success counters incrementing

**Disconnected**
- Device previously connected but now not responding
- `alpaca_device_connected=0` (triggers alerts)
- Attribute metrics frozen at last value
- Error counters incrementing

### State Transitions

```
Permissive Mode:
  [Device Discovered] ──first success──▶ [Connected] ◀──┐
                                              │           │
                                         fails│      │succeeds
                                              │      │
                                              ▼      │
                                         [Disconnected]──┘

Strict Mode:
  [Disconnected] ──first success──▶ [Connected] ◀──────┐
        ▲                                 │             │
        │                            fails│        │succeeds
        │                                 │        │
        └────────────────────────────[Disconnected]─────┘
```

### Connectivity Check

Device connectivity is determined by querying the `name` attribute:
- **Why `name`?** It's the only attribute guaranteed to exist on all ASCOM device types
- **When checked?** Every collection cycle (before any other attributes)
- **Success:** Device is connected, proceed with metric collection
- **Failure:** Device is disconnected, skip metric collection, increment error counter

### State Tracking Implementation

The exporter maintains two data structures:

**`device_status`** - Dictionary tracking current connection state
- Key: `"{device_type}/{device_number}"` (e.g., `"telescope/0"`)
- Value: `True` (connected), `False` (disconnected), `None` (never connected)

**`all_known_devices`** - Dictionary of all devices ever seen (discovery mode only)
- Key: `device_type`
- Value: List of `device_number` values
- Persists across cycles to track devices that disappear from discovery API

## Metric Lifecycle

### Creation Rules

**Permissive Mode:**
- Metrics created on **first successful connection**
- Never-connected devices: no metrics (prevents false alerts)

**Strict Mode:**
- Connection metric (`alpaca_device_connected`) created **immediately** for all specified devices
- Attribute metrics created only when attribute returns valid value

### Metric Types

**Connection Status:**
- `alpaca_device_connected{device_type, device_number}` - Gauge (1=connected, 0=disconnected)
- Created: Permissive (first connect), Strict (immediately)

**Device Attributes:**
- `alpaca_{device_type}_{attribute}{device_type, device_number, ...}` - Gauge
- Created: When attribute first returns valid value
- Updated: Every cycle while connected
- Frozen: At last value when disconnected
- Example: `alpaca_camera_ccdtemperature{device_type="camera", device_number="0"}`

**Success/Error Counters:**
- `alpaca_success_total{device_type, device_number, attribute}` - Counter
- `alpaca_success_created{device_type, device_number, attribute}` - Gauge (timestamp)
- `alpaca_error_total{device_type, device_number, attribute}` - Counter
- `alpaca_error_created{device_type, device_number, attribute}` - Gauge (timestamp)
- Permissive: Created/incremented only after first connection
- Strict: Created/incremented from first query attempt
- ErrorNumber 1024 does NOT increment counters (see Skip List)

**Device Name Label:**
- `alpaca_device_name{device_type, device_number, name}` - Gauge (always 1)
- Purpose: Makes device name searchable in Prometheus queries
- Created: When `name` attribute successfully queried

### Skip List (Error 1024 Handling)

**Problem:** ASCOM drivers don't always implement all attributes. Querying unimplemented attributes returns ErrorNumber 1024 every cycle, creating noise.

**Solution:** Skip list maintains per-device list of attributes to stop querying.

**Structure:** `skip_device_attribute[device_type][device_number] = [attribute_names]`

**Behavior:**
- When attribute returns ErrorNumber 1024: Add to skip list
- Next cycle: Attribute query returns `None` immediately (no API call)
- Does NOT increment error counter (not a real error)
- No metric created for skipped attributes

**Reset Trigger:** Skip list cleared when device transitions to CONNECTED state
- **Why?** Device may reconnect with different driver that DOES implement the attribute
- Allows capability discovery to adapt to driver changes

### Non-1024 Error Handling

Errors other than 1024 (temporary failures, sensor read errors, etc.):
- **Do** increment error counter
- **Do NOT** add to skip list
- **Retry** next cycle
- If subsequent query succeeds: Create/update metric, increment success counter

## Key Files

### Source Code

**`src/alpaca-exporter.py`** (449 lines)
- Main application entry point
- CLI argument parsing and validation
- Discovery and device polling loops
- State management and metric collection
- Special handling for Switch devices (id parameter required)

**`src/utility.py`** (referenced but not shown)
- Prometheus metric registration and manipulation
- `metrics(port)` - Start HTTP server
- `set(metric_name, value, labels)` - Set gauge value
- `inc(metric_name, labels)` - Increment counter

### Configuration

**`config/global.yaml`**
- Labels applied to ALL device metrics
- Must be attributes available on all device types
- Typically just device identification labels

**`config/{device_type}.yaml`** (one per device type)
- `metric_prefix` - Prepended to all metric names (e.g., `alpaca_telescope_`)
- `labels` - Device-specific labels
- `metrics` - List of attributes to monitor
- Each attribute can specify:
  - `alpaca_name` - ASCOM attribute name (required)
  - `metric_name` - Override for Prometheus metric (optional)
  - `cached` - Cache duration in seconds (optional, default: no cache)

**Missing config file = fatal error** - System terminates if device type lacks configuration

### Documentation

**`docs/requirements.md`**
- Authoritative business and functional requirements
- What the system must do (not how)
- Cross-referenced with code and test plans

**`docs/design.md`**
- Work-in-progress design documentation
- Diagrams and implementation details removed from requirements

**`docs/unit-test-plan-general.md`**
- Tests for behavior common to both operating modes
- Device state transitions, skip lists, multiple devices, server unavailability

**`docs/unit-test-plan-discover.md`**
- Tests specific to auto-discovery (permissive) mode
- Never-connected devices, runtime discovery

**`docs/unit-test-plan-manual.md`**
- Tests specific to manual configuration (strict) mode
- Never-connected devices create metrics, missing config file

**`README.md`**
- User-facing setup and usage documentation
- Configuration file format
- Troubleshooting guide

## Design Decisions and Constraints

### Why Two Operating Modes?

**Problem:** Different use cases have conflicting requirements.

**Personal/Development Use:** Observatory setup changes frequently. Devices added/removed between sessions. Want to monitor what's working without alerts for offline devices.

**Production/Critical Use:** Specific devices expected to be present. Should alert if any fail.

**Solution:** Two modes with different philosophies about failure handling.

### Why Require Explicit Configuration Files?

**Problem:** Alpaca API doesn't provide introspection - no way to discover which attributes a device supports.

**Solution:** Pre-define attributes in YAML files. This also allows:
- Metric name translation (ASCOM → Prometheus conventions)
- Caching hints for expensive/slow-changing attributes
- Custom labels per device type

**Trade-off:** Must update config files when new device types added. But this is rare (ASCOM device types are stable).

### Why Metric Name Translation?

**Problem:** ASCOM attribute names don't follow Prometheus naming conventions.
- ASCOM: `CoolerOn` (PascalCase)
- Prometheus: `cooler_on` (snake_case)

**Solution:** Allow `metric_name` override in configuration files.
- Maintains Prometheus ecosystem compatibility
- Allows descriptive names (e.g., `cooleron` → `cooling`)

### Why Cache Some Attributes?

**Problem:** Some attributes (like device name, driver info) change rarely but are queried every cycle.

**Solution:** Optional TTL cache (60 seconds) via `cachetools`.
- Reduces API calls for static/slow-changing values
- Configurable per-attribute via `cached: 1` in YAML
- Don't cache telemetry that changes frequently (temperature, position)

### Why Special Handling for Switch Devices?

**Problem:** Switch devices are unique in ASCOM - they represent multiple switches under one device. Each switch requires an `id` query parameter.

**Solution:** Hardcoded special case in main loop:
1. Query `maxswitch` to get number of switches
2. Loop through each ID (0 to maxswitch-1)
3. Query attributes with `?id=N` parameter
4. Add `id` label to metrics for uniqueness

**Trade-off:** Not configuration-driven like other devices, but Switch devices are rare and behavior is standardized.

### Why Reset Skip List on Reconnect?

**Problem:** Device may disconnect and reconnect with a different ASCOM driver (e.g., switching from Simulator to real hardware, or upgrading driver).

**Solution:** Clear skip list when device transitions to CONNECTED.
- New driver may implement previously unimplemented attributes
- Allows "capability discovery" to adapt to environment changes
- Minimal cost: Only one extra API call per previously-skipped attribute

### Why Wait Indefinitely for Alpaca Server at Startup?

**Problem:** Exporter may start before Alpaca server (e.g., both starting at boot).

**Solution:** Retry loop at startup, no timeout.
- **Startup:** Wait indefinitely for server (no metrics created)
- **Runtime:** If server disappears, mark all devices disconnected but keep running

**Rationale:** In observatory automation, services start in parallel. Better to wait than fail.

### Why `name` Attribute for Connectivity Check?

**Problem:** Need a reliable way to test if device is responsive.

**Solution:** Use `name` attribute.
- **Universal:** Every ASCOM device type has a `name` attribute
- **Lightweight:** Returns simple string, fast to query
- **Authoritative:** If `name` fails, device is definitively offline

**Alternative considered:** Ping/connection test without attribute query. Rejected because Alpaca API doesn't expose this, and `name` query serves the purpose.

## Implementation Notes

### State Change Logging Philosophy

**Log only transitions, not steady states.**
- `DISCOVERED` - Only at startup (permissive mode)
- `CONNECTED` - When transitioning from any non-connected state
- `DISCONNECTED` - When transitioning from connected state
- No logs when state remains unchanged

**Rationale:** Reduces log spam during normal operation. State changes are what matter for troubleshooting.

### Metric Recording Control

**`record_metrics` parameter in `getValue()`:**
- Controls whether success/error counters are incremented
- Set to `True` only if device has been connected at least once (permissive mode)
- Always `True` in strict mode (all queries count from startup)

**Purpose:** Implements different counter creation rules between modes without duplicating query logic.

### Discovery Loop Verbosity

**`verbose` parameter in `discoverDevices()`:**
- `verbose=True`: Print "DISCOVERED" for each device (startup only)
- `verbose=False`: Silent (runtime polling)

**Purpose:** Avoid spamming logs with "DISCOVERED" every cycle. Only print discoveries at startup when they're informative.

### Error Handling Strategy

**Philosophy:** Keep running, log errors, but don't crash.

**Exception handling:**
- Top-level try-except wraps entire collection cycle
- Prints "EXCEPTION" and error message
- Continues to next cycle
- Individual device failures don't affect other devices

**Alpaca Server Failures:**
- Startup: Retry indefinitely
- Runtime: Mark all devices disconnected, keep polling

**Rationale:** Observatory sessions run for hours/days. Better to keep partial monitoring than crash completely.

### Metric Cleanup

**Problem:** If device stops reporting an attribute (e.g., switch disabled), stale metric remains.

**Solution:** Track metrics collected in current cycle vs previous cycle.
- After each cycle, compare `metrics_current` vs `metrics_previous`
- Metrics in previous but not current: Call `utility.set(metric_name, None, labels)` to remove
- Update `metrics_previous = metrics_current`

**Data Structure:** `metrics_previous` is list of `[metric_name, labels_dict]` tuples.

### Boolean to Integer Conversion

**ASCOM returns:** Boolean values for attributes like `connected`, `cooleron`
**Prometheus expects:** Numeric values

**Solution:** Convert `True→1`, `False→0` in `getValue()`:
```python
if isinstance(value, (bool)):
    value = int(value == True)
```

**Rationale:** Prometheus gauges must be numeric. Binary states represented as 0/1.

## Future Considerations

### Potential Enhancements

**Hybrid Mode:** Combine auto-discovery with required device list
- `--discover --require telescope/0 camera/0`
- Discover everything, but alert if specific devices missing
- Not currently implemented (out of scope)

**Dynamic Configuration:** Allow runtime config reload without restart
- Currently: Config loaded once at startup
- Would enable: Add new device types without downtime

**Metric Metadata:** Expose attribute units and descriptions
- ASCOM doesn't standardize metadata format
- Would require per-device-type metadata files

**Historical Data:** Track device connectivity history
- Currently: Only current state
- Could add: Uptime/downtime percentages, connection count

### Known Limitations

**No Control:** Exporter is read-only. Cannot control devices (by design).

**No Alerting:** Exporter only exposes metrics. Alerting is Prometheus/Alertmanager's job.

**Single Server:** Only monitors one Alpaca server. Multiple servers require multiple exporter instances.

**No Authentication:** Assumes Alpaca API is unauthenticated (typical for local observatory network).

**Static Device Types:** Supported device types hardcoded. New ASCOM device types require code change.

---

**Generated By:** Cursor (Claude Sonnet 4.5)

