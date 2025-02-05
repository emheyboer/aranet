#!/usr/bin/env venv/bin/python
import aranet4
import csv
import sys
import os
import tzlocal
import argparse
from datetime import datetime, timezone, timedelta


class History:
    def __init__(self, *, date_format: str, filename, device_mac):
        self.date_format = date_format
        self.filename = filename
        self.device_mac = device_mac

        self.create()
        self.last_recorded = self.latest()


    def stats(self) -> dict:
        stats = {'count': 0}
        for row in ['min', 'max', 'mean', 'sum']:
            stats[row] = {
                    'co2': None,
                    'temperature': None,
                    'humidity': None,
                    'pressure': None,
                    }

        record = None

        with open(self.filename, 'r') as file:
            reader = csv.reader(file, quoting=csv.QUOTE_ALL)
            header = True
            for row in reader:
                if header:
                    header = False
                    continue

                stats['count'] += 1

                record = {
                        'co2': int(row[1]),
                        'temperature': float(row[2]),
                        'humidity': int(row[3]),
                        'pressure': float(row[4]),
                        }

                for stat in ['co2', 'temperature', 'humidity', 'pressure']:

                    if stats['min'][stat] is None or record[stat] < stats['min'][stat]:
                        stats['min'][stat] = record[stat]
                    if stats['max'][stat] is None or record[stat] > stats['max'][stat]:
                         stats['max'][stat] = record[stat]

                    stats['sum'][stat] = record[stat] + (stats['sum'][stat] or 0)

        if stats['count'] > 1:
            for stat in ['co2', 'temperature', 'humidity', 'pressure']:
                stats['mean'][stat] = stats['sum'][stat] / stats['count']

        return stats


    # normally we load the entire file line-by-line and find this in the process
    # if we're only updating, this lets us skip the rest of the file
    def latest(self) -> dict:
        with open(self.filename, 'rb') as file:
            try:
                file.seek(-2, os.SEEK_END)
                while file.read(1) != b'\n':
                    file.seek(-2, os.SEEK_CUR)

                line = file.readline().decode()
                reader = csv.reader([line], quoting=csv.QUOTE_ALL)
                row = reader.__next__()
                return {
                    'date': datetime.strptime(row[0], self.date_format).replace(tzinfo=timezone.utc),
                    'co2': int(row[1]),
                    'temperature': float(row[2]),
                    'humidity': int(row[3]),
                    'pressure': float(row[4]),
                }

            # there's only one line, so there aren't any records
            except OSError:
                return {
                    'date': None,
                    'co2': None,
                    'temperature': None,
                    'humidity': None,
                    'pressure': None,
                }


    def print_table(self, stats: dict, width: int) -> None:
        print(f"{"temp  humid  press    co2":>34}")
        columns = [  # (name, formatter)
            ('temperature', lambda x: f"{(x * 9/5 + 32):,.0f}°"),  # convert celsius to fahrenheit
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
                        'x'
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
        try:
            with open(self.filename, 'x') as file:
                file.write('Time(MM/DD/YYYY hh:mm:ss),Carbon dioxide(ppm),Temperature(°C),Relative humidity(%),Atmospheric pressure(hPa)\n')
        except FileExistsError:
            pass


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

        # get_all_records() returns empty records those that are filtered by "start"
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
    parser.add_argument('--file', metavar='file_path', help='path to the record file', default='records.csv')
    # we're formatting this way for https://github.com/the-butcher/ARANET4_VIS
    parser.add_argument('--format', metavar='date_format', help='date format', default='%m/%d/%Y %H:%M:%S')
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
