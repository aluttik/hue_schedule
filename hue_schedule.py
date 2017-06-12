#!/usr/bin/python
# -*- coding: utf-8 -*-

"""
hue_schedule
~~~~~~~~~~~~

Schedule when to change your change your hue lights


"""
import atexit
import collections
import datetime
import json
import logging
import logging.handlers
import os
import platform
import time

import astral
import dateparser
import phue
import rgbxy

logger = logging.getLogger('hue_schedule')
handler = logging.handlers.RotatingFileHandler('/var/log/hue_schedule.log')
formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)


def get_config_dir():
    homedir = os.getenv(phue.USER_HOME) or os.path.expanduser('~')
    if homedir and homedir.strip(os.path.sep) and os.access(homedir, os.W_OK):
        return os.path.join(homedir, '.hue_schedule')
    elif 'iPad' in platform.machine() or 'iPhone' in platform.machine():
        return os.path.join(homedir, 'Documents', '.hue_schedule')
    elif os.path.exists('/etc/hue_schedule'):
        return '/etc/hue_schedule'
    else:
        return os.getcwd()

_a = astral.Astral()
_a.solar_depression = 'civil'


def parse_time(config, when):
    city = config.get('city', '')
    region = config.get('region', '')
    latitude = config.get('latitude', 0.0)
    longitude = config.get('longitude', 0.0)
    timezone = config.get('timezone', '')
    elevation = config.get('elevation', 0)
    if city:
        latitude = latitude or _a[city].latitude
        longitude = longitude or _a[city].longitude
        region = region or _a[city].region
        timezone = timezone or _a[city].timezone
        elevation = elevation or _a[city].elevation
    info = (city, region, latitude, longitude, timezone, elevation)
    location = astral.Location(info)
    get_time = getattr(location, when)
    return get_time()


class HueJob(collections.namedtuple('HueJob', 'when lights command')):
    @property
    def until(self):
        now = datetime.datetime.now(tz=self.when.tzinfo)
        return (self.when - now).total_seconds()


class HueScheduler(object):
    named_times = ('dawn', 'sunrise', 'noon', 'sunset', 'dusk')

    def __init__(self):
        config_dir = get_config_dir()
        if not os.path.exists(config_dir):
            os.mkdir(config_dir)

        self.queue = collections.deque()
        self.last_mtime = None

        self.bridge_config_path = os.path.join(config_dir, 'bridge.json')
        self.schedule_config_path = os.path.join(config_dir, 'schedule.json')

        logger.info('using bridge config at %s', self.bridge_config_path)
        self.bridge = phue.Bridge(config_file_path=self.bridge_config_path)
        self.schedule_jobs()

    def do_next_job(self):
        job = self.queue.popleft()
        logger.info('running %s for lights %s', job.command, job.lights)
        self.bridge.set_light(job.lights, job.command)

        if self.queue:
            logger.info('%s jobs scheduled; next job at %s', len(self.queue), self.next_job.when.strftime('%I:%M:%S %p %Z').strip())
        else:
            logger.info('no jobs scheduled')

    @property
    def next_job(self):
        if self.queue:
            return self.queue[0]

    @property
    def config_modified(self):
        mtime = os.path.getmtime(self.schedule_config_path)
        return mtime != self.last_mtime

    def schedule_jobs(self):
        now = datetime.datetime.now()
        self.queue.clear()

        self.last_mtime = os.path.getmtime(self.schedule_config_path)
        with open(self.schedule_config_path) as fp:
            logger.info('reading schedule config at %s', self.schedule_config_path)
            config = json.load(fp)

        payload = self.bridge.get_api()

        jobs = []
        for job in config['jobs']:
            lights = job['lights']

            if job['when'] in self.named_times and 'location' in config:
                when = parse_time(config=config['location'], when=job['when'])
            else:
                timezone = payload['config']['timezone']
                settings = {'TIMEZONE': timezone, 'RETURN_AS_TIMEZONE_AWARE': True}
                when = dateparser.parse(job['when'], settings=settings)

            if when < now.replace(tzinfo=when.tzinfo):
                continue

            hex_string = job['color'].lstrip('#')
            transition = int(job.get('transition', 0) * 10) or 1
            on = job['on']

            gamuts = collections.Counter()
            for light_id in lights:
                light = payload['lights'][str(light_id)]
                model = light['modelid']
                gamut = rgbxy.get_light_gamut(model)
                gamuts.update([gamut])
            gamut = gamuts.most_common(1)[0][0]
            converter = rgbxy.Converter(gamut=gamut)
            xy = converter.hex_to_xy(hex_string)

            command = {'on': on, 'xy': xy, 'transitiontime': transition}
            jobs.append(HueJob(when, lights, command))

        for job in sorted(jobs):
            self.queue.append(job)

        if self.queue:
            logger.info('%s jobs scheduled; next job at %s', len(self.queue), self.next_job.when.strftime('%I:%M:%S %p %Z').strip())
        else:
            logger.info('no jobs scheduled')

def main():
    log_exit = lambda: logger.info('exiting script')
    atexit.register(log_exit)
    logger.info('beginning script')

    wait_time = 60
    scheduler = HueScheduler()

    while True:
        while scheduler.next_job.until > 0:
            if not scheduler.queue or scheduler.config_modified:
                if scheduler.queue:
                    logger.info('config modified')

                scheduler.schedule_jobs()

                if scheduler.queue:
                    continue

            time.sleep(wait_time)

        scheduler.do_next_job()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except:
        logger.exception('exception raised')
