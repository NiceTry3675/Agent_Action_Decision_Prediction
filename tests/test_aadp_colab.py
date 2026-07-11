import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from colab import aadp_colab
from colab import cloud_sync
from colab import start_cli_daemon
from colab import vm_agent


class AADPColabWrapperTests(unittest.TestCase):
    def setUp(self):
        self.lane = aadp_colab.Lane(
            name="a",
            session="aadp-a",
            state_file=aadp_colab.REPO / ".colab/test-lane.json",
            exchange="AADP_exchange",
            gpu="A100",
            packages=("transformers==4.46.3", "sentencepiece", "safetensors"),
        )

    def test_lane_config_and_colab_global_option_order(self):
        lane = aadp_colab.get_lane("a")

        self.assertEqual(lane.session, "aadp-a")
        self.assertEqual(lane.gpu, "A100")
        self.assertEqual(
            aadp_colab.colab_argv(lane, "new", "-s", lane.session, "--gpu", "T4"),
            [
                "colab",
                "--auth=adc",
                "--config",
                str(lane.state_file),
                "new",
                "-s",
                "aadp-a",
                "--gpu",
                "T4",
            ],
        )

    def test_adc_check_uses_colab_whoami_without_reading_a_token(self):
        completed = subprocess.CompletedProcess([], 0, "identity metadata\n")
        with mock.patch.object(aadp_colab, "_run_capture", return_value=completed) as run:
            aadp_colab._check_adc(self.lane)

        argv = run.call_args.args[0]
        self.assertEqual(argv[-1], "whoami")
        self.assertIn("--auth=adc", argv)
        self.assertFalse(any("gcloud" in part for part in argv))

    def test_unknown_gpu_is_rejected_before_any_subprocess(self):
        with mock.patch.object(aadp_colab, "_run_capture") as run:
            with self.assertRaisesRegex(aadp_colab.AADPColabError, "invalid GPU"):
                aadp_colab.up(
                    self.lane,
                    gpu="A10G",
                    reuse=False,
                    skip_mount=False,
                    skip_bootstrap=False,
                    skip_daemon=False,
                    allow_gpu_mismatch=False,
                    allow_account_orphans=False,
                )
        run.assert_not_called()

    def test_remote_exec_checks_sentinel_and_injects_lane_environment(self):
        seen = {}

        def fake_run(argv, **kwargs):
            payload = Path(argv[argv.index("-f") + 1])
            seen["argv"] = list(argv)
            seen["source"] = payload.read_text(encoding="utf-8")
            return subprocess.CompletedProcess(
                argv,
                0,
                'AADP_RESULT={"ok":true,"op":"bootstrap"}\n',
            )

        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "bootstrap.py"
            script.write_text("print('remote body')\n", encoding="utf-8")
            with mock.patch.object(aadp_colab, "_run_capture", side_effect=fake_run):
                result = aadp_colab._exec_file(
                    self.lane,
                    script,
                    remote_env={"AADP_EXCHANGE_DIR": "AADP_exchange_b"},
                    timeout=900,
                    expected_op="bootstrap",
                )

        self.assertTrue(result["ok"])
        self.assertEqual(seen["argv"][:5], [
            "colab", "--auth=adc", "--config", str(self.lane.state_file), "exec"
        ])
        self.assertEqual(seen["argv"][-2:], ["--timeout", "900"])
        self.assertIn('os.environ["AADP_EXCHANGE_DIR"] = "AADP_exchange_b"', seen["source"])

    def test_remote_exec_rc_zero_without_sentinel_is_failure(self):
        with self.assertRaisesRegex(aadp_colab.AADPColabError, "no AADP_RESULT"):
            aadp_colab._parse_remote_result("remote traceback only\n", "bootstrap")

    def test_launch_preserves_argv_and_refuses_a_second_live_job(self):
        fresh = {
            "ts": time.time(), "control_mode": "cli", "exchange": "AADP_exchange",
            "release_safe": True, "run": None,
        }
        with mock.patch.object(aadp_colab, "_read_heartbeat", return_value=fresh), \
                mock.patch.object(aadp_colab, "_cloud_call") as cloud:
            aadp_colab.launch(
                self.lane,
                "train_transformer.py",
                ["--", "--notes", "text with spaces", "--bias", "-0.1"],
            )
        cloud.assert_called_once_with(
            self.lane,
            "launch",
            "train_transformer.py",
            "--",
            "--notes",
            "text with spaces",
            "--bias",
            "-0.1",
        )

        live = {
            "ts": time.time(), "control_mode": "cli", "exchange": "AADP_exchange",
            "release_safe": False,
            "run": {"alive": True, "pid": 123, "log": "run.log"},
        }
        with mock.patch.object(aadp_colab, "_read_heartbeat", return_value=live), \
                mock.patch.object(aadp_colab, "_cloud_call") as cloud:
            with self.assertRaisesRegex(aadp_colab.AADPColabError, "already has live pid"):
                aadp_colab.launch(self.lane, "train_transformer.py", ["--", "--epochs", "1"])
        cloud.assert_not_called()

    def test_up_failure_stops_only_the_newly_created_session(self):
        commands = []

        def fake_run(argv, **kwargs):
            commands.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, "ok\n")

        with mock.patch.object(aadp_colab, "_require_program", return_value="colab"), \
                mock.patch.object(aadp_colab, "_check_cli_version"), \
                mock.patch.object(aadp_colab, "_refresh_local_session", return_value=None), \
                mock.patch.object(aadp_colab, "_account_assignments", return_value={}), \
                mock.patch.object(aadp_colab, "_known_lane_endpoints", return_value=set()), \
                mock.patch.object(aadp_colab, "_read_local_session", return_value={"endpoint": "ep-1"}), \
                mock.patch.object(aadp_colab, "_run_capture", side_effect=fake_run), \
                mock.patch.object(aadp_colab, "_verify_stopped", return_value=True), \
                mock.patch.object(aadp_colab, "probe", side_effect=aadp_colab.AADPColabError("bad GPU")):
            with self.assertRaisesRegex(aadp_colab.AADPColabError, "bad GPU"):
                aadp_colab.up(
                    self.lane,
                    gpu="A100",
                    reuse=False,
                    skip_mount=False,
                    skip_bootstrap=False,
                    skip_daemon=False,
                    allow_gpu_mismatch=False,
                    allow_account_orphans=False,
                )

        self.assertIn("new", commands[0])
        self.assertIn("stop", commands[-1])

    def test_down_guards_live_job_and_verifies_stop(self):
        live = {"ts": time.time(), "run": {"alive": True, "pid": 55}}
        with mock.patch.object(
            aadp_colab, "_refresh_local_session", return_value={"endpoint": "ep-1"}
        ), mock.patch.object(aadp_colab, "_read_heartbeat", return_value=live), \
                mock.patch.object(aadp_colab, "_run_capture") as run:
            with self.assertRaisesRegex(aadp_colab.AADPColabError, "still alive"):
                aadp_colab.down(self.lane, force=False, pull_latest=False)
        run.assert_not_called()

        idle = {
            "ts": time.time(), "control_mode": "cli", "exchange": "AADP_exchange",
            "release_safe": True, "run": {"alive": False},
        }
        completed = subprocess.CompletedProcess([], 0, "[colab] Session terminated.\n")
        with mock.patch.object(
            aadp_colab, "_refresh_local_session", return_value={"endpoint": "ep-1"}
        ), mock.patch.object(aadp_colab, "_read_heartbeat", return_value=idle), \
                mock.patch.object(aadp_colab, "_run_capture", return_value=completed) as run, \
                mock.patch.object(aadp_colab, "_verify_stopped", return_value=True):
            aadp_colab.down(self.lane, force=False, pull_latest=False)
        self.assertIn("stop", run.call_args.args[0])

    def test_down_requires_fresh_safe_heartbeat_without_force(self):
        with mock.patch.object(
            aadp_colab, "_refresh_local_session", return_value={"endpoint": "ep-1"}
        ), mock.patch.object(aadp_colab, "_read_heartbeat", return_value=None), \
                mock.patch.object(aadp_colab, "_run_capture") as run:
            with self.assertRaisesRegex(aadp_colab.AADPColabError, "heartbeat is missing"):
                aadp_colab.down(self.lane, force=False, pull_latest=False)
        run.assert_not_called()

    def test_stop_verification_does_not_treat_sessions_failure_as_success(self):
        failed = subprocess.CompletedProcess([], 1, "auth failed\n")
        with mock.patch.object(aadp_colab, "_run_capture", return_value=failed), \
                mock.patch.object(aadp_colab, "_read_local_session", return_value=None), \
                mock.patch.object(aadp_colab.time, "sleep"):
            self.assertFalse(aadp_colab._verify_stopped(self.lane, "ep-1", attempts=2))

    def test_reuse_probes_the_accelerator_saved_in_session_state(self):
        existing = {"endpoint": "ep-1", "accelerator": "T4"}
        with mock.patch.object(aadp_colab, "_require_program", return_value="colab"), \
                mock.patch.object(aadp_colab, "_check_cli_version"), \
                mock.patch.object(aadp_colab, "_refresh_local_session", return_value=existing), \
                mock.patch.object(aadp_colab, "_account_assignments", return_value={"ep-1": "aadp-a"}), \
                mock.patch.object(aadp_colab, "_known_lane_endpoints", return_value={"ep-1"}), \
                mock.patch.object(aadp_colab, "probe") as probe:
            aadp_colab.up(
                self.lane,
                gpu=None,
                reuse=True,
                skip_mount=True,
                skip_bootstrap=True,
                skip_daemon=True,
                allow_gpu_mismatch=False,
                allow_account_orphans=False,
            )
        probe.assert_called_once_with(self.lane, expected_gpu="T4", allow_mismatch=False)

    def test_vm_agent_release_contract_preserves_notebook_mode(self):
        with mock.patch.dict(os.environ, {"AADP_CLI_MODE": "1"}):
            self.assertEqual(vm_agent.release_exit_code(), 0)
            result = vm_agent.run_command({"id": "test", "cmd": "@unassign"})
            self.assertEqual(result["rc"], 126)
            self.assertIn("aadp_colab.py down", result["output"])
        with mock.patch.dict(os.environ, {"AADP_CLI_MODE": "0"}):
            self.assertEqual(vm_agent.release_exit_code(), vm_agent.UNASSIGN_EXIT)

    def test_legacy_unassign_fails_closed_for_cli_heartbeat(self):
        heartbeat = subprocess.CompletedProcess(
            [], 0, '{"control_mode":"cli"}\n', ""
        )
        with mock.patch.object(cloud_sync.subprocess, "run", return_value=heartbeat):
            with self.assertRaisesRegex(SystemExit, "aadp_colab.py down"):
                cloud_sync.refuse_cli_unassign()

    def test_vm_side_launch_is_the_atomic_concurrency_guard(self):
        previous = {"pid": 987, "log": "/content/AADP/logs/run_old.log"}
        with mock.patch.object(vm_agent, "training_status", return_value=(previous, True)), \
                mock.patch.object(vm_agent.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(RuntimeError, "refusing concurrent launch"):
                vm_agent.launch("train_transformer.py", "--epochs 1")
        popen.assert_not_called()


class StartDaemonTests(unittest.TestCase):
    def test_detached_daemon_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "AADP"
            (work / "colab").mkdir(parents=True)
            (work / "logs").mkdir()
            (work / "colab/vm_agent.py").write_text("# fixture\n", encoding="utf-8")
            state = root / "aadp_state.json"
            state.write_text("{}", encoding="utf-8")
            process = mock.Mock(pid=4321)

            with mock.patch.dict(
                os.environ,
                {
                    "AADP_WORK_DIR": str(work),
                    "AADP_STATE_PATH": str(state),
                    "AADP_EXCHANGE_DIR": "AADP_exchange_b",
                },
            ), mock.patch.object(start_cli_daemon.subprocess, "Popen", return_value=process) as popen, \
                    mock.patch.object(start_cli_daemon, "_proc_state", return_value="S"), \
                    mock.patch.object(start_cli_daemon.time, "sleep"):
                result = start_cli_daemon.start_daemon()

        self.assertTrue(result["ok"])
        kwargs = popen.call_args.kwargs
        self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(kwargs["cwd"], work)
        self.assertEqual(kwargs["env"]["AADP_CLI_MODE"], "1")
        self.assertEqual(kwargs["env"]["AADP_EXCHANGE_DIR"], "AADP_exchange_b")


if __name__ == "__main__":
    unittest.main()
