#!/usr/bin/env python3
"""
Controller wrapper for starting/stopping agent processes and sending commands.
Uses multiprocessing.get_context('spawn') to be Windows-safe.

Hardened version:
- Per-account command lock (prevents response mix-up for same agent)
- Better health checks (process alive + heartbeat timestamp)
- Safer stop_all (no recursive deadlock)
- Queue draining helper to avoid stale responses
"""
import time
import uuid
import threading
from multiprocessing import get_context

try:
    from Multi_Account_Trader.ipc1 import agent as agent_module
except Exception:
    try:
        from ipc1 import agent as agent_module
    except Exception:
        try:
            import agent as agent_module
        except Exception:
            agent_module = None

class ControllerAgent:
    def __init__(self):
        self._ctx = get_context("spawn")
        # account_id -> {
        #   "proc", "cmd_q", "resp_q", "cfg",
        #   "last_hb", "cmd_lock"
        # }
        self._agents = {}
        self._lock = threading.RLock()

    # -----------------------------
    # internal helpers
    # -----------------------------
    def _is_agent_alive(self, entry):
        try:
            p = entry.get("proc")
            return p is not None and p.is_alive()
        except Exception:
            return False

    def _drain_resp_queue_nonblocking(self, resp_q):
        """Drain queue quickly; return drained messages list."""
        out = []
        while True:
            try:
                msg = resp_q.get_nowait()
                out.append(msg)
            except Exception:
                break
        return out

    def _update_heartbeat_from_messages(self, acc, messages):
        now = time.time()
        for msg in messages:
            try:
                if isinstance(msg, dict) and msg.get("type") == "hb":
                    if acc in self._agents:
                        self._agents[acc]["last_hb"] = now
            except Exception:
                pass

    # -----------------------------
    # public api
    # -----------------------------
    def start_agent(self, account_cfg, timeout=30):
        """
        Start an agent for account_cfg (dict must include 'account').
        Returns True or (False, reason).
        """
        acc = str(account_cfg.get("account"))
        if not acc:
            return False, "missing account id"

        with self._lock:
            existing = self._agents.get(acc)
            if existing and self._is_agent_alive(existing):
                return True
            elif existing:
                # stale entry cleanup
                try:
                    existing["cmd_q"].close()
                except Exception:
                    pass
                try:
                    existing["resp_q"].close()
                except Exception:
                    pass
                self._agents.pop(acc, None)

            if agent_module is None:
                return False, "agent module not available"

            cmd_q = self._ctx.Queue()
            resp_q = self._ctx.Queue()

            proc = self._ctx.Process(
                target=agent_module.agent_main,
                args=(cmd_q, resp_q, account_cfg),
                daemon=True
            )
            proc.start()

            # Wait for first heartbeat and ensure process didn't crash
            start_ts = time.time()
            healthy = False
            last_hb = 0.0
            while time.time() - start_ts < timeout:
                if not proc.is_alive():
                    return False, "agent process exited during startup"
                # drain and inspect startup messages
                drained = self._drain_resp_queue_nonblocking(resp_q)
                for msg in drained:
                    if isinstance(msg, dict) and msg.get("type") == "hb":
                        healthy = True
                        last_hb = time.time()
                        break
                if healthy:
                    break
                time.sleep(0.15)

            if not healthy:
                # still keep process if alive; caller may still use it
                last_hb = time.time() if proc.is_alive() else 0.0

            self._agents[acc] = {
                "proc": proc,
                "cmd_q": cmd_q,
                "resp_q": resp_q,
                "cfg": account_cfg,
                "last_hb": last_hb,
                "cmd_lock": threading.Lock(),
            }
            return True

    def active_agents(self):
        with self._lock:
            alive = []
            for acc, entry in list(self._agents.items()):
                if self._is_agent_alive(entry):
                    alive.append(acc)
                else:
                    # cleanup dead
                    try:
                        entry["cmd_q"].close()
                    except Exception:
                        pass
                    try:
                        entry["resp_q"].close()
                    except Exception:
                        pass
                    self._agents.pop(acc, None)
            return alive

    def stop_agent(self, account_id, timeout=10):
        acc = str(account_id)
        with self._lock:
            entry = self._agents.get(acc)
            if entry is None:
                return True

        # Do stop outside global lock where possible
        cmd_q = entry["cmd_q"]
        resp_q = entry["resp_q"]
        proc = entry["proc"]
        cmd_lock = entry.get("cmd_lock")

        try:
            if cmd_lock:
                cmd_lock.acquire(timeout=2.0)

            cid = str(uuid.uuid4())
            try:
                cmd_q.put({"id": cid, "action": "quit"})
            except Exception:
                pass

            # wait short for ack while consuming heartbeats
            start = time.time()
            while time.time() - start < timeout:
                try:
                    msg = resp_q.get(timeout=0.4)
                    if isinstance(msg, dict):
                        if msg.get("type") == "hb":
                            with self._lock:
                                if acc in self._agents:
                                    self._agents[acc]["last_hb"] = time.time()
                            continue
                        if msg.get("id") == cid:
                            break
                except Exception:
                    pass

            # terminate if still alive
            try:
                proc.join(timeout=1.0)
            except Exception:
                pass
            try:
                if proc.is_alive():
                    proc.terminate()
                    proc.join(timeout=1.0)
            except Exception:
                pass
        finally:
            try:
                if cmd_lock and cmd_lock.locked():
                    cmd_lock.release()
            except Exception:
                pass

            with self._lock:
                try:
                    cmd_q.close()
                except Exception:
                    pass
                try:
                    resp_q.close()
                except Exception:
                    pass
                self._agents.pop(acc, None)

        return True

    def stop_all(self):
        # Avoid deadlock: snapshot keys first, then stop one-by-one
        with self._lock:
            keys = list(self._agents.keys())
        for acc in keys:
            try:
                self.stop_agent(acc)
            except Exception:
                pass

    def send_command(self, account_id, command, timeout=30):
        """
        Send command dict to agent and wait for matching response.
        Returns (True, result) or (False, error).
        """
        acc = str(account_id)

        with self._lock:
            entry = self._agents.get(acc)
            if entry is None:
                return False, f"agent {acc} not running"
            if not self._is_agent_alive(entry):
                return False, f"agent {acc} not alive"

            cmd_q = entry["cmd_q"]
            resp_q = entry["resp_q"]
            cmd_lock = entry["cmd_lock"]

        # serialize commands per account to avoid response race on shared resp_q
        acquired = False
        try:
            acquired = cmd_lock.acquire(timeout=max(2.0, min(10.0, timeout / 2.0)))
            if not acquired:
                return False, "agent command lock timeout"

            cid = command.get("id", str(uuid.uuid4()))
            command["id"] = cid

            # Best effort: clear stale heartbeats before sending
            drained = self._drain_resp_queue_nonblocking(resp_q)
            with self._lock:
                self._update_heartbeat_from_messages(acc, drained)

            try:
                cmd_q.put(command)
            except Exception as e:
                return False, f"failed to send command: {e}"

            # wait for matching id, pass through unrelated msgs
            deadline = time.time() + timeout
            while time.time() < deadline:
                remaining = max(0.2, min(0.8, deadline - time.time()))
                try:
                    msg = resp_q.get(timeout=remaining)
                except Exception:
                    continue

                if not isinstance(msg, dict):
                    continue

                if msg.get("type") == "hb":
                    with self._lock:
                        if acc in self._agents:
                            self._agents[acc]["last_hb"] = time.time()
                    continue

                if msg.get("id") != cid:
                    # unrelated response; ignore (with per-account lock this should be rare)
                    continue

                if msg.get("status") == "ok":
                    return True, msg.get("result")
                return False, msg.get("error")

            return False, "timeout waiting for agent response"
        finally:
            if acquired:
                try:
                    cmd_lock.release()
                except Exception:
                    pass

    def broadcast(self, account_ids, command, timeout=30):
        """
        Send same command to many agents concurrently.
        Returns dict: account_id -> (True/False, result_or_error)
        """
        threads = []
        results = {}
        results_lock = threading.Lock()

        def worker(acc):
            ok, res = self.send_command(acc, dict(command), timeout=timeout)
            with results_lock:
                results[acc] = (ok, res)

        for acc in account_ids:
            a = str(acc)
            t = threading.Thread(target=worker, args=(a,), daemon=True)
            t.start()
            threads.append(t)

        for t in threads:
            t.join()

        return results