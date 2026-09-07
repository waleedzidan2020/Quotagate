from __future__ import annotations
import threading, time
from . import config, db, network, shaping_policy


def _counter_delta(previous, current):
    """Return a safe byte delta, or 0 when a counter is new/reset."""
    if previous is None:
        return 0
    previous = int(previous)
    current = int(current)
    if current < previous:
        return 0
    return current - previous


def _speed_mbps(delta_bytes, elapsed_seconds):
    elapsed = float(elapsed_seconds or 0)
    if elapsed <= 0:
        return 0.0
    return max(0.0, (int(delta_bytes) * 8.0) / elapsed / 1_000_000.0)


class UsageTracker:
    def __init__(self):
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._prev = {}
        self._live = {}
        self._gateway_live = {'up': 0.0, 'down': 0.0}
        self._last_sample_mono = None
        self._last_poll_mono = 0.0
        self._running = False
        self._poll_seconds = 2.0
        self._alpha = 0.4
        self._last_error = ''
        self._last_error_at = 0
        self._last_logged_error_at = 0.0
        self._last_reset_log_at = 0.0

    def _settings(self):
        c = config.load()
        u = c.get('usage', {})
        enabled = bool(u.get('enabled', True))
        poll = max(1.0, min(float(u.get('poll_seconds', 2) or 2), 60.0))
        alpha = max(0.05, min(float(u.get('speed_smoothing_alpha', 0.4) or 0.4), 1.0))
        return enabled, poll, alpha

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name='quotagate-usage', daemon=True)
            self._thread.start()
            return True

    def stop(self):
        self._stop.set()

    def reset_baseline(self):
        """Make current nft counters the new baseline without recounting old bytes."""
        devices = shaping_policy._sanitize_devices(db.devices())
        owners = {int(d['id']): d for d in devices}
        raw = network.counters()
        cur = {k: int(v) for k, v in raw.items() if int(k[0]) in owners}
        now_mono = time.monotonic()
        with self._lock:
            self._prev = cur
            self._live = {did: {'up': 0.0, 'down': 0.0} for did in owners}
            self._gateway_live = {'up': 0.0, 'down': 0.0}
            self._last_sample_mono = now_mono
            self._last_poll_mono = now_mono
        return {'tracked_devices': len(owners), 'counter_rules': len(cur)}

    def _log_error(self, message):
        now = time.time()
        with self._lock:
            self._last_error = str(message)[:500]
            self._last_error_at = int(now)
        if now - self._last_logged_error_at >= 60:
            self._last_logged_error_at = now
            try:
                db.event('Usage tracker error: ' + str(message), 'error')
            except Exception:
                pass

    def _note_counter_reset(self):
        now = time.time()
        if now - self._last_reset_log_at >= 60:
            self._last_reset_log_at = now
            try:
                db.event('Usage tracker recovered after nftables counter reset/rebuild', 'warning')
            except Exception:
                pass

    def _poll_once(self):
        enabled, poll, alpha = self._settings()
        with self._lock:
            self._poll_seconds = poll
            self._alpha = alpha
        if not enabled:
            with self._lock:
                self._prev = {}
                self._live = {}
                self._gateway_live = {'up': 0.0, 'down': 0.0}
                self._last_sample_mono = None
                self._last_poll_mono = time.monotonic()
            return

        devices = shaping_policy._sanitize_devices(db.devices())
        owners = {int(d['id']): d for d in devices}
        raw = network.counters()
        cur = {k: int(v) for k, v in raw.items() if int(k[0]) in owners}
        now_mono = time.monotonic()

        with self._lock:
            prev = dict(self._prev)
            last_sample = self._last_sample_mono
            live_before = {k: dict(v) for k, v in self._live.items()}
            gateway_before = dict(self._gateway_live)

        elapsed = (now_mono - last_sample) if last_sample is not None else 0.0
        rows = []
        new_live = {}
        reset_seen = False
        gateway_up = 0
        gateway_down = 0

        for did, dev in owners.items():
            deltas = {'up': 0, 'down': 0}
            for direction in ('up', 'down'):
                key = (did, direction)
                current = cur.get(key)
                previous = prev.get(key)
                if current is None:
                    delta = 0
                else:
                    if previous is not None and current < int(previous):
                        reset_seen = True
                    delta = _counter_delta(previous, current)
                deltas[direction] = delta

            if deltas['up'] or deltas['down']:
                rows.append((did, deltas['up'], deltas['down']))
            gateway_up += deltas['up']
            gateway_down += deltas['down']

            old_speed = live_before.get(did, {'up': 0.0, 'down': 0.0})
            instant_up = _speed_mbps(deltas['up'], elapsed)
            instant_down = _speed_mbps(deltas['down'], elapsed)
            if last_sample is None:
                smooth_up = smooth_down = 0.0
            else:
                smooth_up = alpha * instant_up + (1.0 - alpha) * float(old_speed.get('up', 0.0))
                smooth_down = alpha * instant_down + (1.0 - alpha) * float(old_speed.get('down', 0.0))
            new_live[did] = {'up': smooth_up, 'down': smooth_down}

        if rows:
            db.add_usage_batch(rows)
        if gateway_up or gateway_down:
            db.add_gateway_usage(gateway_up, gateway_down)

        inst_gup = _speed_mbps(gateway_up, elapsed)
        inst_gdown = _speed_mbps(gateway_down, elapsed)
        if last_sample is None:
            gateway_live = {'up': 0.0, 'down': 0.0}
        else:
            gateway_live = {
                'up': alpha * inst_gup + (1.0 - alpha) * float(gateway_before.get('up', 0.0)),
                'down': alpha * inst_gdown + (1.0 - alpha) * float(gateway_before.get('down', 0.0)),
            }

        with self._lock:
            self._prev = cur
            self._live = new_live
            self._gateway_live = gateway_live
            self._last_sample_mono = now_mono
            self._last_poll_mono = now_mono
            self._last_error = ''
            self._last_error_at = 0

        if reset_seen:
            self._note_counter_reset()

    def _loop(self):
        with self._lock:
            self._running = True
        try:
            db.event('Usage tracker started', 'info')
        except Exception:
            pass
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as exc:
                self._log_error(exc)
            with self._lock:
                delay = self._poll_seconds
            self._stop.wait(delay)
        with self._lock:
            self._running = False

    def _db_totals(self):
        today = db.day()
        month = db.period()
        device_daily = {}
        device_monthly = {}
        gateway_day = {'up_bytes': 0, 'down_bytes': 0}
        gateway_month = {'up_bytes': 0, 'down_bytes': 0}
        with db.con() as c:
            for r in c.execute('SELECT device_id,up_bytes,down_bytes FROM usage_daily WHERE day=?', (today,)):
                device_daily[int(r['device_id'])] = {'up_bytes': int(r['up_bytes']), 'down_bytes': int(r['down_bytes'])}
            for r in c.execute('SELECT device_id,up_bytes,down_bytes FROM usage_monthly WHERE period=?', (month,)):
                device_monthly[int(r['device_id'])] = {'up_bytes': int(r['up_bytes']), 'down_bytes': int(r['down_bytes'])}
            r = c.execute('SELECT up_bytes,down_bytes FROM gateway_usage_daily WHERE day=?', (today,)).fetchone()
            if r:
                gateway_day = {'up_bytes': int(r['up_bytes']), 'down_bytes': int(r['down_bytes'])}
            r = c.execute('SELECT up_bytes,down_bytes FROM gateway_usage_monthly WHERE period=?', (month,)).fetchone()
            if r:
                gateway_month = {'up_bytes': int(r['up_bytes']), 'down_bytes': int(r['down_bytes'])}
        return device_daily, device_monthly, gateway_day, gateway_month

    def snapshot(self):
        devices = shaping_policy._sanitize_devices(db.devices())
        daily, monthly, gateway_day, gateway_month = self._db_totals()
        with self._lock:
            live = {k: dict(v) for k, v in self._live.items()}
            gateway_live = dict(self._gateway_live)
            running = self._running
            poll_seconds = self._poll_seconds
            last_poll = self._last_poll_mono
            last_error = self._last_error
            last_error_at = self._last_error_at

        out = []
        for d in devices:
            did = int(d['id'])
            td = daily.get(did, {'up_bytes': 0, 'down_bytes': 0})
            tm = monthly.get(did, {'up_bytes': 0, 'down_bytes': 0})
            lv = live.get(did, {'up': 0.0, 'down': 0.0})
            out.append({
                'device_id': did,
                'name': d.get('name') or d.get('mac') or ('Device-' + str(did)),
                'ip': d.get('ip', ''),
                'mac': d.get('mac', ''),
                'today_up_bytes': int(td['up_bytes']),
                'today_down_bytes': int(td['down_bytes']),
                'today_total_bytes': int(td['up_bytes']) + int(td['down_bytes']),
                'month_up_bytes': int(tm['up_bytes']),
                'month_down_bytes': int(tm['down_bytes']),
                'month_total_bytes': int(tm['up_bytes']) + int(tm['down_bytes']),
                'live_up_mbps': round(float(lv['up']), 3),
                'live_down_mbps': round(float(lv['down']), 3),
            })

        age = None if not last_poll else max(0.0, time.monotonic() - last_poll)
        return {
            'timestamp': int(time.time()),
            'gateway': {
                'today_up_bytes': int(gateway_day['up_bytes']),
                'today_down_bytes': int(gateway_day['down_bytes']),
                'today_total_bytes': int(gateway_day['up_bytes']) + int(gateway_day['down_bytes']),
                'month_up_bytes': int(gateway_month['up_bytes']),
                'month_down_bytes': int(gateway_month['down_bytes']),
                'month_total_bytes': int(gateway_month['up_bytes']) + int(gateway_month['down_bytes']),
                'live_up_mbps': round(float(gateway_live['up']), 3),
                'live_down_mbps': round(float(gateway_live['down']), 3),
            },
            'devices': out,
            'tracker': {
                'running': bool(running),
                'poll_seconds': poll_seconds,
                'last_poll_age_seconds': None if age is None else round(age, 3),
                'tracked_devices': len(devices),
                'last_error': last_error,
                'last_error_at': last_error_at,
            },
        }


TRACKER = UsageTracker()


def start():
    return TRACKER.start()


def stop():
    TRACKER.stop()


def reset_baseline():
    return TRACKER.reset_baseline()


def snapshot():
    return TRACKER.snapshot()


def diagnostics():
    return snapshot()['tracker']


def daily_history(n=31):
    """Gateway daily totals only; avoids double-counting device + gateway rows."""
    with db.con() as c:
        return [
            {'day': r['day'], 'bytes': int(r['up_bytes']) + int(r['down_bytes'])}
            for r in c.execute('SELECT day,up_bytes,down_bytes FROM gateway_usage_daily ORDER BY day DESC LIMIT ?', (int(n),))
        ]
