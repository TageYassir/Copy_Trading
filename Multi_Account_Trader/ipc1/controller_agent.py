#!/usr/bin/env python3
"""
Controller wrapper for starting/stopping agent processes and sending commands.
Uses multiprocessing.get_context('spawn') to be Windows-safe.
"""
import time
import uuid
import multiprocessing
from multiprocessing import get_context
from multiprocessing import Queue
import threading

try:
    from Multi_Account_Trader.ipc1 import agent as agent_module
except Exception:
    agent_module = None


# Each agent has (process, cmd_queue, resp_queue)
class ControllerAgent:
    def __init__(self):
        self._ctx = get_context('spawn')
        self._agents = {}  # account_id -> {"proc":..., "cmd_q":..., "resp_q":..., "cfg":...}
        self._lock = threading.Lock()

    def start_agent(self, account_cfg, timeout=30):
        """
        Start an agent for account_cfg (dict must include 'account').
        Returns True or (False, reason).
        """
        acc = str(account_cfg.get("account"))
        with self._lock:
            if acc in self._agents:
                return True
            cmd_q = self._ctx.Queue()
            resp_q = self._ctx.Queue()
            # Use spawn context to create a new process that runs agent.agent_main
            # Use the pre-imported agent_module to avoid hidden-import issues (PyInstaller)
            if agent_module is None:
                return False, "agent module not available"
            proc = self._ctx.Process(target=agent_module.agent_main, args=(cmd_q, resp_q, account_cfg), daemon=True)
            proc.start()
            # Wait for heartbeat or successful start
            start_ts = time.time()
            healthy = False
            while time.time() - start_ts < timeout:
                try:
                    # look for heartbeat messages or initial responses
                    while not resp_q.empty():
                        msg = resp_q.get_nowait()
                        if isinstance(msg, dict) and msg.get("type") == "hb":
                            healthy = True
                            break
                    if healthy:
                        break
                except Exception:
                    pass
                time.sleep(0.2)
            self._agents[acc] = {"proc": proc, "cmd_q": cmd_q, "resp_q": resp_q, "cfg": account_cfg}
            return True

    def active_agents(self):
        with self._lock:
            return list(self._agents.keys())

    def stop_agent(self, account_id, timeout=10):
        acc = str(account_id)
        with self._lock:
            if acc not in self._agents:
                return True
            entry = self._agents[acc]
            cmd_q = entry["cmd_q"]
            resp_q = entry["resp_q"]
            try:
                cid = str(uuid.uuid4())
                cmd_q.put({"id": cid, "action": "quit"})
                # wait for response
                start = time.time()
                while time.time() - start < timeout:
                    try:
                        msg = resp_q.get(timeout=0.5)
                        if isinstance(msg, dict) and msg.get("id") == cid:
                            break
                    except Exception:
                        pass
                # then terminate process if still alive
                proc = entry["proc"]
                proc.join(timeout=1.0)
                if proc.is_alive():
                    proc.terminate()
            except Exception:
                pass
            # cleanup
            try:
                entry["cmd_q"].close()
            except Exception:
                pass
            try:
                entry["resp_q"].close()
            except Exception:
                pass
            del self._agents[acc]
            return True

    def stop_all(self):
        with self._lock:
            for acc in list(self._agents.keys()):
                try:
                    self.stop_agent(acc)
                except Exception:
                    pass

    def send_command(self, account_id, command, timeout=30):
        """
        Send a command dict to the agent identified by account_id and wait for response (synchronous).
        Returns (True, response) or (False, error).
        """
        acc = str(account_id)
        with self._lock:
            if acc not in self._agents:
                return False, f"agent {acc} not running"
            entry = self._agents[acc]
            cmd_q = entry["cmd_q"]
            resp_q = entry["resp_q"]
        cid = command.get("id", str(uuid.uuid4()))
        command["id"] = cid
        try:
            cmd_q.put(command)
        except Exception as e:
            return False, f"failed to send command: {e}"
        # wait for response
        start = time.time()
        while time.time() - start < timeout:
            try:
                msg = resp_q.get(timeout=0.5)
                if isinstance(msg, dict):
                    # heartbeats non-id responses may appear; if id matches return
                    if msg.get("id") == cid:
                        if msg.get("status") == "ok":
                            return True, msg.get("result")
                        else:
                            return False, msg.get("error")
                # skip other messages (heartbeats)
            except Exception:
                pass
        return False, "timeout waiting for agent response"

    def broadcast(self, account_ids, command, timeout=30):
        """
        Send the same command to many agents and collect responses.
        Returns dict: account_id -> (True/False, result_or_error)
        """
        threads = []
        results = {}
        def worker(acc):
            ok, res = self.send_command(acc, dict(command), timeout=timeout)
            results[acc] = (ok, res)
        for acc in account_ids:
            t = threading.Thread(target=worker, args=(str(acc),))
            t.daemon = True
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        return results