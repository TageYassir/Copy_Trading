#!/usr/bin/env python3
"""
MT5 Manager - Multi Account Trader with Group Trading (Prop‑Firm Safe Edition)
Integrated with per-account Agent processes (multiprocessing) to maintain persistent MT5 sessions
and avoid login bursts / mt5 library cross-talk.

This file replaces direct mt5 per-thread usage with an IPC-based approach:
- ipc1/agent.py (per-account worker) and ipc1/controller_agent.py (ControllerAgent) must be placed
  alongside this file (see provided agent.py and controller_agent.py).
"""
import os
import sys
import json
import threading
import queue
import time
import datetime
import traceback
import logging
import subprocess
import random
import shutil
from pathlib import Path

import multiprocessing

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

try:
    import MetaTrader5 as mt5
except Exception:
    mt5 = None

# New imports for agent architecture
try:
    from Multi_Account_Trader.ipc1.controller_agent import ControllerAgent
except Exception:
    ControllerAgent = None

def get_data_root():
    """Return application data root.
    Windows: %APPDATA%/Trading_System
    Linux/Mac: $XDG_CONFIG_HOME/Trading_System or ~/.config/Trading_System
    """
    if os.name == "nt":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        data_root = os.path.join(base, "Trading_System")
    else:
        base = os.environ.get("XDG_CONFIG_HOME", os.path.join(os.path.expanduser("~"), ".config"))
        data_root = os.path.join(base, "Trading_System")
    try:
        os.makedirs(data_root, exist_ok=True)
    except Exception:
        pass
    return data_root

# Config
DATA_ROOT = get_data_root()
ACCOUNTS_FILE = os.path.join(DATA_ROOT, "mt5_accounts.json")
SETTINGS_FILE = os.path.join(DATA_ROOT, "mt5_settings.json")

# Default settings
DEFAULT_SETTINGS = {
    "init_delay": 3.0,
    "login_delay": 2.0,
    "trade_delay": 2.0,
    "shutdown_delay": 2.0,
    "between_accounts_delay": 3.0,
    "retry_delay": 2.0,
    "max_retries": 2,
    "positions_refresh_interval": 1.0,  # Seconds between position refreshes (minimum 1s)
    "auto_refresh_positions": False,
    "debug_mode": True,
    "filling_mode": "FOK",
    "lot_jitter_enabled": True,
    "lot_jitter_percent": 2.0,   # random ±2%
    "micro_delay_enabled": True,
    "micro_delay_min": 0.1,
    "micro_delay_max": 0.5,
}


def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r') as f:
                settings = json.load(f)
            for key, value in DEFAULT_SETTINGS.items():
                if key not in settings:
                    settings[key] = value
            return settings
        except:
            return DEFAULT_SETTINGS.copy()
    return DEFAULT_SETTINGS.copy()


def save_settings(settings):
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=2)


SETTINGS = load_settings()


def setup_logging():
    """Setup logging to file and console"""
    day = datetime.datetime.now().strftime("%Y-%m-%d")
    log_dir = os.path.join(DATA_ROOT, "logs", "app", day)
    os.makedirs(log_dir, exist_ok=True)


logger = setup_logging()


def log_message(message, level='INFO'):
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    full_message = f"{timestamp} - {message}"
    if level == 'ERROR':
        logger.error(message)
    elif level == 'WARNING':
        logger.warning(message)
    elif level == 'DEBUG':
        logger.debug(message)
    else:
        logger.info(message)
    return full_message


def default_store():
    return {
        "accounts": [],
        "groups": [{"name": "default", "vol_multiplier": 1.0, "forced_volume": 0.0, "active": True,
                     "max_positions_per_symbol": 0, "max_total_lots": 0.0}],
        "source_package": ""
    }


def load_store(path=ACCOUNTS_FILE):
    if not os.path.exists(path):
        return default_store()
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return {"accounts": data,
                "groups": [{"name": "default", "vol_multiplier": 1.0, "forced_volume": 0.0, "active": True}],
                "source_package": ""}
    if "accounts" not in data:
        data["accounts"] = []
    if "groups" not in data or not data["groups"]:
        data["groups"] = [{"name": "default", "vol_multiplier": 1.0, "forced_volume": 0.0, "active": True}]
    if "source_package" not in data:
        data["source_package"] = ""
    # Ensure all accounts have terminal_path field (default empty)
    for a in data["accounts"]:
        a.setdefault("terminal_path", "")
    # Ensure group defaults include new risk cap fields
    for g in data.get("groups", []):
        g.setdefault("max_positions_per_symbol", 0)
        g.setdefault("max_total_lots", 0.0)
    return data


def save_store(store, path=ACCOUNTS_FILE):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2)


# Helpers for portable terminal cloning and name sanitization
def sanitize_name(text):
    return "".join(c for c in str(text) if c.isalnum() or c in ("_", "-")).strip() or "default"


def clone_mt5_folder(base_dir, target_dir):
    base_dir = Path(base_dir)
    target_dir = Path(target_dir)
    if not base_dir.exists():
        return False, f"base dir not found: {base_dir}"
    try:
        if target_dir.exists():
            return True, None

        def same_drive(path_a, path_b):
            try:
                if os.name == "nt":
                    return os.path.splitdrive(str(path_a))[0].lower() == os.path.splitdrive(str(path_b))[0].lower()
                # On POSIX, compare device id
                return os.stat(path_a).st_dev == os.stat(path_b).st_dev
            except Exception:
                return False

        # If base and target are on same drive, try to use hardlinks for files to save space
        if same_drive(base_dir, Path(DATA_ROOT)):
            try:
                shutil.copytree(str(base_dir), str(target_dir), copy_function=os.link)
            except Exception:
                # fallback to normal copy if hardlinking fails
                shutil.copytree(str(base_dir), str(target_dir))
        else:
            shutil.copytree(str(base_dir), str(target_dir))

        try:
            (target_dir / "portable.dat").touch()
        except Exception:
            pass
        return True, None
    except Exception as e:
        return False, str(e)


def ensure_account_terminal(acc, base_path):
    """Ensure a per-account portable MT5 copy exists; returns (exe_path, error)"""
    try:
        if not base_path:
            return None, "no base path"
        base_exe = Path(base_path)
        base_dir = base_exe.parent if base_exe.is_file() else base_exe
        acct_name = sanitize_name(acc.get("account") or acc.get("name") or "acct")
        target_root = Path(DATA_ROOT) / "MT5_Portable"
        target_dir = target_root / f"MT5_{acct_name}"
        ok, err = clone_mt5_folder(base_dir, target_dir)
        if not ok:
            return None, err
        # locate exe
        candidates = ["terminal64.exe", "terminal.exe", "metatrader5.exe", "metaeditor64.exe", "metaeditor.exe"]
        for c in candidates:
            p = target_dir / c
            if p.exists():
                return str(p), None
        matches = list(target_dir.rglob('*.exe'))
        if matches:
            return str(matches[0]), None
        return None, "no executable found in cloned folder"
    except Exception as e:
        return None, str(e)


# ----------------------------------------------------------------------
#  Helper functions (kept for backward compatibility / manual ops)
# ----------------------------------------------------------------------
def initialize_one_terminal(path, timeout=30, kill_existing=True):
    """
    Initialize MT5 terminal (without login).
    If kill_existing is True, attempt to kill other instances of the same executable in the same folder.
    If False, do not kill processes (safe for background queries).
    Returns (True, None) or (False, error_string).
    """
    if not path or not os.path.exists(path):
        return False, f"Terminal executable not found: {path}"

    proc_name = os.path.basename(path)  # terminal64.exe
    terminal_dir = os.path.dirname(path)
    log_message(f"Ensuring state for {proc_name} at {terminal_dir} (kill_existing={kill_existing})...", 'DEBUG')

    if kill_existing:
        if os.name == 'nt':
            try:
                subprocess.run(['taskkill', '/F', '/IM', proc_name], capture_output=True, timeout=10)
            except:
                pass
            try:
                result = subprocess.run(
                    f'wmic process where "name=\'{proc_name}\' and commandline like \'%{terminal_dir}%\'" get processid',
                    shell=True, capture_output=True, text=True, timeout=10
                )
                for line in result.stdout.splitlines():
                    pid = line.strip()
                    if pid.isdigit():
                        subprocess.run(['taskkill', '/F', '/PID', pid], capture_output=True, timeout=5)
            except:
                pass
        else:
            try:
                subprocess.run(['pkill', '-9', '-f', proc_name], timeout=10)
            except:
                pass
        time.sleep(2)  # wait for process to die if we killed any

    result = [False, None]
    exception = [None]

    def _init_thread():
        try:
            ok = mt5.initialize(path=path, login=0)
            result[0] = ok
            if not ok:
                err = mt5.last_error()
                result[1] = f"init failed: {err}"
            else:
                result[1] = None
        except Exception as e:
            exception[0] = e
            result[0] = False
            result[1] = str(e)

    t = threading.Thread(target=_init_thread, daemon=True)
    t.start()
    t.join(timeout)

    if t.is_alive():
        log_message(f"Initialization timed out after {timeout}s for {path}.", 'ERROR')
        try:
            mt5.shutdown()
        except:
            pass
        time.sleep(1)
        return False, f"Timeout after {timeout}s"

    if exception[0] is not None:
        return False, str(exception[0])

    if not result[0]:
        log_message(f"MT5 init error: {result[1]}", 'ERROR')
    return result[0], result[1]


def shutdown_one_terminal():
    """Shutdown only the MT5 connection associated with the current thread/library state."""
    try:
        mt5.shutdown()
    except:
        pass


# The old aggressive kill is kept only for manual cleanup if needed (e.g., app exit)
def safe_shutdown_mt5():
    try:
        try:
            terminal_info = mt5.terminal_info()
            if terminal_info is not None:
                mt5.shutdown()
                time.sleep(1)
        except:
            pass
        log_message("Force killing MT5 processes...", 'DEBUG')
        if os.name == 'nt':
            procs = ['terminal64.exe', 'terminal.exe', 'metatrader5.exe', 'metaeditor64.exe', 'metaeditor.exe']
            for proc in procs:
                try:
                    subprocess.run(['taskkill', '/F', '/IM', proc], capture_output=True, timeout=10)
                except:
                    pass
        else:
            try:
                subprocess.run(['pkill', '-9', '-f', 'terminal64'], timeout=10)
                subprocess.run(['pkill', '-9', '-f', 'terminal.exe'], timeout=10)
            except:
                pass
        time.sleep(2)
    except Exception as e:
        log_message(f"Error in safe_shutdown: {e}", 'ERROR')


# ----------------------------------------------------------------------
#  Trading helpers (kept for local/manual, but main app uses Agents)
# ----------------------------------------------------------------------
def symbol_pip_value(symbol):
    try:
        info = mt5.symbol_info(symbol)
        if info is None:
            return None
        digits = getattr(info, "digits", None)
        if digits is None:
            return None
        return 0.0001 if digits > 3 else 0.01
    except:
        return None


def get_filling_mode():
    mode = SETTINGS.get('filling_mode', 'FOK')
    if mode == 'IOC':
        return mt5.ORDER_FILLING_IOC
    elif mode == 'RETURN':
        return mt5.ORDER_FILLING_RETURN
    else:
        return mt5.ORDER_FILLING_FOK


def compute_account_volume(account, base_volume, groups):
    """
    Apply group multiplier, forced volume, and optional random jitter.
    Returns the final volume for that account.
    """
    group_name = account.get('group', 'default')
    group = next((g for g in groups if g['name'] == group_name), None)
    if group is None:
        group = {"vol_multiplier": 1.0, "forced_volume": 0.0}

    if group.get('forced_volume', 0.0) > 0:
        vol = group['forced_volume']
    else:
        vol = base_volume * group.get('vol_multiplier', 1.0)

    if SETTINGS.get('lot_jitter_enabled', True):
        jitter_pct = SETTINGS.get('lot_jitter_percent', 2.0) / 100.0
        vol *= random.uniform(1.0 - jitter_pct, 1.0 + jitter_pct)

    # Normalize to symbol step (done later in place_order, but we can also do it here)
    return vol


def place_order_market(symbol, side, volume, deviation=50, price=None, sl=0.0, tp=0.0, magic=0,
                       comment="mt5_seq_trade", enable_micro_delay=None):
    """Legacy direct place order (kept for manual/testing). Prefer agent-based placing."""
    if enable_micro_delay is None:
        enable_micro_delay = SETTINGS.get('micro_delay_enabled', True)

    max_retries = SETTINGS['max_retries']
    filling_mode = get_filling_mode()

    log_message(f"Placing {side} order: {symbol} {volume}")

    # --- Micro-jitter delay BEFORE any order attempt ---
    if enable_micro_delay:
        delay = random.uniform(SETTINGS.get('micro_delay_min', 0.1), SETTINGS.get('micro_delay_max', 0.5))
        time.sleep(delay)

    try:
        si = mt5.symbol_info(symbol)
        if si is None:
            return False, f"symbol {symbol} not found"
        if not si.visible:
            mt5.symbol_select(symbol, True)

        vol_step = getattr(si, 'volume_step', 0.01)
        min_vol = getattr(si, 'volume_min', 0.01)
        max_vol = getattr(si, 'volume_max', 100000.0)
        normalized_volume = max(min_vol, min(max_vol, round(float(volume) / vol_step) * vol_step))

        for retry in range(max_retries):
            if retry > 0:
                log_message(f"Retry {retry + 1}/{max_retries} for {symbol}")
                time.sleep(SETTINGS['retry_delay'])

            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                continue
            if tick.bid == 0.0 and tick.ask == 0.0:
                return False, "Market closed"

            if side.lower() == "buy":
                order_type = mt5.ORDER_TYPE_BUY
                price_to_use = tick.ask if price is None else price
            else:
                order_type = mt5.ORDER_TYPE_SELL
                price_to_use = tick.bid if price is None else price

            req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": normalized_volume,
                "type": order_type,
                "price": float(price_to_use),
                "sl": float(sl) if sl else 0.0,
                "tp": float(tp) if tp else 0.0,
                "deviation": int(deviation),
                "magic": int(magic),
                "comment": comment,
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": filling_mode,
            }
            try:
                result = mt5.order_send(req)
            except Exception as e:
                log_message(f"Order send exception: {e}", 'ERROR')
                continue

            if result is None:
                continue

            # Handle retcodes
            if result.retcode == 10009:
                # SUCCESS – but DO NOT retry
                log_message(f"Order executed: ticket {result.order}")
                return True, result
            elif result.retcode == 10018:
                return False, "Market closed"
            elif result.retcode == 10030:
                return False, "Unsupported filling mode"
            elif result.retcode == 10016:
                # Invalid stops – order may still be placed!
                # Do NOT retry, return success but log warning
                log_message(f"Order placed but stops invalid: {result.comment}", 'WARNING')
                return True, result
            elif result.retcode == 10019:
                return False, "No prices"
            else:
                # Unknown retcode – do NOT retry, it may already be executed
                log_message(f"Order result: {result.retcode} - {result.comment}")
                return True, result

        return False, "All retries failed"

    except Exception as e:
        return False, str(e)


def close_position(ticket, symbol, volume, position_type):
    """Close a specific position – tries multiple filling modes when necessary."""
    try:
        si = mt5.symbol_info(symbol)
        if si is None:
            mt5.symbol_select(symbol, True)
            time.sleep(0.3)
            si = mt5.symbol_info(symbol)
            if si is None:
                return False, f"symbol {symbol} not found"

        if not si.visible:
            mt5.symbol_select(symbol, True)
            time.sleep(0.3)

        tick = None
        for _ in range(10):
            tick = mt5.symbol_info_tick(symbol)
            if tick is not None and tick.bid > 0 and tick.ask > 0:
                break
            time.sleep(0.5)
        if tick is None or tick.bid == 0 or tick.ask == 0:
            return False, f"no tick for {symbol}"

        if int(position_type) == 0:  # buy position -> sell to close
            price = tick.bid
            order_type = mt5.ORDER_TYPE_SELL
        else:  # sell position -> buy to close
            price = tick.ask
            order_type = mt5.ORDER_TYPE_BUY

        # Try preferred filling modes in order until success or out of options
        filling_options = [mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN, mt5.ORDER_FILLING_FOK]
        last_err = None
        for filling in filling_options:
            req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": float(volume),
                "type": order_type,
                "position": int(ticket),
                "price": price,
                "deviation": 50,
                "magic": 0,
                "comment": "close_mt5_manager",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": filling,
            }
            res = mt5.order_send(req)
            if res is None:
                last_err = "order_send returned None"
                continue
            if res.retcode == 10009:
                return True, res
            else:
                last_err = f"retcode {res.retcode}: {res.comment}"
                if res.retcode == 10030:
                    continue
        return False, last_err or "unknown close error"
    except Exception as e:
        return False, str(e)


# ----------------------------------------------------------------------
#  GUI Application
# ----------------------------------------------------------------------
class MT5ManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("MT5 Manager — Multi Account Trader (Prop‑Safe)")
        self.root.geometry("1120x720")

        self.store = load_store()
        self.settings = load_settings()
        # Ensure all accounts have terminal_path
        for a in self.store["accounts"]:
            a.setdefault("terminal_path", "")
        self.ensure_account_defaults()

        self.positions_q = queue.Queue()
        self.prev_positions_map = {}
        self.auto_refresh = tk.BooleanVar(value=self.settings.get('auto_refresh_positions', False))
        self.refresh_interval = tk.DoubleVar(value=max(1.0, self.settings.get('positions_refresh_interval', 1.0)))
        self.refresh_paused = not self.settings.get('auto_refresh_positions', False)

        self.use_gui_session = tk.BooleanVar(value=False)
        self.terminal_path_var = tk.StringVar(value="")   # global fallback

        # Trading parameters
        self.symbol_var = tk.StringVar(value="EURUSD")
        self.side_var = tk.StringVar(value="buy")
        self.volume_var = tk.StringVar(value="0.1")
        self.deviation_var = tk.StringVar(value="50")
        self.sl_var = tk.StringVar(value="")
        self.tp_var = tk.StringVar(value="")
        self.magic_var = tk.StringVar(value="0")
        self.entry_price_var = tk.StringVar(value="")
        self.sl_mode_var = tk.StringVar(value="pips")
        self.tp_mode_var = tk.StringVar(value="pips")
        self.order_type_var = tk.StringVar(value="market")
        self.pending_type_var = tk.StringVar(value="limit")

        # Delay settings variables
        self.init_delay_var = tk.DoubleVar(value=self.settings['init_delay'])
        self.login_delay_var = tk.DoubleVar(value=self.settings['login_delay'])
        self.trade_delay_var = tk.DoubleVar(value=self.settings['trade_delay'])
        self.shutdown_delay_var = tk.DoubleVar(value=self.settings['shutdown_delay'])
        self.between_accounts_delay_var = tk.DoubleVar(value=self.settings['between_accounts_delay'])
        self.retry_delay_var = tk.DoubleVar(value=self.settings['retry_delay'])
        self.max_retries_var = tk.IntVar(value=self.settings['max_retries'])
        self.filling_mode_var = tk.StringVar(value=self.settings.get('filling_mode', 'FOK'))

        # New anti‑ban settings
        self.lot_jitter_enabled_var = tk.BooleanVar(value=self.settings.get('lot_jitter_enabled', True))
        self.lot_jitter_percent_var = tk.DoubleVar(value=self.settings.get('lot_jitter_percent', 2.0))
        self.micro_delay_enabled_var = tk.BooleanVar(value=self.settings.get('micro_delay_enabled', True))
        self.micro_delay_min_var = tk.DoubleVar(value=self.settings.get('micro_delay_min', 0.1))
        self.micro_delay_max_var = tk.DoubleVar(value=self.settings.get('micro_delay_max', 0.5))

        # Agent controller (multiprocessing-based per-account agents)
        os.environ["TRADING_SYSTEM_DATA_DIR"] = DATA_ROOT
        if ControllerAgent is None:
            self.agent_controller = None
            log_message("ControllerAgent not available; agents disabled", 'WARNING')
        else:
            self.agent_controller = ControllerAgent()

        self.positions_q = queue.Queue()
        self._refresh_in_progress = False
        # ensure refresher starts only once
        self._refresher_started = False

        self._build_ui()
        self._refresh_lists()
        self._start_positions_refresher_thread()
        self._start_positions_queue_processor()

        log_message("MT5 Manager (Prop‑Safe) initialized")

    def ensure_account_defaults(self):
        for a in self.store.get("accounts", []):
            a.setdefault("name", str(a.get("account", "")))
            a.setdefault("account", a.get("account", ""))
            a.setdefault("password", a.get("password", ""))
            a.setdefault("server", a.get("server", ""))
            a.setdefault("active", a.get("active", True))
            a.setdefault("group", a.get("group", "default"))
            a.setdefault("terminal_path", a.get("terminal_path", ""))
            # per-account risk / cooldown defaults
            a.setdefault("max_positions_per_symbol", 0)
            a.setdefault("max_daily_drawdown", 0.0)   # currency, 0 = off
            a.setdefault("fail_count", 0)
            a.setdefault("cooldown_after_failures", 3)
            a.setdefault("cooldown_minutes", 15)
            a.setdefault("cooldown_until", 0.0)

    def log(self, *parts, level='INFO'):
        message = " ".join(str(p) for p in parts)
        full_message = log_message(message, level)
        try:
            self.log_txt.configure(state=tk.NORMAL)
            self.log_txt.insert(tk.END, full_message + "\n")
            self.log_txt.see(tk.END)
            self.log_txt.configure(state=tk.DISABLED)
        except Exception:
            # fallback to global logger
            logger.info(full_message)

    def thread_log(self, *parts, level='INFO'):
        """Schedule a log call on the main thread (safe from worker threads)."""
        msg = " ".join(str(p) for p in parts)
        try:
            self.root.after(0, lambda m=msg, lv=level: self.log(m, level=lv))
        except Exception:
            log_message(msg, level)

    # ------------------------------------------------------------------
    #  UI Construction (same layout + new buttons)
    # ------------------------------------------------------------------
    def _build_ui(self):
        main = ttk.Frame(self.root)
        main.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # Top controls
        top_controls = ttk.Frame(main)
        top_controls.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(top_controls, text="Base Terminal exe:").pack(side=tk.LEFT)
        self.terminal_entry = ttk.Entry(top_controls, textvariable=self.terminal_path_var, width=50)
        self.terminal_entry.pack(side=tk.LEFT, padx=4)
        ttk.Button(top_controls, text="Browse...", command=self.browse_terminal).pack(side=tk.LEFT)
        # NEW button to setup portable terminals
        ttk.Button(top_controls, text="Setup Portable Terminals", command=self.setup_portable_terminals).pack(side=tk.LEFT, padx=10)

        paned = ttk.PanedWindow(main, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # Left panel
        left = ttk.Frame(paned)
        paned.add(left, weight=1)

        # Groups
        group_frame = ttk.LabelFrame(left, text="Groups")
        group_frame.pack(fill=tk.X, pady=(0, 8))
        self.groups_tree = ttk.Treeview(group_frame, columns=("name", "vol", "forced", "active"), show="headings", height=4)
        for col, text in [("name", "Name"), ("vol", "VolX"), ("forced", "ForcedVol"), ("active", "Active")]:
            self.groups_tree.heading(col, text=text)
            self.groups_tree.column(col, width=80, anchor="center")
        self.groups_tree.pack(fill=tk.X)
        gbtns = ttk.Frame(group_frame)
        gbtns.pack(fill=tk.X, pady=4)
        ttk.Button(gbtns, text="+", width=3, command=self.add_group).pack(side=tk.LEFT)
        ttk.Button(gbtns, text="Edit", command=self.edit_group).pack(side=tk.LEFT, padx=2)
        ttk.Button(gbtns, text="Del", command=self.remove_group).pack(side=tk.LEFT)

        # Accounts
        acct_frame = ttk.LabelFrame(left, text="Accounts")
        acct_frame.pack(fill=tk.BOTH, expand=True)
        self.accounts_tree = ttk.Treeview(acct_frame, columns=("id", "server", "group", "active", "path"), show="headings", height=8)
        for col, text in [("id", "Account"), ("server", "Server"), ("group", "Group"), ("active", "Active"), ("path", "Terminal Path")]:
            self.accounts_tree.heading(col, text=text)
            self.accounts_tree.column(col, width=100, anchor="center")
        self.accounts_tree.pack(fill=tk.BOTH, expand=True)
        abtns = ttk.Frame(acct_frame)
        abtns.pack(fill=tk.X, pady=4)
        ttk.Button(abtns, text="+", width=3, command=self.add_account).pack(side=tk.LEFT)
        ttk.Button(abtns, text="Edit", command=self.edit_account).pack(side=tk.LEFT, padx=2)
        ttk.Button(abtns, text="Del", command=self.remove_account).pack(side=tk.LEFT)
        ttk.Button(abtns, text="Save", command=self.save_store_now).pack(side=tk.RIGHT)

        # Right panel
        right = ttk.Frame(paned)
        paned.add(right, weight=2)

        notebook = ttk.Notebook(right)
        notebook.pack(fill=tk.BOTH, expand=True)

        # Trading Tab
        trade_tab = ttk.Frame(notebook)
        notebook.add(trade_tab, text="Trading")

        trade_frame = ttk.LabelFrame(trade_tab, text="Order Parameters")
        trade_frame.pack(fill=tk.X, padx=4, pady=4)

        # Row 1: Symbol, Volume, Deviation, Price
        row1 = ttk.Frame(trade_frame)
        row1.pack(fill=tk.X, pady=2)
        ttk.Label(row1, text="Symbol:").pack(side=tk.LEFT)
        ttk.Entry(row1, textvariable=self.symbol_var, width=10).pack(side=tk.LEFT, padx=4)
        ttk.Label(row1, text="Volume:").pack(side=tk.LEFT, padx=(8, 0))
        ttk.Entry(row1, textvariable=self.volume_var, width=8).pack(side=tk.LEFT, padx=4)
        ttk.Label(row1, text="Deviation:").pack(side=tk.LEFT, padx=(8, 0))
        ttk.Entry(row1, textvariable=self.deviation_var, width=5).pack(side=tk.LEFT, padx=4)
        ttk.Label(row1, text="Price:").pack(side=tk.LEFT, padx=(8, 0))
        ttk.Entry(row1, textvariable=self.entry_price_var, width=12).pack(side=tk.LEFT, padx=4)

        # Row 2: Side, Order Kind, Pending Type, Magic
        row2 = ttk.Frame(trade_frame)
        row2.pack(fill=tk.X, pady=2)
        ttk.Radiobutton(row2, text="BUY", variable=self.side_var, value="buy").pack(side=tk.LEFT)
        ttk.Radiobutton(row2, text="SELL", variable=self.side_var, value="sell").pack(side=tk.LEFT, padx=8)
        ttk.Separator(row2, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)
        ttk.Radiobutton(row2, text="Market", variable=self.order_type_var, value="market").pack(side=tk.LEFT)
        ttk.Radiobutton(row2, text="Pending", variable=self.order_type_var, value="pending").pack(side=tk.LEFT, padx=8)
        ttk.Label(row2, text="Pending Type:").pack(side=tk.LEFT, padx=(8, 0))
        pending_cb = ttk.Combobox(row2, values=["limit", "stop"], textvariable=self.pending_type_var, state="readonly", width=8)
        pending_cb.pack(side=tk.LEFT, padx=4)
        ttk.Label(row2, text="Magic:").pack(side=tk.LEFT, padx=(8, 0))
        ttk.Entry(row2, textvariable=self.magic_var, width=6).pack(side=tk.LEFT, padx=4)

        # Row 3: SL/TP
        row3 = ttk.Frame(trade_frame)
        row3.pack(fill=tk.X, pady=2)
        ttk.Label(row3, text="SL:").pack(side=tk.LEFT)
        ttk.Entry(row3, textvariable=self.sl_var, width=8).pack(side=tk.LEFT, padx=2)
        ttk.Radiobutton(row3, text="Pips", variable=self.sl_mode_var, value="pips").pack(side=tk.LEFT)
        ttk.Radiobutton(row3, text="Price", variable=self.sl_mode_var, value="price").pack(side=tk.LEFT, padx=4)
        ttk.Label(row3, text="TP:").pack(side=tk.LEFT, padx=(8, 0))
        ttk.Entry(row3, textvariable=self.tp_var, width=8).pack(side=tk.LEFT, padx=2)
        ttk.Radiobutton(row3, text="Pips", variable=self.tp_mode_var, value="pips").pack(side=tk.LEFT)
        ttk.Radiobutton(row3, text="Price", variable=self.tp_mode_var, value="price").pack(side=tk.LEFT)

        act_frame = ttk.LabelFrame(trade_tab, text="Trade Actions")
        act_frame.pack(fill=tk.X, padx=4, pady=4)

        btn_frame = ttk.Frame(act_frame)
        btn_frame.pack(fill=tk.X, pady=6)
        ttk.Button(btn_frame, text="Trade Selected Account", command=self.start_selected).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="Trade ALL Active Accounts", command=self.bid_all_accounts).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="Trade GROUP", command=self.trade_group).pack(side=tk.LEFT, padx=4)

        group_frame2 = ttk.Frame(act_frame)
        group_frame2.pack(fill=tk.X, pady=2)
        ttk.Label(group_frame2, text="Select Group:").pack(side=tk.LEFT)
        self.group_trade_var = tk.StringVar(value="default")
        group_names = [g["name"] for g in self.store.get("groups", [])] or ["default"]
        self.group_trade_cb = ttk.Combobox(group_frame2, values=group_names, textvariable=self.group_trade_var,
                                           state="readonly", width=15)
        self.group_trade_cb.pack(side=tk.LEFT, padx=4)

        # Settings Tab
        settings_tab = ttk.Frame(notebook)
        notebook.add(settings_tab, text="Settings")

        settings_frame = ttk.LabelFrame(settings_tab, text="Timing Delays (seconds)")
        settings_frame.pack(fill=tk.X, padx=4, pady=4)

        delays = [
            ("MT5 Init Delay:", self.init_delay_var, "Wait after initializing MT5"),
            ("After Login Delay:", self.login_delay_var, "Wait after login before trading"),
            ("Between Trades Delay:", self.trade_delay_var, "Wait between trade attempts"),
            ("Shutdown Delay:", self.shutdown_delay_var, "Wait after shutdown before next"),
            ("Between Accounts Delay:", self.between_accounts_delay_var, "Wait between processing accounts"),
            ("Retry Delay:", self.retry_delay_var, "Wait between retry attempts"),
        ]
        for label, var, tooltip in delays:
            frame = ttk.Frame(settings_frame)
            frame.pack(fill=tk.X, pady=2)
            ttk.Label(frame, text=label, width=22).pack(side=tk.LEFT)
            ttk.Entry(frame, textvariable=var, width=10).pack(side=tk.LEFT, padx=4)
            ttk.Label(frame, text=tooltip, foreground="gray").pack(side=tk.LEFT, padx=4)

        retry_frame = ttk.Frame(settings_frame)
        retry_frame.pack(fill=tk.X, pady=2)
        ttk.Label(retry_frame, text="Max Trade Retries:", width=22).pack(side=tk.LEFT)
        ttk.Entry(retry_frame, textvariable=self.max_retries_var, width=10).pack(side=tk.LEFT, padx=4)

        fill_frame = ttk.Frame(settings_frame)
        fill_frame.pack(fill=tk.X, pady=2)
        ttk.Label(fill_frame, text="Filling Mode:", width=22).pack(side=tk.LEFT)
        filling_cb = ttk.Combobox(fill_frame, values=["FOK", "IOC", "RETURN"], textvariable=self.filling_mode_var,
                                  state="readonly", width=10)
        filling_cb.pack(side=tk.LEFT, padx=4)
        ttk.Label(fill_frame, text="FOK=Fill or Kill, IOC=Immediate or Cancel", foreground="gray").pack(side=tk.LEFT, padx=4)

        # New anti‑ban settings
        anti_frame = ttk.LabelFrame(settings_tab, text="Anti‑Ban Features")
        anti_frame.pack(fill=tk.X, padx=4, pady=4)

        ttk.Checkbutton(anti_frame, text="Enable Lot Size Jitter", variable=self.lot_jitter_enabled_var).pack(anchor=tk.W, padx=4, pady=2)
        jit_frame = ttk.Frame(anti_frame)
        jit_frame.pack(fill=tk.X, padx=4)
        ttk.Label(jit_frame, text="Jitter % (±):").pack(side=tk.LEFT)
        ttk.Entry(jit_frame, textvariable=self.lot_jitter_percent_var, width=5).pack(side=tk.LEFT, padx=4)

        ttk.Checkbutton(anti_frame, text="Enable Micro‑Delay before Order", variable=self.micro_delay_enabled_var).pack(anchor=tk.W, padx=4, pady=2)
        md_frame = ttk.Frame(anti_frame)
        md_frame.pack(fill=tk.X, padx=4)
        ttk.Label(md_frame, text="Min delay (s):").pack(side=tk.LEFT)
        ttk.Entry(md_frame, textvariable=self.micro_delay_min_var, width=5).pack(side=tk.LEFT, padx=4)
        ttk.Label(md_frame, text="Max delay (s):").pack(side=tk.LEFT, padx=(8, 0))
        ttk.Entry(md_frame, textvariable=self.micro_delay_max_var, width=5).pack(side=tk.LEFT, padx=4)

        ttk.Button(settings_tab, text="Save Settings", command=self.save_settings_now).pack(pady=10)

        # Positions Tab
        pos_tab = ttk.Frame(notebook)
        notebook.add(pos_tab, text="Positions")

        pos_frame = ttk.LabelFrame(pos_tab, text="Open Positions")
        pos_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        cols = ("account", "ticket", "symbol", "type", "volume", "price", "profit")
        self.positions_tree = ttk.Treeview(pos_frame, columns=cols, show="headings", selectmode="extended")
        for c, lab in [("account", "Account"), ("ticket", "Ticket"), ("symbol", "Symbol"),
                       ("type", "Type"), ("volume", "Vol"), ("price", "Price"), ("profit", "Profit")]:
            self.positions_tree.heading(c, text=lab)
            self.positions_tree.column(c, width=90, anchor="center")
        self.positions_tree.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)

        pos_scroll = ttk.Scrollbar(pos_frame, command=self.positions_tree.yview)
        pos_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.positions_tree.configure(yscrollcommand=pos_scroll.set)

        pos_btns = ttk.Frame(pos_tab)
        pos_btns.pack(fill=tk.X, pady=4)
        ttk.Button(pos_btns, text="Refresh Now", command=self.refresh_positions_now).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(pos_btns, text="Auto-Refresh", variable=self.auto_refresh).pack(side=tk.LEFT, padx=4)
        ttk.Button(pos_btns, text="Close Selected", command=self.close_selected_positions).pack(side=tk.RIGHT, padx=4)
        ttk.Button(pos_btns, text="Close All", command=self.close_all_positions).pack(side=tk.RIGHT, padx=4)
        ttk.Button(pos_btns, text="Close Selected Accounts", command=self.close_selected_accounts_positions).pack(side=tk.RIGHT, padx=4)
        ttk.Button(pos_btns, text="Close Group", command=self.close_group_positions).pack(side=tk.RIGHT, padx=4)

        # Log Tab
        log_tab = ttk.Frame(notebook)
        notebook.add(log_tab, text="Log")

        log_frame = ttk.LabelFrame(log_tab, text="Activity Log")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        self.log_txt = tk.Text(log_frame, height=15, state=tk.DISABLED, bg='black', fg='#00FF00')
        self.log_txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        log_scroll = ttk.Scrollbar(log_frame, command=self.log_txt.yview)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_txt.configure(yscrollcommand=log_scroll.set)

        # Dashboard Tab
        dash_tab = ttk.Frame(notebook)
        notebook.add(dash_tab, text="Dashboard")

        dash_frame = ttk.LabelFrame(dash_tab, text="System Status")
        dash_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        # Dashboard variables and simple indicators
        self.dash_agents_var = tk.StringVar(value="0")
        self.dash_positions_var = tk.StringVar(value="0")
        self.dash_disk_var = tk.StringVar(value="0 MB")
        stat_row = ttk.Frame(dash_frame)
        stat_row.pack(anchor=tk.NW, padx=6, pady=6)
        ttk.Label(stat_row, text="Active Agents:").grid(row=0, column=0, sticky=tk.W)
        ttk.Label(stat_row, textvariable=self.dash_agents_var).grid(row=0, column=1, sticky=tk.W, padx=(6,0))
        ttk.Label(stat_row, text="Known Positions:").grid(row=1, column=0, sticky=tk.W, pady=(6,0))
        ttk.Label(stat_row, textvariable=self.dash_positions_var).grid(row=1, column=1, sticky=tk.W, padx=(6,0), pady=(6,0))
        ttk.Label(dash_frame, text="Terminal Disk Usage:").grid(row=3, column=0, sticky=tk.W, padx=6, pady=4)
        ttk.Label(dash_frame, textvariable=self.dash_disk_var).grid(row=3, column=1, sticky=tk.W)

    # ------------------------------------------------------------------
    #  New: Portable Terminals Setup
    # ------------------------------------------------------------------
    def setup_portable_terminals(self):
        base_path = self.terminal_path_var.get().strip()
        if not base_path:
            self.browse_terminal()
            base_path = self.terminal_path_var.get().strip()
            if not base_path:
                messagebox.showerror("Error", "Please select a base terminal executable first.")
                return
        base_exe = Path(base_path)
        if not base_exe.is_file():
            messagebox.showerror("Error", "Base terminal executable not found.")
            return
        base_dir = base_exe.parent

        accounts = self.get_active_accounts()
        if not accounts:
            messagebox.showinfo("Info", "No active accounts to setup.")
            return

        target_root = Path.home() / "Documents" / "MT5_Portable"
        target_root.mkdir(parents=True, exist_ok=True)

        if not messagebox.askyesno("Confirm",
                                   f"This will copy the MT5 folder to\n{target_root}\nfor {len(accounts)} accounts. Continue?"):
            return

        self.thread_log(f"Target folder: {target_root}")
        for i, acc in enumerate(accounts, start=1):
            target_dir = target_root / f"MT5_{i}"
            try:
                if not target_dir.exists():
                    shutil.copytree(str(base_dir), str(target_dir))
                    self.thread_log(f"Created: {target_dir}")
                    # --- NEW: enable portable mode ---
                    portable_marker = target_dir / "portable.dat"
                    portable_marker.touch()  # creates empty file
                    self.thread_log(f"Set portable mode for {target_dir}")
                else:
                    self.thread_log(f"Already exists: {target_dir}")
                    # Also ensure portable.dat exists for existing folders
                    portable_marker = target_dir / "portable.dat"
                    if not portable_marker.exists():
                        portable_marker.touch()
                        self.thread_log(f"Added missing portable.dat to {target_dir}")

                terminal_exe = target_dir / "terminal64.exe"
                if not terminal_exe.exists():
                    terminal_exe = target_dir / "terminal.exe"
                if terminal_exe.exists():
                    acc["terminal_path"] = str(terminal_exe)
                    self.thread_log(f"Set path: {acc['terminal_path']}")
                else:
                    self.thread_log(f"WARNING: No terminal.exe found in {target_dir}", 'WARNING')
            except Exception as e:
                self.thread_log(f"Error for {acc['account']}: {e}", 'ERROR')
        save_store(self.store)
        self._refresh_lists()
        self.thread_log("Portable terminals created with portable mode.")

    # ------------------------------------------------------------------
    #  Data store & UI refresh
    # ------------------------------------------------------------------
    def _refresh_lists(self):
        # groups tree
        for i in self.groups_tree.get_children(): self.groups_tree.delete(i)
        for idx, g in enumerate(self.store.get("groups", [])):
            active = "yes" if g.get("active", True) else "no"
            self.groups_tree.insert("", "end", iid=str(idx),
                                    values=(g.get("name"), g.get("vol_multiplier", 1.0),
                                            g.get("forced_volume", 0.0), active))

        # accounts tree (now includes path)
        for i in self.accounts_tree.get_children(): self.accounts_tree.delete(i)
        for idx, a in enumerate(self.store.get("accounts", [])):
            active = "yes" if a.get("active", True) else "no"
            path_display = os.path.basename(a.get("terminal_path", "")) or "(global)"
            self.accounts_tree.insert("", "end", iid=str(idx),
                                      values=(a.get("account", ""), a.get("server", ""),
                                              a.get("group", "default"), active, path_display))

        group_names = [g["name"] for g in self.store.get("groups", [])] or ["default"]
        if hasattr(self, 'group_trade_cb'):
            self.group_trade_cb['values'] = group_names
            if self.group_trade_var.get() not in group_names:
                self.group_trade_var.set(group_names[0])

    # Account/Group management (unchanged except terminal_path in dialogs)
    def add_group(self):
        dlg = GroupDialog(self.root, None)
        self.root.wait_window(dlg.top)
        if dlg.result:
            self.store["groups"].append(dlg.result)
            save_store(self.store)
            self._refresh_lists()

    def edit_group(self):
        sel = self.groups_tree.selection()
        if not sel: messagebox.showinfo("Edit Group", "Select a group"); return
        idx = int(sel[0])
        dlg = GroupDialog(self.root, self.store["groups"][idx])
        self.root.wait_window(dlg.top)
        if dlg.result:
            self.store["groups"][idx] = dlg.result
            save_store(self.store)
            self._refresh_lists()

    def remove_group(self):
        sel = self.groups_tree.selection()
        if not sel: messagebox.showinfo("Remove Group", "Select a group"); return
        idx = int(sel[0])
        name = self.store["groups"][idx]["name"]
        if name == "default": messagebox.showwarning("Remove Group", "Default group cannot be removed."); return
        for a in self.store["accounts"]:
            if a.get("group") == name: a["group"] = "default"
        del self.store["groups"][idx]
        save_store(self.store)
        self._refresh_lists()

    def add_account(self):
        dlg = AccountDialog(self.root, None, self.store["groups"])
        self.root.wait_window(dlg.top)
        if dlg.result:
            # ensure terminal_path
            dlg.result.setdefault("terminal_path", "")
            self.store["accounts"].append(dlg.result)
            save_store(self.store)
            self._refresh_lists()

    def edit_account(self):
        sel = self.accounts_tree.selection()
        if not sel: messagebox.showinfo("Edit Account", "Select an account"); return
        idx = int(sel[0])
        dlg = AccountDialog(self.root, self.store["accounts"][idx], self.store["groups"])
        self.root.wait_window(dlg.top)
        if dlg.result:
            dlg.result.setdefault("terminal_path", "")
            self.store["accounts"][idx] = dlg.result
            save_store(self.store)
            self._refresh_lists()

    def remove_account(self):
        sel = self.accounts_tree.selection()
        if not sel: messagebox.showinfo("Remove Account", "Select an account"); return
        idx = int(sel[0])
        a = self.store["accounts"][idx]
        if not messagebox.askyesno("Confirm delete", f"Delete account {a.get('account')}?"): return
        # stop agent if running
        if self.agent_controller:
            try:
                self.agent_controller.stop_agent(a.get("account"))
            except Exception:
                pass
        del self.store["accounts"][idx]
        # remove portable terminal folder if it was created under DATA_ROOT
        term_path = a.get("terminal_path", "")
        if term_path and DATA_ROOT in term_path:
            try:
                shutil.rmtree(Path(term_path).parent, ignore_errors=True)
            except Exception:
                pass
        save_store(self.store)
        self._refresh_lists()

    def save_store_now(self):
        save_store(self.store)
        self.thread_log("Accounts saved successfully")

    def get_active_accounts(self):
        return [a for a in self.store.get("accounts", []) if a.get("active", True)]

    def get_accounts_by_group(self, group_name):
        return [a for a in self.store.get("accounts", []) if
                a.get("active", True) and a.get("group", "default") == group_name]

    def compute_sl_tp(self, symbol, side, value_text, mode):
        if not value_text or value_text.strip() == "":
            return 0.0
        try:
            value = float(value_text)
        except ValueError:
            return 0.0
        if mode == "price":
            return value
        pip_val = symbol_pip_value(symbol)
        if pip_val is None:
            pip_val = 0.0001
        if side.lower() == "buy":
            if value > 0:
                return -value * pip_val
            return 0.0
        else:
            if value > 0:
                return value * pip_val
            return 0.0

    # ------------------------------------------------------------------
    #  Parallel trading core (uses Agents now)
    # ------------------------------------------------------------------
    def _trade_one_account_thread(self, acc, symbol, side, base_volume, dev, magic, order_type,
                                  sl_price, tp_price, results, idx):
        """Not used directly — we now use agents via _execute_parallel_trade."""
        pass

    def _execute_parallel_trade(self, accounts, label):
        """Ask each account's agent to place an order in parallel (agents are separate processes).
        Supports market and pending orders via order_kind, pending_type, and price.
        """
        if self.agent_controller is None:
            self.thread_log("Agent controller not available; cannot execute agent-based trades", 'ERROR')
            return

        symbol = self.symbol_var.get().strip()
        side = self.side_var.get()
        try:
            base_volume = float(self.volume_var.get())
        except:
            self.thread_log("Invalid volume", 'ERROR')
            return
        dev = int(self.deviation_var.get() or 50)
        base_magic = int(self.magic_var.get() or 0)
        filling = self.filling_mode_var.get()
        order_kind_ui = self.order_type_var.get()  # "market" or "pending"
        pending_type_ui = self.pending_type_var.get()  # "limit" or "stop"

        # parse price if provided
        price_raw = self.entry_price_var.get().strip()
        price_value = None
        if price_raw != "":
            try:
                price_value = float(price_raw)
            except:
                price_value = None

        n = len(accounts)
        self.thread_log(f"=== Parallel trade on {n} accounts ({label}) ===")

        results = {}
        threads = []

        # --- risk caps per group ---
        group_name = accounts[0].get("group", "default") if accounts else "default"
        max_pos_symbol, max_total_lots = self._get_group_limits(group_name)
        current_positions = self._fetch_positions_for_accounts(accounts)
        # count current positions for this symbol and total lots
        current_pos_count = sum(1 for p in current_positions if p.get("symbol") == symbol)
        current_total_lots = sum(p.get("volume", 0.0) for p in current_positions)

        # pre-assign which accounts will be executed to respect caps (simple sequential allocation)
        to_execute = []
        for acc in accounts:
            acc_id = str(acc.get("account"))
            vol = compute_account_volume(acc, base_volume, self.store.get("groups", []))
            # check per-symbol count cap
            if max_pos_symbol and current_pos_count >= max_pos_symbol:
                results[acc_id] = (False, "group max positions per symbol reached")
                continue
            # check total lots cap
            if max_total_lots and (current_total_lots + vol) > max_total_lots:
                results[acc_id] = (False, "group max total lots exceeded")
                continue
            # reserve capacity
            current_pos_count += 1
            current_total_lots += vol
            to_execute.append(acc)

        def worker(acc):
            acc_id = str(acc.get("account"))
            # block missing credentials early
            if not acc.get("password") or not acc.get("server"):
                results[acc_id] = (False, "missing password or server")
                return
            # cooldown check
            if self._account_in_cooldown(acc):
                results[acc_id] = (False, "cooldown active")
                return
            # ensure agent running
            try:
                base_path = self.settings.get("base_terminal_path") or self.terminal_path_var.get().strip()
                if not acc.get("terminal_path"):
                    new_path, err = ensure_account_terminal(acc, base_path)
                    if new_path:
                        acc["terminal_path"] = new_path
                        save_store(self.store)
                self.agent_controller.start_agent(acc)
            except Exception as e:
                results[acc_id] = (False, f"agent start failed: {e}")
                return

            volume = compute_account_volume(acc, base_volume, self.store.get("groups", []))

            # per-account max positions check
            try:
                maxpos = int(acc.get("max_positions_per_symbol", 0))
            except Exception:
                maxpos = 0
            if maxpos > 0:
                try:
                    okp, pos = self.agent_controller.send_command(acc_id, {"action": "get_positions"}, timeout=6)
                    if okp and isinstance(pos, list):
                        count = sum(1 for p in pos if p.get("symbol") == symbol)
                        if count >= maxpos:
                            results[acc_id] = (False, f"max positions per symbol reached ({maxpos})")
                            return
                except Exception:
                    pass

            # daily drawdown guard
            try:
                max_dd = float(acc.get("max_daily_drawdown", 0.0))
            except Exception:
                max_dd = 0.0
            if max_dd and max_dd > 0:
                try:
                    okp, pnl = self.agent_controller.send_command(acc_id, {"action": "get_today_pnl"}, timeout=6)
                    if okp and pnl is not None:
                        try:
                            if float(pnl) <= -abs(max_dd):
                                results[acc_id] = (False, f"daily drawdown limit reached ({pnl:.2f})")
                                return
                        except Exception:
                            pass
                except Exception:
                    pass

            magic = self._magic_for_account(base_magic, acc_id)
            comment = self._comment_for_account(acc_id)
            cmd = {
                "action": "place",
                "symbol": symbol,
                "side": side,
                "volume": float(volume),
                "deviation": int(dev),
                "sl": None,
                "tp": None,
                "magic": int(magic),
                "comment": comment,
                "filling_mode": filling,
                "order_kind": order_kind_ui,  # "market" or "pending"
                "pending_type": pending_type_ui,  # "limit" or "stop"
                "price": price_value
            }

            # send command and wait for response
            try:
                ok, res = self.agent_controller.send_command(acc_id, cmd,
                                                             timeout=max(15, int(self.trade_delay_var.get()) + 30))
            except Exception as e:
                ok, res = False, str(e)
            results[acc_id] = (ok, res)
            if not ok:
                try:
                    self._mark_failure(acc)
                    save_store(self.store)
                except Exception:
                    pass

        for acc in to_execute:
            t = threading.Thread(target=worker, args=(acc,))
            t.daemon = True
            t.start()
            threads.append(t)

        for t in threads:
            t.join()

        success_count = 0
        for acc in accounts:
            acc_id = str(acc.get("account"))
            ok, detail = results.get(acc_id, (False, "no response"))
            if ok:
                self.thread_log(f"[{acc_id}] Trade OK")
                success_count += 1
            else:
                self.thread_log(f"[{acc_id}] Failure: {detail}", 'ERROR')

        self.thread_log(f"Trade batch complete: {success_count}/{n} succeeded.")

    # ------------------------------------------------------------------
    #  Trade command wrappers
    # ------------------------------------------------------------------
    def start_selected(self):
        sel = self.accounts_tree.selection()
        if not sel:
            messagebox.showinfo("Trade", "Select an account first")
            return
        idx = int(sel[0])
        acc = self.store["accounts"][idx]
        # Single account, still use parallel engine but with one thread
        threading.Thread(target=self._execute_parallel_trade, args=([acc], "selected"), daemon=True).start()

    def bid_all_accounts(self):
        accounts = self.get_active_accounts()
        if not accounts:
            messagebox.showinfo("Trade All", "No active accounts")
            return
        if not messagebox.askyesno("Confirm", f"Execute trade on {len(accounts)} accounts in parallel?"):
            return
        threading.Thread(target=self._execute_parallel_trade, args=(accounts, "ALL ACTIVE"), daemon=True).start()

    def trade_group(self):
        group_name = self.group_trade_var.get()
        accounts = self.get_accounts_by_group(group_name)
        if not accounts:
            messagebox.showinfo("Trade Group", f"No active accounts in group '{group_name}'")
            return
        if not messagebox.askyesno("Confirm",
                                   f"Execute trade on {len(accounts)} accounts in group '{group_name}' (parallel)?"):
            return
        threading.Thread(target=self._execute_parallel_trade, args=(accounts, f"GROUP: {group_name}"),
                         daemon=True).start()

    # ------------------------------------------------------------------
    #  Parallel close helpers (use agents)
    # ------------------------------------------------------------------
    def _close_all_positions_for_accounts(self, accounts):
        """Close every open position for the given list of accounts using agents (non-destructive)."""
        if self.agent_controller is None:
            self.thread_log("_close_all_positions_for_accounts: agent controller not available", 'ERROR')
            return

        results = {}
        threads = []

        def worker(acc):
            acc_id = str(acc.get("account"))
            try:
                self.agent_controller.start_agent(acc)
                ok, res = self.agent_controller.send_command(acc_id, {"action": "close_all"}, timeout=30)
                results[acc_id] = (ok, res)
            except Exception as e:
                results[acc_id] = (False, str(e))

        for acc in accounts:
            t = threading.Thread(target=worker, args=(acc,))
            t.daemon = True
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        for acc_id, (ok, res) in results.items():
            if ok:
                self.thread_log(f"{acc_id}: closed positions: {res}")
            else:
                self.thread_log(f"{acc_id}: close positions failed: {res}", 'ERROR')

    def close_all_positions(self):
        accounts = self.get_active_accounts()
        if not accounts:
            messagebox.showinfo("Close All", "No active accounts")
            return
        if not messagebox.askyesno("Confirm", f"Close ALL positions on {len(accounts)} accounts?"):
            return
        threading.Thread(target=self._close_all_positions_for_accounts, args=(accounts,), daemon=True).start()

    def close_selected_accounts_positions(self):
        sel = self.accounts_tree.selection()
        if not sel:
            messagebox.showinfo("Close Selected", "Select accounts first")
            return
        accounts = [self.store["accounts"][int(i)] for i in sel]
        if not messagebox.askyesno("Confirm", f"Close all positions on {len(accounts)} selected accounts?"):
            return
        threading.Thread(target=self._close_all_positions_for_accounts, args=(accounts,), daemon=True).start()

    def close_group_positions(self):
        group_name = self.group_trade_var.get()
        accounts = self.get_accounts_by_group(group_name)
        if not accounts:
            messagebox.showinfo("Close Group", f"No active accounts in group '{group_name}'")
            return
        if not messagebox.askyesno("Confirm",
                                   f"Close all positions on {len(accounts)} accounts in group '{group_name}'?"):
            return
        threading.Thread(target=self._close_all_positions_for_accounts, args=(accounts,), daemon=True).start()

    # ------------------------------------------------------------------
    #  Positions refresh (uses agents)
    # ------------------------------------------------------------------
    def _start_positions_refresher_thread(self):
        """
        Auto-refresh using Tk root.after every 500 ms.
        This triggers the same work as pressing "Refresh Now" and avoids thread overlap.
        """

        self._last_refresh_ts = 0.0

        def tick():
            try:
                if self.auto_refresh.get() and not self.refresh_paused:
                    # only start a refresh if none is in progress
                    if not getattr(self, "_refresh_in_progress", False):
                        interval = max(1.0, float(self.refresh_interval.get()))
                        now = time.time()
                        if now - self._last_refresh_ts >= interval:
                            self._last_refresh_ts = now
                            # reuse the same worker used by manual refresh
                            self.refresh_positions_now()
                # schedule next tick in 500 ms
                self.root.after(500, tick)
            except Exception as e:
                # log and schedule next tick anyway to avoid stopping the loop
                self.thread_log(f"Auto-refresh tick error: {e}", 'ERROR')
                self.root.after(500, tick)

        # start the periodic timer
        # call tick once after a short delay to give GUI time to initialize
        self.root.after(500, tick)

    def _collect_positions_across_accounts(self):
        accounts = self.get_active_accounts()
        if not accounts:
            self.thread_log("Collect positions: no active accounts", 'DEBUG')
            return
        if self.agent_controller is None:
            self.thread_log("Collect positions: agent_controller not available", 'ERROR')
            return

        self.thread_log(f"Collect positions: querying {len(accounts)} accounts", 'DEBUG')

        # Ensure agents are started (start on demand)
        for acc in accounts:
            try:
                base_path = self.settings.get("base_terminal_path") or self.terminal_path_var.get().strip()
                if not acc.get("terminal_path"):
                    new_path, err = ensure_account_terminal(acc, base_path)
                    if new_path:
                        acc["terminal_path"] = new_path
                        save_store(self.store)
                self.agent_controller.start_agent(acc)
            except Exception as e:
                self.thread_log(f"Failed to start agent for {acc.get('account')}: {e}", 'ERROR')

        account_ids = [str(a.get("account")) for a in accounts]
        cmd = {"action": "get_positions"}
        try:
            results = self.agent_controller.broadcast(account_ids, cmd, timeout=6)
        except Exception as e:
            self.thread_log(f"Collect positions: broadcast failed: {e}", 'ERROR')
            return

        all_positions = []
        for acc_id, (ok, res) in results.items():
            if not ok:
                # res contains error message
                self.thread_log(f"{acc_id}: positions error: {res}", 'DEBUG')
                continue
            # res expected to be a list of position dicts
            if not isinstance(res, list):
                self.thread_log(f"{acc_id}: unexpected positions response type: {type(res)}", 'DEBUG')
                continue
            for p in res:
                try:
                    all_positions.append({
                        "account": acc_id,
                        "ticket": p.get("ticket"),
                        "symbol": p.get("symbol"),
                        "type": p.get("type"),
                        "volume": p.get("volume"),
                        "price": p.get("price"),
                        "profit": p.get("profit"),
                    })
                except Exception:
                    continue

        self.thread_log(f"Collect positions: total positions fetched = {len(all_positions)}", 'DEBUG')
        try:
            self.positions_q.put({"positions": all_positions, "ts": time.time()})
        except Exception as e:
            self.thread_log(f"Collect positions: failed to enqueue positions: {e}", 'ERROR')

    def _start_positions_queue_processor(self):
        def processor():
            while True:
                try:
                    item = self.positions_q.get(timeout=0.5)
                except:
                    time.sleep(0.1)
                    continue
                self.root.after(0, self._update_positions_ui, item["positions"], item["ts"])
        threading.Thread(target=processor, daemon=True).start()

    def _start_dashboard_updater(self):
        def tick():
            try:
                agents = self.agent_controller.active_agents() if self.agent_controller else []
                self.dash_agents_var.set(str(len(agents)))
                self.dash_positions_var.set(str(len(self.positions_tree.get_children())))
                # update disk usage for DATA_ROOT/terminals
                try:
                    term_dir = os.path.join(DATA_ROOT, "terminals")
                    mb = self._dir_size_mb(term_dir)
                    self.dash_disk_var.set(f"{mb:.1f} MB")
                except Exception:
                    try:
                        self.dash_disk_var.set("0 MB")
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                self.root.after(1000, tick)
            except Exception:
                pass

        try:
            self.root.after(1000, tick)
        except Exception:
            pass

    def _dir_size_mb(self, path):
        total = 0
        try:
            for root, _, files in os.walk(path):
                for f in files:
                    try:
                        fp = os.path.join(root, f)
                        if os.path.islink(fp):
                            continue
                        total += os.path.getsize(fp)
                    except Exception:
                        continue
        except Exception:
            return 0.0
        return total / (1024.0 * 1024.0)

    def _get_group_limits(self, group_name):
        g = next((x for x in self.store.get("groups", []) if x.get("name") == group_name), None)
        if not g:
            return 0, 0.0
        return int(g.get("max_positions_per_symbol", 0)), float(g.get("max_total_lots", 0.0))

    def _fetch_positions_for_accounts(self, accounts):
        """Return list of positions across given accounts via agents: [{'account','symbol','volume'}, ...]"""
        out = []
        if not accounts or self.agent_controller is None:
            return out
        ids = [str(a.get("account")) for a in accounts]
        try:
            # ensure agents started
            for a in accounts:
                try:
                    base_path = self.settings.get("base_terminal_path") or self.terminal_path_var.get().strip()
                    if not a.get("terminal_path"):
                        new_path, err = ensure_account_terminal(a, base_path)
                        if new_path:
                            a["terminal_path"] = new_path
                            save_store(self.store)
                    self.agent_controller.start_agent(a)
                except Exception:
                    continue
            results = self.agent_controller.broadcast(ids, {"action": "get_positions"}, timeout=6)
        except Exception:
            return out
        for acc_id, (ok, res) in results.items():
            if not ok or not isinstance(res, list):
                continue
            for p in res:
                try:
                    out.append({
                        "account": acc_id,
                        "symbol": p.get("symbol"),
                        "volume": float(p.get("volume", 0.0)),
                    })
                except Exception:
                    continue
        return out

    def _magic_for_account(self, base_magic, acc_id):
        try:
            return int(base_magic) + (int(str(acc_id)) % 10000)
        except Exception:
            return int(base_magic)

    def _comment_for_account(self, acc_id):
        return f"mt5_{acc_id}"

    def _account_in_cooldown(self, acc):
        until = float(acc.get("cooldown_until", 0.0))
        try:
            return time.time() < until
        except Exception:
            return False

    def _mark_failure(self, acc):
        acc["fail_count"] = int(acc.get("fail_count", 0)) + 1
        # if fail count reached threshold, set cooldown
        try:
            thresh = int(acc.get("cooldown_after_failures", 3))
            mins = int(acc.get("cooldown_minutes", 15))
            if acc.get("fail_count", 0) >= thresh:
                acc["cooldown_until"] = time.time() + (mins * 60)
        except Exception:
            pass

    def _update_positions_ui(self, positions, ts):
        for i in self.positions_tree.get_children():
            self.positions_tree.delete(i)
        for p in positions:
            values = (p["account"], p["ticket"], p["symbol"], p["type"],
                      f"{p['volume']:.2f}", f"{p['price']:.5f}", f"{p['profit']:.2f}")
            self.positions_tree.insert("", "end", iid=f"{p['account']}_{p['ticket']}", values=values)

    def refresh_positions_now(self):
        # avoid overlapping manual/auto refreshes
        if getattr(self, "_refresh_in_progress", False):
            self.thread_log("Manual refresh skipped: refresh already in progress", 'DEBUG')
            return

        def worker():
            try:
                self._refresh_in_progress = True
                self._collect_positions_across_accounts()
            finally:
                self._refresh_in_progress = False

        threading.Thread(target=worker, daemon=True).start()

    def close_selected_positions(self):
        sel = self.positions_tree.selection()
        if not sel:
            messagebox.showinfo("Close", "Select positions to close")
            return
        if not messagebox.askyesno("Confirm", f"Close {len(sel)} positions?"):
            return
        # For simplicity / safety we will close all positions on affected accounts via agent.
        # This avoids per-ticket mt5 RPC from the GUI process and keeps agents persistent.
        tasks = set()
        for iid in sel:
            try:
                acct, ticket = iid.split("_", 1)
                tasks.add(acct)
            except:
                pass
        accounts = [next((a for a in self.store["accounts"] if str(a.get("account")) == acct), None) for acct in tasks]
        accounts = [a for a in accounts if a]
        if not accounts:
            self.thread_log("No matching accounts for selected positions", 'ERROR')
            return
        if not messagebox.askyesno("Confirm", f"This will close ALL positions on {len(accounts)} accounts. Continue?"):
            return
        threading.Thread(target=self._close_all_positions_for_accounts, args=(accounts,), daemon=True).start()

    def _close_positions_worker(self, tasks):
        # kept for compatibility; not used when agents are enabled
        for acct_id, ticket in tasks:
            acc = next((a for a in self.store["accounts"] if str(a.get("account")) == acct_id), None)
            if not acc:
                continue
            terminal_path = acc.get("terminal_path", "") or self.terminal_path_var.get().strip()
            if not terminal_path:
                continue
            ok, _ = initialize_one_terminal(terminal_path)
            if not ok:
                shutdown_one_terminal()
                continue
            if not mt5.login(int(acc["account"]), acc["password"], acc["server"]):
                shutdown_one_terminal()
                continue
            positions = mt5.positions_get()
            pos = next((p for p in (positions or []) if int(p.ticket) == int(ticket)), None)
            if pos:
                close_ok, res = close_position(pos.ticket, pos.symbol, pos.volume, pos.type)
                self.thread_log(f"Close {pos.ticket} on {acc['account']}: {'OK' if close_ok else res}")
            shutdown_one_terminal()
        self.refresh_positions_now()

    def browse_terminal(self):
        p = filedialog.askopenfilename(title="Select terminal executable", filetypes=[("EXE files", "*.exe")])
        if p:
            self.terminal_path_var.set(p)

    def apply_dark_theme(self):
        """Apply a simple dark theme using ttk.Style and root palette."""
        try:
            style = ttk.Style(self.root)
            # Prefer 'clam' for better element colorability
            try:
                style.theme_use('clam')
            except Exception:
                pass
            bg = '#1e1e1e'
            panel = '#252525'
            fg = '#dcdcdc'
            accent = '#0a84ff'  # blue accent
            btn_bg = '#2e2e2e'
            entry_bg = '#222222'

            # general root bg
            try:
                self.root.configure(background=bg)
            except Exception:
                pass

            # Frames and labels
            style.configure('TFrame', background=bg)
            style.configure('TLabel', background=bg, foreground=fg)
            # Buttons
            style.configure('TButton', background=btn_bg, foreground=fg)
            style.map('TButton', background=[('active', '#3a3a3a')])
            style.configure('Accent.TButton', background=accent, foreground='white')
            style.map('Accent.TButton', background=[('active', '#0066d6')])
            # Entry / Combobox
            style.configure('TEntry', fieldbackground=entry_bg, foreground=fg)
            style.configure('TCombobox', fieldbackground=entry_bg, foreground=fg)
            # Treeview background
            style.configure('Treeview', background=panel, fieldbackground=panel, foreground=fg)
            style.configure('Treeview.Heading', background=btn_bg, foreground=fg)
        except Exception:
            # do not fail startup on theme errors
            pass

    def run_startup_checks(self):
        """Quick checks at startup to log important configuration issues."""
        try:
            # ControllerAgent availability
            if ControllerAgent is None or not getattr(self, "agent_controller", None):
                self.thread_log("ControllerAgent not available — agents disabled.", 'WARNING')
            else:
                self.thread_log("ControllerAgent is available.", 'DEBUG')

            # MetaTrader5 presence
            if mt5 is None:
                self.thread_log("MetaTrader5 (mt5) module not available in Python environment.", 'WARNING')
            else:
                try:
                    term = mt5.__version__
                    self.thread_log(f"MetaTrader5 module available (version {term}).", 'DEBUG')
                except Exception:
                    self.thread_log("MetaTrader5 module imported, but version info unavailable.", 'DEBUG')

            # Accounts terminal paths + portable.dat
            any_accounts = False
            for a in self.store.get("accounts", []):
                any_accounts = True
                acc_id = a.get("account", "(no id)")
                term_path = a.get("terminal_path", "") or self.terminal_path_var.get().strip()
                if not term_path:
                    self.thread_log(f"Account {acc_id}: terminal_path not set.", 'WARNING')
                else:
                    if not os.path.exists(term_path):
                        self.thread_log(f"Account {acc_id}: terminal executable not found: {term_path}", 'WARNING')
                    else:
                        pmarker = Path(term_path).parent / "portable.dat"
                        if not pmarker.exists():
                            self.thread_log(f"Account {acc_id}: portable.dat missing in {pmarker.parent}", 'WARNING')
            if not any_accounts:
                self.thread_log("No accounts configured in store.", 'WARNING')

            self.thread_log("Startup checks complete.", 'DEBUG')
        except Exception as e:
            self.thread_log(f"Startup checks failed: {e}", 'ERROR')

    def save_settings_now(self):
        self.settings = {
            'init_delay': self.init_delay_var.get(),
            'login_delay': self.login_delay_var.get(),
            'trade_delay': self.trade_delay_var.get(),
            'shutdown_delay': self.shutdown_delay_var.get(),
            'between_accounts_delay': self.between_accounts_delay_var.get(),
            'retry_delay': self.retry_delay_var.get(),
            'max_retries': self.max_retries_var.get(),
            'positions_refresh_interval': max(1.0, self.refresh_interval.get()),
            'auto_refresh_positions': self.auto_refresh.get(),
            'debug_mode': SETTINGS.get('debug_mode', True),
            'filling_mode': self.filling_mode_var.get(),
            'lot_jitter_enabled': self.lot_jitter_enabled_var.get(),
            'lot_jitter_percent': self.lot_jitter_percent_var.get(),
            'micro_delay_enabled': self.micro_delay_enabled_var.get(),
            'micro_delay_min': self.micro_delay_min_var.get(),
            'micro_delay_max': self.micro_delay_max_var.get(),
            'base_terminal_path': self.terminal_path_var.get().strip(),
        }
        save_settings(self.settings)
        SETTINGS.update(self.settings)
        self.thread_log("Settings saved successfully")

    def on_closing(self):
        self.refresh_paused = True
        self.auto_refresh.set(False)
        # stop agents gracefully
        try:
            if self.agent_controller:
                self.agent_controller.stop_all()
        except Exception:
            pass
        safe_shutdown_mt5()
        self.root.destroy()


# ----------------------------------------------------------------------
#  Dialogs (modified AccountDialog to include terminal path)
# ----------------------------------------------------------------------
class GroupDialog:
    def __init__(self, parent, group):
        self.top = tk.Toplevel(parent)
        self.top.transient(parent)
        self.top.grab_set()
        self.result = None
        self.group = dict(group) if group else {"name": "", "vol_multiplier": 1.0, "forced_volume": 0.0, "active": True,
                                               "max_positions_per_symbol": 0, "max_total_lots": 0.0}
        self._build()

    def _build(self):
        f = ttk.Frame(self.top)
        f.pack(padx=8, pady=8)
        ttk.Label(f, text="Group name:").grid(row=0, column=0, sticky=tk.W)
        self.name_e = ttk.Entry(f)
        self.name_e.grid(row=0, column=1)
        ttk.Label(f, text="Volume multiplier:").grid(row=1, column=0, sticky=tk.W)
        self.vol_e = ttk.Entry(f)
        self.vol_e.grid(row=1, column=1)
        ttk.Label(f, text="Forced volume (0=none):").grid(row=2, column=0, sticky=tk.W)
        self.forced_e = ttk.Entry(f)
        self.forced_e.grid(row=2, column=1)
        ttk.Label(f, text="Max pos/symbol (0=none):").grid(row=3, column=0, sticky=tk.W)
        self.maxpos_e = ttk.Entry(f)
        self.maxpos_e.grid(row=3, column=1)

        ttk.Label(f, text="Max total lots (0=none):").grid(row=4, column=0, sticky=tk.W)
        self.maxlots_e = ttk.Entry(f)
        self.maxlots_e.grid(row=4, column=1)
        self.active_var = tk.BooleanVar(value=self.group.get("active", True))
        ttk.Checkbutton(f, text="Active", variable=self.active_var).grid(row=5, column=0, columnspan=2)
        self.name_e.insert(0, self.group.get("name", ""))
        self.vol_e.insert(0, str(self.group.get("vol_multiplier", 1.0)))
        self.forced_e.insert(0, str(self.group.get("forced_volume", 0.0)))
        self.maxpos_e.insert(0, str(self.group.get("max_positions_per_symbol", 0)))
        self.maxlots_e.insert(0, str(self.group.get("max_total_lots", 0.0)))
        ttk.Button(self.top, text="OK", command=self.ok).pack(pady=6)

    def ok(self):
        n = self.name_e.get().strip()
        if not n: messagebox.showerror("Error", "Group name required"); return
        try:
            vol = float(self.vol_e.get() or 1.0)
            forced = float(self.forced_e.get() or 0.0)
            maxpos = int(self.maxpos_e.get() or 0)
            maxlots = float(self.maxlots_e.get() or 0.0)
            self.result = {"name": n, "vol_multiplier": vol, "forced_volume": forced,
                           "active": bool(self.active_var.get()),
                           "max_positions_per_symbol": maxpos, "max_total_lots": maxlots}
            self.top.destroy()
        except Exception as e:
            messagebox.showerror("Error", f"Invalid numbers: {e}")


class AccountDialog:
    def __init__(self, parent, account, groups):
        self.top = tk.Toplevel(parent)
        self.top.transient(parent)
        self.top.grab_set()
        self.result = None
        self.account = dict(account) if account else {"name": "", "account": "", "password": "", "server": "",
                                                      "active": True, "group": "default", "terminal_path": ""}
        self.groups = groups
        self._build()

    def _build(self):
        f = ttk.Frame(self.top)
        f.pack(padx=8, pady=8)
        ttk.Label(f, text="Account ID:").grid(row=0, column=0, sticky=tk.W)
        self.acc_e = ttk.Entry(f)
        self.acc_e.grid(row=0, column=1)
        ttk.Label(f, text="Password:").grid(row=1, column=0, sticky=tk.W)
        self.pwd_e = ttk.Entry(f, show="*")
        self.pwd_e.grid(row=1, column=1)
        ttk.Label(f, text="Server:").grid(row=2, column=0, sticky=tk.W)
        self.serv_e = ttk.Entry(f)
        self.serv_e.grid(row=2, column=1)
        ttk.Label(f, text="Max pos/symbol (0=none):").grid(row=3, column=0, sticky=tk.W)
        self.maxpos_e = ttk.Entry(f)
        self.maxpos_e.grid(row=3, column=1)

        ttk.Label(f, text="Max daily DD (0=none):").grid(row=4, column=0, sticky=tk.W)
        self.maxdd_e = ttk.Entry(f)
        self.maxdd_e.grid(row=4, column=1)

        ttk.Label(f, text="Cooldown after failures:").grid(row=5, column=0, sticky=tk.W)
        self.coolfails_e = ttk.Entry(f)
        self.coolfails_e.grid(row=5, column=1)

        ttk.Label(f, text="Cooldown minutes:").grid(row=6, column=0, sticky=tk.W)
        self.coolmins_e = ttk.Entry(f)
        self.coolmins_e.grid(row=6, column=1)
        self.active_var = tk.BooleanVar(value=self.account.get("active", True))
        ttk.Checkbutton(f, text="Active", variable=self.active_var).grid(row=7, column=0, columnspan=2)
        ttk.Label(f, text="Group:").grid(row=8, column=0, sticky=tk.W)
        self.group_var = tk.StringVar(value=self.account.get("group", "default"))
        group_names = [g["name"] for g in self.groups] or ["default"]
        self.group_cb = ttk.Combobox(f, values=group_names, textvariable=self.group_var, state="readonly")
        self.group_cb.grid(row=8, column=1)

        # New: Terminal Path
        ttk.Label(f, text="Terminal Path:").grid(row=9, column=0, sticky=tk.W)
        self.path_var = tk.StringVar(value=self.account.get("terminal_path", ""))
        path_frame = ttk.Frame(f)
        path_frame.grid(row=9, column=1, sticky=tk.EW)
        ttk.Entry(path_frame, textvariable=self.path_var, width=40).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(path_frame, text="Browse", command=self.browse_path).pack(side=tk.RIGHT)

        self.acc_e.insert(0, self.account.get("account", ""))
        self.pwd_e.insert(0, self.account.get("password", ""))
        self.serv_e.insert(0, self.account.get("server", ""))
        self.maxpos_e.insert(0, str(self.account.get("max_positions_per_symbol", 0)))
        self.maxdd_e.insert(0, str(self.account.get("max_daily_drawdown", 0.0)))
        self.coolfails_e.insert(0, str(self.account.get("cooldown_after_failures", 3)))
        self.coolmins_e.insert(0, str(self.account.get("cooldown_minutes", 15)))
        ttk.Button(self.top, text="OK", command=self.ok).pack(pady=6)

    def browse_path(self):
        p = filedialog.askopenfilename(title="Select terminal64.exe for this account",
                                        filetypes=[("EXE files", "*.exe")])
        if p:
            self.path_var.set(p)

    def ok(self):
        accid = self.acc_e.get().strip()
        if not accid: messagebox.showerror("Error", "Account ID required"); return
        try:
            maxpos = int(self.maxpos_e.get() or 0)
        except Exception:
            maxpos = 0
        try:
            maxdd = float(self.maxdd_e.get() or 0.0)
        except Exception:
            maxdd = 0.0
        try:
            coolfails = int(self.coolfails_e.get() or 3)
        except Exception:
            coolfails = 3
        try:
            coolmins = int(self.coolmins_e.get() or 15)
        except Exception:
            coolmins = 15

        self.result = {
            "name": accid,
            "account": accid,
            "password": self.pwd_e.get().strip(),
            "server": self.serv_e.get().strip(),
            "active": bool(self.active_var.get()),
            "group": self.group_var.get() or "default",
            "terminal_path": self.path_var.get().strip(),
            "max_positions_per_symbol": maxpos,
            "max_daily_drawdown": maxdd,
            "fail_count": int(self.account.get("fail_count", 0)),
            "cooldown_after_failures": coolfails,
            "cooldown_minutes": coolmins,
            "cooldown_until": float(self.account.get("cooldown_until", 0.0)),
        }
        self.top.destroy()


def main():
    multiprocessing.freeze_support()
    root = tk.Tk()
    root.state("zoomed")
    app = MT5ManagerApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)




if __name__ == "__main__":
    main()