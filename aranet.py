#!/usr/bin/env venv/bin/python
import aranet4
import sys
import tzlocal
import argparse
import sqlite3
import configparser
import http.client
import urllib
import asyncio
from datetime import datetime, timezone, timedelta
from enum import Enum


class DisplayMode(Enum):
    """
    Represents the type of display we're outputting to
    """
    terminal = 1
    notification = 2


class Reading:
    """
    A single sensor reading
    """
    def __init__(self, *, date: datetime, co2: float, temperature: float, humidity: float, pressure: float,
        battery: float = None, status = None, interval: int = None):
        self.date = date
        self.co2 = co2
        self.temperature = temperature
        self.humidity = humidity
        self.pressure = pressure

        # optionals
        self.battery = battery
        self.status = status
        self.interval = interval


    def __getitem__(self, item: str):
        return getattr(self, item)


    def age(self) -> int:
        """
        Number of seconds since this reading was taken
        """
        now = datetime.now().astimezone(timezone.utc)
        delta = now - self.date
        return round(delta.total_seconds())


    def show_change(self, prev: float, curr: float) -> str:
        """
        Visually indicates the change in a value
        """
        if prev is None:
            prev = curr

        delta = curr - prev
        symbol = '⇵'
        if delta > 0:
            symbol = '↑'
        elif delta < 0:
            symbol = '↓'
        return f"{symbol} {delta:.01f}"


    def display(self, mode: DisplayMode, previous: 'Reading' = None) -> str:
        """
        Represents the reading as a string suitable for the specified display mode.
        If the previous reading is specified, the change in each value is shown
        """
        if previous is None:
            previous = Reading(
                date = None,
                co2 = None,
                temperature = None,
                humidity = None,
                pressure = None,
            )

        output = '\n' if mode == DisplayMode.terminal else ''

        co2 = self.co2
        if self.status is not None:
            co2 = colorize(self.status.name.lower(), self.co2, mode)
        output += f"  CO2:           {co2} ppm {self.show_change(previous.co2, self.co2)}" + '\n'

        output += f"  Temperature:   {(self.temperature):.01f}°F {self.show_change(previous.temperature, self.temperature)}" + '\n'
        output += f"  Humidity:      {self.humidity}% {self.show_change(previous.humidity, self.humidity)}" + '\n'
        output += f"  Pressure:      {self.pressure:.01f} hPa {self.show_change(previous.pressure, self.pressure)}" + '\n'

        if self.battery is not None:
            output += f"  Battery:       {self.battery}%" + '\n'

        output += f"  Age:           {self.age()}"
        if self.interval is not None:
            output += f"/{self.interval}"

        return output


class History:
    """
    Creates, reads, and updates the history of a device's readings
    """
    def __init__(self, *, config_file: str, args: argparse.Namespace):
        self.config = self.load_config(config_file, args)

        self.create()

        # last_recorded stores the last known reading even if it hasn't been written to the history
        # this is overridden whenver write() is called
        self.last_recorded = self.latest()


    def load_config(self, filename: str, args: argparse.Namespace) -> configparser.ConfigParser:
        """
        Loads config from filename. Any options specified by command line arguments will be overridden
        """
        config = configparser.ConfigParser(interpolation=configparser.ExtendedInterpolation())

        config['DEFAULT'] = {
            'file': 'records.sqlite',
            'date format': '%Y/%m/%d %H:%M:%S',
            'notify': 'False',
            'update': 'False',
            'monitor': 'False',
        }

        config.read(filename)

        sections = ['aranet', 'pushover', 'history', 'monitor']
        for section in sections:
            if section not in config:
                config[section] = {}


        if args.mac is not None:
            config['aranet']['mac'] = args.mac
        if args.file is not None:
            config['history']['file'] = args.file
        if args.format is not None:
            config['history']['date format'] = args.format
        if args.notify is not None:
            config['monitor']['notify'] = str(args.notify)
        if args.update is not None:
            config['history']['update'] = str(args.update)
        if args.monitor is not None:
            config['monitor']['monitor'] = str(args.monitor)


        return config


    def stats(self) -> dict:
        """
        Returns a dictionary containing mix, max, and mean values for co2, temperature, humidity, and pressure
        as well as the total number of records.
        """
        with sqlite3.connect(self.config['history']['file']) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cols = ['co2', 'temperature', 'humidity', 'pressure']
            stats = {
                'min': f"select {', '.join([f"min({col}) as {col}" for col in cols])} from records;",
                'max': f"select {', '.join([f"max({col}) as {col}" for col in cols])} from records;",
                'mean': f"select {', '.join([f"avg({col}) as {col}" for col in cols])} from records;",
                'count': "select count(*) from records",
            }
            for stat in stats:
                cursor.execute(stats[stat])
                row = cursor.fetchone()
                if stat == 'count':
                    stats[stat] = row[0]
                else:
                    stats[stat] = dict(row)
            return stats



    def latest(self) -> Reading:
        """
        Returns the last recorded reading
        """
        with sqlite3.connect(self.config['history']['file']) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("select * from records order by date desc limit 1")
            row = cursor.fetchone()
            if row is not None:
                result = Reading(
                    date = datetime.strptime(row['date'], self.config['history']['date format']).replace(tzinfo=timezone.utc),
                    co2 = row['co2'],
                    temperature = row['temperature'],
                    humidity = row['humidity'],
                    pressure = row['pressure'],
                )
            else:
                result = Reading(
                    date = None,
                    co2 = None,
                    temperature = None,
                    humidity = None,
                    pressure = None,
                )
            return result


    def print_table(self, stats: dict, width: int) -> None:
        """
        Prints a table containing the min, max, mean, and last recorded values for each column.
        Omitted rows will be skipped. Omitted values will be replaced with 'x'
        """
        print(f"{"temp  humid  press    co2":>34}")
        columns = [  # (name, suffix)
            ('temperature', '°'),
            ('humidity','%'),
            ('pressure', ''),
            ('co2', ''),
        ]
        for row in ['min', 'max', 'mean', 'latest']:
            if row in stats:
                line = f"{row:6}"
                for (stat, suffix) in columns:
                    value = stats[row][stat]
                    if value is not None:
                        value = f"{value:,.0f}{suffix}"
                    else:
                        value = 'x'
                    line += f"{value:>7}"
                print(line)


    def print(self, get_stats=True, new_records=None) -> None:
        """
        Prints the table and other information for the user.
        If get_stats == False, the min, max, and mean rows will be omitted
        """
        width = 34

        print('-'*width)

        date = self.last_recorded.date
        if date is not None:
            tz = tzlocal.get_localzone()
            date = date.astimezone(tz).strftime(self.config['history']['date format'])
        else:
            date = 'never'

        print('last recorded' + f"{date:>{width - 13}}")

        if new_records is not None:
            print('new records' + f"{new_records:{width - 11},}")

        stats = {}
        if get_stats:
            stats = self.stats()
        stats['latest'] = self.last_recorded

        if get_stats:
            print('records' + f"{stats['count']:{width - 7},}")

        print('-'*width)

        self.print_table(stats, width)


    def create(self) -> None:
        """
        Creates both the sqlite db and the records table if they don't already exist
        """
        with sqlite3.connect(self.config['history']['file']) as conn:
                cursor = conn.cursor()
                cursor.execute("""
create table if not exists records (
    date text,
    co2 real,
    temperature real,
    humidity real,
    pressure real,
    primary key(date)
);
                           """)
                conn.commit()


    def update(self) -> int:
        """
        Connects to the aranet device to request all records since the last recorded reading.
        Returns the number of new records
        """

        # we use latest() instead of last_recorded to ensure
        # that there are never gaps in the history
        latest = self.latest().date or datetime.fromtimestamp(0, tz=timezone.utc)

        records = aranet4.client.get_all_records(
            self.config['aranet']['mac'],
            entry_filter = {
                "temp": True,
                "humi": True,
                "pres": True,
                "co2": True,
                "start": latest,
            },
            remove_empty=True
        )

        new_records = []
        for entry in records.value:
            # a record isn't always returned with the same time
            # so (entry.date > latest) may repeat entries
            if (entry.date - latest).total_seconds() > 60:
                reading = Reading(
                        date = entry.date,
                        co2 = entry.co2,
                        temperature = entry.temperature * 9/5 + 32,  # convert celsius to fahrenheit
                        humidity = entry.humidity,
                        pressure = entry.pressure,
                    )
                new_records.append(reading)

        self.write(new_records)
        return len(new_records)


    def write(self, records: list[Reading]) -> None:
        """
        Writes records to the sqlite db
        """
        with sqlite3.connect(self.config['history']['file']) as conn:
            cursor = conn.cursor()

            for reading in records:
                cursor.execute("insert into records(date, co2, temperature, humidity, pressure) values(?,?,?,?,?)", [
                    reading.date.strftime(self.config['history']['date format']),
                    reading.co2,
                    reading.temperature,
                    reading.humidity,
                    reading.pressure,
                ])
            conn.commit()
        self.last_recorded = self.latest()


class Monitor:
    """
    Passively scans for readings from a device
    """
    def __init__(self, *, config: configparser.ConfigParser, history: History):
        self.interval = None
        self.history = history
        self.current = None
        self.config = config

    
    async def start(self) -> None:
        """
        Starts the scanner and updates the displayed age of the current reading
        """
        known_age = None
        offset = 0

        scanner = aranet4.Aranet4Scanner(self.on_scan)
        await scanner.start()
        while True: # Run forever
            await asyncio.sleep(1)
            if self.current is not None:
                print(f"  Age:           {self.current.age()}/{self.interval}" + ' '*5, end='\r')
        await scanner.stop()


    def notify(self, title: str, body: str, ttl: int) -> None:
        """
        Sends a notification to the user which will disappear after ttl.
        Uses app and user tokens from config 
        """
        conn = http.client.HTTPSConnection("api.pushover.net:443")
        conn.request("POST", "/1/messages.json",
        urllib.parse.urlencode({
            "token": self.config['pushover']['token'],
            "user": self.config['pushover']['user'],
            "title": title,
            "message": body,
            "ttl": ttl,
            "html": 1,
        }), { "Content-type": "application/x-www-form-urlencoded" })
        conn.getresponse()


    def maybe_notify(self, body: str) -> None:
        """
        Dertermine whether to alert the user and, if so, what alerts to send and for how long
        """
        if not self.config['monitor'].getboolean('notify'):
            return

        current = self.current
        previous = self.history.last_recorded
            
        ttl = self.interval - self.current.age()
        alerts = []
        
        dco2 = current.co2 - (previous.co2 or current.co2)
        if dco2 > 0 and current.co2 > 1400:
            alerts.append('rising co2')
        if current.temperature < 50:
            alerts.append('low temperature')
        if current.temperature > 80:
            alerts.append('high temperature')

        if len(alerts) > 0:
            title = '; '.join(alerts)
            self.notify(title, body, max(ttl, 60))


    def on_scan(self, advertisement) -> None:
        """
        Responds to each new reading from the scanner.
        New distinct readings are displayed and (potentially) written to the history
        """
        if advertisement.device.address != self.config['aranet']['mac']:
            return

        if not advertisement.readings:
            return
        
        current = advertisement.readings

        if current.interval != self.interval:
            self.interval = current.interval

        is_first_reading = self.current is None

        self.current = Reading(
            date = datetime.now().astimezone(timezone.utc) - timedelta(seconds=current.ago),
            co2 = current.co2,
            temperature = current.temperature * 9/5 + 32,
            humidity = current.humidity,
            pressure = current.pressure,
            battery = current.battery,
            status = current.status,
            interval = current.interval
            )

        latest = self.history.last_recorded
        delta = (self.current.date - latest.date).total_seconds() 

        # if the reading is new,
        # display and (maybe) add it to the history
        if delta > 60 or is_first_reading:
            term_output = self.current.display(DisplayMode.terminal, previous=latest)
            notif_output = self.current.display(DisplayMode.notification, previous=latest)

            print(term_output, end='\r')
            self.maybe_notify(notif_output)

            # a new distinct reading
            if 60 < delta:
                # if we're writing to the history, we have to ensure that there are no gaps
                # delta > (self.interval + 60) indicates that we've missed at least one reading
                if self.config['history'].getboolean('update') and  delta < (self.interval + 60):
                        self.history.write([self.current])
                else:
                    self.history.last_recorded = self.current


def parse_args(argv) -> argparse.Namespace:
    """
    Parses command line arguments. All arguments without a default exist as config-file options
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('--stats', action=argparse.BooleanOptionalAction, help='load stats from record file', default=True)
    parser.add_argument('--update', action=argparse.BooleanOptionalAction, help='get new records from device')
    parser.add_argument('--file', metavar='file_path', help='path to the record file (defaults to records.sqlite)')
    parser.add_argument('--config', metavar='config_path', help='path to the config file (defaults to config.ini)', default='config.ini')
    parser.add_argument('--format', metavar='date_format', help='date format')
    parser.add_argument('--mac', metavar='mac_address', help='mac address of the device')
    parser.add_argument('--notify', action=argparse.BooleanOptionalAction, help='send notifcations when appropriate')
    parser.add_argument('--monitor', action=argparse.BooleanOptionalAction, help='passively scan for updates')

    return parser.parse_args(argv)



def find_device_mac() -> str | None:
    """
    Starts a scanner to identify nearby aranet devices.
    If exactly one is found, return its address
    """
    print('No MAC address supplied. Scanning for devices...')

    devices = set()

    def store_scan_result(advertisement) -> None:
        if not advertisement.device:
            return
        devices.add(advertisement.device.address)

    aranet4.client.find_nearby(store_scan_result)

    print(f"Found {len(devices)} device(s)")

    if len(devices) == 1:
        mac = devices.pop()
        print(f"mac = {mac}")
        return mac

    return None


def colorize(color: str, text: str, mode: DisplayMode) -> str:
    """
    Colors text for the specified display mode
    """
    colors = {
        'black': '\x1b[30m{}\x1b[0m',
        'red': '\x1b[31m{}\x1b[0m',
        'green': '\x1b[32m{}\x1b[0m',
        'yellow': '\x1b[33m{}\x1b[0m',
        'blue': '\x1b[34m{}\x1b[0m',
        'magenta': '\x1b[35m{}\x1b[0m',
        'cyan': '\x1b[36m{}\x1b[0m',
        'white': '\x1b[37m{}\x1b[0m',
    }
    color = color.replace('amber', 'yellow')
    
    if mode == DisplayMode.notification:
        result = f"<font color='{color}'>{text}</font>"
    elif mode == DisplayMode.terminal:
        result = colors[color].format(text)
    else:
        result = text
    return result


def main():
    args = parse_args(sys.argv[1:])

    history = History(config_file=args.config, args=args)

    # if a MAC address isn't supplied by config or arguments,
    # we can try to scan for bluetooth devices
    needs_mac = history.config['history'].getboolean('update') or history.config['monitor'].getboolean('monitor')
    if 'mac' not in history.config['aranet'] and needs_mac:
        history.config['aranet']['mac'] = find_device_mac()
        if history.config['aranet']['mac'] is None:
            print('Unable to get device MAC address')
            exit(1)

    new_records = None
    if history.config['history'].getboolean('update'):
        new_records = history.update()

    history.print(get_stats=args.stats, new_records=new_records)

    if history.config['monitor'].getboolean('monitor'):
        monitor = Monitor(config=history.config, history=history)
        try:
            asyncio.run(monitor.start())
        except KeyboardInterrupt:
            print("User interupted.")


if __name__ == '__main__':
    main()
