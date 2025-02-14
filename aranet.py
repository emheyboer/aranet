#!/usr/bin/env venv/bin/python
import aranet4
import sys
import tzlocal
import argparse
import sqlite3
import configparser
from datetime import datetime, timezone


class History:
    def __init__(self, *, date_format: str, config_file: str, args):
        self.date_format = date_format
        self.config = self.load_config(config_file, args)

        self.create()
        self.last_recorded = self.latest()


    def load_config(self, filename, args):
        config = configparser.ConfigParser()
        config.read(filename)

        if 'aranet' not in config:
            config['aranet'] = {}
        if args.mac is not None:
            config['aranet']['mac'] = args.mac

        if 'history' not in config:
            config['history'] = {}
        if args.file is not None:
            config['history']['file'] = args.file
        elif 'file' not in config['history']:
            config['history']['file'] = 'records.sqlite'

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
                result['date'] = datetime.strptime(result['date'], self.date_format).replace(tzinfo=timezone.utc)
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
            date = date.astimezone(tz).strftime(self.date_format)
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
                new_records.append(entry)

        self.write(new_records)
        self.last_recorded = self.latest()
        return len(new_records)


    def write(self, records: list) -> None:
        with sqlite3.connect(self.config['history']['file']) as conn:
            cursor = conn.cursor()

            for entry in records:
                cursor.execute("insert into records(date, co2, temperature, humidity, pressure) values(?,?,?,?,?)", [
                    entry.date.strftime(self.date_format),
                    entry.co2,
                    entry.temperature * 9/5 + 32,  # convert celsius to fahrenheit
                    entry.humidity,
                    entry.pressure,
                ])
            conn.commit()


def parse_args(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument('--stats', action=argparse.BooleanOptionalAction, help='load stats from record file', default=True)
    parser.add_argument('--update', action=argparse.BooleanOptionalAction, help='get new records from device', default=True)
    parser.add_argument('--file', metavar='file_path', help='path to the record file (defaults to records.sqlite)')
    parser.add_argument('--config', metavar='config_path', help='path to the config file (defaults to config.ini)', default='config.ini')
    parser.add_argument('--format', metavar='date_format', help='date format', default='%Y/%m/%d %H:%M:%S')
    parser.add_argument('--mac', metavar='mac_address', help='mac address of the device')

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


def main():
    args = parse_args(sys.argv[1:])

    history = History(date_format=args.format, config_file=args.config, args=args)

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
