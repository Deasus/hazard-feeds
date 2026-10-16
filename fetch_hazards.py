#!/usr/bin/env python3
"""Fetch public global hazard feeds and publish normalized JSON for browser clients.

Outputs (data/):
  gdacs.json    GDACS multi-hazard alerts (earthquake, flood, cyclone, wildfire,
                drought, volcano), from the GDACS RSS feed
  volcano.json  GDACS volcanic-eruption events that are not already in gdacs.json
  status.json   per-feed health: ok, item count, upstream, timestamps, last error

Both data files hold {"generated_at", "upstream", "count", "alerts": [...]}. Each
alert is {title, event, severity, area, onset, expires, lat, lng, source,
_gdacsLevel, _evCode}.

A failed or invalid fetch never overwrites the last good file; status.json records
the failure, so a client can tell a quiet feed from a broken one. A data file is
rewritten only when its alerts change, so the git history only churns status.json.

Stdlib only.
"""
import json
import os
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'data')
UA = 'hazard-feeds/1.0 (+https://github.com/Deasus/hazard-feeds)'

GDACS_RSS = 'https://www.gdacs.org/xml/rss.xml'
GDACS_VOLCANO = ('https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH'
                 '?eventlist=VO&alertlevel=green;orange;red')

NS = {
    'gdacs': 'http://www.gdacs.org',
    'geo': 'http://www.w3.org/2003/01/geo/wgs84_pos#',
    'georss': 'http://www.georss.org/georss',
}
EVENT_NAMES = {'EQ': 'Earthquake', 'FL': 'Flood', 'TC': 'Tropical Cyclone',
               'WF': 'Wildfire', 'VO': 'Volcano', 'DR': 'Drought'}


def now_iso():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def fetch(url, timeout=30, attempts=3):
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': '*/*'})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f'fetch failed after {attempts} attempts: {last}')


def valid_point(lat, lng):
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    if -90 <= lat <= 90 and -180 <= lng <= 180:
        return lat, lng
    return None


def rss_point(item):
    """geo:Point/geo:lat+long first (what browsers read), georss:point "lat lng" second."""
    for lat_path, lng_path in (('geo:Point/geo:lat', 'geo:Point/geo:long'), ('geo:lat', 'geo:long')):
        p = valid_point(item.findtext(lat_path, namespaces=NS), item.findtext(lng_path, namespaces=NS))
        if p:
            return p
    parts = (item.findtext('georss:point', default='', namespaces=NS) or '').split()
    return valid_point(*parts[:2]) if len(parts) >= 2 else None


def parse_gdacs(raw):
    root = ET.fromstring(raw)
    if root.find('channel') is None:
        raise ValueError('not an RSS document (no <channel>)')
    alerts, ids = [], set()
    for it in root.iter('item'):
        point = rss_point(it)
        if not point:
            continue
        g = lambda name: (it.findtext(f'gdacs:{name}', default='', namespaces=NS) or '').strip()
        ev, level = g('eventtype'), g('alertlevel').upper()
        event = EVENT_NAMES.get(ev, 'Hazard')
        alerts.append({
            'title': (it.findtext('title') or '').strip() or event,
            'event': event,
            'severity': level or 'Moderate',
            'area': g('country') or 'Global',
            'onset': (it.findtext('pubDate') or '').strip(),
            'expires': None,
            'lat': point[0], 'lng': point[1],
            'source': 'GDACS',
            '_gdacsLevel': level, '_evCode': ev,
        })
        if ev and g('eventid'):
            ids.add((ev, g('eventid')))
    return alerts, ids


def parse_volcano(raw, skip):
    data = json.loads(raw)
    if 'features' not in data:
        raise ValueError('GDACS event list has no "features" key')
    alerts = []
    for f in data['features']:
        p = f.get('properties') or {}
        coords = (f.get('geometry') or {}).get('coordinates') or []
        if p.get('eventtype') != 'VO' or len(coords) < 2:
            continue
        if ('VO', str(p.get('eventid'))) in skip:
            continue
        point = valid_point(coords[1], coords[0])
        if not point:
            continue
        level = str(p.get('alertlevel') or '').upper()
        name = ' '.join(str(p.get('name') or p.get('eventname') or 'Volcanic activity').split())
        country = p.get('country') or 'Global'
        alerts.append({
            'title': f'{name} ({country})',
            'event': 'Volcano',
            'severity': level or 'Moderate',
            'area': country,
            'onset': p.get('fromdate') or '',
            'expires': None,
            'lat': point[0], 'lng': point[1],
            'source': 'GDACS',
            '_gdacsLevel': level, '_evCode': 'VO',
        })
    alerts.sort(key=lambda a: a['onset'], reverse=True)
    return alerts


def read_json(name, default):
    try:
        with open(os.path.join(DATA, name), encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(name, obj):
    path = os.path.join(DATA, name)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
        fh.write('\n')
    os.replace(tmp, path)


def publish(name, upstream, alerts, stamp):
    """Rewrite data/<name>.json only when the alerts changed. Returns True if written."""
    prev = read_json(f'{name}.json', {})
    if prev.get('alerts') == alerts:
        return False
    write_json(f'{name}.json', {'generated_at': stamp, 'upstream': upstream,
                                'count': len(alerts), 'alerts': alerts})
    return True


def main():
    os.makedirs(DATA, exist_ok=True)
    stamp = now_iso()
    status = read_json('status.json', {'feeds': {}})
    feeds = status.setdefault('feeds', {})
    gdacs_ids = set()

    def run(name, upstream, work):
        st = feeds.setdefault(name, {})
        st.update(upstream=upstream, last_attempt=stamp)
        try:
            alerts = work()
            changed = publish(name, upstream, alerts, stamp)
            st.update(ok=True, count=len(alerts), last_success=stamp, error=None)
            if changed:
                st['last_change'] = stamp
            print(f'{name}: ok, {len(alerts)} alerts{" (changed)" if changed else ""}')
        except Exception as e:  # keep the last good file; record why
            st.update(ok=False, last_failure=stamp, error=f'{type(e).__name__}: {e}'[:300])
            print(f'{name}: FAILED — {st["error"]}')

    def gdacs():
        alerts, ids = parse_gdacs(fetch(GDACS_RSS))
        if not alerts:
            raise ValueError('RSS parsed but yielded 0 geolocated items')
        gdacs_ids.update(ids)
        return alerts

    run('gdacs', GDACS_RSS, gdacs)
    run('volcano', GDACS_VOLCANO, lambda: parse_volcano(fetch(GDACS_VOLCANO), gdacs_ids))

    status['generated_at'] = stamp
    write_json('status.json', status)


if __name__ == '__main__':
    main()
