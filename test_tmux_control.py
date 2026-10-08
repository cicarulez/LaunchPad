import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tmux_control import ActionManager, discover


class TmuxControlTests(unittest.TestCase):
    def wait_for_job(self, manager):
        for _ in range(200):
            job = manager.public_jobs()[0]
            if job["state"] != "running":
                return job
            time.sleep(.01)
        self.fail("Il comando non è terminato")

    def test_restart_stops_then_starts_with_detach(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "kill-example.sh").write_text("echo stop > sequence\n")
            (root / "example.sh").write_text(
                '# --detach\ntest "$(cat sequence)" = stop || exit 1\necho start:$1 >> sequence\n')
            manager = ActionManager(root)
            manager.submit("example", "restart", discover(root, {"example"}, []))
            self.assertEqual(self.wait_for_job(manager)["state"], "done")
            self.assertEqual((root / "sequence").read_text(), "stop\nstart:--detach\n")

    def test_restart_does_not_start_after_failed_stop(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "kill-example.sh").write_text("echo stop-failed\nexit 2\n")
            (root / "example.sh").write_text("# --detach\ntouch started\n")
            manager = ActionManager(root)
            manager.submit("example", "restart", discover(root, {"example"}, []))
            job = self.wait_for_job(manager)
            self.assertEqual(job["state"], "failed")
            self.assertIn("stop-failed", job["output"])
            self.assertFalse((root / "started").exists())

    def test_restart_requires_active_session_and_both_scripts(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            manager = ActionManager(root)
            for active, start, stop in [(False, True, True), (True, False, True), (True, True, False)]:
                with self.subTest(active=active, start=start, stop=stop):
                    with self.assertRaisesRegex(ValueError, "Riavvio non disponibile"):
                        manager.submit("example", "restart", [
                            {"id": "example", "active": active, "start": start, "stop": stop}])

    def test_discovers_new_scripts_and_declared_actions(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "example.sh").write_text("#!/bin/bash\n# launchpad-session: example3\n# --detach\n")
            (root / "kill-example.sh").write_text("#!/bin/bash\n")
            (root / "switch-example.sh").write_text("#!/bin/bash\n# launchpad-actions: status fast\n")
            projects = discover(root, {"example3"}, [{"tmux": {"session": "example3"}}])
            self.assertEqual(projects, [{"id": "example", "session": "example3", "active": True,
                                         "service_count": 1, "start": True, "stop": True,
                                         "actions": ["status", "fast"]}])

    def test_rejects_unlisted_actions_and_runs_only_declared_script(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "example.sh").write_text("#!/bin/bash\n# --detach\n")
            (root / "switch-example.sh").write_text(
                "#!/bin/bash\n# launchpad-actions: status\necho result:$1\n")
            manager = ActionManager(root)
            projects = discover(root, {"example"}, [])
            with self.assertRaises(ValueError):
                manager.submit("example", "remove-all", projects)
            job = manager.submit("example", "status", projects)
            for _ in range(50):
                latest = manager.public_jobs()[0]
                if latest["state"] != "running":
                    break
                time.sleep(.01)
            self.assertEqual(latest["state"], "done")
            self.assertEqual(latest["output"], "result:status")
            self.assertEqual(job["action"], "status")


if __name__ == "__main__":
    unittest.main()
