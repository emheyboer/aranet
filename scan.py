#!/usr/bin/env venv/bin/python
import http.client, urllib
import sys
import asyncio
from aranet4 import Aranet4Scanner
from enum import Enum
import configparser


class DisplayMode(Enum):
    terminal = 1
    notification = 2


class Monitor:
    def __init__(self, config_filename):
        self.last_seen = None
        self.interval = None
        self.history = []
        self.current = None
        self.config = self.load_config(config_filename)

    
    async def start(self):
        known_age = None
        offset = 0

        scanner = Aranet4Scanner(self.on_scan)
        await scanner.start()
        while True: # Run forever
            await asyncio.sleep(1)
            if len(self.history) > 0:
                # If the scanner finds something new, use that age
                if known_age is None or known_age != self.last_seen:
                    known_age = self.last_seen
                    offset = 0
                offset += 1
                print(f"  Age:           {known_age + offset}/{self.interval}" + ' '*5, end='\r')
        await scanner.stop()


    def load_config(self, filename):
            config = configparser.ConfigParser()
            config.read(filename)
            return config


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
        delta = curr - prev
        symbol = '⇵'
        if delta > 0:
            symbol = '↑'
        elif delta < 0:
            symbol = '↓'
        return f"{symbol} {delta:.01f}"


    def maybe_notify(self, body):
        current = self.current
        previous = self.history[-1]
        ttl = self.interval - self.last_seen
        alerts = []
        
        dco2 = current.co2 - previous.co2
        if dco2 > 0 and curr.co2 > 1400:
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
        previous = self.history[-1]
        color = current.status.name.lower()

        output = '\n' if mode == DisplayMode.terminal else ''
        output += f"  CO2:           {colorize(color, current.co2, mode)} ppm {self.show_change(previous.co2, current.co2)}" + '\n'
        output += f"  Temperature:   {(current.temperature):.01f} °F {self.show_change(previous.temperature, current.temperature)}" + '\n'
        output += f"  Humidity:      {current.humidity}% {self.show_change(previous.humidity, current.humidity)}" + '\n'
        output += f"  Pressure:      {current.pressure:.01f} hPa {self.show_change(previous.pressure, current.pressure)}" + '\n'
        output += f"  Battery:       {current.battery}%" + '\n'
        output += f"  Age:           {self.last_seen}/{self.interval}"

        return output

    def on_scan(self, advertisement):
        if advertisement.device.address != self.config['aranet']['mac']:
            return

        if not advertisement.readings:
            return
        
        self.current = advertisement.readings

        if self.last_seen is None or advertisement.readings.ago < self.last_seen: 
            self.current.temperature = self.current.temperature * 9/5 + 32

            term_output = self.display_readings(DisplayMode.terminal)
            notif_output = self.display_readings(DisplayMode.notification)

            print(term_output, end='\r')
            self.maybe_notify(notif_output)

            self.history.append(self.current)

            
        self.history.last_seen = self.current.last_seen


def colorize(color, text, mode):
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
        

def main(argv):
    monitor = Monitor('config.ini')

    try:
        asyncio.run(monitor.start())
    except KeyboardInterrupt:
        print("User interupted.")


if __name__== "__main__":
    main(sys.argv[1:])