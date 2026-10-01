import os
import time
import unittest

from discovery import build_services, proc_info
from docker_discovery import container_bindings, match_binding, parse_started_at


class DockerDiscoveryTests(unittest.TestCase):
    def test_same_port_on_three_ips_maps_to_distinct_containers(self):
        containers = [
            {"Id": digit * 64, "Names": [f"/mongo-{index}"], "Image": "mongo:7",
             "Labels": {"com.docker.compose.project": "mongo-compose"},
             "Ports": [{"IP": f"127.0.20.{index}", "PrivatePort": 27017,
                        "PublicPort": 27017, "Type": "tcp"}]}
            for index, digit in enumerate("abc", start=1)
        ]
        details = {container["Id"]: {"State": {"Pid": index,
                    "StartedAt": "2026-10-01T05:53:25.795593875Z"}}
                   for index, container in enumerate(containers, start=1)}
        bindings = container_bindings(containers, details)
        listeners = [{"address": f"127.0.20.{index}", "port": 27017, "pids": []}
                     for index in range(1, 4)]
        services = build_services(listeners, [], probe=lambda *args: None,
                                  fingerprint=lambda *args: {"protocol": "MongoDB", "protocol_evidence": "port"},
                                  docker_bindings=bindings)
        self.assertEqual([item["docker"]["name"] for item in services],
                         ["mongo-1", "mongo-2", "mongo-3"])
        self.assertTrue(all("pid" not in item["docker"] for item in services))
        self.assertIsNotNone(parse_started_at("2026-10-01T05:53:25.795593875Z"))

    def test_wildcard_bindings_merge_dual_stack(self):
        container = {"id": "abc", "name": "postgres"}
        bindings = [{"address": "0.0.0.0", "port": 5432, "container": container},
                    {"address": "::", "port": 5432, "container": container}]
        self.assertEqual(match_binding("*", 5432, bindings), container)
        self.assertEqual(match_binding("127.0.0.1", 5432, bindings), container)

    def test_own_pid_start_time_is_in_the_past(self):
        started_at = proc_info(os.getpid())["started_at"]
        self.assertIsNotNone(started_at)
        self.assertLessEqual(started_at, time.time())
        self.assertGreater(started_at, time.time() - 120)


if __name__ == "__main__":
    unittest.main()
