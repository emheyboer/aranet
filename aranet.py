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


class History:
    def __init__(self, *, config_file: str, args):
        self.config = self.load_config(config_file, args)

        self.create()
        self.last_recorded = self.latest()


    def load_config(self, filename, args):
        config = configparser.ConfigParser(interpolation=configparser.ExtendedInterpolation())

        config['DEFAULT'] = {
            'file': 'records.sqlite',
            'date format': '%Y/%m/%d %H:%M:%S',
            'notify': False,
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
            config['monitor']['notify'] = args.notify

        return config


    def stats(self) -> dict:
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



    def latest(self) -> dict:
        with sqlite3.connect(self.config['history']['file']) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("select * from records order by date desc limit 1")
            row = cursor.fetchone()
            if row is not None:
                result = dict(row)
                result['date'] = datetime.strptime(result['date'],
                    self.config['history']['date format']).replace(tzinfo=timezone.utc)
            else:
                result = {col: None for col in ['date', 'co2', 'temperature', 'humidity', 'pressure']}
            return result


    def print_table(self, stats: dict, width: int) -> None:
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
        width = 34

        print('-'*width)

        date = self.last_recorded['date']
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
        latest = self.last_recorded['date'] or datetime.fromtimestamp(0, tz=timezone.utc)

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
                entry.temperature = entry.temperature * 9/5 + 32  # convert celsius to fahrenheit

                new_records.append(entry)

        self.write(new_records)
        self.last_recorded = self.latest()
        return len(new_records)


    def write(self, records: list) -> None:
        with sqlite3.connect(self.config['history']['file']) as conn:
            cursor = conn.cursor()

            for entry in records:
                cursor.execute("insert into records(date, co2, temperature, humidity, pressure) values(?,?,?,?,?)", [
                    entry.date.strftime(self.config['history']['date format']),
                    entry.co2,
                    entry.temperature,
                    entry.humidity,
                    entry.pressure,
                ])
            conn.commit()


class Monitor:
    def __init__(self, config: ConfigParser, history: History):
        self.last_seen = None
        self.interval = None
        self.history = history
        self.current = None
        self.config = config

    
    async def start(self):
        known_age = None
        offset = 0

        scanner = aranet.Aranet4Scanner(self.on_scan)
        await scanner.start()
        while True: # Run forever
            await asyncio.sleep(1)
            if self.current is not None:
                # If the scanner finds something new, use that age
                if known_age is None or known_age != self.last_seen:
                    known_age = self.last_seen
                    offset = 0
                offset += 1
                print(f"  Age:           {known_age + offset}/{self.interval}" + ' '*5, end='\r')
        await scanner.stop()


    def notify(self, title, body, ttl):
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


    def show_change(self, prev, curr):
        if prev is None:
            prev = curr
            
        delta = curr - prev
        symbol = '⇵'
        if delta > 0:
            symbol = '↑'
        elif delta < 0:
            symbol = '↓'
        return f"{symbol} {delta:.01f}"


    def maybe_notify(self, body):
        if not config['monitor'].getboolean('notify'):
            return

        current = self.current
        previous = self.history.latest()
            
        ttl = self.interval - self.last_seen
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


    def display_readings(self, mode):
        current = self.current
        previous = self.history.latest()
        color = current.status.name.lower()

        tz = tzlocal.get_localzone()

        output = '\n' if mode == DisplayMode.terminal else ''
        output += f"  CO2:           {colorize(color, current.co2, mode)} ppm {self.show_change(previous.co2, current.co2)}" + '\n'
        output += f"  Temperature:   {(current.temperature):.01f} °F {self.show_change(previous.temperature, current.temperature)}" + '\n'
        output += f"  Humidity:      {current.humidity}% {self.show_change(previous.humidity, current.humidity)}" + '\n'
        output += f"  Pressure:      {current.pressure:.01f} hPa {self.show_change(previous.pressure, current.pressure)}" + '\n'
        output += f"  Battery:       {current.battery}%" + '\n'
        output += f"  Date:          {current.date.astimezone(tz).strftime(self.config['history']['date format'])}" + '\n'
        output += f"  Age:           {self.last_seen}/{self.interval}"

        return output


    def on_scan(self, advertisement):
        if advertisement.device.address != self.config['aranet']['mac']:
            return

        if not advertisement.readings:
            return
        
        self.current = advertisement.readings

        if self.current.interval != self.interval:
            self.interval = self.current.interval

        if self.last_seen is None or advertisement.readings.ago < self.last_seen:
            self.last_seen = self.current.ago
            self.current.temperature = self.current.temperature * 9/5 + 32
            self.current.date = datetime.now().astimezone(timezone.utc) - timedelta(seconds=self.last_seen)

            term_output = self.display_readings(DisplayMode.terminal)
            notif_output = self.display_readings(DisplayMode.notification)

            print(term_output, end='\r')
            self.maybe_notify(notif_output)

            self.history.append(self.current)
        else:
            self.last_seen = self.current.ago


def parse_args(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument('--stats', action=argparse.BooleanOptionalAction, help='load stats from record file', default=True)
    parser.add_argument('--update', action=argparse.BooleanOptionalAction, help='get new records from device', default=True)
    parser.add_argument('--file', metavar='file_path', help='path to the record file (defaults to records.sqlite)')
    parser.add_argument('--config', metavar='config_path', help='path to the config file (defaults to config.ini)', default='config.ini')
    parser.add_argument('--format', metavar='date_format', help='date format')
    parser.add_argument('--mac', metavar='mac_address', help='mac address of the device')
    parser.add_argument('--notify', action=argparse.BooleanOptionalAction, help='send notifcations when appropriate', default=True)
    parser.add_argument('--monitor', action=argparse.BooleanOptionalAction, help='passively scan for updates', default=False)

    return parser.parse_args(argv)


def store_scan_result(advertisement):
    global devices
    if not advertisement.device:
        return

    devices.add(advertisement.device.address)


def find_device_mac() -> str | None:
    global devices

    print('No MAC address supplied. Scanning for devices...')

    devices = set()
    aranet4.client.find_nearby(store_scan_result)

    print(f"Found {len(devices)} device(s)")

    if len(devices) == 1:
        mac = devices.pop()
        print(f"mac = {mac}")
        return mac

    return None


def colorize(color: str, text: str, mode: DisplayMode) -> str:
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
    if 'mac' not in history.config['aranet']:
        history.config['aranet']['mac'] = find_device_mac()
        if history.config['aranet']['mac'] is None:
            print('Unable to get device MAC address')
            exit(1)

    new_records = None
    if args.update:
        new_records = history.update()

    history.print(get_stats=args.stats, new_records=new_records)


if __name__ == '__main__':
    main()
