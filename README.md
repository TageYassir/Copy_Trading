# MT5 Manager – Multi Account Trader with Group Trading  

## 🤝 Contributors
| Avatar | Contributor |
| :---: | :--- |
| <img src="https://github.com/TageYassir.png" width="40px;"/> | **Yassir Tagemouati** [@yassir](https://github.com/TageYassir) |

### Prop‑Firm Safe Edition

**MT5 Manager** is a professional multi‑account trade copier for MetaTrader 5, built with safety and reliability at its core. It uses per‑account **agent processes** (multiprocessing) to maintain independent, persistent MT5 sessions – eliminating login bursts, library cross‑talk, and single‑point‑of‑failure. Designed for **prop‑firm challenges** and multi‑account management, it includes intelligent anti‑ban features, risk controls, and an easy‑to‑use GUI.

---

## Features

- **Multi‑account parallel trading** – Send market or pending orders to any number of MT5 accounts simultaneously.
- **Group‑based trade management** – Define groups with custom volume multipliers, forced lot sizes, and risk caps (max positions per symbol, max total lots).
- **Per‑account safety** – Configurable daily drawdown limits, cooldown after consecutive failures, and max positions per symbol.
- **Anti‑ban protections** – Random lot‑size jitter, micro‑delay before order submission, and natural timing variations to mimic human trading.
- **Persistent per‑account agents** – Each MT5 login runs in its own operating system process, avoiding the need to repeatedly initialise/shutdown the library.
- **Portable MT5 setup** – Automatically clone MT5 installations into separate portable folders, one per account, ensuring full isolation.
- **Comprehensive logging** – Activity log in the GUI, plus per‑account CSV trade journals and debug logs.
- **Real‑time position tracking** – Automatic (or manual) refresh of open positions across all accounts, with the ability to close individual, per‑account, or per‑group positions.
- **Configurable delays** – Fine‑tune timings for initialisation, login, between trades, shutdown, and retries.
- **Dark theme GUI** – Tkinter‑based interface with a clean, modern look.
- **Fail‑safe shutdown** – Force‑quit option and global sentinel file ensure all agent processes and MT5 terminals are cleanly terminated on exit.

---

## Architecture

```
MT5 Manager (GUI)
      │
      ├── ControllerAgent (IPC manager)
      │        │
      │        ├── Agent process 1 → MT5 instance (portable)
      │        ├── Agent process 2 → MT5 instance (portable)
      │        └── ...
      │
      └── Trade/Management logic
```

- **GUI** – Tkinter application (`mt5_manager.py` or your compiled executable).
- **ControllerAgent** – Manages per‑account agent processes, sends commands, and collects responses via multiprocessing queues.
- **Agent processes** (`agent.py`) – Long‑lived processes that each:
  - Start and keep a persistent MT5 connection (login once).
  - Listen for commands (place orders, close all, get positions, get PnL).
  - Log all trade events (open/close) to a CSV file.
  - Automatically re‑login if connection drops.
- **Portable terminals** – On first run, the app can copy your base MT5 installation into isolated folders (one per account) and create `portable.dat` markers, so each account runs in its own environment without conflicts.

---

## Prerequisites

- **Windows 10/11** (recommended) – The portable terminal and process management are tailored for Windows. Limited Linux support is available but not thoroughly tested.
- **MetaTrader 5** installed on your machine (any broker version; the app will clone it).
- **Python 3.9+** (if running from source) with the following packages:
  - `MetaTrader5`
  - `tkinter` (usually bundled with Python on Windows)
- The app can also be packaged into a standalone `.exe` (see [Packaging](#packaging)).

---

## Installation

### From source

1. Clone or download the repository.
2. Place the following files together:
   - `mt5_manager.py` (main GUI)
   - `ipc1/agent.py` (per‑account agent)
   - `ipc1/controller_agent.py` (agent manager)
3. Install dependencies:
   ```bash
   pip install MetaTrader5
   ```
4. *(Optional)* Install PyInstaller to build a standalone executable.

### Portable executable

If you have a pre‑built `.exe`, simply run it. All configuration and data will be stored in `%APPDATA%/Trading_System` (or `~/.config/Trading_System` on Linux).

---

## Configuration

All settings, accounts, and groups are stored in JSON files inside the data directory (`%APPDATA%/Trading_System` on Windows, `~/.config/Trading_System` on Linux).

- **`mt5_accounts.json`** – Accounts, groups, and their settings.
- **`mt5_settings.json`** – Global delays, anti‑ban toggles, filling mode, etc.

### First‑time setup

1. Launch the app.
2. **Select your base MT5 terminal** – Click *Browse...* and locate `terminal64.exe` (usually in `C:\Program Files\MetaTrader 5\`).
3. **Add accounts** – Click the *+* button under *Accounts*. For each account enter:
   - **Account ID** (login number)
   - **Password**
   - **Server** (broker server name)
   - **Group** (or leave “default”)
   - *(Optional)* Max positions per symbol, daily drawdown limit, cooldown settings.
4. **Setup Portable Terminals** – Click the *Setup Portable Terminals* button. The app will clone the base MT5 installation into isolated folders under `%APPDATA%/Trading_System/MT5_Portable` and assign a terminal path to each account.
5. **Create groups** (if needed) – Define volume multipliers, forced lots, and risk caps in the *Groups* panel.

### Settings tab

All timing delays, filling mode, and anti‑ban features can be adjusted here and saved persistently.

---

## Usage

### Main window

- **Trading tab** – Choose symbol, side (buy/sell), volume, deviation, SL/TP, and order type (market or pending).  
  *Buttons:*
  - *Trade Selected Account* – execute on the highlighted account in the account list.
  - *Trade ALL Active Accounts* – trade on every active account.
  - *Trade GROUP* – trade on all accounts belonging to the selected group (dropdown).
- **Positions tab** – See open positions across all accounts. Use auto‑refresh (adjustable interval) or refresh manually.  
  *Buttons:* Close selected position(s), close all, close per‑account, close per‑group.
- **Log tab** – Real‑time activity log.
- **Dashboard** – Quick overview of active agents, known positions, and disk usage.

### Kill Terminals / Force Quit

If something hangs, use *Kill Terminals* to forcefully stop all MT5 processes, or *Force Quit* to terminate the entire application (and agents) immediately.

---

## Anti‑Ban Features

Prop‑firms often use algorithms to detect trade copiers. To remain undetected, enable the built‑in anti‑ban measures in the **Settings** tab:

- **Lot Size Jitter** – Randomly adjusts the lot size by ±X% for each order. Default: 2.0%.
- **Micro‑Delay before Order** – Inserts a random short delay (0.1‑0.5 seconds) before each order is sent, imitating human reaction time.
- **Natural sequence delays** – Configurable init, login, between‑accounts, and retry delays avoid robotic behaviour.
- **Unique magic numbers and comments** – Each account gets a slightly different magic number and comment to appear unique.

> ⚠️ **Important:** These features reduce but do not guarantee undetectability. Use at your own risk, and always test in demo first.

---

## Logging & Monitoring

- **GUI log** – All important events are displayed in the *Log* tab and written to `logs/app/<date>/app.log`.
- **Per‑agent logs** – Each account has its own debug log at `ipc1/agent_logs/agent_<account>.log`.
- **Trade CSV** – Every open and close is recorded in `ipc1/agent_logs/trades_<account>.csv` with timestamp, ticket, symbol, side, volume, and price. Useful for performance tracking and auditing.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| “ControllerAgent import failed” | Ensure `ipc1/agent.py` and `ipc1/controller_agent.py` are in the correct subdirectory relative to the main script. |
| “Terminal executable not found” | Browse to `terminal64.exe` again or check that your portable terminals were set up correctly. |
| Orders not being placed on some accounts | Check the log: missing password/server, cooldown active, daily drawdown limit hit, or symbol not visible. Also verify that **AutoTrading** is enabled in MT5 (Ctrl+E in the terminal). |
| Agent process dies repeatedly | Check the per‑account agent log for exceptions. Could be due to missing `portable.dat`, antivirus blocking `terminal64.exe`, or insufficient system resources. |
| GUI freezes | Use *Force Quit* or kill `python.exe` from Task Manager. Re‑launch and check for long‑running agent operations. |

---

## Contributing – Fork, Improve, and Share

We ❤️ contributions! MT5 Manager is open-source under the MIT license, and you’re encouraged to **fork the project**, add your own features, and submit pull requests. Whether it’s a bug fix, a new anti‑ban trick, support for another broker, or a complete UI overhaul – your help is welcome.

### How to fork and contribute

1. **Fork the repository**  
   Click the “Fork” button on the GitHub page (top right) to create your own copy.

2. **Clone your fork**  
   ```bash
   git clone https://github.com/YOUR_USERNAME/mt5-manager.git
   cd mt5-manager
   ```

3. **Create a feature branch**  
   ```bash
   git checkout -b my-new-feature
   ```

4. **Make your changes**  
   - Add your code, documentation, or assets.  
   - If you add a new feature, consider updating the README and configuration schema.  
   - Keep the code style consistent with the existing project (PEP 8, descriptive variable names).

5. **Test your changes**  
   Run the application locally with a few demo accounts to ensure nothing is broken.  
   ```bash
   python mt5_manager.py
   ```

6. **Commit and push**  
   ```bash
   git add .
   git commit -m "Add my new feature: description"
   git push origin my-new-feature
   ```

7. **Open a Pull Request**  
   Go to the original repository on GitHub and click “New Pull Request”. Choose your feature branch and describe what you’ve done.

### Ideas for contributions

- **GUI enhancements** – modern widgets, chart integration, real‑time equity curves.
- **New order types** – trailing stops, OCO (one‑cancels‑other) groups.
- **Broker compatibility** – adapt the agent to work with cTrader or other platforms.
- **Risk management engine** – more advanced drawdown control, hedging rules, correlation filters.
- **Mobile/web remote control** – a simple REST API server to manage trades from your phone.
- **Packaging improvements** – CI/CD pipelines to auto‑build executables for Windows/macOS/Linux.

### Code of Conduct

Be respectful, constructive, and collaborative. All contributions, big or small, are appreciated. If you’re unsure where to start, look for issues tagged `good first issue` or ask in the Discussions section.

---

## Packaging into a Standalone Executable

You can create a single `.exe` with PyInstaller:

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --add-data "ipc1;ipc1" mt5_manager.py
```

Add an icon with `--icon=assets/app_icon.ico`. Make sure to include the `ipc1` folder.

> **Note:** Multiprocessing with `spawn` context requires the `agent.py` module to be importable. The `--add-data` flag and proper `sys.path` handling in the script already handle this.

---

## Disclaimer

This software is provided for **educational and legal purposes only**. Using trade copiers on prop‑firm platforms may violate their terms of service. The author assumes no liability for any account suspensions, financial losses, or other consequences resulting from the use of this tool. Always test thoroughly on demo accounts before applying to live or evaluation accounts.

---

## License

This project is open‑source under the [MIT License](LICENSE). Feel free to use, modify, and distribute it as you see fit.

---
