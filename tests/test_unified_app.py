import asyncio
import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import alerting.bot.app as bot_app
import main_collector


class UnifiedAppTests(unittest.IsolatedAsyncioTestCase):
    async def test_update_errors_are_logged_without_stopping_application(self):
        context = Mock(error=RuntimeError("handler failed"))

        with self.assertLogs("telegram_bot", level="ERROR") as captured:
            await bot_app._handle_update_error(None, context)

        self.assertIn("bot tiếp tục nhận update khác", captured.output[0])

    def test_collector_supervisor_retries_after_startup_failure(self):
        class StopEvent:
            stopped = False
            waits = []

            def is_set(self):
                return self.stopped

            def wait(self, timeout):
                self.waits.append(timeout)
                return self.stopped

            def set(self):
                self.stopped = True

        stop_event = StopEvent()
        calls = []

        def fail_then_stop(stop_event, install_signal_handlers):
            calls.append(install_signal_handlers)
            if len(calls) == 1:
                raise RuntimeError("startup failure")
            stop_event.set()

        with patch.object(bot_app, "run_loop", side_effect=fail_then_stop):
            bot_app._run_collector_supervised(stop_event)

        self.assertEqual(calls, [False, False])
        self.assertEqual(stop_event.waits, [bot_app._COLLECTOR_RESTART_INITIAL_SEC])

    async def test_app_lifecycle_starts_and_stops_collector_thread(self):
        def wait_for_stop(stop_event, install_signal_handlers):
            self.assertFalse(install_signal_handlers)
            stop_event.wait(2)

        with patch.object(bot_app, "run_loop", side_effect=wait_for_stop):
            await bot_app._start_collector(None)
            thread = bot_app._collector_thread
            self.assertTrue(thread.is_alive())

            await bot_app._stop_collector(None)

        self.assertFalse(thread.is_alive())
        self.assertTrue(bot_app._collector_stop.is_set())

    async def test_collector_loop_cleans_up_after_stop_event(self):
        stop_event = threading.Event()
        collector = Mock()
        collector.fetch_active_alarms.return_value = []
        pipeline = Mock()
        pipeline.run_cycle.return_value = {
            "new_alerts": 0,
            "resolved_alerts": 0,
            "new_partial_alerts": 0,
            "resolved_partial_alerts": 0,
        }

        def has_active_alarms():
            stop_event.set()
            return False

        future_report = datetime.now(timezone.utc) + timedelta(days=1)
        with (
            patch.object(main_collector, "build_collector", return_value=collector),
            patch.object(main_collector, "AlarmPipeline", return_value=pipeline),
            patch.object(main_collector, "sync_alarms") as sync_alarms,
            patch.object(main_collector, "next_report_time", return_value=future_report),
            patch.object(main_collector, "has_active_alarms", side_effect=has_active_alarms),
            patch.object(main_collector, "start_outbox_worker") as start_outbox,
            patch.object(main_collector, "stop_outbox_worker") as stop_outbox,
            patch.object(main_collector, "close_connection") as close_connection,
        ):
            main_collector.run_loop(stop_event, install_signal_handlers=False)

        collector.fetch_active_alarms.assert_called_once_with()
        sync_alarms.assert_called_once_with([])
        pipeline.run_cycle.assert_called_once_with()
        start_outbox.assert_called_once_with()
        stop_outbox.assert_called_once_with()
        close_connection.assert_called_once_with()

    async def test_collector_startup_failure_cleans_up_before_raising(self):
        with (
            patch.object(main_collector, "build_collector", side_effect=RuntimeError("bad config")),
            patch.object(main_collector, "stop_outbox_worker") as stop_outbox,
            patch.object(main_collector, "close_connection") as close_connection,
        ):
            with self.assertRaisesRegex(RuntimeError, "bad config"):
                main_collector.run_loop(install_signal_handlers=False)

        stop_outbox.assert_called_once_with()
        close_connection.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
