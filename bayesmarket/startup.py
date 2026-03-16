"""Interactive startup wizard — runs before main engine starts.

Terminal mode (local/VPS): interactive prompts via stdin.
Railway mode: skipped (Telegram /setup command handles it).
"""

import os
import sys
import time
from typing import Optional

from bayesmarket import config


# ── ANSI color helpers ────────────────────────────────────────────────────────

BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
MAGENTA = "\033[35m"
WHITE = "\033[97m"


def _banner() -> str:
    return f"""{CYAN}{BOLD}
    ╔══════════════════════════════════════════════════╗
    ║                                                  ║
    ║   ██████╗  █████╗ ██╗   ██╗███████╗███████╗     ║
    ║   ██╔══██╗██╔══██╗╚██╗ ██╔╝██╔════╝██╔════╝     ║
    ║   ██████╔╝███████║ ╚████╔╝ █████╗  ███████╗     ║
    ║   ██╔══██╗██╔══██║  ╚██╔╝  ██╔══╝  ╚════██║     ║
    ║   ██████╔╝██║  ██║   ██║   ███████╗███████║     ║
    ║   ╚═════╝ ╚═╝  ╚═╝   ╚═╝   ╚══════╝╚══════╝     ║
    ║               M A R K E T                        ║
    ║                                                  ║
    ║   Automated BTC-PERP Trading Engine              ║
    ║   for Hyperliquid                                ║
    ║                                                  ║
    ╚══════════════════════════════════════════════════╝
{RESET}"""


def _section(title: str) -> None:
    print(f"\n{YELLOW}{BOLD}{'─' * 50}")
    print(f"  {title}")
    print(f"{'─' * 50}{RESET}")


def _info(key: str, value: str) -> None:
    print(f"  {DIM}{key:<22}{RESET} {WHITE}{value}{RESET}")


def _prompt(text: str, default: str = "") -> str:
    suffix = f" [{GREEN}{default}{RESET}]" if default else ""
    try:
        val = input(f"  {CYAN}>{RESET} {text}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    return val if val else default


def _prompt_choice(text: str, options: list[str], default: str = "") -> str:
    opts_display = " / ".join(
        f"{GREEN}{BOLD}{o}{RESET}" if o == default else o for o in options
    )
    while True:
        val = _prompt(f"{text} ({opts_display})", default)
        if val.lower() in [o.lower() for o in options]:
            return val.lower()
        print(f"  {RED}  Invalid choice. Pick one of: {', '.join(options)}{RESET}")


def _prompt_float(text: str, default: float, min_v: float, max_v: float) -> float:
    while True:
        val = _prompt(text, str(default))
        try:
            f = float(val)
            if min_v <= f <= max_v:
                return f
            print(f"  {RED}  Must be between {min_v} and {max_v}{RESET}")
        except ValueError:
            print(f"  {RED}  Must be a number{RESET}")


def _prompt_yn(text: str, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    val = _prompt(f"{text} ({d})", "y" if default else "n")
    return val.lower() in ("y", "yes", "1", "true")


# ── Startup result ────────────────────────────────────────────────────────────

class StartupConfig:
    """Holds wizard selections, applied to config + runtime before engine starts."""

    def __init__(self) -> None:
        self.mode: str = "shadow"            # shadow / testnet / live
        self.capital: float = 1000.0
        self.scoring_threshold: float = 7.0
        self.bias_threshold: float = 3.0
        self.vwap_sensitivity: float = 20.0
        self.poc_sensitivity: float = 20.0
        self.max_leverage: float = 5.0
        self.risk_per_trade: float = 0.02
        self.daily_loss_limit: float = 0.07
        self.telegram_token: str = ""
        self.telegram_chat_id: str = ""
        self.hl_private_key: str = ""
        self.hl_account_address: str = ""
        self.hl_rest_url: str = "https://api.hyperliquid.xyz"
        self.hl_ws_url: str = "wss://api.hyperliquid.xyz/ws"
        self.db_path: str = "bayesmarket.db"
        self.skip_wizard: bool = False


def _step_mode(sc: StartupConfig) -> None:
    """Step 1: Choose operating mode."""
    _section("STEP 1 — Operating Mode")
    print(f"  {DIM}Choose how BayesMarket will operate:{RESET}")
    print()
    print(f"  {GREEN}shadow{RESET}   — No real orders. Simulate trades on live data. (default)")
    print(f"  {YELLOW}testnet{RESET}  — Real orders on Hyperliquid testnet (mock USDC).")
    print(f"  {RED}live{RESET}     — Real orders on Hyperliquid mainnet. Real money.")
    print()

    mode = _prompt_choice("Select mode", ["shadow", "testnet", "live"], "shadow")
    sc.mode = mode


def _step_credentials(sc: StartupConfig) -> None:
    """Step 2: Credentials (testnet/live only)."""
    if sc.mode == "shadow":
        return

    network = "TESTNET" if sc.mode == "testnet" else "MAINNET"
    _section(f"STEP 2 — Hyperliquid Credentials ({network})")

    if sc.mode == "testnet":
        print(f"  {DIM}Get mock USDC: https://app.hyperliquid-testnet.xyz/drip{RESET}")
        print(f"  {DIM}Create API wallet: https://app.hyperliquid-testnet.xyz/API{RESET}")
        sc.hl_rest_url = "https://api.hyperliquid-testnet.xyz"
        sc.hl_ws_url = "wss://api.hyperliquid-testnet.xyz/ws"
    else:
        print(f"  {DIM}Create API wallet: https://app.hyperliquid.xyz/API{RESET}")
        print(f"  {RED}{BOLD}  WARNING: This uses REAL money. Start small.{RESET}")
        sc.hl_rest_url = "https://api.hyperliquid.xyz"
        sc.hl_ws_url = "wss://api.hyperliquid.xyz/ws"

    print()

    # Check if already in env
    env_key = os.getenv("HL_PRIVATE_KEY", "")
    env_addr = os.getenv("HL_ACCOUNT_ADDRESS", "")

    if env_key and env_addr:
        masked_key = env_key[:6] + "..." + env_key[-4:] if len(env_key) > 10 else "***"
        masked_addr = env_addr[:6] + "..." + env_addr[-4:] if len(env_addr) > 10 else "***"
        print(f"  {GREEN}Found credentials in .env:{RESET}")
        _info("Private Key", masked_key)
        _info("Account Address", masked_addr)
        if _prompt_yn("Use these credentials?", True):
            sc.hl_private_key = env_key
            sc.hl_account_address = env_addr
            return

    sc.hl_private_key = _prompt("HL API Wallet Private Key")
    sc.hl_account_address = _prompt("HL Main Wallet Address")

    if not sc.hl_private_key or not sc.hl_account_address:
        print(f"  {RED}Both fields required for {sc.mode} mode. Falling back to shadow.{RESET}")
        sc.mode = "shadow"


def _step_telegram(sc: StartupConfig) -> None:
    """Step 3: Telegram setup."""
    _section("STEP 3 — Telegram Bot (optional)")
    print(f"  {DIM}Telegram enables remote monitoring, control panel, and push dashboard.{RESET}")
    print()

    # Check env
    env_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    env_chat = os.getenv("TELEGRAM_CHAT_ID", "")

    if env_token and env_chat:
        masked_token = env_token[:8] + "..." if len(env_token) > 8 else "***"
        print(f"  {GREEN}Found Telegram config in .env:{RESET}")
        _info("Bot Token", masked_token)
        _info("Chat ID", env_chat)
        if _prompt_yn("Use this Telegram config?", True):
            sc.telegram_token = env_token
            sc.telegram_chat_id = env_chat
            return

    if not _prompt_yn("Configure Telegram bot?", bool(env_token)):
        return

    print(f"  {DIM}1. @BotFather -> /newbot -> copy token{RESET}")
    print(f"  {DIM}2. @userinfobot -> /start -> copy chat ID{RESET}")
    print()
    sc.telegram_token = _prompt("Bot Token")
    sc.telegram_chat_id = _prompt("Chat ID")


def _step_parameters(sc: StartupConfig) -> None:
    """Step 4: Tuning parameters."""
    _section("STEP 4 — Parameters")

    if sc.mode == "shadow":
        sc.capital = _prompt_float("Simulated capital (USD)", 1000.0, 100.0, 1_000_000.0)
    else:
        print(f"  {DIM}Capital will be auto-detected from your Hyperliquid account.{RESET}")

    print()
    print(f"  {DIM}Scoring & cascade thresholds:{RESET}")
    sc.scoring_threshold = _prompt_float("5m trigger threshold", 7.0, 1.0, 13.5)
    sc.bias_threshold = _prompt_float("4h bias threshold", 3.0, 1.0, 10.0)

    print()
    print(f"  {DIM}Sensitivity (higher = more reactive to price deviation):{RESET}")
    sc.vwap_sensitivity = _prompt_float("VWAP sensitivity", 20.0, 1.0, 500.0)
    sc.poc_sensitivity = _prompt_float("POC sensitivity", 20.0, 1.0, 500.0)

    print()
    print(f"  {DIM}Risk management:{RESET}")
    sc.max_leverage = _prompt_float("Max leverage", 5.0, 1.0, 20.0)
    risk_pct = _prompt_float("Risk per trade (%)", 2.0, 0.5, 10.0)
    sc.risk_per_trade = risk_pct / 100.0
    dd_pct = _prompt_float("Daily loss limit (%)", 7.0, 1.0, 25.0)
    sc.daily_loss_limit = dd_pct / 100.0


def _step_database(sc: StartupConfig) -> None:
    """Step 5: Database path."""
    _section("STEP 5 — Database")
    env_db = os.getenv("DB_PATH", "bayesmarket.db")
    if sc.mode == "testnet":
        default_db = "bayesmarket_testnet.db"
    elif sc.mode == "live":
        default_db = "bayesmarket_live.db"
    else:
        default_db = env_db
    sc.db_path = _prompt("SQLite database path", default_db)


def _step_confirm(sc: StartupConfig) -> bool:
    """Final confirmation before launch."""
    _section("CONFIGURATION SUMMARY")

    mode_colors = {"shadow": GREEN, "testnet": YELLOW, "live": RED}
    mode_c = mode_colors.get(sc.mode, WHITE)

    _info("Mode", f"{mode_c}{BOLD}{sc.mode.upper()}{RESET}")

    if sc.mode == "shadow":
        _info("Capital", f"${sc.capital:,.2f}")
    else:
        _info("Network", "Testnet" if sc.mode == "testnet" else "Mainnet")
        masked_addr = sc.hl_account_address[:6] + "..." if sc.hl_account_address else "—"
        _info("Wallet", masked_addr)

    _info("Telegram", "Enabled" if sc.telegram_token else "Disabled")
    _info("Trigger Threshold", str(sc.scoring_threshold))
    _info("Bias Threshold", str(sc.bias_threshold))
    _info("VWAP Sensitivity", str(sc.vwap_sensitivity))
    _info("POC Sensitivity", str(sc.poc_sensitivity))
    _info("Max Leverage", f"{sc.max_leverage}x")
    _info("Risk/Trade", f"{sc.risk_per_trade * 100:.1f}%")
    _info("Daily Loss Limit", f"{sc.daily_loss_limit * 100:.1f}%")
    _info("Database", sc.db_path)

    print()
    return _prompt_yn("Launch with this configuration?", True)


def apply_startup_config(sc: StartupConfig) -> None:
    """Apply wizard selections to config module and env vars."""
    # Mode
    config.LIVE_MODE = sc.mode in ("testnet", "live")
    config.SIMULATED_CAPITAL = sc.capital

    # Network
    config.HL_REST_URL = sc.hl_rest_url
    config.HL_WS_URL = sc.hl_ws_url
    config.IS_TESTNET = "testnet" in sc.hl_rest_url

    # Credentials
    if sc.hl_private_key:
        config.HL_PRIVATE_KEY = sc.hl_private_key
    if sc.hl_account_address:
        config.HL_ACCOUNT_ADDRESS = sc.hl_account_address

    # Telegram
    if sc.telegram_token:
        config.TELEGRAM_BOT_TOKEN = sc.telegram_token
    if sc.telegram_chat_id:
        config.TELEGRAM_CHAT_ID = sc.telegram_chat_id

    # Parameters
    config.MAX_LEVERAGE = sc.max_leverage
    config.MAX_RISK_PER_TRADE = sc.risk_per_trade
    config.DAILY_LOSS_LIMIT = sc.daily_loss_limit
    config.VWAP_SENSITIVITY = sc.vwap_sensitivity
    config.POC_SENSITIVITY = sc.poc_sensitivity
    config.CASCADE_BIAS_THRESHOLD = sc.bias_threshold

    # DB
    from pathlib import Path
    config.DB_PATH = Path(sc.db_path)

    # Update 5m scoring threshold
    config.TIMEFRAMES["5m"]["scoring_threshold"] = sc.scoring_threshold


def run_startup_wizard() -> StartupConfig:
    """Run interactive startup wizard. Returns config selections."""
    sc = StartupConfig()

    print(_banner())

    # Quick launch option
    _section("STARTUP")

    # Check if .env exists and has meaningful config
    has_env = os.path.exists(".env") and os.path.getsize(".env") > 10
    if has_env:
        print(f"  {GREEN}Found .env file with existing configuration.{RESET}")
        print()
        choice = _prompt_choice(
            "Start with .env defaults or run setup wizard?",
            ["start", "wizard"],
            "start",
        )
        if choice == "start":
            sc.skip_wizard = True
            print(f"\n  {GREEN}{BOLD}Launching with .env defaults...{RESET}")
            time.sleep(0.5)
            return sc

    # Full wizard
    _step_mode(sc)
    _step_credentials(sc)
    _step_telegram(sc)
    _step_parameters(sc)
    _step_database(sc)

    if not _step_confirm(sc):
        print(f"\n  {YELLOW}Aborted. Run again to reconfigure.{RESET}")
        sys.exit(0)

    # Offer to save to .env
    print()
    if _prompt_yn("Save this configuration to .env for next time?", True):
        _save_env(sc)
        print(f"  {GREEN}Saved to .env{RESET}")

    print(f"\n  {GREEN}{BOLD}Launching BayesMarket...{RESET}\n")
    time.sleep(0.5)
    return sc


def _save_env(sc: StartupConfig) -> None:
    """Save wizard config to .env file."""
    lines = [
        "# BayesMarket Configuration (generated by startup wizard)",
        f"# Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"LIVE_MODE={'true' if sc.mode in ('testnet', 'live') else 'false'}",
        f"SIMULATED_CAPITAL={sc.capital}",
        "",
        f"COIN=BTC",
        f"BINANCE_SYMBOL=BTCUSDT",
        "",
        f"TELEGRAM_BOT_TOKEN={sc.telegram_token}",
        f"TELEGRAM_CHAT_ID={sc.telegram_chat_id}",
        "",
        f"HL_PRIVATE_KEY={sc.hl_private_key}",
        f"HL_ACCOUNT_ADDRESS={sc.hl_account_address}",
        f"HL_REST_URL={sc.hl_rest_url}",
        f"HL_WS_URL={sc.hl_ws_url}",
        "",
        f"DB_PATH={sc.db_path}",
        "",
        f"DEPLOYMENT_ENV=local",
    ]
    with open(".env", "w") as f:
        f.write("\n".join(lines) + "\n")
