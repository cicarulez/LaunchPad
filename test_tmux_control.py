import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tmux_control import ActionManager, discover


class TmuxControlTests(unittest.TestCase):
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
