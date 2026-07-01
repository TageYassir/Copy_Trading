#!/usr/bin/env python3
"""
Per-account Agent process for persistent MT5 connection (hardened).

Writes per-account CSV at: ipc1/agent_logs/trades_<account>.csv
Columns: timestamp,event,ticket,symbol,side,volume,price,comment,extra
"""
import os
import sys
import time
import uuid
import json
import csv
import logging
import traceback
import subprocess
from pathlib import Path
import datetime
import math
import site

# Inherit parent env paths for spawned process
for p in site.getsitepackages():
    if p not in sys.path:
        sys.path.append(p)

user_site = site.getusersitepackages()
if user_site and user_site not in sys.path:
    sys.path.append(user_site)

try:
    import MetaTrader5 as mt5
except Exception as e:
    mt5 = None
    _MT5_IMPORT_ERROR = str(e)
else:
    _MT5_IMPORT_ERROR = None


# ----------------- app paths -----------------
def get_data_root():
    env_root = os.environ.get("TRADING_SYSTEM_DATA_DIR", "").strip()
    if env_root:
        return env_root
    if os.name == "nt":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    else:
        base = os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "Trading_System")


# ----------------- Logging -----------------
def setup_logger(account_id):
    log = logging.getLogger(f"agent_{account_id}")
    if log.handlers:
        return log
    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    log.addHandler(ch)

    try:
        root = Path(get_data_root()) / "ipc1" / "agent_logs"
        root.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(root / f"agent_{account_id}.log", encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        log.addHandler(fh)
    except Exception:
        try:
            log.exception("Failed to setup file logger")
        except Exception:
            pass
    return log


# ----------------- Safe serialization -----------------
def is_primitive(obj):
    return obj is None or isinstance(obj, (str, int, float, bool))


def deep_serialize(obj):
    try:
        if is_primitive(obj):
            return obj
        if isinstance(obj, dict):
            return {str(k): deep_serialize(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple, set)):
            return [deep_serialize(i) for i in obj]
        if hasattr(obj, "__dict__"):
            return deep_serialize(obj.__dict__)
        return str(obj)
    except Exception:
        return "<unserializable>"


def serialize_mt5_result(obj):
    if obj is None:
        return None
    try:
        out = {}
        attrs = (
            "retcode", "comment", "order", "request", "deal",
            "volume", "price", "position", "trade", "transaction"
        )
        for attr in attrs:
            if hasattr(obj, attr):
                out[attr] = deep_serialize(getattr(obj, attr))
        out["_repr"] = repr(obj)
        return out
    except Exception:
        return {"_error": "serialize_failed", "_repr": str(obj)}


# ----------------- CSV trade logging -----------------
def trades_csv_path_for(account_id):
    root = Path(get_data_root()) / "ipc1" / "agent_logs"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"trades_{account_id}.csv"


def ensure_trades_csv_header(path):
    if not path.exists():
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "event", "ticket", "symbol", "side", "volume", "price", "comment", "extra"])


def append_trade_record(account_id, record):
    try:
        path = trades_csv_path_for(account_id)
        ensure_trades_csv_header(path)
        with open(path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                record.get("timestamp", ""),
                record.get("event", ""),
                record.get("ticket", ""),
                record.get("symbol", ""),
                record.get("side", ""),
                record.get("volume", ""),
                record.get("price", ""),
                record.get("comment", ""),
                json.dumps(deep_serialize(record.get("extra", "")), ensure_ascii=False),
            ])
    except Exception:
        try:
            logging.getLogger().exception("Failed to append trade record")
        except Exception:
            pass


# ----------------- MT5 helpers -----------------
def get_positions_serialized():
    if mt5 is None:
        logger.error(f"MetaTrader5 import failed in agent: {_MT5_IMPORT_ERROR}")
    try:
        positions = mt5.positions_get()
        out = []
        if positions:
            for p in positions:
                out.append({
                    "ticket": int(getattr(p, "ticket", 0)),
                    "symbol": getattr(p, "symbol", ""),
                    "type": "buy" if int(getattr(p, "type", 0)) == 0 else "sell",
                    "volume": float(getattr(p, "volume", 0.0)),
                    "price": float(getattr(p, "price_open", 0.0)),
                    "profit": float(getattr(p, "profit", 0.0)),
                })
        return out
    except Exception:
        return []


def get_today_pnl():
    if mt5 is None:
        logger.error(f"MetaTrader5 import failed in agent: {_MT5_IMPORT_ERROR}")
    try:
        now_dt = datetime.datetime.now()
        start_dt = datetime.datetime(now_dt.year, now_dt.month, now_dt.day)
        deals = mt5.history_deals_get(start_dt, now_dt)
        if not deals:
            return 0.0
        total = 0.0
        for d in deals:
            total += float(getattr(d, "profit", 0.0))
        return total
    except Exception:
        return None


def _normalize_volume(symbol_info, volume):
    vol_step = float(getattr(symbol_info, "volume_step", 0.01) or 0.01)
    min_vol = float(getattr(symbol_info, "volume_min", 0.01) or 0.01)
    max_vol = float(getattr(symbol_info, "volume_max", 100000.0) or 100000.0)
    v = float(volume)
    steps = round(v / vol_step)
    normalized = steps * vol_step
    normalized = max(min_vol, min(max_vol, normalized))
    decimals = max(0, int(round(-math.log10(vol_step))) if vol_step < 1 else 0)
    return round(normalized, decimals)


def _map_filling_mode(name):
    n = (name or "FOK").upper()
    if n == "IOC":
        return mt5.ORDER_FILLING_IOC
    if n == "RETURN":
        return mt5.ORDER_FILLING_RETURN
    return mt5.ORDER_FILLING_FOK


def place_order_local(symbol, side, volume, deviation=50, price=None, sl=0.0, tp=0.0, magic=0,
                      comment="agent_trade", filling_mode_name="FOK",
                      order_kind="market", pending_type="limit", logger=None):
    if mt5 is None:
        logger.error(f"MetaTrader5 import failed in agent: {_MT5_IMPORT_ERROR}")

    try:
        si = mt5.symbol_info(symbol)
        if si is None:
            return False, f"symbol {symbol} not found"

        if not si.visible:
            mt5.symbol_select(symbol, True)
            time.sleep(0.15)

        tick = None
        for _ in range(20):
            tick = mt5.symbol_info_tick(symbol)
            if tick is not None and getattr(tick, "bid", 0) > 0 and getattr(tick, "ask", 0) > 0:
                break
            time.sleep(0.2)

        if tick is None:
            return False, "No prices"

        normalized_volume = _normalize_volume(si, volume)
        filling = _map_filling_mode(filling_mode_name)

        kind = (order_kind or "market").lower()
        side_l = (side or "buy").lower()

        if kind == "market":
            if side_l == "buy":
                order_type = mt5.ORDER_TYPE_BUY
                price_to_use = float(tick.ask if price is None else price)
            else:
                order_type = mt5.ORDER_TYPE_SELL
                price_to_use = float(tick.bid if price is None else price)

            req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": normalized_volume,
                "type": order_type,
                "price": price_to_use,
                "sl": float(sl) if sl else 0.0,
                "tp": float(tp) if tp else 0.0,
                "deviation": int(deviation),
                "magic": int(magic),
                "comment": comment,
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": filling,
            }
        else:
            if price is None:
                return False, "Pending order requires price"
            pt = (pending_type or "limit").lower()
            if side_l == "buy":
                ptype = mt5.ORDER_TYPE_BUY_LIMIT if pt == "limit" else mt5.ORDER_TYPE_BUY_STOP
            else:
                ptype = mt5.ORDER_TYPE_SELL_LIMIT if pt == "limit" else mt5.ORDER_TYPE_SELL_STOP

            req = {
                "action": mt5.TRADE_ACTION_PENDING,
                "symbol": symbol,
                "volume": normalized_volume,
                "type": ptype,
                "price": float(price),
                "sl": float(sl) if sl else 0.0,
                "tp": float(tp) if tp else 0.0,
                "deviation": int(deviation),
                "magic": int(magic),
                "comment": comment,
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": filling,
            }

        res = mt5.order_send(req)
        if res is None:
            return False, "order_send returned None"

        retcode = int(getattr(res, "retcode", -1))
        # success: done / placed
        if retcode in (10009, 10008):
            return True, serialize_mt5_result(res)

        if logger and retcode in (10026, 10027):
            logger.warning(f"AutoTrading disabled (retcode={retcode}) for {symbol}")

        return False, serialize_mt5_result(res)
    except Exception as e:
        if logger:
            logger.error(f"place_order exception: {e}\n{traceback.format_exc()}")
        return False, str(e)


def close_all_positions_local(logger=None):
    if mt5 is None:
        logger.error(f"MetaTrader5 import failed in agent: {_MT5_IMPORT_ERROR}")
    try:
        positions = mt5.positions_get()
        if not positions:
            return True, {"closed": 0}

        closed = 0
        last_err = None

        for p in positions:
            ticket = int(getattr(p, "ticket", 0))
            symbol = getattr(p, "symbol", "")
            vol = float(getattr(p, "volume", 0.0))
            ptype = int(getattr(p, "type", 0))

            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                last_err = f"no tick for {symbol}"
                continue

            if ptype == 0:
                price = tick.bid
                order_type = mt5.ORDER_TYPE_SELL
            else:
                price = tick.ask
                order_type = mt5.ORDER_TYPE_BUY

            for f in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN, mt5.ORDER_FILLING_FOK):
                req = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": symbol,
                    "volume": vol,
                    "type": order_type,
                    "position": ticket,
                    "price": price,
                    "deviation": 50,
                    "magic": 0,
                    "comment": "agent_close_all",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": f,
                }
                res = mt5.order_send(req)
                if res is None:
                    last_err = "order_send returned None"
                    continue
                if int(getattr(res, "retcode", -1)) == 10009:
                    closed += 1
                    break
                last_err = f"retcode {getattr(res, 'retcode', None)}: {getattr(res, 'comment', '')}"

        if closed > 0:
            return True, {"closed": closed}
        return False, last_err or "no-close"
    except Exception as e:
        if logger:
            logger.error(f"close_all_positions_local exception: {e}\n{traceback.format_exc()}")
        return False, str(e)


def initialize_mt5(path, logger=None):
    if mt5 is None:
        logger.error(f"MetaTrader5 import failed in agent: {_MT5_IMPORT_ERROR}")
    try:
        ok = mt5.initialize(path=path, login=0)
        if not ok:
            return False, f"init failed: {mt5.last_error()}"
        return True, None
    except Exception as e:
        if logger:
            logger.error(f"initialize_mt5 exception: {e}\n{traceback.format_exc()}")
        return False, str(e)


def ensure_portable_and_start(terminal_path, logger, start_terminal=True):
    proc = None
    tpath = Path(terminal_path)
    if not tpath.exists():
        return None, f"terminal executable not found: {terminal_path}"

    tdir = tpath.parent
    try:
        pmarker = tdir / "portable.dat"
        if not pmarker.exists():
            pmarker.touch()
            logger.info(f"Created portable.dat in {tdir}")
    except Exception as e:
        logger.warning(f"Could not ensure portable.dat: {e}")

    if not start_terminal:
        return None, None

    try:
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
            proc = subprocess.Popen([str(tpath), "/portable"], cwd=str(tdir), creationflags=creationflags)
        else:
            proc = subprocess.Popen([str(tpath)], cwd=str(tdir))
        logger.info(f"Started terminal PID={proc.pid} for {tdir}")
        time.sleep(5.0)
        return proc, None
    except Exception as e:
        logger.error(f"Failed to start terminal exe: {e}")
        return None, str(e)


# ----------------- Main agent loop -----------------
def agent_main(cmd_q, resp_q, account_cfg):
    account_id = str(account_cfg.get("account", "unknown"))
    logger = setup_logger(account_id)
    logger.info(f"Agent starting for account {account_id}")

    try:
        logger.info(f"Agent file: {__file__} | pid={os.getpid()} | python={sys.executable}")
    except Exception:
        pass

    terminal_path = account_cfg.get("terminal_path", "") or ""
    proc_started = None

    if terminal_path:
        proc, err = ensure_portable_and_start(terminal_path, logger, start_terminal=True)
        if err:
            logger.warning(f"Terminal start warning: {err}")
        else:
            proc_started = proc
    else:
        logger.warning("No terminal_path provided in account_cfg")

    connected = False

    def try_connect():
        nonlocal connected
        try:
            if not account_cfg.get("password") or not account_cfg.get("server"):
                return False, "missing password or server"

            ok, err = initialize_mt5(terminal_path, logger=logger)
            if not ok:
                return False, f"init failed: {err}"

            if not mt5.login(int(account_cfg["account"]), account_cfg.get("password", ""), account_cfg.get("server", "")):
                le = mt5.last_error()
                try:
                    mt5.shutdown()
                except Exception:
                    pass
                return False, f"login failed: {le}"

            connected = True
            logger.info(f"Logged in: {account_id}")
            return True, None
        except Exception as e:
            logger.error(f"connect exception: {e}\n{traceback.format_exc()}")
            return False, str(e)

    # initial connect retries
    backoff = 1.0
    for _ in range(10):
        ok, err = try_connect()
        if ok:
            break
        time.sleep(backoff)
        backoff = min(backoff * 1.8, 20.0)

    hb_interval = 5.0
    last_hb = 0.0

    try:
        while True:
            now = time.time()

            # heartbeat
            if now - last_hb >= hb_interval:
                try:
                    resp_q.put({"type": "hb", "account": account_id, "ts": now})
                except Exception:
                    pass
                last_hb = now

            # keep session alive
            if connected:
                try:
                    ai = mt5.account_info()
                    if ai is None:
                        connected = False
                except Exception:
                    connected = False

            # receive command
            try:
                cmd = cmd_q.get(timeout=0.35)
            except Exception:
                cmd = None

            if cmd is None:
                if not connected:
                    ok, _ = try_connect()
                    if not ok:
                        time.sleep(0.7)
                continue

            cid = cmd.get("id", str(uuid.uuid4()))
            action = cmd.get("action")

            if action == "quit":
                try:
                    resp_q.put({"id": cid, "status": "ok", "result": "quitting"})
                except Exception:
                    pass
                break

            if action == "get_positions":
                if not connected:
                    ok, err = try_connect()
                    if not ok:
                        resp_q.put({"id": cid, "status": "error", "error": f"not connected: {err}"})
                        continue
                pos = get_positions_serialized()
                resp_q.put({"id": cid, "status": "ok", "result": pos})
                continue

            if action == "get_autotrade":
                if not connected:
                    ok, err = try_connect()
                    if not ok:
                        resp_q.put({"id": cid, "status": "error", "error": f"not connected: {err}"})
                        continue
                try:
                    ai = mt5.account_info()
                except Exception:
                    ai = None
                try:
                    ti = mt5.terminal_info()
                except Exception:
                    ti = None

                out = {
                    "account_trade_allowed": bool(getattr(ai, "trade_allowed", False)) if ai is not None else None,
                    "account_info": deep_serialize(ai),
                    "terminal_info": deep_serialize(ti),
                }
                resp_q.put({"id": cid, "status": "ok", "result": out})
                continue

            if action == "get_today_pnl":
                if not connected:
                    ok, err = try_connect()
                    if not ok:
                        resp_q.put({"id": cid, "status": "error", "error": f"not connected: {err}"})
                        continue
                pnl = get_today_pnl()
                resp_q.put({"id": cid, "status": "ok", "result": pnl})
                continue

            if action == "place":
                if not connected:
                    ok, err = try_connect()
                    if not ok:
                        resp_q.put({"id": cid, "status": "error", "error": f"not connected: {err}"})
                        continue

                # snapshot before
                try:
                    before = mt5.positions_get() or []
                    before_tickets = {int(getattr(p, "ticket", 0)): p for p in before}
                except Exception:
                    before_tickets = {}

                symbol = cmd.get("symbol")
                side = cmd.get("side", "buy")
                volume = float(cmd.get("volume", 0.0))
                deviation = int(cmd.get("deviation", 50))
                sl = cmd.get("sl", 0.0) or 0.0
                tp = cmd.get("tp", 0.0) or 0.0
                magic = int(cmd.get("magic", 0))
                comment = cmd.get("comment", "agent_trade")
                filling = cmd.get("filling_mode", "FOK")
                order_kind = cmd.get("order_kind", "market")
                pending_type = cmd.get("pending_type", "limit")
                price_value = cmd.get("price", None)

                ok, res = place_order_local(
                    symbol, side, volume,
                    deviation=deviation,
                    price=price_value,
                    sl=sl, tp=tp, magic=magic,
                    comment=comment,
                    filling_mode_name=filling,
                    order_kind=order_kind,
                    pending_type=pending_type,
                    logger=logger
                )

                # snapshot after (for open logging)
                try:
                    after = mt5.positions_get() or []
                    after_tickets = {int(getattr(p, "ticket", 0)): p for p in after}
                except Exception:
                    after_tickets = {}

                new_tickets = set(after_tickets.keys()) - set(before_tickets.keys())
                for t in new_tickets:
                    p = after_tickets.get(t)
                    rec = {
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
                        "event": "open",
                        "ticket": int(getattr(p, "ticket", 0)),
                        "symbol": getattr(p, "symbol", ""),
                        "side": "buy" if int(getattr(p, "type", 0)) == 0 else "sell",
                        "volume": float(getattr(p, "volume", 0.0)),
                        "price": float(getattr(p, "price_open", 0.0)),
                        "comment": comment,
                        "extra": deep_serialize(res),
                    }
                    append_trade_record(account_id, rec)

                if ok:
                    resp_q.put({"id": cid, "status": "ok", "result": res})
                else:
                    resp_q.put({"id": cid, "status": "error", "error": deep_serialize(res)})
                continue

            if action == "close_all":
                if not connected:
                    ok, err = try_connect()
                    if not ok:
                        resp_q.put({"id": cid, "status": "error", "error": f"not connected: {err}"})
                        continue

                try:
                    before = mt5.positions_get() or []
                    before_map = {int(getattr(p, "ticket", 0)): p for p in before}
                except Exception:
                    before_map = {}

                ok, res = close_all_positions_local(logger=logger)
                time.sleep(0.5)

                try:
                    after = mt5.positions_get() or []
                    after_map = {int(getattr(p, "ticket", 0)): p for p in after}
                except Exception:
                    after_map = {}

                closed_tickets = set(before_map.keys()) - set(after_map.keys())
                for t in closed_tickets:
                    p = before_map.get(t)
                    rec = {
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
                        "event": "close",
                        "ticket": int(getattr(p, "ticket", 0)),
                        "symbol": getattr(p, "symbol", ""),
                        "side": "buy" if int(getattr(p, "type", 0)) == 0 else "sell",
                        "volume": float(getattr(p, "volume", 0.0)),
                        "price": "",
                        "comment": "close_all",
                        "extra": deep_serialize(res),
                    }
                    append_trade_record(account_id, rec)

                if ok:
                    resp_q.put({"id": cid, "status": "ok", "result": res})
                else:
                    resp_q.put({"id": cid, "status": "error", "error": deep_serialize(res)})
                continue

            resp_q.put({"id": cid, "status": "error", "error": f"unknown action {action}"})

    except KeyboardInterrupt:
        logger.info("Agent keyboard interrupt")
    except Exception as e:
        logger.error(f"Agent main loop exception: {e}\n{traceback.format_exc()}")
    finally:
        try:
            if mt5 is not None:
                mt5.shutdown()
        except Exception:
            pass

        if proc_started is not None:
            try:
                proc_started.terminate()
            except Exception:
                pass
            try:
                proc_started.wait(timeout=3)
            except Exception:
                pass

        logger.info(f"Agent for {account_id} exiting")


if __name__ == "__main__":
    print("agent.py module (agent_main) - start via controller")