#!/usr/bin/python
# -*- coding: utf-8 -*-
import time
import json

import logging
logger = logging.getLogger('lights-on-before-sunset')
handler = logging.FileHandler('/var/log/lights-on-before-sunset.log')
formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)

import requests
import astral
import phue

def connect():
    r = requests.get('https://www.meethue.com/api/nupnp')
    r.raise_for_status()
    data = r.json()
    ip = data[0]['internalipaddress']
    bridge = phue.Bridge(ip)
    logger.info('Connecting to Philips Hue Bridge at %s ...' % ip)
    bridge.connect()
    logger.info('Connected to bridge successfully.')
    return bridge



def read_config():
    with open('/home/pi/.config/lights-on-before-sunset.json') as fp:
        config = json.load(fp)

    for group in config['groups']:
        name = group['name']

        if not all(x in light for x in ['id', 'on_time', 'off_time', 'color']):
            logger.error('Not all lights configured correctly')


def main():
    b = connect()


#while True:
#    main()
#    time.sleep(2)
