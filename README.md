A tool for storing and displaying records from an Aranet4 sensor.

# Usage
## Finding the Device
First, you'll need to specify the mac address of your Aranet4 device. This can be done via either the `--mac` flag or `mac` in `config.ini`. If you don't know its mac address, calling `aranet --update` will initiate a scan for devices and will request all the readings on the device (the last ~2 weeks of records). The scan output (as shown below) provides the addresses of any nearby Aranet4 devices. The mac address should then be added to `config.ini` to skip the scanning process on future invocations.

<img src=images/find_device.png />

## Updating and Monitoring
In most cases, you'll want to set `--update` or `update = true` before calling `aranet.py`. This requests new records from the device and adds them to the sqlite database. To keep the program running and scanning for new readings, you'll need to set `--monitor` or `monitor = true`. This mode passively listens for bluetooth advertising packets from the Aranet4, parses the readings, and adds them to the database. On-screen, it displays the latest readings as well as some historical information to provide context.

<img src=images/monitor.png />

## Viewing Historical Stats
When invoked with `--no-short` or `short = false`, `aranet.py` will print out a table summarizing the historical records in the sqlite database.
<img src=images/table.png />

## Sending Push Notifications
`aranet.py` supports sending push notifications via Pushover. To get this working, you'll need to create a pushover application and copy both the application token and your user token to `token` and `user` in `config.ini` respectively. When invoked with `--notify` or `notify = true`, a push notification will be sent in any of the following cases:
- CO2 level is above 1,400 ppm and rising
- Temperature is below 50°F
- Temperature is above 90°F 
- There's a new highscore for CO2, temperature, humidity, or pressure

<img width="50%" height="50%" src=images/notification.jpg />

## Printing Readings
New readings can be automatically sent to a receipt printer. To do this, you'll need to have the connected printer configured using CUPS. Then set `printer name` to the printer's name in CUPS. Once invoked with either `--print` or `print = true`, all new readings will be automatically printed as long as the printer is connected. If supported by the receipt printer, printoffs will be cut.

<img width="50%" height="50%" src=images/printoff.jpg />

# Configuration
The file `config.ini` (see `config-example` for usage) stores all user configuration options. For most uses, you'll want to at least set `mac` and `file` to indicate the device and sqlite file to use respectively. Sending push notifications requires `token`, `user`, and `notify = true`.  Receipt printer integration requires `print = true` and `printer name`. Keep in mind that notifications and printouts are only sent on passive scans, so `monitor = true` is also required for both.

Command line flags also provide most of the same options (as shown below).

<img src=images/usage.png />