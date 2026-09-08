#!/usr/bin/env python3
from pathlib import Path
import sys, tempfile, time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import db, gaming, qos_priority, shaping


def cfg():
    return {
        'guest': {'speed_down_kbit': 1000, 'speed_up_kbit': 256},
        'network': {'line_down_mbit': 20, 'line_up_mbit': 2},
        'features': {'speed_limits': True},
    }


def main():
    with tempfile.TemporaryDirectory() as td:
        db.DB = Path(td) / 'test.db'
        gaming.STATE = Path(td) / 'gaming.json'
        db.init()
        with db.con() as c:
            cols = {r['name'] for r in c.execute('PRAGMA table_info(devices)')}
        assert 'priority' in cols

        a, _ = db.upsert_device('02:00:00:00:00:01', '192.168.2.101', 'Game PC')
        b, _ = db.upsert_device('02:00:00:00:00:02', '192.168.2.102', 'Phone')
        assert next(d for d in db.devices() if d['id'] == a)['priority'] == 'normal'
        db.update_device(a, priority='high')
        assert next(d for d in db.devices() if d['id'] == a)['priority'] == 'high'
        db.update_device(b, is_guest=1)
        assert next(d for d in db.devices() if d['id'] == b)['priority'] == 'low'

        assert qos_priority.normalize('HIGH') == 'high'
        try:
            qos_priority.normalize('urgent')
            raise AssertionError('invalid priority accepted')
        except ValueError:
            pass

        # Regression: priority must keep the duplicate-IP sanitizer installed by
        # shaping_policy. Two rows sharing one IP must never emit two nft marks.
        cdup, _ = db.upsert_device('02:00:00:00:00:03', '192.168.2.102', 'Stale duplicate')
        db.update_device(cdup, priority='low')
        down, up = shaping._limits(cfg(), db.devices(), [])
        assert len({x['ip'] for x in down}) == len(down)
        assert len({x['ip'] for x in up}) == len(up)

        s = gaming.start(cfg(), a, 30, 'low', True, 3000, 700, 1000)
        assert s['active'] and s['device_id'] == a
        devices = db.devices(); ga = next(d for d in devices if d['id'] == a); gb = next(d for d in devices if d['id'] == b)
        rate, prio = gaming.effective_policy(ga, 'down', 0, 'high')
        assert rate == 0 and prio == 'high'
        rate, prio = gaming.effective_policy(gb, 'down', 5000, 'low')
        assert rate == 1000 and prio == 'low'
        rate, prio = gaming.effective_policy(gb, 'up', 1200, 'low')
        assert rate == 700 and prio == 'low'
        gaming.stop('test')
        assert not gaming.status()['active']

        gaming.start(cfg(), a, 15)
        raw = gaming.current(); raw['expires_at'] = int(time.time()) - 1; gaming._write(raw)
        assert gaming.tick(cfg()) is True
        assert not gaming.status()['active']

        source = Path('app/qos_priority.py').read_text()
        assert "'high': 0" in source and "'normal': 1" in source and "'low': 2" in source
        assert "'parent', '1:1'" in source
        assert "'fq_codel'" in source
        assert '_remove_old_device_classes' in source
        assert "'class', 'add'" in source
        assert 'fair_share' in source
        assert "'burst', '32kb'" in source
        assert 'shaping_policy._sanitize_devices' in source
        assert 'priority HTB verification failed' in source
        main_source = Path('app/main.py').read_text()
        assert 'restart_wifi_after_save' in main_source
        qos_routes = main_source[main_source.find("if p=='/api/device/priority'"):main_source.find("if p=='/api/device/update'")]
        assert 'restart_wifi_after_save' not in qos_routes and 'restart_wifi' not in qos_routes
        print('QoS priority / Smart Gaming semantic checks passed')


if __name__ == '__main__':
    main()
