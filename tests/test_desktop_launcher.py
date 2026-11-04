import unittest
from unittest.mock import Mock, patch
from desktop import launcher


class DesktopLauncherTests(unittest.TestCase):
    def test_existing_service_is_reused(self):
        with patch.object(launcher, 'is_studio', return_value=True), patch.object(launcher.subprocess, 'Popen') as spawn:
            self.assertIsNone(launcher.ensure_server(launcher.workspace()))
            spawn.assert_not_called()

    def test_closing_never_stops_an_active_job(self):
        proc = Mock(); proc.poll.return_value = None
        with patch.object(launcher, 'request', return_value=b'{"active_job":{"id":"job"}}'):
            launcher.shutdown_owned(proc)
        proc.terminate.assert_not_called()

    def test_closing_stops_owned_idle_service(self):
        proc = Mock(); proc.poll.return_value = None
        with patch.object(launcher, 'request', return_value=b'{"active_job":null}'):
            launcher.shutdown_owned(proc)
        proc.terminate.assert_called_once()

    def test_unknown_service_state_is_not_stopped(self):
        proc = Mock(); proc.poll.return_value = None
        with patch.object(launcher, 'request', side_effect=OSError('unreachable')):
            launcher.shutdown_owned(proc)
        proc.terminate.assert_not_called()


if __name__ == '__main__': unittest.main()
