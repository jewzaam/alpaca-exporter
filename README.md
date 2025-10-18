# alpaca-exporter

Export metrics from ASCOM devices via Alpaca in a form that Prometheus can scrape.

## Setup

1. install requirements
2. install all ASCOM drivers needed
3. install Alpaca (ASCOM Remote)
4. configure ASCOM Remote

```shell
# install required modules
pip3 install -r requirements.txt
```

## Usage

Run the exporter with the port to expose metrics on, the base alpaca URL, and either manually specify device IDs or use auto-discovery.

### Auto-Discovery (Recommended)

The exporter can automatically discover all configured devices via the Alpaca Management API. Discovery runs continuously on every collection cycle, enabling dynamic device detection:

- **New devices**: Automatically added to monitoring when discovered (prints `NEW DEVICE` message)
- **Connection status**: Devices report their connection state
  - `DISCOVERED`: Device found via management API (startup only)
  - `CONNECTED`: Device successfully responds (first connection or after being offline)
  - `DISCONNECTED`: Device becomes unavailable (not responding or removed from configuration)
- **Metric lifecycle**: 
  - Metrics are **only created** when a device first successfully connects
  - Discovered but never-connected devices will **not** have any metrics (preventing false alerts)
  - When a previously-connected device disconnects, `alpaca_device_connected` is set to 0 to trigger alerts

```shell
python src/alpaca-exporter.py --port 8001 --alpaca_base_url http://127.0.0.1:11111/api/v1 --refresh_rate 10 --discover
```

This is ideal for environments where devices may be added or removed dynamically.

### Manual Device Configuration

Alternatively, you can manually specify which devices to monitor. If you have more than one of a type of device simply use the device type argument multiple times.

The following runs the exporter for one telescope and two cameras:

```shell
python src/alpaca-exporter.py --port 8001 --alpaca_base_url http://127.0.0.1:11111/api/v1 --refresh_rate 10 --telescope 0 --camera 0 --camera 1
```

## Verify

In your favorite browser look at the metrics endpoint.  If it's local, you can use http://localhost:8001

# Configuration Files

## global.yaml

Configure global labels.  They are applied to every single device metric.  Each of the configured labels must be available for _all_ devices!

```yaml
labels:
- alpaca_name: name of the alpaca property [required]
  label_name: override alpaca name to something else [optional]
  cached: 1 # if 1, values are cached for 60 seconds
```

## device_type.yaml

The filename is used to group configuration for a specific device.

Three things to configure:
1. base metric name
1. device specific labels
1. the metrics

The `metric_prefix` is prepended to each metric name.

```yaml
metric_prefix: alpaca_telescope_
```

The device specific labels behave the same as global.yaml labels, except specific to this device.  See [global.yaml](#global-yaml).

The metrics are similar to labels but result in a metric at the end.

```yaml
metrics:
- alpaca_name: name of the alpaca property [required]
  metric_name: override alpaca name to something else [optional]
  cached: 1 # if 1, values are cached for 60 seconds
```

And the `metric_prefix` is prepended.  For example, the `alpaca_telescope_tracking_rage` metric is created from:

```yaml
metric_prefix: alpaca_telescope_

metrics:
- alpaca_name: trackingrate
  metric_name: tracking_rate
  cached: 1
```

Note it overrides the property in Alpaca and caches the result (not expected to change often).

# Troubleshooting

## Connection Issues

On startup the exporter lists what could be connected and what couldn't.  In addition, there is a `alpaca_device_connected` metric that indicates what is connected over time.

## Missing Metrics

PR's welcome!  The `config/` directory contains all the configurations.  Everything is device specific with the exception of `global.yaml`.  The global configuration applies labels to every metric you export.

## Switch Configuration

Unfortunetly switches are special.  There is an `id` query param required for getting the individual switch device metrics.  This is managed in the exporter code and you do not have to worry about it in the configuration.  The downside is every switch device is queried, which may result in higher traffic.