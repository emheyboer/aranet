#!/usr/bin/env venv/bin/python
import aranet4
import csv
import sys
import os
import tzlocal
import argparse
import sqlite3
from datetime import datetime, timezone, timedelta


class History:
    def __init__(self, *, date_format: str, filename, device_mac):
        self.date_format = date_format
        self.filename = filename
        self.device_mac = device_mac

        self.create()
        self.last_recorded = self.latest()


    def stats(self) -> dict:
        with sqlite3.connect(self.filename) as conn:
            cursor = conn.cursor()

            cols = ['co2', 'temperature', 'humidity', 'pressure']
            stats = {
                'min': f"select {', '.join([f"min({col})" for col in cols])} from records;",
                'max': f"select {', '.join([f"max({col})" for col in cols])} from records;",
                'mean': f"select {', '.join([f"avg({col})" for col in cols])} from records;",
                'count': "select count(*) from records",
            }
            for stat in stats:
                cursor.execute(stats[stat])
                row = cursor.fetchone()
                if stat == 'count':
                    stats[stat] = row[0]
                else:
                    stats[stat] = {cols[i]: row[i] for i in range(len(row))}
            return stats



    def latest(self) -> dict:
        with sqlite3.connect(self.filename) as conn:
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
        columns = [  # (name, formatter)
            ('temperature', lambda x: f"{x:,.0f}°"),
            ('humidity', lambda x: f"{x:,.0f}%"),
            ('pressure', lambda x: f"{x:,.0f}"),
            ('co2', lambda x: f"{x:,.0f}"),
        ]
        for row in ['min', 'max', 'mean', 'latest']:
            if row in stats:
                print(f"{row:6}", end='')
                for (stat, fn) in columns:
                    value = stats[row][stat]
                    if value is not None:
                        value = fn(value)
                    else:
                        value = 'x'
                    print(f"{value:>7}", end='')
                print()


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
        with sqlite3.connect(self.filename) as conn:
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
            self.device_mac,
            entry_filter = {
                "temp": True,
                "humi": True,
                "pres": True,
                "co2": True,
                "start": latest,
            },
        )

        # get_all_records() returns empty records for those that are filtered by "start"
        # so we have to remove them before writing
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
        with open(self.filename, 'a', newline='') as file:
            writer = csv.writer(file, quoting=csv.QUOTE_ALL)
            for entry in records:
                writer.writerow([
                    entry.date.strftime(self.date_format),
                    entry.co2,
                    entry.temperature,
                    entry.humidity,
                    entry.pressure
                    ])


def parse_args(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument('--stats', action=argparse.BooleanOptionalAction, help='load stats from record file', default=True)
    parser.add_argument('--update', action=argparse.BooleanOptionalAction, help='get new records from device', default=True)
    parser.add_argument('--file', metavar='file_path', help='path to the record file', default='records.sqlite')
    # we're formatting this way for https://github.com/the-butcher/ARANET4_VIS
    parser.add_argument('--format', metavar='date_format', help='date format', default='%Y/%m/%d %H:%M:%S')
    parser.add_argument('--mac', metavar='mac_address', help='mac address of the device (defaults to value of ARANET_MAC)', default=device_mac_from_envvar())

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


def device_mac_from_envvar() -> str | None:
    if 'ARANET_MAC' in os.environ:
        return os.environ['ARANET_MAC']
    return None


def main():
    args = parse_args(sys.argv[1:])

    # if a MAC address isn't supplied by env var or arguments,
    # we can try to scan for bluetooth devices
    if args.mac is None and args.update:
        args.mac = find_device_mac()
        if args.mac is None:
            print('Unable to get device MAC address')
            exit(1)

    history = History(date_format=args.format, filename=args.file, device_mac=args.mac)

    new_records = None
    if args.update:
        new_records = history.update()

    history.print(get_stats=args.stats, new_records=new_records)


if __name__ == '__main__':
    main()
