# Copy Trading – MT5 Multi-Account Manager (Agent Architecture)

## 🤝 Contributors
| Avatar | Contributor |
| :---: | :--- |
| <img src="https://github.com/TageYassir.png" width="40px;"/> | **Yassir Tagemouati** [@yassir](https://github.com/TageYassir) |

Professional MetaTrader 5 multi-account copier with per-account process isolation, persistent sessions, and prop-firm-oriented execution controls.

> Repository: `TageYassir/Copy_Trading`  
> Main desktop app: `Multi_Account_Trader/tool_v4.py`

---

## Highlights

- **Per-account agent processes** (`multiprocessing spawn`)  
  Each account runs in its own OS process to reduce MT5 session conflicts.
- **Persistent MT5 sessions**  
  Agents stay connected and receive commands via IPC queues.
- **Parallel execution across accounts**
- **Group controls** (volume multiplier / forced volume / caps)
- **Risk protections**  
  - per-account max positions per symbol  
  - daily drawdown guard  
  - cooldown after repeated failures
- **CSV + file logging per account**
- **Portable MT5 terminal support** (one terminal clone per account)
- **GUI-based workflow** (Tkinter)

---

## Demo Video

> 📹 A walkthrough video will be added here soon.


[![Watch the demo](https://img.youtube.com/vi/1PcWj2LBUkQ/maxresdefault.jpg)](https://youtu.be/1PcWj2LBUkQ)

Or use this direct link placeholder:  
**Demo:** VIDEO_URL_HERE

## Requirements (User Quick Setup)

## Windows (recommended)
- Windows 10/11
- Python **3.10+** (same interpreter for install + run)
- MetaTrader 5 terminal installed
- At least one broker account (demo/live)

## Python package
```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

---

## Install & Run

From repository root:

```bash
python -m Multi_Account_Trader.tool_v4
```

If you run direct file path, imports may fail. Prefer module mode.

---

## Project Structure

- `Multi_Account_Trader/tool_v4.py` — GUI + orchestration
- `Multi_Account_Trader/ipc1/controller_agent.py` — agent lifecycle + IPC
- `Multi_Account_Trader/ipc1/agent.py` — per-account MT5 worker
- `Multi_Account_Trader/mt5_accounts.json` — accounts/groups
- `Multi_Account_Trader/mt5_settings.json` — timings & behavior

---

## New Technologies/Improvements Implemented

- **Agent-based architecture** for MT5 session isolation
- **Spawn-safe multiprocessing controller**
- **Queue-based command/response IPC**
- **Heartbeat monitoring for agent health**
- **Per-account trade journaling** (`trades_<account>.csv`)
- **Failure throttling / cooldown logic**
- **Portable terminal per account** to reduce cross-account terminal state bleed

---

## First-Time Setup

1. Open app  
2. Set base `terminal64.exe` path  
3. Add accounts (login/server/password/group)  
4. Click **Setup Portable Terminals**  
5. Save store/settings  
6. Test with tiny lot size on demo

---

## Troubleshooting

### `MetaTrader5 module not available` / `No module named MetaTrader5`
Install in the exact Python used to run app:
```bash
C:\Path\To\python.exe -m pip install MetaTrader5
```

### `agent module not available`
- Ensure:
  - `Multi_Account_Trader/__init__.py` exists
  - `Multi_Account_Trader/ipc1/__init__.py` exists
- Run as module:
```bash
python -m Multi_Account_Trader.tool_v4
```

### Account mismatch or cross-account behavior
- Ensure each account uses its own portable terminal path
- Ensure terminal launches in portable mode and unique directory

---

## Security Note

Do **not** commit live passwords to git.  
Use local private config only (or env/protected storage).

---

## Disclaimer

Use at your own risk.  
Always test on demo accounts first and ensure compliance with your broker/prop-firm terms.

---

## License

This project is open‑source under the [MIT License](LICENSE). Feel free to use, modify, and distribute it as you see fit.

---
