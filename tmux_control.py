"""Discover and run local tmux launchers by filename convention."""

from __future__ import annotations

import os
from pathlib import Path
import re
import secrets
import subprocess
import threading
import time


NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
HEADER = re.compile(r"^# launchpad-(session|actions):\s*(.*?)\s*$", re.M)


def read_header(path: Path) -> tuple[dict[str, str], str]:
    try:
        source = path.read_text(errors="replace")
    except OSError:
        return {}, ""
    return dict(HEADER.findall("\n".join(source.splitlines()[:40]))), source


def discover(directory: Path, sessions: set[str], services: list[dict]) -> list[dict]:
    if not directory.is_dir():
        return []
    projects = []
    for path in sorted(directory.glob("*.sh")):
        name = path.stem
        if not NAME.fullmatch(name) or name.startswith(("kill-", "switch-")) or name == "tmux-kill-common":
            continue
        header, source = read_header(path)
        if not source:
            continue
        session = header.get("session", name)
        if not NAME.fullmatch(session):
            continue
        switch_path = directory / f"switch-{name}.sh"
        switch_header, _ = read_header(switch_path)
        actions = [item for item in switch_header.get("actions", "").split()
                   if NAME.fullmatch(item)] if switch_path.is_file() else []
        projects.append({
            "id": name, "session": session, "active": session in sessions,
            "service_count": sum(service.get("tmux", {}).get("session") == session
                                 for service in services if service.get("tmux")),
            "start": "--detach" in source,
            "stop": (directory / f"kill-{name}.sh").is_file(),
            "actions": actions,
        })
    return projects


class ActionManager:
    def __init__(self, directory: Path):
        self.directory = directory
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.jobs: dict[str, dict] = {}
        self.on_change = lambda: None

    def public_jobs(self) -> list[dict]:
        with self.lock:
            return [dict(job) for job in list(self.jobs.values())[-20:]]

    def submit(self, project: str, action: str, projects: list[dict]) -> dict:
        item = next((item for item in projects if item["id"] == project), None)
        if item is None or not NAME.fullmatch(action):
            raise ValueError("Progetto o azione non disponibili")
        if action == "start":
            if item["active"] or not item["start"]:
                raise ValueError("Avvio non disponibile")
            script, args = self.directory / f"{project}.sh", ["--detach"]
        elif action == "stop":
            if not item["active"] or not item["stop"]:
                raise ValueError("Arresto non disponibile")
            script, args = self.directory / f"kill-{project}.sh", []
        elif action == "restart":
            if not item["active"] or not item["stop"] or not item["start"]:
                raise ValueError("Riavvio non disponibile")
            script, args = self.directory / f"kill-{project}.sh", []
        elif action in item["actions"]:
            if not item["active"]:
                raise ValueError("Sessione non attiva")
            script, args = self.directory / f"switch-{project}.sh", [action]
        else:
            raise ValueError("Azione non dichiarata dallo script")
        commands = [(script, args)]
        if action == "restart":
            commands.append((self.directory / f"{project}.sh", ["--detach"]))
        if any(not path.is_file() for path, _ in commands):
            raise ValueError("Script non trovato")
        with self.lock:
            if any(job["project"] == project and job["state"] == "running" for job in self.jobs.values()):
                raise ValueError("Un comando per questo progetto è già in corso")
            job_id = secrets.token_urlsafe(12)
            job = {"id": job_id, "project": project, "action": action, "state": "running",
                   "output": "", "started_at": int(time.time())}
            self.jobs[job_id] = job
            if len(self.jobs) > 100:
                for old_id in list(self.jobs)[:-100]:
                    if self.jobs[old_id]["state"] != "running":
                        del self.jobs[old_id]
        threading.Thread(target=self._run, args=(job_id, commands), daemon=True).start()
        self.on_change()
        return dict(job)

    def _run(self, job_id: str, commands: list[tuple[Path, list[str]]]):
        env = os.environ.copy()
        node_root = Path.home() / ".nvm/versions/node"
        if node_root.is_dir():
            versions = sorted(node_root.glob("*/bin"), reverse=True)
            if versions:
                env["PATH"] = f"{versions[0]}:{env.get('PATH', '')}"
        env["PATH"] = f"{Path.home() / '.local/bin'}:{env.get('PATH', '')}"
        env.pop("TMUX", None)
        output = ""
        state = "done"
        try:
            for script, args in commands:
                result = subprocess.run(["bash", str(script), *args], cwd=self.directory,
                                        env=env, stdin=subprocess.DEVNULL,
                                        capture_output=True, text=True, errors="replace", timeout=600)
                output = (output + result.stdout + result.stderr)[-4000:]
                if result.returncode:
                    state = "failed"
                    output = (output + f"\n{script.name}: comando terminato con codice {result.returncode}")[-4000:]
                    break
            output = output.strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            state, output = "failed", (output + "\n" + str(exc))[-4000:].strip()
        with self.lock:
            self.jobs[job_id].update(state=state, output=output, finished_at=int(time.time()))
        self.on_change()
