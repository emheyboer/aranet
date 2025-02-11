#!/usr/bin/env venv/bin/python
import http.client, urllib
import sys
import asyncio
from aranet4 import Aranet4Scanner
from enum import Enum

global ago
global prev


class DisplayMode(Enum):
    terminal = 1
    notification = 2


def notify(title, body, ttl):
    conn = http.client.HTTPSConnection("api.pushover.net:443")
    conn.request("POST", "/1/messages.json",
    urllib.parse.urlencode({
        "token": "",
        "user": "",
        "title": title,
        "message": body,
        "ttl": ttl,
        "html": 1,
    }), { "Content-type": "application/x-www-form-urlencoded" })
    conn.getresponse()


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


def show_change(prev, curr):
    delta = curr - prev
    symbol = '⇵'
    if delta > 0:
        symbol = '↑'
    elif delta < 0:
        symbol = '↓'
    return f"{symbol} {delta:.01f}"


def maybe_notify(prev, curr, body):
    ttl = curr.interval - curr.ago
    alerts = []
    
    dco2 = curr.co2 - prev.co2
    if dco2 > 0 and curr.co2 > 1400:
        alerts.append('rising co2')
    if curr.temperature < 50:
        alerts.append('low temperature')
    if curr.temperature > 80:
        alerts.append('high temperature')

    if len(alerts) > 0:
        title = '; '.join(alerts)
        notify(title, body, max(ttl, 60))


def display_readings(prev, curr, mode):
    color = curr.status.name.lower()

    output = '\n' if mode == DisplayMode.terminal else ''
    output += f"  CO2:           {colorize(color, curr.co2, mode)} ppm {show_change(prev.co2, curr.co2)}" + '\n'
    output += f"  Temperature:   {(curr.temperature):.01f} °F {show_change(prev.temperature, curr.temperature)}" + '\n'
    output += f"  Humidity:      {curr.humidity}% {show_change(prev.humidity, curr.humidity)}" + '\n'
    output += f"  Pressure:      {curr.pressure:.01f} hPa {show_change(prev.pressure, curr.pressure)}" + '\n'
    output += f"  Battery:       {curr.battery}%" + '\n'
    output += f"  Age:           {curr.ago}/{curr.interval}"

    return output

def on_scan(advertisement):
    global ago
    global prev

    if advertisement.device.address != mac:
        return

    if not advertisement.readings:
        return
    
    curr = advertisement.readings

    if ago is None or advertisement.readings.ago < ago: 
        if prev is None:
            prev = curr

        curr.temperature = curr.temperature * 9/5 + 32

        term_output = display_readings(prev, curr, DisplayMode.terminal)
        notif_output = display_readings(prev, curr, DisplayMode.notification)

        print(term_output, end='\r')
        maybe_notify(prev, curr, notif_output)

        prev = curr

        
    ago = curr.ago
        

async def main(argv):
    global ago, prev
    ago = None
    prev = None

    known_age = None
    offset = 0

    scanner = Aranet4Scanner(on_scan)
    await scanner.start()
    while True: # Run forever
        await asyncio.sleep(1)
        if prev is not None:
            # If the scanner finds something new, use that age
            if known_age is None or known_age != ago:
                known_age = ago
                offset = 0
            offset += 1
            print(f"  Age:           {known_age + offset}/{prev.interval}" + ' '*5, end='\r')
    await scanner.stop()


if __name__== "__main__":
    mac = ''

    try:
        asyncio.run(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print("User interupted.")