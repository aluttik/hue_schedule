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
import socket

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


def parse_time(config, when, tomorrow=False):
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

    date = datetime.datetime.now(tz=location.tz).replace(hour=0, minute=0, second=0, microsecond=0)
    result = getattr(location, when)(date=date)

    if result < datetime.datetime.now(tz=location.tz):
        result = getattr(location, when)(date=date+datetime.timedelta(days=1))

    return result


class HueJob(collections.namedtuple('HueJob', 'when lights command')):
    @property
    def until(self):
        now = datetime.datetime.now(tz=self.when.tzinfo)
        return (self.when - now).total_seconds()

    def __str__(self):
        return '%s(when=%r, lights=%r, command=%s)' % (self.__class__.__name__, self.when.isoformat(), self.lights, json.dumps(self.command))


class HueScheduler(object):
    named_times = ('dawn', 'sunrise', 'noon', 'sunset', 'dusk')

    def __init__(self):
        config_dir = get_config_dir()
        if not os.path.exists(config_dir):
            print 'NOT EXISTS: %r' % config_dir
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
        try:
            self.bridge.set_light(job.lights, job.command)
        except socket.error:
            logger.error('socket error; could not perform job')
            self.queue.appendleft(job)
            time.sleep(10)
            return

        if self.queue:
            logger.info('success! next job is %s', self.next_job)
        else:
            self.schedule_jobs()

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
                when = parse_time(config=config['location'], when=job['when'], tomorrow=False)
            else:
                timezone = payload['config']['timezone']
                settings = {'TIMEZONE': timezone, 'RETURN_AS_TIMEZONE_AWARE': True}
                when = dateparser.parse(job['when'] + ' today', settings=settings)
                if when < now.replace(tzinfo=when.tzinfo):
                    when += datetime.timedelta(days=1)

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
            logger.info('scheduled job: %s', job)

        if not self.queue:
            logger.info('no jobs to schedule')


def main():
    log_exit = lambda: logger.info('exiting script')
    atexit.register(log_exit)
    logger.info('beginning script')

    wait_time = 1
    scheduler = HueScheduler()

    while True:
        if scheduler.config_modified:
            logger.info('config modified')
            scheduler.schedule_jobs()
        elif scheduler.next_job and scheduler.next_job.until <= 0:
            scheduler.do_next_job()
        else:
            time.sleep(wait_time)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except:
        logger.exception('exception raised')
