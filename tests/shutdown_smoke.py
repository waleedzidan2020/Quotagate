from __future__ import annotations

from pathlib import Path

from app import main, system_power

ROOT = Path(__file__).resolve().parent.parent


def check_power_helper() -> None:
    assert system_power.SHUTDOWN_COMMAND == ('/sbin/shutdown', '-h', 'now')
    try:
        system_power.validate_shutdown_request('shutdown')
    except ValueError as exc:
        assert 'SHUTDOWN' in str(exc)
    else:
        raise AssertionError('lowercase confirmation must be rejected')

    call = {}

    def fake_popen(argv, **kwargs):
        call['argv'] = argv
        call['kwargs'] = kwargs
        return object()

    system_power.execute_shutdown(fake_popen)
    assert call['argv'] == ['/sbin/shutdown', '-h', 'now']
    assert call['kwargs']['shell'] is False
    assert call['kwargs']['stdin'] is not None
    assert call['kwargs']['stdout'] is not None
    assert call['kwargs']['stderr'] is not None

    started = []
    original_validate = system_power.validate_shutdown_request
    system_power.validate_shutdown_request = lambda confirm: system_power.SHUTDOWN_COMMAND

    class FakeThread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            started.append(self.kwargs)

    try:
        result = system_power.schedule_shutdown('SHUTDOWN', thread_factory=FakeThread)
    finally:
        system_power.validate_shutdown_request = original_validate

    assert result['ok'] is True
    assert len(started) == 1
    assert started[0]['daemon'] is True
    assert started[0]['name'] == 'quotagate-shutdown'


def make_handler():
    h = object.__new__(main.Handler)
    h.client_address = ('192.168.2.50', 45678)
    replies = []

    def fake_json(obj, status=200, cookie=None):
        replies.append((status, obj))
        return obj

    h.json = fake_json
    return h, replies


def check_route_security() -> None:
    original_config_load = main.config.load
    original_validate = main.system_power.validate_shutdown_request
    original_schedule = main.system_power.schedule_shutdown
    original_event = main.db.event

    try:
        main.config.load = lambda: {}
        events = []
        main.db.event = lambda message, level='info': events.append((level, message))

        # No authenticated dashboard session: shutdown helper must never be reached.
        h, replies = make_handler()
        called = []
        h.need = lambda: False
        main.system_power.validate_shutdown_request = lambda confirm: called.append(confirm)
        h.api_post('/api/admin/shutdown', {'confirm': 'SHUTDOWN'})
        assert called == []
        assert replies == []

        # Authenticated GET is explicitly rejected: endpoint is POST-only.
        h, replies = make_handler()
        h.need = lambda: True
        h.api_get('/api/admin/shutdown', {})
        assert replies[-1][0] == 405

        # Wrong confirmation or unavailable privilege/path is surfaced as an error.
        h, replies = make_handler()
        h.need = lambda: True
        main.system_power.validate_shutdown_request = lambda confirm: (_ for _ in ()).throw(PermissionError('permission denied'))
        main.system_power.schedule_shutdown = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('must not schedule'))
        h.api_post('/api/admin/shutdown', {'confirm': 'SHUTDOWN'})
        assert replies[-1][0] == 503
        assert 'permission denied' in replies[-1][1]['error']

        # Authenticated POST schedules only the fixed action; arbitrary command fields are ignored.
        h, replies = make_handler()
        h.need = lambda: True
        validated = []
        scheduled = []
        main.system_power.validate_shutdown_request = lambda confirm: validated.append(confirm) or system_power.SHUTDOWN_COMMAND

        def fake_schedule(confirm, **kwargs):
            scheduled.append((confirm, kwargs))
            return {'ok': True, 'message': 'Shutdown initiated', 'delay_seconds': 2.0}

        main.system_power.schedule_shutdown = fake_schedule
        h.api_post('/api/admin/shutdown', {'confirm': 'SHUTDOWN', 'command': 'rm -rf /'})
        assert validated == ['SHUTDOWN']
        assert len(scheduled) == 1 and scheduled[0][0] == 'SHUTDOWN'
        assert replies[-1][0] == 202
        assert replies[-1][1]['ok'] is True
        assert any('192.168.2.50' in message for _, message in events)
    finally:
        main.config.load = original_config_load
        main.system_power.validate_shutdown_request = original_validate
        main.system_power.schedule_shutdown = original_schedule
        main.db.event = original_event


def check_frontend() -> None:
    js = (ROOT / 'web' / 'shutdown.js').read_text(encoding='utf-8')
    loader = (ROOT / 'web' / 'app.js').read_text(encoding='utf-8')
    css = (ROOT / 'web' / 'styles.css').read_text(encoding='utf-8')
    assert "api('/api/admin/shutdown',{confirm:'SHUTDOWN'})" in js
    assert 'id="shutdownConfirmBtn"' in js and 'disabled' in js
    assert "input.value!=='SHUTDOWN'" in js
    assert 'Shutdown Server' in js
    assert "load('shutdown.js')" in loader
    assert '.modal[hidden]{display:none!important}' in css


if __name__ == '__main__':
    check_power_helper()
    check_route_security()
    check_frontend()
    print('shutdown feature smoke checks: OK')
