import importlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

ENV_VARS = {
    "ZOHO_CLIENT_ID": "client",
    "ZOHO_CLIENT_SECRET": "secret",
    "ZOHO_REFRESH_TOKEN": "refresh",
    "ZOHO_FOLDER_ID": "folder",
    "ZOHO_REGION": "us",
}


class FakeResponse:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        if text:
            self.text = text
        elif json_data is not None:
            self.text = json.dumps(json_data)
        else:
            self.text = ""

    def raise_for_status(self):
        if 400 <= self.status_code < 600:
            raise requests.HTTPError(response=self)

    def json(self):
        if self._json is None:
            raise ValueError("No JSON data available.")
        return self._json


class UploadActionTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.sample_file = Path(self.tmpdir.name) / "sample.txt"
        self.sample_file.write_text("content")

        self._restore_env = {key: os.environ.get(key) for key in ENV_VARS}
        for key, value in ENV_VARS.items():
            os.environ[key] = value

        if "upload_zoho" in sys.modules:
            del sys.modules["upload_zoho"]
        self.upload = importlib.import_module("upload_zoho")
        # ensure module reads latest environment
        self.upload = importlib.reload(self.upload)

    def tearDown(self):
        for key, old in self._restore_env.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old

    def test_conflict_abort_exits(self):
        responses = [
            FakeResponse(409, {"Error": "FILE_NAME_ALREADY_EXISTS"}, "conflict"),
        ]

        def fake_post(*args, **kwargs):
            return responses.pop(0)

        with mock.patch.object(self.upload.requests, "post", side_effect=fake_post):
            with self.assertRaises(SystemExit) as ctx:
                self.upload.upload_file(
                    api_base="https://api",
                    token="token",
                    path=str(self.sample_file),
                    remote_name="artifact.txt",
                    conflict_mode="abort",
                    max_retries=1,
                    retry_delay=0,
                    enable_logs=False,
                )
        self.assertIn("File already exists", str(ctx.exception))

    def test_conflict_rename_generates_new_name(self):
        call_history = []
        responses = [
            FakeResponse(409, {"Error": "FILE_NAME_ALREADY_EXISTS"}, "conflict"),
            FakeResponse(
                200,
                {"data": [{"id": "res123", "attributes": {"Permalink": "https://p"}}]},
                "",
            ),
        ]

        def fake_post(url, headers=None, files=None, data=None, timeout=None, json=None):
            call_history.append(
                {"filename": files["content"][0], "data": dict(data)}
            )
            return responses.pop(0)

        with mock.patch.object(self.upload.requests, "post", side_effect=fake_post):
            with mock.patch.object(
                self.upload, "generate_unique_name", return_value="artifact-renamed.txt"
            ):
                resource_id, permalink, final_name = self.upload.upload_file(
                    api_base="https://api",
                    token="token",
                    path=str(self.sample_file),
                    remote_name="artifact.txt",
                    conflict_mode="rename",
                    max_retries=2,
                    retry_delay=0,
                    enable_logs=False,
                )

        self.assertEqual(resource_id, "res123")
        self.assertEqual(permalink, "https://p")
        self.assertEqual(final_name, "artifact-renamed.txt")
        self.assertEqual(call_history[0]["filename"], "artifact.txt")
        self.assertEqual(call_history[1]["filename"], "artifact-renamed.txt")

    def test_conflict_replace_sets_override_flag(self):
        call_history = []
        responses = [
            FakeResponse(409, {"Error": "FILE_NAME_ALREADY_EXISTS"}, "conflict"),
            FakeResponse(
                200,
                {"data": [{"id": "res999", "attributes": {"Permalink": "https://p"}}]},
                "",
            ),
        ]

        def fake_post(url, headers=None, files=None, data=None, timeout=None, json=None):
            call_history.append(
                {"filename": files["content"][0], "data": dict(data)}
            )
            return responses.pop(0)

        with mock.patch.object(self.upload.requests, "post", side_effect=fake_post):
            res_id, permalink, final_name = self.upload.upload_file(
                api_base="https://api",
                token="token",
                path=str(self.sample_file),
                remote_name="artifact.txt",
                conflict_mode="replace",
                max_retries=2,
                retry_delay=0,
                enable_logs=False,
            )

        self.assertEqual(res_id, "res999")
        self.assertEqual(permalink, "https://p")
        self.assertEqual(final_name, "artifact.txt")
        self.assertNotIn("override-name-exist", call_history[0]["data"])
        self.assertEqual(call_history[1]["data"].get("override-name-exist"), "true")

    def test_retry_on_server_error(self):
        responses = [
            FakeResponse(500, None, "server"),
            FakeResponse(
                200,
                {"data": [{"id": "res500", "attributes": {"Permalink": "https://p"}}]},
                "",
            ),
        ]
        calls = []

        def fake_post(url, headers=None, files=None, data=None, timeout=None, json=None):
            calls.append(files["content"][0])
            return responses.pop(0)

        with mock.patch.object(self.upload.requests, "post", side_effect=fake_post):
            with mock.patch.object(self.upload.time, "sleep", return_value=None) as sleeper:
                res_id, _, _ = self.upload.upload_file(
                    api_base="https://api",
                    token="token",
                    path=str(self.sample_file),
                    remote_name="artifact.txt",
                    conflict_mode="abort",
                    max_retries=3,
                    retry_delay=0.01,
                    enable_logs=False,
                )

        self.assertEqual(res_id, "res500")
        self.assertEqual(len(calls), 2)
        sleeper.assert_called_once()

    def test_main_share_skip_uses_internal_link(self):
        internal_link = "https://workdrive.zoho.com/file/internal123"
        with mock.patch.object(
            self.upload, "get_access_token", return_value="token"
        ), mock.patch.object(
            self.upload, "upload_file", return_value=("resABC", internal_link, "doc.txt")
        ), mock.patch.object(self.upload, "share_everyone_view") as share_mock, mock.patch.object(
            self.upload, "create_external_link"
        ) as link_mock, mock.patch.object(
            self.upload, "log_line", return_value=None
        ):
            share_mock.side_effect = AssertionError("share should not be called")
            link_mock.side_effect = AssertionError("link should not be created")
            argv = [
                "upload_zoho.py",
                str(self.sample_file),
                "--share-mode=skip",
                "--link-mode=preview",
                "--stdout-mode=json",
            ]
            with mock.patch.object(sys, "argv", argv):
                buffer = io.StringIO()
                with mock.patch("sys.stdout", buffer):
                    self.upload.main()

        payload = json.loads(buffer.getvalue())
        self.assertIsNone(payload.get("direct_url"))
        self.assertEqual(payload.get("preview_url"), internal_link)
        self.assertEqual(payload.get("resource_id"), "resABC")

    def test_resolve_file_path_returns_absolute(self):
        resolved = self.upload.resolve_file_path(str(self.sample_file))
        self.assertEqual(resolved, os.path.abspath(self.sample_file))

    def test_resolve_file_path_outside_workspace_guidance(self):
        os.environ["GITHUB_WORKSPACE"] = "/github/workspace"
        self.addCleanup(os.environ.pop, "GITHUB_WORKSPACE", None)
        with self.assertRaises(SystemExit) as ctx:
            self.upload.resolve_file_path("/tmp/missing.txt")
        message = str(ctx.exception)
        self.assertIn("Docker-based GitHub Actions", message)
        self.assertIn("workspace", message)

    def test_resolve_file_path_missing_inside_workspace(self):
        workspace = os.path.abspath(self.tmpdir.name)
        os.environ["GITHUB_WORKSPACE"] = workspace
        self.addCleanup(os.environ.pop, "GITHUB_WORKSPACE", None)
        missing = os.path.join(workspace, "not-here.txt")
        with self.assertRaises(SystemExit) as ctx:
            self.upload.resolve_file_path(missing)
        message = str(ctx.exception)
        self.assertIn("File not found in workspace", message)

    def test_main_multiple_files_json_outputs_all_results(self):
        second_file = Path(self.tmpdir.name) / "sample2.txt"
        second_file.write_text("more content")

        output_file = Path(self.tmpdir.name) / "outputs.txt"
        os.environ["GITHUB_OUTPUT"] = str(output_file)
        self.addCleanup(os.environ.pop, "GITHUB_OUTPUT", None)

        upload_side_effects = [
            ("resA", "https://permalinkA", "artifact-a.txt"),
            ("resB", "https://permalinkB", "artifact-b.txt"),
        ]

        link_side_effects = [
            "https://files.example.com/a/download",
            "https://files.example.com/b/download",
        ]

        with mock.patch.object(
            self.upload, "get_access_token", return_value="token"
        ), mock.patch.object(
            self.upload, "upload_file", side_effect=upload_side_effects
        ) as upload_mock, mock.patch.object(
            self.upload, "share_everyone_view"
        ) as share_mock, mock.patch.object(
            self.upload, "create_external_link", side_effect=link_side_effects
        ), mock.patch.object(
            self.upload, "log_line", return_value=None
        ):
            argv = [
                "upload_zoho.py",
                str(self.sample_file),
                str(second_file),
                "--stdout-mode=json",
                "--link-mode=both",
            ]
            with mock.patch.object(sys, "argv", argv):
                buffer = io.StringIO()
                with mock.patch("sys.stdout", buffer):
                    self.upload.main()

        payload = json.loads(buffer.getvalue())
        self.assertIsInstance(payload, list)
        self.assertEqual(len(payload), 2)
        first, second = payload
        self.assertEqual(first["resource_id"], "resA")
        self.assertEqual(second["resource_id"], "resB")
        self.assertIn("direct_url", first)
        self.assertIn("preview_url", second)

        written = output_file.read_text().splitlines()
        self.assertTrue(any(line.startswith("zoho_results_json=") for line in written))
        self.assertTrue(any("zoho_direct_url_2" in line for line in written))

        self.assertEqual(upload_mock.call_count, 2)
        self.assertEqual(share_mock.call_count, 2)

    def test_remote_name_multiple_files_exits(self):
        second_file = Path(self.tmpdir.name) / "sample2.txt"
        second_file.write_text("more content")

        with mock.patch.object(
            self.upload, "get_access_token", return_value="token"
        ), mock.patch.object(
            self.upload, "log_line", return_value=None
        ):
            argv = [
                "upload_zoho.py",
                str(self.sample_file),
                str(second_file),
                "--remote-name=custom.bin",
            ]
            with mock.patch.object(sys, "argv", argv):
                with self.assertRaises(SystemExit) as ctx:
                    self.upload.main()

        self.assertIn("--remote-name", str(ctx.exception))

    def test_glob_pattern_expands_multiple_files(self):
        png_one = Path(self.tmpdir.name) / "image1.png"
        png_two = Path(self.tmpdir.name) / "image2.png"
        png_one.write_bytes(b"one")
        png_two.write_bytes(b"two")

        with mock.patch.object(
            self.upload, "get_access_token", return_value="token"
        ), mock.patch.object(
            self.upload, "upload_file",
            side_effect=[
                ("res1", "https://permalink1", "image1.png"),
                ("res2", "https://permalink2", "image2.png"),
            ],
        ) as upload_mock, mock.patch.object(
            self.upload, "share_everyone_view"
        ) as share_mock, mock.patch.object(
            self.upload, "create_external_link",
            side_effect=[
                "https://files.example.com/1/download",
                "https://files.example.com/2/download",
            ],
        ), mock.patch.object(
            self.upload, "log_line", return_value=None
        ):
            argv = [
                "upload_zoho.py",
                str(Path(self.tmpdir.name) / "*.png"),
                "--stdout-mode=json",
            ]
            with mock.patch.object(sys, "argv", argv):
                buffer = io.StringIO()
                with mock.patch("sys.stdout", buffer):
                    self.upload.main()

        self.assertEqual(upload_mock.call_count, 2)
        first_call = upload_mock.call_args_list[0].kwargs["path"]
        second_call = upload_mock.call_args_list[1].kwargs["path"]
        self.assertTrue(first_call.endswith("image1.png"))
        self.assertTrue(second_call.endswith("image2.png"))
        self.assertEqual(share_mock.call_count, 2)

    def test_glob_pattern_without_matches_exits(self):
        argv = [
            "upload_zoho.py",
            str(Path(self.tmpdir.name) / "*.png"),
        ]

        with mock.patch.object(sys, "argv", argv):
            with self.assertRaises(SystemExit) as ctx:
                self.upload.main()

        self.assertIn("No files matched pattern", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
