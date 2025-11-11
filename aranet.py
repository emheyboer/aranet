#!/usr/bin/env venv/bin/python
import aranet4
import sys
import os
import tzlocal
import argparse
import sqlite3
import configparser
import http.client
import urllib
import asyncio
from datetime import datetime, timezone, timedelta
from enum import Enum
from escpos.printer import CupsPrinter


class DisplayMode(Enum):
    """
    Represents the type of display we're outputting to
    """
    terminal = 1
    notification = 2
    printer = 3


class Column(Enum):
    co2 = "co2"
    temperature = "temperature"
    humidity = "humidity"
    pressure = "pressure"


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


    def col(self, column: Column):
        return self.__getitem__(column.value)


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
    

    def display_row(self, column: Column, value: str, suffix: str, mode: DisplayMode, previous: 'Reading' = None,
                history: 'History' = None) -> str:
        if column == Column.co2 and self.status is not None:
            value = colorize(self.status.name.lower(), value, mode)

        line = f"  {column.value}:{' ' * (14 - len(column.value))}{value}{suffix}"

        if previous is not None:
            line += f" {self.show_change(previous.col(column), self.col(column))}"
        if history is not None:
            line += f" — {addSuffix(history.ranking(column.value, self.col(column)))} place"
            line += f" — {addSuffix(history.percentile(column.value, self.col(column)))} percentile"
        return line


    def display(self, mode: DisplayMode, previous: 'Reading' = None,
                history: 'History' = None) -> str:
        """
        Represents the reading as a string suitable for the specified display mode.
        If the previous reading is specified, the change in each value is shown
        """
        lines = [
            self.display_row(Column.co2, f"{self.co2}", " ppm", mode, previous, history),
            self.display_row(Column.temperature, f"{(self.temperature):.01f}", "°F", mode, previous, history),
            self.display_row(Column.humidity, f"{self.humidity}","%", mode, previous, history),
            self.display_row(Column.pressure, f"{self.pressure:.01f}", " hPa", mode, previous, history),
        ]

        line = "  battery:"
        if self.battery is not None:
            line = f"  battery:       {self.battery}%"
        lines.append(line)

        return "\n".join(lines)


class History:
    """
    Creates, reads, and updates the history of a device's readings
    """
    def __init__(self, *, config_file: str, args: argparse.Namespace):
        self.config = self.load_config(config_file, args)


    def __enter__(self):
        self.connection = sqlite3.connect(self.config['history']['file'])
        self.connection.row_factory = sqlite3.Row
        self.cursor = self.connection.cursor()

        self.create()

        # last_recorded stores the last known reading even if it hasn't been written to the history
        # this is overridden whenver write() is called
        self.last_recorded = self.latest()

        return self


    def __exit__(self, ext_type, exc_value, traceback):
        self.cursor.close()
        self.connection.close()


    def load_config(self, filename: str, args: argparse.Namespace) -> configparser.ConfigParser:
        """
        Loads config from filename. Any options specified by command line arguments will be overridden
        """
        config = configparser.ConfigParser(interpolation=configparser.ExtendedInterpolation())

        config['DEFAULT'] = {
            'file': 'records.sqlite',
            'date format': '%m/%d/%Y %H:%M:%S',
            'notify': 'False',
            'update': 'False',
            'monitor': 'False',
            'short': 'False',
            'print': 'False',
        }

        config.read(filename)

        sections = ['aranet', 'pushover', 'printer', 'history', 'monitor']
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
        if args.short is not None:
            config['history']['short'] = str(args.short)
        if args.print is not None:
            config['printer']['print'] = str(args.print)


        return config


    def stats(self) -> dict:
        """
        Returns a dictionary containing min, max, and mean values for co2, temperature, humidity, and pressure
        as well as the total number of records.
        """
        cols = ['co2', 'temperature', 'humidity', 'pressure']
        stats = {
            'min': "select " + ', '.join([f"min({col}) as {col}" for col in cols]) + " from records;",
            'max': "select " + ', '.join([f"max({col}) as {col}" for col in cols]) + " from records;",
            'mean': "select " + ', '.join([f"avg({col}) as {col}" for col in cols]) + " from records;",
            'count': "select count(*) from records",
        }
        for stat in stats:
            self.cursor.execute(stats[stat])
            row = self.cursor.fetchone()
            if stat == 'count':
                stats[stat] = row[0]
            else:
                stats[stat] = dict(row)
        return stats



    def latest(self) -> Reading:
        """
        Returns the last recorded reading
        """
        self.cursor.execute("select * from records order by date desc limit 1")
        row = self.cursor.fetchone()
        if row is not None:
            result = Reading(
                date = datetime.fromisoformat(row['date']),
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
        print(f"{'temp  humid  press    co2':>34}")
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


    def print(self, new_records=None) -> None:
        """
        Prints the table and other information for the user.
        If short == True, the min, max, and mean rows will be omitted
        """
        short = self.config['history'].getboolean('short')

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
        if not short:
            stats = self.stats()
        stats['latest'] = self.last_recorded

        if not short:
            print('records' + f"{stats['count']:{width - 7},}")

        print('-'*width)

        self.print_table(stats, width)


    def create(self) -> None:
        """
        Creates both the sqlite db and the records table if they don't already exist
        """
        self.cursor.execute("""
create table if not exists records (
date text,
co2 real,
temperature real,
humidity real,
pressure real,
primary key(date)
);
                   """)
        self.connection.commit()


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
        with self.connection:
            for reading in records:
                self.cursor.execute("insert into records(date, co2, temperature, humidity, pressure) values(?,?,?,?,?)", [
                    reading.date.isoformat(),
                    reading.co2,
                    reading.temperature,
                    reading.humidity,
                    reading.pressure,
                ])
        self.last_recorded = self.latest()


    def ranking(self, column: str, value: float) -> int:
        """
        Returns the ranking of the value
        """
        self.cursor.execute(f"select count(distinct {column}) as count from records where {column} > ?",
            [value])
        row = self.cursor.fetchone()
        rank = row['count'] + 1

        return rank


    def percentile(self, column: str, value: float) -> int: 
        """
        Returns which percentile the value falls in
        """
        self.cursor.execute(f"select count(*) as count from records where {column} < ?", [value])
        row = self.cursor.fetchone()
        count = row['count'] + 1

        self.cursor.execute(f"select count(*) as total from records")
        row = self.cursor.fetchone()
        total = row['total']

        percentile = count / total * 100

        percentile = round(percentile)

        return percentile


class Monitor:
    """
    Passively scans for readings from a device
    """
    def __init__(self, *, config: configparser.ConfigParser, history: History):
        self.interval = None
        self.history = history
        self.current = None
        self.output = None
        self.config = config


    async def start(self) -> None:
        """
        Starts the scanner and updates the displayed age of the current reading
        """
        first_time = True  # whether we've called update_output() before
        output = None

        scanner = aranet4.Aranet4Scanner(self.on_scan)
        await scanner.start()
        while True: # Run forever
            if output is None and self.history.last_recorded is not None:
                output = self.history.last_recorded.display(DisplayMode.terminal,
                    history=self.history)

            if self.output is not None:
                output = self.output

            if output is not None:
                age = f"\n  age:           {(self.current or self.history.last_recorded).age()}"
                if self.interval is not None:
                    age += f"/{self.interval}"

                update_output(output + age, first_time=first_time)
                first_time = False

            await asyncio.sleep(1)
        await scanner.stop()


    def notify(self, title: str, body: str, ttl: int = None) -> None:
        """
        Sends a notification to the user which will disappear after ttl.
        Uses app and user tokens from config 
        """
        form = {
            "token": self.config['pushover']['token'],
            "user": self.config['pushover']['user'],
            "title": title,
            "message": body,
            "html": 1,
        }
        if ttl is not None:
            form["ttl"] = ttl

        conn = http.client.HTTPSConnection("api.pushover.net:443")
        conn.request("POST", "/1/messages.json",
            urllib.parse.urlencode(form), { "Content-type": "application/x-www-form-urlencoded" })
        conn.getresponse()


    def maybe_notify(self, body: str) -> None:
        """
        Dertermine whether to alert the user and, if so, what alerts to send and for how long
        """
        if not self.config['monitor'].getboolean('notify'):
            return

        current = self.current
        previous = self.history.last_recorded
            
        ttl = max(self.interval - self.current.age(), 60)
        should_expire = True
        alerts = []
        
        dco2 = current.co2 - (previous.co2 or current.co2)
        if dco2 > 0 and current.co2 > 1400:
            alerts.append('rising co2')
        if current.temperature < 50:
            alerts.append('low temperature')
        if current.temperature > 90:
            alerts.append('high temperature')
        for col in ['co2', 'temperature', 'humidity', 'pressure']:
            rank = self.history.ranking(col, current[col])
            if rank == 1:
                alerts.append(f"new {col} high score")
                should_expire = False

        if len(alerts) > 0:
            title = '; '.join(alerts)
            if not should_expire:
                ttl = None
            self.notify(title, body, ttl=ttl)


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
        delta = (self.current.date - (latest.date or datetime.fromtimestamp(0, tz=timezone.utc))).total_seconds() 


        # the reading is (probably) new
        if delta > 60 or self.output is None:
            self.output = self.current.display(DisplayMode.terminal, previous=latest, history=self.history)

            # a new distinct reading
            if delta > 60:
                notif_output = self.current.display(DisplayMode.notification, previous=latest, history=self.history)
                self.maybe_notify(notif_output)

                print_output = self.current.display(DisplayMode.printer)
                self.maybe_print(print_output)
                    
                # if we're writing to the history, we have to ensure that there are no gaps
                # delta > (self.interval + 60) indicates that we've missed at least one reading
                if self.config['history'].getboolean('update') and  delta < (self.interval + 60):
                        self.history.write([self.current])
                else:
                    self.history.last_recorded = self.current


    def maybe_print(self, output: str) -> None:
        if not self.config['printer'].getboolean('print'):
            return

        printer = CupsPrinter(self.config['printer']['printer name'],
            profile="default")

        if not (printer.is_usable() or self.config['history'].getboolean('short')):
            print('printer is not usable')
            return
        if not (printer.is_online() or self.config['history'].getboolean('short')):
            print('printer is offline')
            return

        printer.text(output)
        printer.cut()
        printer.close()


class RedirectedStdout:
    """
    Temporarily redirects stdout to target
    """
    def __init__(self, target):
        self._target = target

    def __enter__(self):
        self._stdout = sys.stdout
        sys.stdout = open(self._target, 'w')

    def __exit__(self, *args):
        sys.stdout.close()
        sys.stdout = self._stdout


def addSuffix(n: int) -> str:
    """
    Adds the suffix used with a given integer.
    """
    ones = n % 10
    tens = n % 100 // 10
    if tens == 1:
        suffix = 'th'
    elif ones == 1:
        suffix = 'st'
    elif ones == 2:
        suffix = 'nd'
    elif ones == 3:
        suffix = 'rd'
    else:
        suffix = 'th'
    return f"{n:,}{suffix}"


def parse_args(argv) -> argparse.Namespace:
    """
    Parses command line arguments. All arguments without a default exist as config-file options
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('--short', action=argparse.BooleanOptionalAction, help='minimal output')
    parser.add_argument('--update', action=argparse.BooleanOptionalAction, help='get new records from device')
    parser.add_argument('--file', metavar='file_path', help='path to the record file (defaults to records.sqlite)')
    parser.add_argument('--config', metavar='config_path', help='path to the config file (defaults to config.ini)', default='config.ini')
    parser.add_argument('--format', metavar='date_format', help='date format')
    parser.add_argument('--mac', metavar='mac_address', help='mac address of the device')
    parser.add_argument('--notify', action=argparse.BooleanOptionalAction, help='send notifcations when appropriate')
    parser.add_argument('--monitor', action=argparse.BooleanOptionalAction, help='passively scan for updates')
    parser.add_argument('--print', action=argparse.BooleanOptionalAction, help='send readings to a printer')

    return parser.parse_args(argv)


def update_output(text, first_time=False):
    """
    For any output with a fixed number of lines, replace the previous.
    """
    # https://en.wikipedia.org/wiki/ANSI_escape_code

    lines = len(text.split('\n'))
    # move cursor up x lines, clear to end of screen
    codes = f"\033[{lines - 1}A\r\033[0J{text}"

    output = text if first_time else codes

    print(output, end='')


def find_device() -> str | None:
    """
    Starts a scanner to identify nearby aranet devices.
    If exactly one is found, return its address
    """
    print('No MAC address supplied. Scanning for devices...')

    devices = set()

    def store_scan_result(advertisement) -> None:
        if not advertisement.device:
            return
        devices.add(advertisement.device)

    aranet4.client.find_nearby(store_scan_result, duration=30)

    print(f"Found {len(devices)} device(s)")

    for device in devices:
        print(f"name = {device.name} mac = {device.address}")

    if len(devices) == 1:
        device = devices.pop()
        return device.address

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

    with History(config_file=args.config, args=args) as history:
        # if a MAC address isn't supplied by config or arguments,
        # we can try to scan for bluetooth devices
        needs_mac = history.config['history'].getboolean('update') or history.config['monitor'].getboolean('monitor')
        if 'mac' not in history.config['aranet'] and needs_mac:
            mac = find_device()
            if mac is None:
                print('Unable to get device MAC address')
                exit(1)
            history.config['aranet']['mac'] = mac

        new_records = None
        if history.config['history'].getboolean('update'):
            if history.config['history'].getboolean('short'):
                # aranet4's get_all_records function makes calls to print()
                # if we're minimizing output, those should be prevented
                with RedirectedStdout(os.devnull):
                    new_records = history.update()
            else:
                new_records = history.update()

        if not (history.config['history'].getboolean('short') and
            history.config['monitor'].getboolean('monitor')):
            history.print(new_records=new_records)

        if history.config['monitor'].getboolean('monitor'):
            monitor = Monitor(config=history.config, history=history)
            try:
                asyncio.run(monitor.start())
            except KeyboardInterrupt:
                print("User interupted.")


if __name__ == '__main__':
    main()
