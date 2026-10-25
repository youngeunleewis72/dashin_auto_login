import contextlib
from datetime import datetime
import importlib.util
import io
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1] / "collector_fix"


class FakeComError(Exception):
    def __init__(self, hresult=-2147352567, scode=-2147024809):
        self.hresult = hresult
        self.excepinfo = (0, None, None, None, 0, scode)
        super().__init__(hresult, "예외가 발생했습니다.", self.excepinfo, None)


def load(name):
    client = types.ModuleType("win32com.client")
    client.Dispatch = Mock(return_value=types.SimpleNamespace(IsConnect=0))
    win32com = types.ModuleType("win32com")
    win32com.client = client
    database = types.ModuleType("pg8000.dbapi")
    database.OperationalError = type("FakeDbError", (Exception,), {})
    database.connect = Mock()
    pg8000 = types.ModuleType("pg8000")
    pg8000.dbapi = database
    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = Mock()
    with patch.dict(sys.modules, {"win32com": win32com, "win32com.client": client,
                                 "pg8000": pg8000, "pg8000.dbapi": database, "dotenv": dotenv}):
        spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class CollectorFixTests(unittest.TestCase):
    def program_fixture(self):
        module = load("collect_investor_trend_min")
        status = Mock()
        status.GetLimitRemainCount.return_value = 10
        program = Mock()
        program.GetDibStatus.return_value = 0
        program.GetHeaderValue.return_value = 1926
        return module, status, program

    def test_wrapped_and_direct_invalidarg_at_next_row_preserve_complete_data(self):
        for error in [FakeComError(), FakeComError(-2147024809, None), FakeComError(0x80070057, None)]:
            module, status, program = self.program_fixture()
            def data(field, index):
                if index == 1:
                    raise error
                return {0: 900, 1: 10, 2: 30, 3: 15, 4: 40, 7: 45}[field]
            program.GetDataValue.side_effect = data
            with contextlib.redirect_stdout(io.StringIO()):
                result = module.fetch_program_trend("KOSPI", status, program, datetime(2026, 10, 25))
            self.assertEqual([row[2] for row in result], [20, 25, 45])
            self.assertEqual(len(result), 3)
            self.assertEqual(program.GetDataValue.call_args.args, (0, 1))

    def test_invalidarg_in_first_row_or_another_field_remains_failure(self):
        for bad_field in [0, 7]:
            module, status, program = self.program_fixture()
            def data(field, index):
                if field == bad_field:
                    raise FakeComError()
                return 900 if field == 0 else 1
            program.GetDataValue.side_effect = data
            with self.assertRaises(FakeComError):
                module.fetch_program_trend("KOSPI", status, program, datetime(2026, 10, 25))

    def test_non_invalidarg_at_next_row_remains_failure(self):
        module, status, program = self.program_fixture()
        def data(field, index):
            if index == 1:
                raise FakeComError(scode=-2147024891)
            return 900 if field == 0 else 1
        program.GetDataValue.side_effect = data
        with self.assertRaises(FakeComError):
            module.fetch_program_trend("KOSPI", status, program, datetime(2026, 10, 25))

    def test_runner_stops_after_first_nonzero_exit(self):
        runner = load("daily_runner")
        with patch.object(runner, "send_telegram_message") as notify, \
             patch.object(runner.subprocess, "run", side_effect=[types.SimpleNamespace(returncode=0),
                                                                types.SimpleNamespace(returncode=7)]) as run, \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(runner.main(), 7)
            self.assertEqual(run.call_count, 2)
            self.assertIn("collect_market_index_min.py", run.call_args.args[0][-1])
            self.assertFalse(any("정상적으로 완료" in call.args[0] for call in notify.call_args_list))

    def test_runner_returns_failure_on_spawn_exception(self):
        runner = load("daily_runner")
        with patch.object(runner, "send_telegram_message"), \
             patch.object(runner.subprocess, "run", side_effect=OSError("test spawn error")) as run, \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(runner.main(), 1)
            self.assertEqual(run.call_count, 1)

    def test_runner_completes_all_four_scripts(self):
        runner = load("daily_runner")
        with patch.object(runner, "send_telegram_message"), \
             patch.object(runner.subprocess, "run", return_value=types.SimpleNamespace(returncode=0)) as run, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main(), 0)
            self.assertEqual(run.call_count, 4)
            self.assertEqual(run.call_args.kwargs["env"]["PYTHONUNBUFFERED"], "1")
            self.assertEqual(run.call_args.kwargs["cwd"], str(ROOT))

    def test_disconnected_collectors_raise_instead_of_succeeding(self):
        for name in ["collect_stock_info", "collect_market_index_min", "collect_investor_trend_min", "collect_stock_history"]:
            with self.subTest(name=name), contextlib.redirect_stdout(io.StringIO()):
                module = load(name)
                entry = module.fetch_daishin_stocks if name == "collect_stock_info" else module.main
                with self.assertRaises(RuntimeError):
                    entry()

    def test_database_errors_are_not_swallowed(self):
        for name in ["collect_market_index_min", "collect_investor_trend_min", "collect_stock_history"]:
            with self.subTest(name=name), contextlib.redirect_stdout(io.StringIO()):
                module = load(name)
                connection = Mock()
                connection.cursor.return_value.executemany.side_effect = RuntimeError("test database failure")
                module.get_db_connection = Mock(return_value=connection)
                args = ("stock_min", [(1,)]) if name == "collect_stock_history" else ([(1,)],)
                with self.assertRaisesRegex(RuntimeError, "test database failure"):
                    module.save_to_db(*args)
                connection.rollback.assert_called_once()
                connection.close.assert_called_once()

    def test_notification_error_does_not_print_token_url(self):
        runner = load("daily_runner")
        output = io.StringIO()
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "fake-secret", "TELEGRAM_CHAT_ID": "fake-chat"}), \
             patch.object(runner.urllib.request, "urlopen", side_effect=OSError("https://api.telegram.org/botfake-secret/sendMessage")), \
             contextlib.redirect_stdout(output):
            runner.send_telegram_message("test")
        self.assertNotIn("fake-secret", output.getvalue())
        self.assertIn("OSError", output.getvalue())

    def test_api_errors_are_not_returned_as_partial_success(self):
        cases = [
            ("collect_market_index_min", "fetch_index_history"),
            ("collect_investor_trend_min", "fetch_investor_trend"),
            ("collect_investor_trend_min", "fetch_program_trend"),
            ("collect_stock_history", "fetch_history"),
        ]
        for module_name, function in cases:
            with self.subTest(function=function), contextlib.redirect_stdout(io.StringIO()):
                module = load(module_name)
                status = Mock()
                status.GetLimitRemainCount.return_value = 10
                chart = Mock()
                chart.GetDibStatus.return_value = 1
                chart.GetDibMsg1.return_value = "fake API failure"
                if function == "fetch_index_history":
                    args = ("U001", "KOSPI", 20260101, 20261025, status, chart)
                elif function == "fetch_history":
                    args = ("005930", "A005930", "D", 20260101, 20261025, status, chart)
                elif function == "fetch_investor_trend":
                    args = ("KOSPI", 1, status, chart, datetime(2026, 10, 25))
                else:
                    args = ("KOSPI", status, chart, datetime(2026, 10, 25))
                with self.assertRaises(RuntimeError):
                    getattr(module, function)(*args)

    def test_metadata_database_error_propagates(self):
        module = load("collect_stock_info")
        connection = Mock()
        connection.cursor.return_value.executemany.side_effect = RuntimeError("fake DB error")
        module.psycopg2.connect.return_value = connection
        stock = dict(stock_code="005930", stock_name="test", market_type="KOSPI",
                     sector="test", listing_shares=1, is_active=True)
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, "fake DB error"):
            module.update_db([stock])
        connection.rollback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
