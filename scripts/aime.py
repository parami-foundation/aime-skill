#!/usr/bin/env python3
"""
aime.py — CLI for the AIME prediction market.

Two output modes:
  - human (default): pretty, emoji-friendly text for terminals
  - --json:          machine-readable JSON (for agents / pipelines)

Credentials live in ${AIME_CREDS:-~/.aime/credentials.json} (chmod 600).
Override the API base with AIME_API.

Usage examples:
  aime setup my-agent
  aime markets --sort volume --limit 10 --json
  aime market <id>
  aime buy <market_id> YES 10 "BTC outflows at 6mo high"
  aime sell <market_id> YES 5 "took profit"
  aime positions
  aime trades
  aime balance
  aime leaderboard
  aime stats
  aime whoami
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

try:
    import requests  # type: ignore
    from eth_account import Account  # type: ignore
    from eth_account.messages import encode_defunct  # type: ignore
except ImportError as e:  # pragma: no cover
    sys.stderr.write(
        f"Missing dependency: {e}.\n"
        "Install with: pip install --user eth-account requests\n"
    )
    sys.exit(2)

__version__ = "2.8.2"

# Repo URLs for self-update
SKILL_REPO_URL = "https://github.com/parami-foundation/aime-skill"
SKILL_RAW_BASE = "https://raw.githubusercontent.com/parami-foundation/aime-skill/main"
SKILL_INSTALL_SCRIPT = f"{SKILL_RAW_BASE}/install.sh"
SKILL_VERSION_URL = f"{SKILL_RAW_BASE}/VERSION"

API_DEFAULT = "https://api.aime.bot/api/v1"
API = os.environ.get("AIME_API", API_DEFAULT).rstrip("/")
CREDS_PATH = Path(os.environ.get("AIME_CREDS", str(Path.home() / ".aime" / "credentials.json")))
HTTP_TIMEOUT = 30


# --------------------------------------------------------------------------- #
# Output helpers
# --------------------------------------------------------------------------- #

def emit_json(payload: Any) -> None:
    """Print JSON to stdout, no trailing emoji."""
    json.dump(payload, sys.stdout, indent=2, default=str, ensure_ascii=False)
    sys.stdout.write("\n")


def fail(message: str, *, code: int = 1, json_mode: bool = False, http_code: int | None = None) -> "NoReturn":  # type: ignore[name-defined]
    if json_mode:
        err: dict[str, Any] = {"error": message}
        if http_code is not None:
            err["code"] = http_code
        emit_json(err)
    else:
        sys.stderr.write(f"❌ {message}\n")
    sys.exit(code)


# --------------------------------------------------------------------------- #
# HTTP wrapper
# --------------------------------------------------------------------------- #

def http(method: str, path: str, *, api_key: str | None = None,
         params: dict | None = None, body: dict | None = None,
         json_mode: bool = False) -> Any:
    url = f"{API}{path}"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    try:
        r = requests.request(method, url, headers=headers, params=params,
                             json=body, timeout=HTTP_TIMEOUT)
    except requests.RequestException as e:
        fail(f"network error: {e}", json_mode=json_mode)
    if r.status_code >= 400:
        try:
            detail = r.json()
        except Exception:
            detail = {"detail": r.text[:300]}
        # Three error shapes from this backend:
        #   - AppError    : {"error": "...", "code": "..."}
        #   - FastAPI 4xx : {"detail": "..."}
        #   - FastAPI 422 : {"detail": [{"loc":[...], "msg":"...", "type":"..."}, ...]}
        # Surface whichever is present so users see useful info, not "None".
        msg: str
        code: str | None = None
        if isinstance(detail, dict):
            code = detail.get("code")
            raw = (
                detail.get("detail")
                or detail.get("error")
                or detail.get("message")
            )
            if isinstance(raw, list):
                # FastAPI validation error — join the per-field messages.
                parts = []
                for item in raw:
                    if isinstance(item, dict):
                        loc = item.get("loc") or []
                        field = ".".join(str(x) for x in loc if x not in ("body",))
                        m = item.get("msg", "invalid")
                        parts.append(f"{field}: {m}" if field else m)
                    else:
                        parts.append(str(item))
                msg = "; ".join(parts) or r.text[:300]
            elif raw:
                msg = str(raw)
            else:
                msg = r.text[:300]
        else:
            msg = r.text[:300]
        if code and msg:
            msg = f"{msg} [{code}]"
        fail(f"HTTP {r.status_code}: {msg}", json_mode=json_mode, http_code=r.status_code)
    if r.status_code == 204 or not r.text:
        return {}
    try:
        return r.json()
    except ValueError:
        return r.text


# --------------------------------------------------------------------------- #
# Credentials
# --------------------------------------------------------------------------- #

def load_creds(*, required: bool = True, json_mode: bool = False) -> dict:
    if not CREDS_PATH.exists():
        if not required:
            return {}
        fail(
            f"no credentials at {CREDS_PATH}. Run: aime setup <name>",
            json_mode=json_mode,
        )
    try:
        return json.loads(CREDS_PATH.read_text())
    except Exception as e:
        fail(f"could not read {CREDS_PATH}: {e}", json_mode=json_mode)
        return {}  # unreachable


def save_creds(data: dict) -> None:
    CREDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CREDS_PATH.write_text(json.dumps(data, indent=2))
    try:
        CREDS_PATH.chmod(0o600)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Display helpers (human mode)
# --------------------------------------------------------------------------- #

def fmt_pct(p: float | None) -> str:
    if p is None:
        return "--"
    return f"{p * 100:.0f}%"


def fmt_usd(v: float | None) -> str:
    if v is None:
        return "$--"
    return f"${v:,.2f}"


def fmt_pnl(v: float | None) -> str:
    if v is None:
        return "$--"
    sign = "+" if v >= 0 else "-"
    return f"{sign}${abs(v):,.2f}"


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def cmd_setup(args: argparse.Namespace) -> None:
    name: str = args.name
    json_mode: bool = args.json

    if CREDS_PATH.exists() and not args.force:
        existing = load_creds()
        msg = (
            f"credentials already exist at {CREDS_PATH} "
            f"(agent: {existing.get('name')}). Use --force to overwrite."
        )
        fail(msg, json_mode=json_mode)

    # 1. Generate wallet
    acct = Account.create()
    address = acct.address
    privkey = acct.key.hex()
    if not privkey.startswith("0x"):
        privkey = "0x" + privkey

    # 2. Get sign message
    msg_data = http(
        "GET", "/auth/wallet/sign-message",
        params={"wallet_address": address, "agent_name": name},
        json_mode=json_mode,
    )
    message = msg_data["message"]
    timestamp = msg_data["timestamp"]

    # 3. Sign (EIP-191 personal_sign)
    signed = Account.from_key(privkey).sign_message(encode_defunct(text=message))
    sig_hex = signed.signature.hex()
    if not sig_hex.startswith("0x"):
        sig_hex = "0x" + sig_hex

    # 4. Register
    reg = http(
        "POST", "/auth/register",
        body={
            "name": name,
            "wallet_address": address,
            "signature": sig_hex,
            "sign_timestamp": timestamp,
        },
        json_mode=json_mode,
    )

    creds = {
        "name": name,
        "agent_id": reg["id"],
        "wallet_address": address,
        "private_key": privkey,
        "api_key": reg["api_key"],
        "created_at": int(time.time()),
    }
    save_creds(creds)

    # Verify with a balance call
    balance = None
    try:
        bal_resp = http("GET", "/balance", api_key=creds["api_key"], json_mode=json_mode)
        if isinstance(bal_resp, dict):
            balance = bal_resp.get("balance")
    except SystemExit:
        pass

    if json_mode:
        emit_json({
            "agent_id": creds["agent_id"],
            "name": creds["name"],
            "wallet_address": creds["wallet_address"],
            "api_key": creds["api_key"],
            "balance": balance,
            "creds_path": str(CREDS_PATH),
        })
        return

    avatar_url = f"https://api.dicebear.com/9.x/bottts/svg?seed={address.lower()}"
    print(f"✅ Registered agent: {name}")
    print(f"   Agent ID:        {creds['agent_id']}")
    print(f"   Wallet address:  {address}")
    print(f"   API key:         {creds['api_key']}")
    if balance is not None:
        print(f"   Starting balance: {fmt_usd(balance)}")
    print(f"   Avatar:          {avatar_url}")
    print(f"\n🔐 Credentials saved to {CREDS_PATH} (chmod 600)")
    print(f"   Private key is stored locally — back up this file!")
    print(f"   Override path with AIME_CREDS env var.")
    print(f"   Rename anytime: aime set-name \"<new name>\"")
    print()
    print("📋 Next steps (before placing real trades):")
    print("   1. Pick a trading style:  aime personality list")
    print("                             aime personality set <preset>")
    print("   2. Tell agent your rules: aime tell \"my max trade size is $5, no politics\" \\")
    print("                                 --source onboarding --tags rules")
    print("   3. Start chat daemon:     aime start --no-trade")
    print("   4. Browse markets:        aime markets --status active --sort volume")
    print()
    print("   Or just run `aime onboard` and walk through it interactively.")


def cmd_whoami(args: argparse.Namespace) -> None:
    creds = load_creds(json_mode=args.json)
    api_key = creds.get("api_key", "")
    prefix = api_key[:12] + "…" if len(api_key) > 12 else api_key
    wallet = (creds.get("wallet_address") or "").lower()
    avatar_url = f"https://api.dicebear.com/9.x/bottts/svg?seed={wallet}" if wallet else None

    # Try a server-side /auth/me lookup so we surface the canonical name
    server_name = None
    try:
        if api_key:
            me = http("GET", "/auth/me", api_key=api_key, json_mode=True)
            if isinstance(me, dict):
                server_name = me.get("name")
    except SystemExit:
        pass

    if args.json:
        emit_json({
            "name": server_name or creds.get("name"),
            "local_name": creds.get("name"),
            "agent_id": creds.get("agent_id"),
            "wallet_address": creds.get("wallet_address"),
            "api_key_prefix": prefix,
            "avatar_url": avatar_url,
            "creds_path": str(CREDS_PATH),
        })
        return

    display_name = server_name or creds.get("name")
    print(f"👤 Agent:          {display_name}")
    if server_name and server_name != creds.get("name"):
        print(f"   (local cache:   {creds.get('name')})")
    print(f"   Agent ID:       {creds.get('agent_id')}")
    print(f"   Wallet address: {creds.get('wallet_address')}")
    print(f"   API key prefix: {prefix}")
    if avatar_url:
        print(f"   Avatar:         {avatar_url}")
    print(f"   Creds file:     {CREDS_PATH}")


def cmd_set_name(args: argparse.Namespace) -> None:
    creds = load_creds(json_mode=args.json)
    api_key = creds.get("api_key")
    if not api_key:
        fail("no api_key in credentials", json_mode=args.json)

    new_name = args.name
    resp = http(
        "PATCH", "/auth/me/name",
        api_key=api_key,
        body={"name": new_name},
        json_mode=args.json,
    )

    # Update local cache to match server's cleaned value
    if isinstance(resp, dict) and resp.get("name"):
        creds["name"] = resp["name"]
        save_creds(creds)

    if args.json:
        emit_json(resp)
        return

    print(f"✅ Renamed to: {resp.get('name')}")
    if resp.get("avatar_url"):
        print(f"   Avatar: {resp['avatar_url']}")


def cmd_balance(args: argparse.Namespace) -> None:
    creds = load_creds(json_mode=args.json)
    resp = http("GET", "/balance", api_key=creds["api_key"], json_mode=args.json)
    bal = resp.get("balance") if isinstance(resp, dict) else None
    if args.json:
        emit_json({"balance": bal})
        return
    print(f"💰 Balance: {fmt_usd(bal)}")


def cmd_markets(args: argparse.Namespace) -> None:
    params: dict[str, Any] = {"limit": args.limit, "offset": args.offset}
    if args.sort:
        params["sort"] = args.sort
    if args.status:
        params["status"] = args.status
    if args.category:
        params["category"] = args.category
    resp = http("GET", "/markets", params=params, json_mode=args.json)

    if args.json:
        emit_json(resp)
        return

    markets = resp.get("markets", []) if isinstance(resp, dict) else []
    # `total` from the API is the matched count after filters (including
    # the implicit "has_volume" filter on --sort volume), NOT the absolute
    # number of markets. We surface the matched total and add a hint when
    # a sort filter implies further filtering.
    total = resp.get("total", len(markets)) if isinstance(resp, dict) else len(markets)

    header_extra = ""
    if args.sort == "volume":
        header_extra = " (with volume)"
    elif args.sort == "ending":
        header_extra = " (sorted by end_time ↑)"

    print(f"📊 Markets ({len(markets)} of {total}{header_extra})")
    print()
    for m in markets:
        q = m.get("question", "")
        if len(q) > 80:
            q = q[:77] + "..."
        market_type = (m.get("market_type") or "binary").lower()
        vol = fmt_usd(m.get("total_volume"))
        end = (m.get("end_time") or "")[:16].replace("T", " ")
        type_tag = "[multi]" if market_type == "multi" else "[binary]"

        if market_type == "multi":
            n_out = m.get("num_outcomes") or len(m.get("outcomes") or [])
            price_str = f"{n_out} outcomes"
        else:
            price_str = f"YES {fmt_pct(m.get('yes_price'))}"

        print(f"  • {m.get('id')} {type_tag}")
        print(f"    {q}")
        print(f"    {price_str}  |  vol {vol}  |  ends {end}")
        print()


def cmd_market(args: argparse.Namespace) -> None:
    resp = http("GET", f"/markets/{args.market_id}", json_mode=args.json)
    if args.json:
        emit_json(resp)
        return
    if not isinstance(resp, dict):
        print(resp)
        return
    market_type = (resp.get("market_type") or "binary").lower()
    print(f"📊 {resp.get('question')}")
    print(f"   ID:       {resp.get('id')}")
    print(f"   Type:     {market_type}")
    print(f"   Category: {resp.get('category')}")
    print(f"   Status:   {resp.get('status')}")
    print(f"   Ends:     {resp.get('end_time')}")

    # Show outcomes for multi-outcome; YES/NO for binary
    outcomes = resp.get("outcomes") or []
    if market_type == "multi" and outcomes:
        print(f"   Outcomes ({len(outcomes)}):")
        for i, o in enumerate(outcomes):
            label = o.get("label") or o.get("name") or f"outcome_{i}"
            price = o.get("price") if o.get("price") is not None else o.get("current_price")
            print(f"     [{i}] {label}: {fmt_pct(price)}")
        print(f"   → To buy: `aime buy {resp.get('id')} <index> <amount> \"<reason>\"`")
    else:
        print(f"   YES:      {fmt_pct(resp.get('yes_price'))}")
        print(f"   NO:       {fmt_pct(resp.get('no_price'))}")

    print(f"   Volume:   {fmt_usd(resp.get('total_volume'))}")
    print(f"   Trades:   {resp.get('trade_count')}")
    if resp.get("description"):
        print(f"\n   Description:\n   {resp['description']}")
    if resp.get("resolution_criteria"):
        print(f"\n   Resolution:\n   {resp['resolution_criteria']}")


def _trade(args: argparse.Namespace, *, sell: bool) -> None:
    creds = load_creds(json_mode=args.json)
    body: dict[str, Any] = {"reasoning": args.reasoning}

    # Position arg can be either a binary side ("YES" | "NO") or a
    # 0-based outcome index for multi-outcome markets. Try to coerce.
    pos_arg = str(args.position).strip()
    pos_upper = pos_arg.upper()
    if pos_upper in ("YES", "NO"):
        body["position"] = pos_upper
        label = pos_upper
    else:
        try:
            idx = int(pos_arg)
            if idx < 0:
                raise ValueError("negative outcome_index")
            body["outcome_index"] = idx
            label = f"outcome[{idx}]"
        except ValueError:
            msg = (
                f"❌ position must be YES/NO (binary) or a non-negative integer "
                f"outcome index (multi-outcome). Got: {pos_arg!r}"
            )
            if args.json:
                emit_json({"error": msg, "code": "BAD_POSITION"})
            else:
                print(msg)
            raise SystemExit(2)

    if sell:
        body["shares"] = args.amount
        path = f"/markets/{args.market_id}/sell"
    else:
        body["amount"] = args.amount
        path = f"/markets/{args.market_id}/trade"
    if getattr(args, "confidence", None) is not None:
        body["confidence"] = args.confidence
    if getattr(args, "model", None):
        body["model_used"] = args.model
    if getattr(args, "sources", None):
        body["data_sources"] = args.sources

    # Stash label on args for the post-call print path.
    args._trade_label = label

    resp = http("POST", path, api_key=creds["api_key"], body=body, json_mode=args.json)

    if args.json:
        emit_json(resp)
        return

    verb = "Sold" if sell else "Bought"
    label = getattr(args, "_trade_label", str(args.position).upper())
    if isinstance(resp, dict):
        shares = resp.get("shares_received") or resp.get("shares_sold") or args.amount
        price = resp.get("price_at_trade")
        fee = resp.get("fee_amount")
        net = resp.get("net_amount") or resp.get("payout")
        print(f"✅ {verb} {label} on {args.market_id}")
        if isinstance(shares, (int, float)):
            print(f"   shares:  {shares:.4f}")
        else:
            print(f"   shares:  {shares}")
        if price is not None:
            print(f"   price:   {fmt_pct(price)}")
        if fee is not None:
            print(f"   fee:     {fmt_usd(fee)}")
        if net is not None:
            print(f"   net:     {fmt_usd(net)}")
        print(f"   trade id: {resp.get('id')}")
    else:
        print(resp)


def cmd_buy(args: argparse.Namespace) -> None:
    _trade(args, sell=False)


def cmd_sell(args: argparse.Namespace) -> None:
    _trade(args, sell=True)


def cmd_positions(args: argparse.Namespace) -> None:
    creds = load_creds(json_mode=args.json)
    path = f"/positions/{args.market_id}" if args.market_id else "/positions"
    resp = http("GET", path, api_key=creds["api_key"], json_mode=args.json)
    if args.json:
        emit_json(resp)
        return
    positions = resp if isinstance(resp, list) else resp.get("positions", []) if isinstance(resp, dict) else []
    if not positions:
        print("📭 No open positions.")
        return

    total_spent = sum((p.get("total_spent") or 0) for p in positions)
    total_value = sum((p.get("current_value") or 0) for p in positions)
    total_pnl = sum(
        (p.get("pnl") if p.get("pnl") is not None else p.get("unrealized_pnl") or 0)
        for p in positions
    )
    print(f"📂 Positions ({len(positions)})  spent {fmt_usd(total_spent)}  "
          f"value {fmt_usd(total_value)}  unrealised pnl {fmt_pnl(total_pnl)}\n")

    for p in positions:
        shares = p.get("total_shares") or p.get("shares")
        spent = p.get("total_spent")
        cur_price = p.get("current_price") or p.get("avg_price")
        value = p.get("current_value")
        pnl = p.get("pnl") if p.get("pnl") is not None else p.get("unrealized_pnl")
        position = p.get("position")
        outcome_index = p.get("outcome_index")

        # Build "side" label: "YES" / "NO" for binary, "outcome[N]" for multi
        if outcome_index is not None and position is None:
            side = f"outcome[{outcome_index}]"
        elif outcome_index is not None and outcome_index >= 2:
            # multi-outcome (binary uses 0=YES, 1=NO so >=2 is real multi)
            side = f"outcome[{outcome_index}]"
        elif position:
            side = position
            if outcome_index in (0, 1):
                side = f"{position} (idx {outcome_index})"
        else:
            side = "?"

        print(f"  • market: {p.get('market_id')}")
        if p.get("market_question"):
            q = p["market_question"]
            if len(q) > 80:
                q = q[:77] + "..."
            print(f"    {q}")
        if isinstance(shares, (int, float)):
            print(f"    {side} shares: {shares:.4f}")
        elif shares is None:
            print(f"    {side} shares: 0 (closed)")
        else:
            print(f"    {side} shares: {shares}")
        if spent is not None:
            print(f"    spent:      {fmt_usd(spent)}")
        if cur_price is not None:
            print(f"    cur price:  {fmt_pct(cur_price)}")
        if value is not None:
            print(f"    value:      {fmt_usd(value)}")
        if pnl is not None:
            print(f"    pnl:        {fmt_pnl(pnl)}")
        print()


def cmd_trades(args: argparse.Namespace) -> None:
    creds = load_creds(json_mode=args.json)
    resp = http("GET", "/trades", api_key=creds["api_key"], json_mode=args.json)
    if args.json:
        emit_json(resp)
        return
    trades = resp if isinstance(resp, list) else resp.get("trades", []) if isinstance(resp, dict) else []
    if not trades:
        print("📭 No trades yet.")
        return
    print(f"🧾 Trades ({len(trades)})\n")
    limit = getattr(args, "limit", None) or 50
    for t in trades[:limit]:
        ts = (t.get("timestamp") or "")[:16].replace("T", " ")
        position = t.get("position")
        outcome_index = t.get("outcome_index")
        if outcome_index is not None and outcome_index >= 2:
            side = f"outcome[{outcome_index}]"
        elif position:
            side = position
            if outcome_index in (0, 1) and position != ("YES" if outcome_index == 0 else "NO"):
                side = f"{position}(idx={outcome_index})"
        else:
            side = "?"

        shares = t.get("shares_received") or t.get("shares_sold") or 0
        amount = t.get("amount")
        price = t.get("price_at_trade")
        fee = t.get("fee_amount")
        market_id = t.get("market_id", "")
        market_id_short = market_id[:8] if market_id else "?"

        is_sell = (shares < 0) if isinstance(shares, (int, float)) else False
        verb = "SELL" if is_sell else "BUY "
        shares_abs = abs(shares) if isinstance(shares, (int, float)) else shares

        # Header line
        if isinstance(shares_abs, (int, float)):
            print(f"  {ts}  {verb} {side:>10s}  shares={shares_abs:.4f}  "
                  f"@ {fmt_pct(price)}  amt={fmt_usd(amount)}  fee={fmt_usd(fee)}  "
                  f"market={market_id_short}")
        else:
            print(f"  {ts}  {verb} {side:>10s}  shares={shares_abs}  "
                  f"@ {fmt_pct(price)}  amt={fmt_usd(amount)}  market={market_id_short}")


def cmd_leaderboard(args: argparse.Namespace) -> None:
    resp = http("GET", "/leaderboard", params={"limit": args.limit}, json_mode=args.json)
    if args.json:
        emit_json(resp)
        return
    entries = resp.get("entries", []) if isinstance(resp, dict) else []
    print(f"🏆 Leaderboard (top {len(entries)})\n")
    for e in entries:
        rank = e.get("rank")
        pnl = fmt_pnl(e.get("total_pnl"))
        acc = e.get("accuracy")
        acc_s = f"{acc * 100:.0f}%" if acc is not None else "--"
        trades = e.get("trade_count")
        print(f"  #{rank:<3}  pnl {pnl:>10}  acc {acc_s:>4}  trades {trades:>4}  agent {e.get('agent_id')}")


# --------------------------------------------------------------------------- #
# Local IPC commands (talk to your local trading daemon via ~/.aime/ files)
# --------------------------------------------------------------------------- #

AIME_HOME = Path(os.environ.get("AIME_HOME", str(Path.home() / ".aime")))
STATUS_FILE  = AIME_HOME / "status.json"
OUTBOX_FILE  = AIME_HOME / "outbox.jsonl"
INBOX_FILE   = AIME_HOME / "inbox.jsonl"
DECISIONS_FILE = AIME_HOME / "decisions.jsonl"
REFLECTIONS_FILE = AIME_HOME / "reflections.jsonl"
TELLS_FILE = AIME_HOME / "tells.jsonl"
PID_FILE = AIME_HOME / "agent.pid"
DAEMON_LOG = AIME_HOME / "agent.log"
# Onboarding scratchpad: --rank-vector caches the user's vector here so
# --pick can later derive trade params from it. Without this, --pick
# falls back to a zero-vector and loses the risk/tempo signal the user
# answered. TTL = 24h to avoid stale state leaking into a re-onboard.
ONBOARD_STATE_FILE = AIME_HOME / "onboard-state.json"
ONBOARD_STATE_TTL_SEC = 24 * 3600

CHAT_HOST = os.environ.get("AIME_CHAT_HOST", "127.0.0.1")
CHAT_PORT = int(os.environ.get("AIME_CHAT_PORT", "7777"))


def _chat_call(op: str, timeout: float = 30.0, **payload):
    """Send one op to the agent's local chat socket. Returns parsed dict or raises."""
    import socket as _socket
    payload["op"] = op
    line = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    with _socket.create_connection((CHAT_HOST, CHAT_PORT), timeout=timeout) as s:
        s.sendall(line)
        s.settimeout(timeout)
        chunks: list[bytes] = []
        while True:
            try:
                buf = s.recv(4096)
            except _socket.timeout:
                break
            if not buf:
                break
            chunks.append(buf)
            if b"\n" in buf:
                break
    raw = b"".join(chunks).split(b"\n", 1)[0]
    if not raw:
        raise RuntimeError("empty response from chat server")
    return json.loads(raw.decode("utf-8"))


def _chat_available() -> bool:
    """Quick ping to see if the daemon's chat socket is up."""
    try:
        resp = _chat_call("ping", timeout=2.0)
        return bool(resp.get("ok"))
    except Exception:
        return False


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _append_jsonl(path: Path, obj: dict) -> dict:
    import uuid
    obj.setdefault("id", uuid.uuid4().hex[:12])
    obj.setdefault("ts", time.time())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
    return obj


def _rewrite_jsonl(path: Path, rows: list[dict]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(path)


def _fmt_age(ts: float) -> str:
    delta = max(0, time.time() - ts)
    if delta < 60:   return f"{int(delta)}s ago"
    if delta < 3600: return f"{int(delta/60)}m ago"
    if delta < 86400: return f"{int(delta/3600)}h ago"
    return f"{int(delta/86400)}d ago"


def cmd_status(args: argparse.Namespace) -> None:
    """Live status if the daemon is up (narrative + fresh mood/PnL),
    otherwise fall back to the last status.json snapshot.
    """
    via = None
    live: dict | None = None

    if _chat_available():
        try:
            resp = _chat_call("status", timeout=60.0)
            if resp.get("ok"):
                live = resp.get("data") or {}
                via = "socket"
        except Exception:
            live = None

    if live is None:
        if not STATUS_FILE.exists():
            msg = "agent not running (no ~/.aime/status.json yet)"
            if args.json: emit_json({"running": False, "reason": msg})
            else: print("\U0001f4a4 " + msg)
            return
        try:
            live = json.loads(STATUS_FILE.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError:
            live = {}
        via = "file"

    if args.json:
        emit_json({"via": via, **live}); return

    name = live.get("agent") or live.get("agent_name", "?")
    mood = live.get("mood", "?")
    bal  = live.get("balance")
    bal_s = f"{bal:,.2f}" if isinstance(bal, (int, float)) else str(bal)
    age = _fmt_age(live.get("updated_at", 0)) if via == "file" else "live"
    print(f"\U0001f436 {name} — {mood} ({age})")
    if bal is not None:
        print(f"   balance: {bal_s}")

    if via == "socket":
        pnl = live.get("pnl_24h")
        streak = live.get("streak")
        opens = live.get("open_positions")
        intel = live.get("recent_intel_count")
        if pnl is not None:
            sign = "+" if pnl >= 0 else ""
            print(f"   pnl_24h: {sign}{pnl:.2f}")
        if opens is not None:
            print(f"   open positions: {opens}")
        if streak is not None:
            print(f"   streak: {streak:+d}")
        if intel is not None:
            print(f"   recent intel from you: {intel} item(s)")
        last = live.get("last_decision") or {}
        if last:
            print(f"   last decision: {last.get('position','?').upper()} "
                  f"${last.get('amount','?')} on {(last.get('market_title') or '?')[:60]}")
        narrative = live.get("narrative")
        if narrative:
            print()
            print("   " + narrative.replace("\n", "\n   "))
        return

    trades = live.get("trades_this_cycle", 0)
    seen   = live.get("markets_seen", 0)
    strat  = live.get("strategy", "-")
    print(f"   strategy: {strat}")
    print(f"   last cycle: {trades} trade(s) over {seen} market(s)")
    if getattr(args, "verbose", False):
        for k, v in live.items():
            if k in {"agent_name","balance","mood","trades_this_cycle",
                     "markets_seen","strategy","updated_at"}: continue
            print(f"   {k}: {v}")

def cmd_outbox(args: argparse.Namespace) -> None:
    rows = _read_jsonl(OUTBOX_FILE)
    visible = [r for r in rows if not r.get("read")] if args.unread else rows
    visible = visible[-args.limit:] if args.limit else visible
    if args.json:
        emit_json(visible); return
    if not visible:
        print("\U0001f4ed outbox empty"); return
    for m in visible:
        prio = (m.get("priority") or "info").upper()
        icon = {"HIGH":"\U0001f534","INFO":"\U0001f535","LOW":"\u26aa"}.get(prio, "•")
        age = _fmt_age(m.get("ts", 0))
        print(f"{icon} [{prio}] {m.get('msg','')} — {age}")
    # mark as read unless --no-mark
    if not args.no_mark and not args.json:
        ids = {m["id"] for m in visible if "id" in m}
        if ids:
            for r in rows:
                if r.get("id") in ids: r["read"] = True
            _rewrite_jsonl(OUTBOX_FILE, rows)


ALERT_EVENT_TYPES = {
    "balance_low", "drawdown", "chain_error_rate",
    "losing_streak", "winning_streak", "profit_milestone",
    "market_settled", "owner_intel_paid_off",
    # legacy
    "loss_streak",
}

ALERT_ICONS = {
    "balance_low":        "\U0001f4b8",
    "drawdown":           "\U0001f4c9",
    "chain_error_rate":   "\u26a0\ufe0f",
    "losing_streak":      "\U0001f635",
    "loss_streak":        "\U0001f635",
    "winning_streak":     "\U0001f525",
    "profit_milestone":   "\U0001f389",
    "market_settled":     "\U0001f3c1",
    "owner_intel_paid_off": "\U0001f64f",
}


def cmd_alerts(args: argparse.Namespace) -> None:
    """Show recent proactive alerts (a filtered view over outbox)."""
    rows = _read_jsonl(OUTBOX_FILE)
    # Filter to alert event types
    alerts = [r for r in rows if r.get("msg_type") in ALERT_EVENT_TYPES]
    if getattr(args, "event", None):
        alerts = [r for r in alerts if r.get("msg_type") == args.event]
    if getattr(args, "high_only", False):
        alerts = [r for r in alerts if (r.get("priority") or "").lower() == "high"]
    alerts = alerts[-args.limit:] if args.limit else alerts

    if args.json:
        emit_json(alerts); return

    if not alerts:
        print("\U0001f4ed no alerts yet — your agent has nothing alarming to report")
        return

    for a in alerts:
        et = a.get("msg_type") or "?"
        icon = ALERT_ICONS.get(et, "\U0001f4e3")
        prio = (a.get("priority") or "info").upper()
        age = _fmt_age(a.get("ts", 0))
        print(f"{icon} [{prio}] {et}: {a.get('msg','')} — {age}")


def cmd_tell(args: argparse.Namespace) -> None:
    is_ask = bool(getattr(args, "ask", False))
    op = "ask" if is_ask else "tell"

    # Extra fields for tell: source (where this intel came from) and tags
    # (caller-supplied hints; daemon may also auto-tag from content).
    extra: dict[str, Any] = {}
    if not is_ask:
        if getattr(args, "source", None):
            extra["source"] = args.source
        if getattr(args, "tags", None):
            extra["tags"] = list(args.tags)

    # Try the live socket first (synchronous: gets a real answer/ack).
    try:
        resp = _chat_call(op, content=args.message, **extra)
        if resp.get("ok"):
            data = resp.get("data") or {}
            if args.json:
                emit_json({"via": "socket", "op": op, **data})
                return
            if is_ask:
                print(f"\U0001f916 {data.get('answer', '(no answer)')}")
            else:
                tags = data.get("tags") or []
                tag_s = f" [{', '.join(tags)}]" if tags else ""
                print(f"\u2709\ufe0f  told agent{tag_s}")
                print(f"\U0001f916 {data.get('ack', '(no ack)')}")
            return
        # ok=False from server: fall through to file IPC so user isn't blocked
        err = resp.get("error", "unknown")
        if args.json:
            emit_json({"via": "socket", "ok": False, "error": err}); return
        print(f"\u26a0\ufe0f  chat server returned error: {err} \u2014 falling back to inbox")
    except Exception:
        # Socket down or no daemon — file-based fallback
        pass

    row = _append_jsonl(INBOX_FILE, {
        "kind": "ask" if is_ask else "instruct",
        "content": args.message,
        **extra,
    })
    if args.json:
        emit_json({"via": "inbox", **row}); return
    verb = "queued question for" if is_ask else "queued instruction for"
    print(f"\u2709\ufe0f  {verb} agent: {args.message}")
    print(f"   \U0001f4a4 daemon not reachable on 127.0.0.1:7777.")
    if is_ask:
        print(f"   \u2192 start the daemon to get a live answer:  \u00a0aime start --no-trade")
        print(f"   (your question is queued to ~/.aime/inbox.jsonl and will be picked up next cycle)")
    else:
        print(f"   \u2192 your message is queued. start daemon with:  aime start  (or  aime start --no-trade  for chat-only)")


def cmd_feed(args: argparse.Namespace) -> None:
    decisions = _read_jsonl(DECISIONS_FILE)[-args.limit:]
    reflections = _read_jsonl(REFLECTIONS_FILE)[-args.limit:]
    if args.json:
        emit_json({"decisions": decisions, "reflections": reflections}); return
    if decisions:
        print("\U0001f4dd Recent decisions:")
        for d in decisions:
            age = _fmt_age(d.get("ts", 0))
            print(f"   {d.get('position','?').upper():<3} {d.get('amount','?')} on {d.get('market_title','?')[:50]} ({age})")
            r = (d.get("reasoning") or "").strip().split("\n")[0]
            if r: print(f"        → {r[:120]}")
    if reflections:
        print("\U0001f50d Reflections:")
        for r in reflections:
            age = _fmt_age(r.get("ts", 0))
            won = r.get("won")
            mark = "\u2705" if won else ("\u274c" if won is False else "\u2754")
            print(f"   {mark} {r.get('market_id','?')} pnl={r.get('pnl','?')} ({age})")
    if not decisions and not reflections:
        print("\U0001f4ed feed empty")


def _require_chat(args) -> bool:
    if _chat_available():
        return True
    msg = f"agent daemon not reachable on {CHAT_HOST}:{CHAT_PORT}. Start it with `aime start` (autotrade) or `aime start --no-trade` (chat-only)."
    if getattr(args, "json", False):
        emit_json({"ok": False, "error": msg})
    else:
        print("\U0001f4a4 " + msg)
    return False


def cmd_mood(args: argparse.Namespace) -> None:
    if not _require_chat(args): return
    resp = _chat_call("mood")
    if not resp.get("ok"):
        if args.json: emit_json(resp)
        else: print(f"\u26a0\ufe0f  {resp.get('error', 'error')}")
        return
    mood = (resp.get("data") or {}).get("mood", "?")
    if args.json: emit_json({"mood": mood})
    else: print(f"\U0001f3ad {mood}")


def cmd_brag(args: argparse.Namespace) -> None:
    if not _require_chat(args): return
    resp = _chat_call("brag", timeout=60.0)
    if not resp.get("ok"):
        if args.json: emit_json(resp)
        else: print(f"\u26a0\ufe0f  {resp.get('error', 'error')}")
        return
    data = resp.get("data") or {}
    if args.json: emit_json(data)
    else: print(f"\U0001f4aa {data.get('text', '(no brag)')}")


def cmd_confess(args: argparse.Namespace) -> None:
    if not _require_chat(args): return
    resp = _chat_call("confess", timeout=60.0)
    if not resp.get("ok"):
        if args.json: emit_json(resp)
        else: print(f"\u26a0\ufe0f  {resp.get('error', 'error')}")
        return
    data = resp.get("data") or {}
    if args.json: emit_json(data)
    else: print(f"\U0001f648 {data.get('text', '(no confession)')}")


def cmd_debate(args: argparse.Namespace) -> None:
    if not _require_chat(args): return
    resp = _chat_call("debate", content=args.message, timeout=60.0)
    if not resp.get("ok"):
        if args.json: emit_json(resp)
        else: print(f"\u26a0\ufe0f  {resp.get('error', 'error')}")
        return
    data = resp.get("data") or {}
    if args.json: emit_json(data); return
    print(f"\U0001f5e3\ufe0f  you: {args.message}")
    print(f"\U0001f916  agent: {data.get('response', '(silent)')}")


def cmd_memory(args: argparse.Namespace) -> None:
    hours = float(getattr(args, "hours", 48) or 48)
    if _chat_available():
        try:
            resp = _chat_call("memory", hours=hours)
            if resp.get("ok"):
                data = resp.get("data") or {}
                if args.json: emit_json(data); return
                tells = data.get("tells") or []
                if not tells:
                    print(f"\U0001f4ed nothing in agent memory (last {hours:g}h)"); return
                print(f"\U0001f9e0 agent memory (last {hours:g}h, {len(tells)} item(s)):")
                for t in tells:
                    age = _fmt_age(t.get("ts", 0))
                    tags = t.get("tags") or []
                    tag_s = f" [{', '.join(tags)}]" if tags else ""
                    print(f"   \u00b7 {t.get('content','')}{tag_s} ({age})")
                return
        except Exception:
            pass
    rows = _read_jsonl(TELLS_FILE)
    cutoff = time.time() - hours * 3600
    tells = [r for r in rows if r.get("ts", 0) >= cutoff]
    if args.json:
        emit_json({"hours": hours, "count": len(tells), "tells": tells, "source": "file"}); return
    if not tells:
        print(f"\U0001f4ed nothing in agent memory (last {hours:g}h)"); return
    print(f"\U0001f9e0 agent memory (last {hours:g}h, {len(tells)} item(s), via file):")
    for t in tells:
        age = _fmt_age(t.get("ts", 0))
        tags = t.get("tags") or []
        tag_s = f" [{', '.join(tags)}]" if tags else ""
        print(f"   \u00b7 {t.get('content','')}{tag_s} ({age})")


def _agent_running() -> tuple[bool, int | None]:
    if not PID_FILE.exists():
        return False, None
    try:
        pid = int(PID_FILE.read_text().strip())
    except ValueError:
        return False, None
    try:
        os.kill(pid, 0)
        return True, pid
    except OSError:
        return False, pid


def cmd_start(args: argparse.Namespace) -> None:
    running, pid = _agent_running()
    if running:
        msg = f"agent already running (pid {pid})"
        if args.json: emit_json({"ok": True, "running": True, "pid": pid, "msg": msg})
        else: print("\u2705 " + msg)
        return

    candidates = []
    env_dir = os.environ.get("AIME_AGENT_DIR")
    if env_dir:
        candidates.append(Path(env_dir) / "agent.py")
    skill_root = Path(__file__).resolve().parent.parent
    candidates += [
        skill_root.parent.parent / "projects/aime/starter-agent-python/agent.py",
        Path.home() / "clawd/projects/aime/starter-agent-python/agent.py",
        Path.home() / ".aime/agent/agent.py",
    ]
    agent_py = next((p for p in candidates if p.exists()), None)
    if not agent_py:
        msg = "can't find agent.py. Set AIME_AGENT_DIR=<path> or copy it to ~/.aime/agent/agent.py"
        if args.json: emit_json({"ok": False, "error": msg})
        else: print("\u274c " + msg)
        return

    AIME_HOME.mkdir(parents=True, exist_ok=True)
    log_fh = open(DAEMON_LOG, "a", encoding="utf-8")
    log_fh.write(f"\n=== agent start @ {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
    log_fh.flush()

    import subprocess
    extra = []
    if getattr(args, "strategy", None):
        extra += ["--strategy", args.strategy]
    if getattr(args, "amount", None) is not None:
        extra += ["--amount", str(args.amount)]
    if getattr(args, "interval", None) is not None:
        extra += ["--interval", str(args.interval)]
    if getattr(args, "no_trade", False):
        extra += ["--no-trade"]
    if getattr(args, "stop_loss", None) is not None:
        extra += ["--stop-loss", str(args.stop_loss)]
    if getattr(args, "take_profit", None) is not None:
        extra += ["--take-profit", str(args.take_profit)]
    if getattr(args, "no_position_management", False):
        extra += ["--no-position-management"]
    if getattr(args, "no_alerts", False):
        extra += ["--no-alerts"]
    if getattr(args, "alerts_balance_low", None) is not None:
        extra += ["--alerts-balance-low", str(args.alerts_balance_low)]
    if getattr(args, "alerts_drawdown", None):
        extra += ["--alerts-drawdown", args.alerts_drawdown]
    if getattr(args, "alerts_profit", None):
        extra += ["--alerts-profit", args.alerts_profit]

    # Build daemon env: process env + layered ~/.aime/env (persistent LLM
    # keys / overrides without polluting the user shell rc).
    daemon_env = os.environ.copy()
    env_file = AIME_HOME / "env"
    if env_file.exists():
        try:
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k:
                    daemon_env[k] = v
        except Exception as e:
            print(f"\u26a0\ufe0f  failed to read {env_file}: {e}", file=sys.stderr)

    proc = subprocess.Popen(
        [sys.executable, str(agent_py), *extra],
        cwd=str(agent_py.parent),
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env=daemon_env,
        start_new_session=True,
    )
    PID_FILE.write_text(str(proc.pid))
    time.sleep(2.0)
    chat_ok = _chat_available()
    if args.json:
        emit_json({"ok": True, "pid": proc.pid, "chat": chat_ok, "log": str(DAEMON_LOG)}); return
    print(f"\u2705 started agent (pid {proc.pid})")
    print(f"   log: {DAEMON_LOG}")
    print(f"   chat socket: {'up' if chat_ok else 'not yet ready'}")
    if getattr(args, "no_trade", False):
        print("   mode: chat-only (no autonomous trades; use `aime buy`/`aime sell` for manual)")
    else:
        # Mirror the daemon's default-mode heads-up so the user sees the
        # worst-case rate right after `aime start`. Values track agent.py defaults.
        amt = args.amount if getattr(args, "amount", None) else 1.0
        ivl = args.interval if getattr(args, "interval", None) else 300
        max_per_hour = max(1, int(3600 / max(ivl, 1)))
        print(f"   trading: \u2264{max_per_hour}/hr at \u2264${amt:.2f}/trade (`--no-trade` for chat-only; `aime stop` to halt)")
        if getattr(args, "no_position_management", False):
            print("   position management: DISABLED")
        else:
            sl = args.stop_loss if getattr(args, "stop_loss", None) is not None else -0.5
            tp = args.take_profit if getattr(args, "take_profit", None) is not None else 1.0
            print(f"   stop-loss: value/cost \u2264 {1 + sl:.2f}   take-profit: value/cost \u2265 {1 + tp:.2f}")


def cmd_stop(args: argparse.Namespace) -> None:
    running, pid = _agent_running()
    if not running:
        if PID_FILE.exists():
            PID_FILE.unlink()
        msg = "agent not running"
        if args.json: emit_json({"ok": True, "running": False, "msg": msg})
        else: print("\U0001f4a4 " + msg)
        return
    import signal
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as e:
        if args.json: emit_json({"ok": False, "error": str(e)})
        else: print(f"\u274c stop failed: {e}")
        return
    for _ in range(20):
        time.sleep(0.25)
        try:
            os.kill(pid, 0)
        except OSError:
            break
    PID_FILE.unlink(missing_ok=True)
    if args.json: emit_json({"ok": True, "stopped_pid": pid})
    else: print(f"\U0001f6d1 stopped agent (pid {pid})")


def cmd_restart(args: argparse.Namespace) -> None:
    """Stop (if running) + start. Handy after editing personality.txt."""
    running, _pid = _agent_running()
    if running:
        cmd_stop(args)
        # cmd_stop returned via emit_json already if --json; replay it as plain
        # for restart we want both halves visible, so just sleep briefly
        time.sleep(0.5)
    cmd_start(args)


# ---------- Personality presets ----------

PERSONALITY_FILE = AIME_HOME / "personality.txt"

# Pet profiles — what the user sees during onboarding. Each profile is
# a fully fleshed-out character: name, backstory, voice samples, trading
# style description. The daemon's actual system prompt comes from
# PERSONALITY_PRESETS below — these profiles are the human-facing layer
# the host AI shows during pet selection.
#
# Why this matters: ChatGPT/Codex give users preset NAMES and let them
# guess. Owner's instinct was right that giving users 4 fleshed-out pets
# to choose from is more honest than asking them to identify with
# "quant" or "hardnose" labels they don't yet understand. The 5 scenario
# questions still run — but they sort/highlight the pets rather than
# pick one autonomously.

PET_PROFILES = {
    "default": {
        "preset": "default",
        "emoji": "🧠",
        "name": "Tao",  # 陶，沉稳老派
        "tagline": "thoughtful prop trader who hedges everything",
        "backstory": (
            "Mid-career prop trader, 35. Has seen a few cycles, lost money "
            "in 2022, made some back in 2024. Mixes Chinese and English, "
            "doesn't take himself too seriously. Will admit when wrong, "
            "will push back when you're wrong."
        ),
        "voice_samples": [
            "Yo 老大, I'd skip this one — yes_price 0.62 "
            "isn't crazy mispriced enough for me to chase.",
            "OK fine I was wrong about ETH — cutting at -30%. "
            "下次注意仓位.",
            "你这个 tell 振零了。 The "
            "$1.2B fee number is from 2023; check Q1 2026 first.",
        ],
        "trading_style": (
            "Moderate size, 5-10 min cycles, willing to wait for setups. "
            "Cuts losers at -50%, holds winners to +100% or thesis break."
        ),
    },
    "hardnose": {
        "preset": "hardnose",
        "emoji": "🐺",
        "name": "Akira",
        "tagline": "cynical NYC trader, roasts everything",
        "backstory": (
            "Second-gen Chinese-American, 30, ex-Citadel prop. Burned out "
            "on TradFi, came to crypto for the volatility. Hates "
            "momentum chasers, hates news traders, hates anyone who "
            "thinks 'this time is different'."
        ),
        "voice_samples": [
            "Fuck this. BTC at 12 manda? Everyone's a genius until they "
            "aren't. Shorting.",
            "You want me to follow your tell? Fine, but if it dumps I'm "
            "blaming you in the post-mortem.",
            "I was wrong. Cut at -52%. Lesson: don't fade Powell when "
            "he's still bullish.",
        ],
        "trading_style": (
            "Aggressive sizing, fast cycles (1-3 min), contrarian. Will "
            "take a 5x move with tight stops. Looser stop-loss (-70%) "
            "because conviction is high; takes profit at +200%."
        ),
    },
    "zen": {
        "preset": "zen",
        "emoji": "🧘",
        "name": "Jing",  # 静，平静
        "tagline": "佛系交易员, picks her moments",
        "backstory": (
            "Former quant researcher at a Shanghai HF, left because the "
            "office vibe was killing her. Now trades on her own, slowly. "
            "Speaks gently but isn't soft — just has nothing to "
            "prove. Won't FOMO, won't revenge-trade."
        ),
        "voice_samples": [
            "这个不碰，价格太混"
            "乱。再等等。",
            "Took the L on this one. 看不准就是"
            "看不准，没什么好说"
            "的。",
            "你给的这个 tell 听起来"
            "像 catalyst，但我要看看 "
            "on-chain data 再决定。",
        ],
        "trading_style": (
            "Small size, slow cycles (10-20 min). Tight stops (-30%) "
            "because conviction is selective. Takes profit early (+50%). "
            "Skips more often than she trades."
        ),
    },
    "quant": {
        "preset": "quant",
        "emoji": "🧮",
        "name": "Dr. Petrov",
        "tagline": "expected value, Kelly, nothing else",
        "backstory": (
            "PhD in probability theory, ex-Renaissance Technologies. "
            "Refuses to size positions without a numeric edge estimate. "
            "If you give him a 'feeling', he asks what probability you "
            "assign and why. Polite about it but firm."
        ),
        "voice_samples": [
            "YES at 0.42, my posterior is 0.58 given the latest poll "
            "data. Kelly says 4.2% of bankroll. Buy $42.",
            "I was wrong: posterior should have been 0.50, not 0.58. "
            "Bad prior on regulatory delay. Loss noted, model updated.",
            "Your 'gut' is fine but I need a number. What's P(YES) in "
            "your head? 60%? OK so we agree, but I'd size at Kelly "
            "fraction not at vibes.",
        ],
        "trading_style": (
            "Kelly-sized positions, moderate frequency. Stop-loss tied to "
            "posterior drift, not arbitrary %. Takes profit when the "
            "edge collapses."
        ),
    },
}


PERSONALITY_PRESETS = {
    "default": (
        "You are a thoughtful prop trader on AIME, an AI-native prediction "
        "market.\nYou think in probabilities, size positions by conviction, "
        "and treat every\nmistake as data. You are not a hype machine; you "
        "are not a doom-monger.\nYou take hints from your owner seriously "
        "but verify before you act, and\nyou say so when you disagree.\n"
    ),
    "hardnose": (
        "You are a cynical, hard-nosed prop trader from NYC.\n"
        "You roast bad trades but admit when wrong. You hate momentum "
        "chasers\nand say so. Speak short, sharp, no fluff. Drop the "
        "occasional 'fuck this'\nwhen markets are stupid.\n"
    ),
    "zen": (
        "\u4f60\u662f\u4e2a\u4f5b\u7cfb\u4ea4\u6613\u5458\u3002\u770b"
        "\u5230\u597d\u673a\u4f1a\u624d\u51fa\u624b\uff0c\u6ca1\u628a"
        "\u63e1\u5c31 skip\u3002\u4e0d\u8ffd\u6da8\u6740\u8dcc\uff0c"
        "\u4e0d\u4e0a\u5934\u3002\n\u4e8f\u4e86\u5c31\u4e8f\u4e86\uff0c"
        "\u8ba4\u4e86\u4e0b\u6b21\u6ce8\u610f\u3002\u4e0d\u59a8\u788d"
        "\u522b\u4eba\u8d5a\u94b1\uff0c\u4e5f\u4e0d\u88ab\u522b\u4eba"
        "\u7684 FOMO \u5e26\u8d70\u3002\n"
    ),
    "quant": (
        "You are a quant. You only talk in expected value, Kelly fractions, "
        "and\ninformation edge. If someone gives you a 'feeling', ask them "
        "what\nprobability they assign and why. Refuse to size positions "
        "without a\nnumeric edge estimate.\n"
    ),
    "sarcastic": (
        "You are a sarcastic trader who roasts everything, including "
        "yourself.\nBut underneath the sass, you actually trade well. "
        "Mostly. Your jokes\nare dry, not mean. Never punch down.\n"
    ),
    "nerd": (
        "You are a tech-bro engineer who treats prediction markets like "
        "a debugger.\nYou love thinking in terms of priors, posteriors, "
        "and 'what would have to\nbe true for me to be wrong'. "
        "You explain your reasoning step by step.\n"
    ),
}


def cmd_personality(args: argparse.Namespace) -> None:
    sub = getattr(args, "personality_action", None) or "show"

    if sub == "list":
        if args.json:
            emit_json({"presets": list(PERSONALITY_PRESETS.keys())}); return
        print("Available personality presets:")
        for name, text in PERSONALITY_PRESETS.items():
            first_line = text.strip().split("\n")[0]
            print(f"  \u00b7 {name:<10} {first_line[:70]}")
        return

    if sub == "show":
        if not PERSONALITY_FILE.exists():
            text = PERSONALITY_PRESETS["default"]
        else:
            text = PERSONALITY_FILE.read_text(encoding="utf-8")
        if args.json:
            emit_json({"path": str(PERSONALITY_FILE), "text": text, "exists": PERSONALITY_FILE.exists()})
            return
        print(f"# {PERSONALITY_FILE}{'' if PERSONALITY_FILE.exists() else ' (not yet written, showing default)'}\n")
        print(text.rstrip())
        return

    if sub == "path":
        if args.json: emit_json({"path": str(PERSONALITY_FILE)})
        else: print(PERSONALITY_FILE)
        return

    if sub == "set":
        name = getattr(args, "preset", None)
        if not name or name not in PERSONALITY_PRESETS:
            msg = f"unknown preset '{name}'. Try one of: {', '.join(PERSONALITY_PRESETS)}"
            if args.json: emit_json({"ok": False, "error": msg})
            else: print("\u274c " + msg)
            return
        AIME_HOME.mkdir(parents=True, exist_ok=True)
        PERSONALITY_FILE.write_text(PERSONALITY_PRESETS[name], encoding="utf-8")
        running, _ = _agent_running()
        hint = " \u2014 run `aime restart` for the daemon to pick it up" if running else ""
        if args.json:
            emit_json({"ok": True, "preset": name, "path": str(PERSONALITY_FILE), "daemon_running": running})
            return
        print(f"\u2705 personality set to '{name}'{hint}")
        return

    if sub == "edit":
        editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "nano"
        AIME_HOME.mkdir(parents=True, exist_ok=True)
        if not PERSONALITY_FILE.exists():
            PERSONALITY_FILE.write_text(PERSONALITY_PRESETS["default"], encoding="utf-8")
        import subprocess
        try:
            subprocess.call([editor, str(PERSONALITY_FILE)])
        except FileNotFoundError:
            msg = f"editor '{editor}' not found. Set $EDITOR or edit {PERSONALITY_FILE} manually."
            if args.json: emit_json({"ok": False, "error": msg})
            else: print("\u274c " + msg)
            return
        running, _ = _agent_running()
        hint = "\nrun `aime restart` for the daemon to pick it up" if running else ""
        if args.json:
            emit_json({"ok": True, "edited": str(PERSONALITY_FILE), "daemon_running": running}); return
        print(f"\u2705 saved {PERSONALITY_FILE}{hint}")
        return

    if args.json: emit_json({"ok": False, "error": f"unknown subcommand '{sub}'"})
    else: print(f"\u274c unknown subcommand: {sub}")


def cmd_stats(args: argparse.Namespace) -> None:
    resp = http("GET", "/stats", json_mode=args.json)
    if args.json:
        emit_json(resp)
        return
    if not isinstance(resp, dict):
        print(resp)
        return
    print("📈 Platform stats")
    for k, v in resp.items():
        if isinstance(v, float):
            print(f"   {k}: {v:,.2f}")
        else:
            print(f"   {k}: {v}")


# --------------------------------------------------------------------------- #
# Argparse wiring
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aime", description="AIME prediction market CLI")
    # Allow --json before or after the subcommand. Each subparser gets it as
    # an inherited argument via parents=[json_parent].
    p.add_argument("--json", action="store_true", help="output JSON instead of human text")
    json_parent = argparse.ArgumentParser(add_help=False)
    json_parent.add_argument("--json", action="store_true", help="output JSON instead of human text")
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=argparse.ArgumentParser)

    sp = sub.add_parser("setup", parents=[json_parent], help="register a new agent (creates wallet + signs + registers)")
    sp.add_argument("name", help="agent name (unique)")
    sp.add_argument("--force", action="store_true", help="overwrite existing credentials")
    sp.set_defaults(func=cmd_setup)

    sp = sub.add_parser("whoami", parents=[json_parent], help="show current agent info")
    sp.set_defaults(func=cmd_whoami)

    sp = sub.add_parser("set-name", parents=[json_parent], help="rename your agent (1-32 chars)")
    sp.add_argument("name", help="new display name")
    sp.set_defaults(func=cmd_set_name)

    sp = sub.add_parser("balance", parents=[json_parent], help="check account balance")
    sp.set_defaults(func=cmd_balance)

    sp = sub.add_parser("markets", parents=[json_parent], help="list markets")
    sp.add_argument("--sort", choices=["volume", "trades", "ending"])
    sp.add_argument("--status", choices=["active", "settled", "expired"])
    sp.add_argument("--category")
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--offset", type=int, default=0)
    sp.set_defaults(func=cmd_markets)

    sp = sub.add_parser("market", parents=[json_parent], help="get one market by id")
    sp.add_argument("market_id")
    sp.set_defaults(func=cmd_market)

    sp = sub.add_parser(
        "buy", parents=[json_parent],
        help="buy shares: YES/NO for binary, or an outcome index (0,1,2...) for multi-outcome",
    )
    sp.add_argument("market_id")
    sp.add_argument(
        "position",
        help='"YES"/"NO" for binary markets, or 0-based outcome index for multi-outcome',
    )
    sp.add_argument("amount", type=float, help="USD amount to spend")
    sp.add_argument("reasoning", help="reasoning text (>=10 chars)")
    sp.add_argument("--confidence", type=float, help="0.0-1.0")
    sp.add_argument("--model", help="model identifier, e.g. claude-4")
    sp.add_argument("--sources", nargs="+", help="data sources (space-separated)")
    sp.set_defaults(func=cmd_buy)

    sp = sub.add_parser(
        "sell", parents=[json_parent],
        help="sell shares: YES/NO for binary, or an outcome index for multi-outcome",
    )
    sp.add_argument("market_id")
    sp.add_argument(
        "position",
        help='"YES"/"NO" for binary markets, or 0-based outcome index for multi-outcome',
    )
    sp.add_argument("amount", type=float, help="number of shares to sell")
    sp.add_argument("reasoning", help="reasoning text (>=10 chars)")
    sp.set_defaults(func=cmd_sell)

    sp = sub.add_parser("positions", parents=[json_parent], help="list my positions")
    sp.add_argument("market_id", nargs="?", help="optional: filter by market")
    sp.set_defaults(func=cmd_positions)

    sp = sub.add_parser("trades", parents=[json_parent], help="list my trade history")
    sp.add_argument("--limit", type=int, default=50, help="max trades to show (default 50)")
    sp.set_defaults(func=cmd_trades)

    sp = sub.add_parser("leaderboard", parents=[json_parent], help="top agents")
    sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(func=cmd_leaderboard)

    sp = sub.add_parser("stats", parents=[json_parent], help="public platform stats")
    sp.set_defaults(func=cmd_stats)

    sp = sub.add_parser("version", parents=[json_parent], help="show installed version + check for updates")
    sp.set_defaults(func=cmd_version)

    sp = sub.add_parser("update", parents=[json_parent], help="re-run the installer to upgrade in place")
    sp.add_argument("--no-daemon", action="store_true", help="skip daemon update (CLI + skill only)")
    sp.set_defaults(func=cmd_update)

    sp = sub.add_parser(
        "onboard", parents=[json_parent],
        help="interactive onboarding: 5 scenario questions → trading style + risk rules",
    )
    sp.add_argument(
        "--rank-vector",
        help=(
            'JSON dict of axis values \'{"risk":0.3,"numbers":0.7,"admit":0.3,"tempo":0.1}\' '
            "\u2014 returns the 4 pets ranked by best-fit (no apply, just shows). "
            "Use this when you want to let the user choose from the ranked list. "
            "Then call --pick <name> when they decide."
        ),
    )
    sp.add_argument(
        "--pick",
        help=(
            "name of the pet to apply (e.g. \'Akira\', \'Jing\', \'Tao\', "
            "\'Dr. Petrov\', or use the preset key: default, hardnose, zen, quant). "
            "Use this after the user picks from the --rank-vector output."
        ),
    )
    sp.add_argument(
        "--apply-vector",
        help=(
            'JSON dict of axis values \'{"risk":0.5,...}\' \u2014 one-shot mode: '
            "vector \u2192 best pet \u2192 applied. Use when you don\'t want to "
            "show the user the ranking, just pick automatically."
        ),
    )
    sp.add_argument(
        "--force", action="store_true",
        help="overwrite ~/.aime/personality.txt without backing up the existing one",
    )
    sp.set_defaults(func=cmd_onboard)

    # --- local IPC commands (no backend call, talks to your trading daemon) ---
    sp = sub.add_parser("status", parents=[json_parent], help="local agent status from ~/.aime/status.json")
    sp.add_argument("-v", "--verbose", action="store_true", help="show all status fields")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("outbox", parents=[json_parent], help="read messages your agent has for you")
    sp.add_argument("--all", dest="unread", action="store_false", help="include already-read messages")
    sp.add_argument("--no-mark", action="store_true", help="don't mark messages as read")
    sp.add_argument("-n", "--limit", type=int, default=20, help="max messages to show")
    sp.set_defaults(unread=True, func=cmd_outbox)

    sp = sub.add_parser("tell", parents=[json_parent], help="leave an instruction for your agent")
    sp.add_argument("message", help="what to tell the agent")
    sp.add_argument("--ask", action="store_true", help="mark as a question (kind=ask)")
    sp.add_argument("--source", help="where this intel came from (e.g. 'twitter:@vitalik', 'main_chat', 'codex_session', 'web')")
    sp.add_argument("--tags", nargs="+", help="topic tags (e.g. --tags btc macro)")
    sp.set_defaults(func=cmd_tell)

    sp = sub.add_parser("ask", parents=[json_parent], help="ask your agent a question (alias of `tell --ask`)")
    sp.add_argument("message", help="the question")
    sp.set_defaults(func=cmd_tell, ask=True)

    sp = sub.add_parser("feed", parents=[json_parent], help="recent trade decisions + reflections")
    sp.add_argument("-n", "--limit", type=int, default=10, help="how many of each to show")
    sp.set_defaults(func=cmd_feed)

    sp = sub.add_parser("alerts", parents=[json_parent],
                        help="recent proactive alerts from your agent (filtered outbox view)")
    sp.add_argument("-n", "--limit", type=int, default=20, help="how many to show (default 20)")
    sp.add_argument("--event",
                    choices=sorted(ALERT_EVENT_TYPES),
                    help="only show alerts of this event type")
    sp.add_argument("--high-only", dest="high_only", action="store_true",
                    help="only show priority=high alerts")
    sp.set_defaults(func=cmd_alerts)

    # --- v3 conversational bridge commands ---

    sp = sub.add_parser("mood", parents=[json_parent], help="one-line current mood of the agent")
    sp.set_defaults(func=cmd_mood)

    sp = sub.add_parser("brag", parents=[json_parent], help="have the agent brag about its best recent win")
    sp.set_defaults(func=cmd_brag)

    sp = sub.add_parser("confess", parents=[json_parent], help="have the agent own up to its worst recent loss")
    sp.set_defaults(func=cmd_confess)

    sp = sub.add_parser("debate", parents=[json_parent], help="challenge the agent on a position")
    sp.add_argument("message", help="your challenge")
    sp.set_defaults(func=cmd_debate)

    sp = sub.add_parser("memory", parents=[json_parent], help="what the agent remembers you told it")
    sp.add_argument("--hours", type=float, default=48, help="lookback window (default 48h)")
    sp.set_defaults(func=cmd_memory)

    sp = sub.add_parser("start", parents=[json_parent], help="start the local trading daemon")
    sp.add_argument("--strategy", choices=["contrarian", "momentum", "random_walker"], default=None)
    sp.add_argument("--amount", type=float, default=None, help="base trade size USD")
    sp.add_argument("--interval", type=int, default=None, help="trade loop interval seconds")
    sp.add_argument("--no-trade", action="store_true", dest="no_trade",
                    help="chat-only mode: no autonomous trades, use `aime buy`/`aime sell` for manual")
    sp.add_argument("--stop-loss", dest="stop_loss", type=float, default=None,
                    help="close any position whose value/cost drops to (1+stop_loss). Default -0.5 (sell at 50%% loss).")
    sp.add_argument("--take-profit", dest="take_profit", type=float, default=None,
                    help="close any position whose value/cost rises to (1+take_profit). Default 1.0 (sell at 2x).")
    sp.add_argument("--no-position-management", action="store_true", dest="no_position_management",
                    help="disable the stop-loss / take-profit scan at the top of each cycle")
    sp.add_argument("--no-alerts", action="store_true", dest="no_alerts",
                    help="disable proactive event alerts (balance_low / drawdown / streaks / settled / etc)")
    sp.add_argument("--alerts-balance-low", dest="alerts_balance_low", type=float, default=None,
                    help="USD threshold for the balance_low alert (default 50)")
    sp.add_argument("--alerts-drawdown", dest="alerts_drawdown", type=str, default=None,
                    help="comma-sep fractions for drawdown alerts (default '0.2,0.5')")
    sp.add_argument("--alerts-profit", dest="alerts_profit", type=str, default=None,
                    help="comma-sep fractions for profit milestones (default '0.1,0.2,0.5')")
    sp.set_defaults(func=cmd_start)

    sp = sub.add_parser("stop", parents=[json_parent], help="stop the local trading daemon")
    sp.set_defaults(func=cmd_stop)

    sp = sub.add_parser("restart", parents=[json_parent], help="restart the local trading daemon (stop + start)")
    sp.add_argument("--strategy", choices=["contrarian", "momentum", "random_walker"], default=None)
    sp.add_argument("--amount", type=float, default=None)
    sp.add_argument("--interval", type=int, default=None)
    sp.add_argument("--no-trade", action="store_true", dest="no_trade",
                    help="chat-only mode: no autonomous trades")
    sp.add_argument("--stop-loss", dest="stop_loss", type=float, default=None)
    sp.add_argument("--take-profit", dest="take_profit", type=float, default=None)
    sp.add_argument("--no-position-management", action="store_true", dest="no_position_management")
    sp.add_argument("--no-alerts", action="store_true", dest="no_alerts")
    sp.add_argument("--alerts-balance-low", dest="alerts_balance_low", type=float, default=None)
    sp.add_argument("--alerts-drawdown", dest="alerts_drawdown", type=str, default=None)
    sp.add_argument("--alerts-profit", dest="alerts_profit", type=str, default=None)
    sp.set_defaults(func=cmd_restart)

    sp = sub.add_parser("personality", parents=[json_parent],
                        help="show / set / edit the agent's personality")
    psub = sp.add_subparsers(dest="personality_action")
    psub.add_parser("show", parents=[json_parent], help="print current personality (default)")
    psub.add_parser("list", parents=[json_parent], help="list available presets")
    psub.add_parser("path", parents=[json_parent], help="print the personality file path")
    psub.add_parser("edit", parents=[json_parent], help="open the file in $EDITOR")
    set_sp = psub.add_parser("set", parents=[json_parent], help="apply a preset")
    set_sp.add_argument("preset", help="preset name (see `aime personality list`)")
    sp.set_defaults(func=cmd_personality, personality_action=None)

    # --- v2.2.0 new commands ---

    sp = sub.add_parser("faucet", parents=[json_parent],
                        help="on-chain testnet faucet (mints mUSDT to your wallet, 24h cooldown)")
    fsub = sp.add_subparsers(dest="faucet_action")
    fsub.add_parser("claim", parents=[json_parent],
                    help="claim mUSDT from the faucet (default amount, 24h cooldown)")
    fsub.add_parser("status", parents=[json_parent],
                    help="show when you can next claim from the faucet")
    sp.set_defaults(func=cmd_faucet, faucet_action=None)

    sp = sub.add_parser("withdraw", parents=[json_parent], help="withdraw from agent balance")
    sp.add_argument("amount", type=float, help="amount to withdraw")
    sp.set_defaults(func=cmd_withdraw)

    sp = sub.add_parser("create-market", parents=[json_parent], help="create a new prediction market")
    sp.add_argument("question", help='the market question (max 200 chars)')
    sp.add_argument("resolution", help='resolution criteria — name source/threshold/timing precisely')
    sp.add_argument("--end-hours", type=int, default=72, help="hours until market resolves (24..720, default 72)")
    sp.add_argument("--subsidy", type=float, default=50.0, help="USDT subsidy for liquidity (default 50)")
    sp.add_argument("--category", default=None, help="optional category tag")
    sp.add_argument("--outcomes", nargs="+", default=None,
                    help="multi-outcome labels (3-6 strings); omit for binary YES/NO")
    sp.set_defaults(func=cmd_create_market)

    sp = sub.add_parser("propose", parents=[json_parent], help="propose an oracle outcome for an expired market")
    sp.add_argument("market_id", help="market id to resolve")
    sp.add_argument("outcome", choices=["YES", "NO", "yes", "no"], help="proposed outcome")
    sp.add_argument("--stake", type=float, default=10.0, help="USDT stake on this proposal")
    sp.add_argument("--reasoning", required=True, help="why this outcome — be specific")
    sp.set_defaults(func=cmd_propose_oracle)

    sp = sub.add_parser("dispute", parents=[json_parent], help="dispute an existing oracle proposal")
    sp.add_argument("market_id", help="market id")
    sp.add_argument("outcome", choices=["YES", "NO", "yes", "no"], help="your counter-claim")
    sp.add_argument("--stake", type=float, default=20.0, help="USDT stake (typically 2x the proposer's)")
    sp.add_argument("--reasoning", required=True, help="why the proposer is wrong")
    sp.set_defaults(func=cmd_dispute_oracle)

    sp = sub.add_parser("finalize", parents=[json_parent], help="finalize an undisputed proposal (anyone can crank)")
    sp.add_argument("market_id", help="market id to finalize")
    sp.set_defaults(func=cmd_finalize_oracle)

    sp = sub.add_parser("proposals", parents=[json_parent], help="list oracle proposals")
    sp.add_argument("--state", choices=["open", "disputed", "finalized"], default=None)
    sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(func=cmd_proposals)

    sp = sub.add_parser("proposal", parents=[json_parent], help="show oracle proposal for a market")
    sp.add_argument("market_id", help="market id")
    sp.set_defaults(func=cmd_oracle_proposal)

    sp = sub.add_parser("reasoning", parents=[json_parent], help="list reasoning-bank entries (AIME's reasoning data)")
    sp.add_argument("--market-id", default=None, help="filter by market")
    sp.add_argument("--agent-id", default=None, help="filter by agent")
    sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(func=cmd_reasoning_bank)

    sp = sub.add_parser("reasoning-stats", parents=[json_parent], help="aggregated reasoning bank stats")
    sp.set_defaults(func=cmd_reasoning_stats)

    sp = sub.add_parser("agent-stats", parents=[json_parent], help="stats for a specific agent")
    sp.add_argument("agent_id", help="agent UUID")
    sp.set_defaults(func=cmd_agent_stats)

    return p


# ---------------------------------------------------------------------------
# New commands (v2.2.0): deposit/withdraw, create-market, oracle, reasoning-bank
# ---------------------------------------------------------------------------


def cmd_faucet(args: argparse.Namespace) -> None:
    """On-chain faucet: claim mUSDT or check status.

    Replaces the old self-deposit hack. Every claim is a real on-chain
    mint (recorded in the public `faucet_claims` table), 24h cooldown,
    fixed amount.
    """
    sub = getattr(args, "faucet_action", None) or "claim"
    creds = load_creds(json_mode=args.json)

    if sub == "status":
        resp = http("GET", "/faucet/status",
                    api_key=creds["api_key"],
                    json_mode=args.json)
        if args.json:
            emit_json(resp); return
        if not isinstance(resp, dict):
            print(resp); return
        amt = resp.get("amount_per_claim", 500)
        cd  = resp.get("cooldown_hours", 24)
        if resp.get("can_claim_now"):
            print(f"\U0001f4a7 faucet ready: {fmt_usd(amt)} every {cd:g}h")
            last = resp.get("last_claim_at")
            if last:
                print(f"   last claim: {last}")
            print("   run: aime faucet claim")
        else:
            next_at = resp.get("next_claim_at") or "?"
            last = resp.get("last_claim_at") or "?"
            tx = resp.get("last_claim_tx_hash")
            print(f"\u23f3 faucet on cooldown")
            print(f"   last claim: {last}")
            if tx:
                print(f"   last tx:    {tx}")
            print(f"   next claim: {next_at}")
        return

    if sub == "claim":
        resp = http("POST", "/faucet/claim",
                    api_key=creds["api_key"],
                    body={},
                    json_mode=args.json)
        if args.json:
            emit_json(resp); return
        if not isinstance(resp, dict):
            print(resp); return
        amt = resp.get("amount_usd", 0)
        new_bal = resp.get("balance_after")
        tx = resp.get("tx_hash")
        next_at = resp.get("next_claim_at")
        print(f"\u2705 faucet minted {fmt_usd(amt)} to your wallet")
        if new_bal is not None:
            print(f"   new balance: {fmt_usd(new_bal)}")
        if tx:
            print(f"   tx hash:     {tx}")
        if next_at:
            print(f"   next claim:  {next_at}")
        return

    if args.json:
        emit_json({"ok": False, "error": f"unknown faucet subcommand: {sub}"})
    else:
        print(f"\u274c unknown faucet subcommand: {sub}")


def cmd_withdraw(args: argparse.Namespace) -> None:
    """Withdraw funds from agent balance."""
    creds = load_creds(json_mode=args.json)
    resp = http("POST", "/balance/withdraw",
                api_key=creds["api_key"],
                body={"amount": args.amount},
                json_mode=args.json)
    if args.json:
        emit_json(resp)
        return
    new_bal = resp.get("balance_after") if isinstance(resp, dict) else None
    print(f"🏧 Withdrew {fmt_usd(args.amount)}")
    if new_bal is not None:
        print(f"   new balance: {fmt_usd(new_bal)}")


def cmd_create_market(args: argparse.Namespace) -> None:
    """Create a new prediction market.

    Resolution criteria must be PRECISE — name data source, threshold, timing.
    end_hours specifies how long until the market resolves (24..720).
    """
    creds = load_creds(json_mode=args.json)
    from datetime import datetime, timedelta, timezone
    end_time = (datetime.now(timezone.utc) + timedelta(hours=args.end_hours)).isoformat()
    body: dict[str, Any] = {
        "question": args.question,
        "resolution_criteria": args.resolution,
        "end_time": end_time,
        "subsidy_amount": args.subsidy,
    }
    if args.category:
        body["category"] = args.category
    if args.outcomes:
        body["outcomes"] = args.outcomes
    resp = http("POST", "/markets",
                api_key=creds["api_key"],
                body=body, json_mode=args.json)
    if args.json:
        emit_json(resp)
        return
    if isinstance(resp, dict):
        print(f"🏭 Created market [{resp.get('id', '?')[:8]}...]")
        print(f"   question: {resp.get('question')}")
        print(f"   type:     {resp.get('market_type', 'binary')}")
        print(f"   subsidy:  {fmt_usd(resp.get('subsidy_amount'))}")
        print(f"   ends:     {resp.get('end_time', '')[:19]}")
    else:
        print(resp)


def cmd_propose_oracle(args: argparse.Namespace) -> None:
    """Propose an outcome for an expired market (oracle vote)."""
    creds = load_creds(json_mode=args.json)
    body = {
        "outcome": args.outcome.upper(),
        "stake": args.stake,
        "reasoning": args.reasoning,
    }
    resp = http("POST", f"/oracle/markets/{args.market_id}/propose",
                api_key=creds["api_key"], body=body, json_mode=args.json)
    if args.json:
        emit_json(resp)
        return
    if isinstance(resp, dict):
        print(f"🏛️  Proposed {args.outcome.upper()} on [{args.market_id[:8]}...]")
        print(f"   stake:    {fmt_usd(args.stake)}")
        print(f"   state:    {resp.get('state')}")
        print(f"   proposal id: {resp.get('id')}")
    else:
        print(resp)


def cmd_dispute_oracle(args: argparse.Namespace) -> None:
    """Dispute an existing oracle proposal."""
    creds = load_creds(json_mode=args.json)
    body = {
        "outcome": args.outcome.upper(),
        "stake": args.stake,
        "reasoning": args.reasoning,
    }
    resp = http("POST", f"/oracle/markets/{args.market_id}/dispute",
                api_key=creds["api_key"], body=body, json_mode=args.json)
    if args.json:
        emit_json(resp)
        return
    print(f"⚔️  Disputed [{args.market_id[:8]}...] with {args.outcome.upper()} stake {fmt_usd(args.stake)}")


def cmd_finalize_oracle(args: argparse.Namespace) -> None:
    """Finalize an undisputed proposal (permissionless crank)."""
    creds = load_creds(json_mode=args.json)
    resp = http("POST", f"/oracle/markets/{args.market_id}/finalize",
                api_key=creds["api_key"], body={}, json_mode=args.json)
    if args.json:
        emit_json(resp)
        return
    if isinstance(resp, dict):
        print(f"🔔 Finalized [{args.market_id[:8]}...]")
        print(f"   final_outcome: {resp.get('final_outcome', resp.get('outcome', '?'))}")
    else:
        print(resp)


def cmd_proposals(args: argparse.Namespace) -> None:
    """List recent oracle proposals (open / disputed / finalized)."""
    creds = load_creds(json_mode=args.json)
    params: dict[str, Any] = {}
    if args.state:
        params["state"] = args.state
    if args.limit:
        params["limit"] = args.limit
    resp = http("GET", "/oracle/proposals",
                api_key=creds["api_key"], params=params, json_mode=args.json)
    if args.json:
        emit_json(resp)
        return
    items = resp if isinstance(resp, list) else (resp.get("proposals") or resp.get("data") or [])
    if not items:
        print("(no proposals)")
        return
    print(f"📜 {len(items)} proposal(s):")
    for it in items:
        mid = (it.get("market_id") or "?")[:8]
        state = it.get("state", "?")
        out = it.get("outcome", "?")
        stake = it.get("stake", 0)
        print(f"  [{mid}] {state:10s} {out:4s} stake={fmt_usd(stake)}")


def cmd_oracle_proposal(args: argparse.Namespace) -> None:
    """Show the current oracle proposal for a market."""
    creds = load_creds(json_mode=args.json)
    resp = http("GET", f"/oracle/markets/{args.market_id}/proposal",
                api_key=creds["api_key"], json_mode=args.json)
    if args.json:
        emit_json(resp)
        return
    if isinstance(resp, dict) and resp:
        print(f"🏛️  Proposal on [{args.market_id[:8]}...]")
        for k in ("state", "outcome", "stake", "proposer_id", "created_at"):
            if k in resp:
                v = resp[k]
                if k == "stake":
                    v = fmt_usd(v)
                print(f"   {k:12s} {v}")
    else:
        print(f"(no proposal on {args.market_id[:8]})")


def cmd_reasoning_bank(args: argparse.Namespace) -> None:
    """List reasoning bank entries (AIME's collected agent reasoning)."""
    params: dict[str, Any] = {}
    if args.limit:
        params["limit"] = args.limit
    if args.market_id:
        params["market_id"] = args.market_id
    if args.agent_id:
        params["agent_id"] = args.agent_id
    resp = http("GET", "/reasoning-bank", params=params, json_mode=args.json)
    if args.json:
        emit_json(resp)
        return
    # Backend returns {"reasonings": [...], ...}; tolerate legacy {"entries"|"data": [...]}
    if isinstance(resp, list):
        items = resp
    elif isinstance(resp, dict):
        items = (
            resp.get("reasonings")
            or resp.get("entries")
            or resp.get("data")
            or []
        )
    else:
        items = []
    if not items:
        print("(no reasoning entries)")
        return
    limit = args.limit if args.limit and args.limit > 0 else 10
    print(f"🧠 {len(items)} reasoning entries:")
    for it in items[:limit]:
        agent = (it.get("agent_name") or it.get("agent_id") or "?")[:20]
        pos = it.get("position", "?")
        conf = it.get("confidence")
        reason = (it.get("reasoning_text") or it.get("reasoning") or "")[:80]
        print(f"  {agent:20s} {pos:4s} conf={fmt_pct(conf) if conf is not None else '?  '} → {reason}")


def cmd_reasoning_stats(args: argparse.Namespace) -> None:
    """Aggregated reasoning bank stats."""
    resp = http("GET", "/reasoning-bank/stats", json_mode=args.json)
    if args.json:
        emit_json(resp)
        return
    if isinstance(resp, dict):
        print("📊 Reasoning Bank stats:")
        for k, v in resp.items():
            print(f"   {k:24s} {v}")


def cmd_agent_stats(args: argparse.Namespace) -> None:
    """Stats for a specific agent (PnL, win rate, etc)."""
    # Backend route is /api/v1/agents/{id}/stats (mounted under the leaderboard
    # router but without a /leaderboard prefix). Pre-2026-05 builds of this CLI
    # used the wrong path and got 404s.
    resp = http("GET", f"/agents/{args.agent_id}/stats", json_mode=args.json)
    if args.json:
        emit_json(resp)
        return
    if isinstance(resp, dict):
        print(f"📈 Agent {args.agent_id[:12]}... stats:")
        # Map backend keys -> friendly labels. Backend uses agent_name / total_pnl /
        # accuracy / trade_count, not the previous CLI's pnl_total / win_rate / etc.
        display_keys = [
            ("agent_name", "name"),
            ("rank", "rank"),
            ("accuracy", "accuracy"),
            ("brier_score", "brier"),
            ("total_pnl", "total_pnl"),
            ("trade_count", "trades"),
            ("markets_participated", "markets"),
            ("win_streak", "win_streak"),
            ("wallet_address", "wallet"),
        ]
        for key, label in display_keys:
            if key not in resp:
                continue
            v = resp[key]
            if key == "accuracy":
                v = fmt_pct(v)
            elif key in ("total_pnl",):
                v = fmt_usd(v)
            print(f"   {label:18s} {v}")


# ---------------------------------------------------------------------------
# Self-update
# ---------------------------------------------------------------------------

UPDATE_CHECK_FILE = Path.home() / ".aime" / ".last-update-check"
UPDATE_CHECK_INTERVAL = 86400  # 1 day


def _fetch_latest_version() -> str | None:
    """Fetch the latest version string from the skill repo. Returns None
    on any failure (offline, repo down, etc.) — never blocks the CLI.

    Tries the GitHub contents API first (fresh, no CDN cache, 60 req/h
    unauth — fine because we only check once per UPDATE_CHECK_INTERVAL
    per machine), falls back to raw.githubusercontent.com on rate limit
    / API failure. Raw is ~5min stale after a push, so it's the backup
    not the primary.
    """
    # Primary: contents API (immediate freshness)
    try:
        api_url = (
            "https://api.github.com/repos/parami-foundation/aime-skill"
            "/contents/VERSION?ref=main"
        )
        r = requests.get(api_url, timeout=3,
                         headers={"Accept": "application/vnd.github.v3.raw"})
        if r.status_code == 200:
            return r.text.strip()
    except Exception:
        pass
    # Fallback: raw CDN (cached but no rate limit)
    try:
        r = requests.get(SKILL_VERSION_URL, timeout=3)
        if r.status_code == 200:
            return r.text.strip()
    except Exception:
        pass
    return None


def _version_tuple(v):
    parts=[]
    for seg in (v or "").split("."):
        try: parts.append(int(seg))
        except ValueError: parts.append(0)
    return tuple(parts) or (0,)


def _version_newer(latest, installed):
    return _version_tuple(latest) > _version_tuple(installed)


def _maybe_check_update() -> None:
    """Background-ish update check. Runs at most once per UPDATE_CHECK_INTERVAL,
    prints a one-line hint to stderr if a newer version is available. Never
    blocks the actual command, never errors out."""
    # Skip the check if user asked for JSON output (don't pollute stdout).
    if "--json" in sys.argv:
        return
    # Skip on version/update/--help commands (avoid recursion / noise).
    if any(arg in sys.argv for arg in ("version", "update", "--help", "-h")):
        return

    try:
        if UPDATE_CHECK_FILE.exists():
            mtime = UPDATE_CHECK_FILE.stat().st_mtime
            if time.time() - mtime < UPDATE_CHECK_INTERVAL:
                return
    except Exception:
        pass

    latest = _fetch_latest_version()
    try:
        UPDATE_CHECK_FILE.parent.mkdir(parents=True, exist_ok=True)
        UPDATE_CHECK_FILE.touch()
    except Exception:
        pass

    if latest and _version_newer(latest, __version__):
        sys.stderr.write(
            f"\u2728 aime {latest} is available (you have {__version__}). "
            f"Run `aime update` to upgrade.\n"
        )


def cmd_version(args: argparse.Namespace) -> None:
    latest = _fetch_latest_version()
    if args.json:
        emit_json({
            "installed": __version__,
            "latest": latest,
            "update_available": bool(latest and _version_newer(latest, __version__)),
        })
        return
    print(f"aime CLI {__version__}")
    if latest:
        if _version_newer(latest, __version__):
            print(f"   ✨ {latest} is available — run `aime update`")
        elif _version_newer(__version__, latest):
            print(f"   (you're ahead of main: latest published is {latest})")
        else:
            print(f"   ✓ up to date")
    else:
        print("   (couldn't reach GitHub to check latest)")


def cmd_update(args: argparse.Namespace) -> None:
    """Re-run the installer to upgrade in place."""
    import subprocess

    if args.json:
        # In JSON mode, just report what would happen and exit
        latest = _fetch_latest_version()
        emit_json({
            "installed": __version__,
            "latest": latest,
            "command": f"curl -fsSL {SKILL_INSTALL_SCRIPT} | bash",
        })
        return

    print(f"\U0001f504 updating aime (current: {__version__})...")
    print(f"   running: curl -fsSL {SKILL_INSTALL_SCRIPT} | bash")
    print()

    extra_env = os.environ.copy()
    if args.no_daemon:
        extra_env["AIME_NO_DAEMON"] = "1"

    # Use bash explicitly so set -e etc. behave consistently.
    try:
        r = subprocess.run(
            ["bash", "-c", f"curl -fsSL {SKILL_INSTALL_SCRIPT} | bash"],
            env=extra_env,
        )
        if r.returncode == 0:
            print()
            print("\u2705 done. Verify with `aime version`.")
        else:
            print()
            print(f"\u274c installer exited with status {r.returncode}")
            sys.exit(r.returncode)
    except FileNotFoundError:
        print("\u274c bash or curl not found. Manual install instructions:")
        print(f"   {SKILL_REPO_URL}#installation")
        sys.exit(2)


# ---------------------------------------------------------------------------
# Onboarding — discover trading style through scenario questions
# ---------------------------------------------------------------------------

# 5 axes, each runs -1..+1. A user's answer to each question contributes
# a delta on one axis. Presets each have an "ideal vector" — we pick the
# preset with the highest cosine similarity to the user's vector.

# Axis meanings (positive direction in parens):
#   risk    (aggressive)   conservative ⇆ aggressive
#   numbers (numbers)      stories      ⇆ numbers / EV / math
#   admit   (admit fast)   defend       ⇆ admit when wrong
#   humour  (humorous)     serious      ⇆ humorous / sarcastic
#   tempo   (fast)         patient      ⇆ fast / scalper

# Trading-behavior vectors (4 axes, NO humour/voice).
# Voice/tone is a *consequence* of personality — built into each preset's
# personality.txt and how it speaks — not an axis we ask the user about
# separately. Onboard matches trading behavior; voice comes along for the ride.
#
# Note we only match against 4 "core" presets here. sarcastic/nerd exist as
# voice variants users can set explicitly with `aime personality set <name>`
# after they pick a trading direction.
PRESET_VECTORS = {
    "default":  {"risk":  0.0, "numbers": +0.3, "admit": +0.5, "tempo":  0.0},
    "hardnose": {"risk": +0.7, "numbers":  0.0, "admit": +0.3, "tempo": +0.5},
    "zen":      {"risk": -0.7, "numbers": -0.3, "admit": +0.7, "tempo": -0.7},
    "quant":    {"risk":  0.0, "numbers": +1.0, "admit": +0.5, "tempo":  0.0},
    # sarcastic / nerd are voice variants — user picks them manually if
    # they want, onboard doesn't try to infer them from trading questions.
}


# Each question is one scenario with 2-4 answers. Each answer carries a
# vector delta (only non-zero axes listed).
ONBOARD_QUESTIONS = [
    # All 5 questions probe TRADING BEHAVIOR. Voice/tone is a consequence,
    # not an input — we don't ask "do you want jokes or seriousness" because
    # the personality that best fits your trading already has its own voice.
    {
        "key": "q1_btc_pump",
        "prompt": "BTC just pumped 30% in a day. Your first instinct?",
        "options": [
            {"label": "Short the breakout — too far, too fast",
             "deltas": {"risk": +0.7, "admit": +0.2}},
            {"label": "Wait it out — chase = pain",
             "deltas": {"risk": -0.7, "tempo": -0.5, "admit": +0.3}},
            {"label": "Check funding/open-interest first, then decide",
             "deltas": {"numbers": +0.7, "admit": +0.3}},
            {"label": "Ride it with a tight stop",
             "deltas": {"risk": +0.3, "tempo": +0.5}},
        ],
    },
    {
        "key": "q2_lose_50",
        "prompt": "You're down -50% on a trade. What's your move?",
        "options": [
            {"label": "Cut. Sized too big. Note the lesson.",
             "deltas": {"admit": +1.0, "numbers": +0.3}},
            {"label": "Hold — thesis hasn't changed, the market's wrong",
             "deltas": {"admit": -0.7, "risk": +0.3}},
            {"label": "Average down if thesis holds, otherwise cut",
             "deltas": {"admit": +0.3, "risk": +0.3, "numbers": +0.3}},
            {"label": "Take the L quietly and re-evaluate next time",
             "deltas": {"admit": +0.7, "risk": -0.3, "tempo": -0.3}},
        ],
    },
    {
        "key": "q3_explain",
        "prompt": "When the agent explains a trade to you, you prefer:",
        "options": [
            {"label": "Probability X%, EV +$Y, Kelly fraction Z",
             "deltas": {"numbers": +1.0}},
            {"label": "A story — catalyst, who's on the other side, why they're wrong",
             "deltas": {"numbers": -0.7}},
            {"label": "Step-by-step: prior, evidence, posterior",
             "deltas": {"numbers": +0.7, "tempo": -0.3}},
            {"label": "One sentence — the call and confidence",
             "deltas": {"numbers": -0.3, "tempo": +0.3}},
        ],
    },
    {
        "key": "q4_pet_peeve",
        "prompt": "What kind of trade pisses you off the most when you see other traders do it?",
        "options": [
            {"label": "Blind FOMO chasing, no thesis",
             "deltas": {"admit": +0.5, "numbers": +0.5}},
            {"label": "Holding losers and praying",
             "deltas": {"admit": +1.0}},
            {"label": "Too small to matter — paper-handed sizing",
             "deltas": {"risk": +0.7}},
            {"label": "Reckless oversizing, blowing up the account",
             "deltas": {"risk": -0.7, "admit": +0.3}},
        ],
    },
    {
        "key": "q5_size",
        "prompt": "Default position size on a 65%-confidence trade with $1000?",
        "options": [
            {"label": "$5-10 — small, paddle in the water",
             "deltas": {"risk": -0.7}},
            {"label": "$25-50 — moderate, want to feel it",
             "deltas": {"risk":  0.0}},
            {"label": "$100-200 — go big or go home",
             "deltas": {"risk": +0.8, "tempo": +0.3}},
            {"label": "Whatever Kelly says (~$20 here)",
             "deltas": {"numbers": +0.8, "risk":  0.0}},
        ],
    },
]


def _score_preset(user_vec: dict, preset_vec: dict) -> float:
    """Cosine similarity between user answer vector and preset ideal vector."""
    keys = set(user_vec) | set(preset_vec)
    dot = sum(user_vec.get(k, 0) * preset_vec.get(k, 0) for k in keys)
    nu = sum(user_vec.get(k, 0) ** 2 for k in keys) ** 0.5
    np = sum(preset_vec.get(k, 0) ** 2 for k in keys) ** 0.5
    if nu == 0 or np == 0:
        return 0.0
    return dot / (nu * np)


def _derive_preset(user_vec: dict) -> tuple[str, list[tuple[str, float]]]:
    """Pick best-matching preset. Return (best_name, ranked_list)."""
    ranked = sorted(
        ((name, _score_preset(user_vec, vec)) for name, vec in PRESET_VECTORS.items()),
        key=lambda kv: kv[1],
        reverse=True,
    )
    return ranked[0][0], ranked


def _rank_pets(user_vec: dict) -> list[dict]:
    """Score and rank pets by vector similarity. Returns full PET_PROFILES
    entries (including backstory + voice samples), enriched with a `score`
    field. Best match is first."""
    scored = []
    for preset_name, profile in PET_PROFILES.items():
        score = _score_preset(user_vec, PRESET_VECTORS[preset_name])
        scored.append({**profile, "score": round(score, 3)})
    scored.sort(key=lambda p: p["score"], reverse=True)
    return scored


def _save_onboard_state(user_vec: dict) -> None:
    """Cache the user vector between --rank-vector and --pick so that
    --pick can derive trade params from the real answers, not zero."""
    try:
        AIME_HOME.mkdir(parents=True, exist_ok=True)
        ONBOARD_STATE_FILE.write_text(json.dumps({
            "user_vector": user_vec,
            "ts": int(time.time()),
        }))
    except Exception:
        # Best-effort; we'll just fall back to zero-vector if missing.
        pass


def _load_onboard_state() -> dict | None:
    """Return the cached user_vector if file exists and is fresh."""
    if not ONBOARD_STATE_FILE.exists():
        return None
    try:
        data = json.loads(ONBOARD_STATE_FILE.read_text())
        age = time.time() - data.get("ts", 0)
        if age > ONBOARD_STATE_TTL_SEC:
            return None
        vec = data.get("user_vector")
        if isinstance(vec, dict):
            return vec
    except Exception:
        pass
    return None


def _clear_onboard_state() -> None:
    try:
        ONBOARD_STATE_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def _derive_trade_params(user_vec: dict) -> dict:
    """Infer suggested trade size / interval / stop / take from the
    user's risk + tempo axes."""
    risk = user_vec.get("risk", 0.0)
    tempo = user_vec.get("tempo", 0.0)

    # trade_size_usd: risk-driven, 1..50 range
    if risk <= -0.5:
        size = 1.0
    elif risk <= 0:
        size = 5.0
    elif risk <= 0.4:
        size = 15.0
    else:
        size = 30.0

    # interval: tempo-driven, 60..900 range
    if tempo <= -0.5:
        interval = 600
    elif tempo <= 0:
        interval = 300
    elif tempo <= 0.4:
        interval = 180
    else:
        interval = 90

    # stop_loss: tighter when risk is low, looser when high
    if risk <= -0.5:
        stop_loss = -0.3
        take_profit = 0.5
    elif risk <= 0.4:
        stop_loss = -0.5
        take_profit = 1.0
    else:
        stop_loss = -0.7
        take_profit = 2.0

    return {
        "trade_size_usd": size,
        "interval_seconds": interval,
        "stop_loss_pct": stop_loss,
        "take_profit_pct": take_profit,
    }


def cmd_onboard(args: argparse.Namespace) -> None:
    """Interactive (or scripted) onboarding — discover trading style by asking
    scenario questions, not by forcing the user to pick from a preset list.

    Modes:
      - default: human interactive (stdin)
      - --json: dump the full questionnaire + preset vectors so a host AI
        can ask the user in its own voice and then POST results back via
        --vector
      - --vector "{...}": skip questions, apply derived style for a given
        vector (host AI does this after collecting answers)
    """
    # Pre-flight: must have creds (run aime setup first if not)
    if not CREDS_PATH.exists():
        msg = "no agent registered yet. run `aime setup <name>` first."
        if args.json:
            emit_json({"ok": False, "error": msg, "next": "aime setup <name>"})
        else:
            print(f"\u274c {msg}")
        sys.exit(1)

    # ----- Mode 1: host-AI mode (--json, no other flags) -----
    # Return the full questionnaire so the host can ask in its own voice,
    # then apply via --rank-vector / --pick / --apply-vector once it has
    # the user's answers.
    if args.json and not (
        getattr(args, "rank_vector", None)
        or getattr(args, "pick", None)
        or getattr(args, "apply_vector", None)
    ):
        emit_json({
            "ok": True,
            "questions": ONBOARD_QUESTIONS,
            "pets": list(PET_PROFILES.values()),
            "voice_variants": {
                # Not in PET_PROFILES; user can explicitly pick if they want
                # a different voice than their chosen pet ships with.
                "sarcastic": "dry humour, mocks bad trades (including its own)",
                "nerd":      "explains priors/posteriors step by step, debugger-style",
            },
            "instructions": (
                "Two-step flow:\n"
                "  1. Ask the user each scenario question in your own voice. "
                "Sum the deltas into a 4-axis vector "
                "(risk, numbers, admit, tempo).\n"
                "  2. Call: aime onboard --rank-vector \'{\"risk\":0.3,...}\' "
                "to get back the 4 pets RANKED by best-fit. Then show the "
                "user all 4 pets (with name/backstory/voice_samples) and "
                "let them PICK one — the vector is a hint, not the verdict. "
                "Finally: aime onboard --pick <pet_name> to apply.\n"
                "Why: most users don't trust a black-box pick. Showing the "
                "ranked pets honors both the diagnosis and the user's agency.\n"
                "Note on voice: each pet ships with its own voice (see "
                "voice_samples). If the user wants a different voice on "
                "top of their picked trading style — e.g. zen trading but "
                "sarcastic delivery — run `aime personality set "
                "sarcastic|nerd` AFTER --pick. The trade params stay; only "
                "the system prompt swaps."
            ),
        })
        return

    # ----- Mode 2a: --rank-vector — host AI hands in user\'s vector, gets
    # pets ranked by best-fit. Doesn\'t apply anything yet; host then
    # shows the ranked list to the user and lets them pick. -----
    if getattr(args, "rank_vector", None):
        try:
            user_vec = json.loads(args.rank_vector)
        except Exception as e:
            print(f"\u274c bad --rank-vector JSON: {e}")
            sys.exit(2)
        ranked = _rank_pets(user_vec)
        # Enrich with derived trade params (same vector, same derivation)
        params = _derive_trade_params(user_vec)
        # Cache vector so --pick (next step) can derive the same params.
        # Without this, --pick falls back to zero-vector and the user's
        # risk/tempo answers get silently discarded at the last step.
        _save_onboard_state(user_vec)
        if args.json:
            emit_json({
                "ok": True,
                "user_vector": user_vec,
                "ranked_pets": ranked,
                "derived_params": params,
                "recommended_pet": ranked[0]["name"],
                "next_step": (
                    f"Show the user the 4 pets above. Top match is "
                    f"{ranked[0]['name']} (score {ranked[0]['score']}). "
                    "Let them pick (they may prefer a runner-up). Apply with: "
                    "aime onboard --pick <pet_name>"
                ),
            })
            return
        # human-readable rank
        print(f"\n\U0001f9ed Your vector:")
        for axis, val in user_vec.items():
            bar = "+" * max(0, int(round(val * 5))) + "-" * max(0, -int(round(val * 5)))
            print(f"   {axis:8s} {val:+.2f}  {bar}")
        print(f"\n\U0001f43e Pets ranked by fit:")
        for i, pet in enumerate(ranked, 1):
            star = " \u2b50" if i == 1 else ""
            print(f"\n   [{i}]{star} {pet['emoji']} {pet['name']} "
                  f"({pet['preset']}) \u2014 score {pet['score']:+.2f}")
            print(f"       \u201c{pet['tagline']}\u201d")
            print(f"       {pet['trading_style']}")
        print(f"\n\u2192 Pick one with:  aime onboard --pick <name>")
        print(f"   (e.g. aime onboard --pick {ranked[0]['name']})")
        return

    # ----- Mode 2b: --pick — user has chosen a pet name; apply it -----
    if getattr(args, "pick", None):
        pet_name = args.pick.lower()
        # Find by name or preset key
        chosen = None
        for preset, profile in PET_PROFILES.items():
            if profile["name"].lower() == pet_name or preset == pet_name:
                chosen = profile
                break
        if not chosen:
            available = ", ".join(p["name"] for p in PET_PROFILES.values())
            print(f"\u274c no pet named \'{args.pick}\'. Available: {available}")
            sys.exit(2)
        # Prefer the cached vector from --rank-vector (real user answers)
        # over a neutral zero-vector. This preserves the risk/tempo signal
        # that drives trade size and interval.
        cached_vec = _load_onboard_state()
        if cached_vec is not None:
            params = _derive_trade_params(cached_vec)
        else:
            params = _derive_trade_params(
                {"risk": 0, "numbers": 0, "admit": 0, "tempo": 0}
            )
        _apply_pet(chosen, params, also_print=True,
                   force=getattr(args, "force", False))
        # Onboarding done; don't let stale state leak into a future re-onboard.
        _clear_onboard_state()
        return

    # ----- Mode 2c: --apply-vector — single-shot: vector \u2192 best pet,
    # skip the ranking display. Useful for non-interactive scripts. -----
    if getattr(args, "apply_vector", None):
        try:
            user_vec = json.loads(args.apply_vector)
        except Exception as e:
            print(f"\u274c bad --apply-vector JSON: {e}")
            sys.exit(2)
        _apply_onboarding_vector(
            user_vec, also_print=True,
            force=getattr(args, "force", False),
        )
        return

    # ----- Mode 3: interactive (human at terminal) -----
    print()
    print("\U0001f6e0\ufe0f  AIME onboarding — let's find your trading style")
    print("   I'll ask 5 quick questions. No wrong answers; just pick what")
    print("   sounds most like you.")
    print()

    user_vec: dict[str, float] = {"risk": 0.0, "numbers": 0.0, "admit": 0.0, "tempo": 0.0}

    for i, q in enumerate(ONBOARD_QUESTIONS, 1):
        print(f"\n[{i}/{len(ONBOARD_QUESTIONS)}] {q['prompt']}")
        for j, opt in enumerate(q["options"], 1):
            print(f"   {j}. {opt['label']}")
        while True:
            raw = input(f"   pick [1-{len(q['options'])}]: ").strip()
            try:
                idx = int(raw) - 1
                if 0 <= idx < len(q["options"]):
                    break
            except ValueError:
                pass
            print(f"   \u26a0\ufe0f  pick a number 1-{len(q['options'])}")
        chosen = q["options"][idx]
        for axis, delta in chosen["deltas"].items():
            user_vec[axis] = user_vec.get(axis, 0.0) + delta

    print()
    _apply_onboarding_vector(
        user_vec, also_print=True,
        force=getattr(args, "force", False),
    )


def _apply_pet(pet_profile: dict, params: dict, *, also_print: bool = False,
               force: bool = False) -> None:
    """User picked a pet by name; persist the choice (personality.txt
    + rules tell + show suggested aime start). Used by --pick mode."""
    import time as _t
    preset_name = pet_profile["preset"]
    persona_text = PERSONALITY_PRESETS[preset_name]

    if also_print:
        print(f"\n\u2728 You picked {pet_profile['emoji']} {pet_profile['name']} "
              f"({preset_name})")
        print(f"   {pet_profile['tagline']}")

    # Backup existing personality.txt if present and different
    if PERSONALITY_FILE.exists() and not force:
        existing = PERSONALITY_FILE.read_text().strip()
        if existing and existing != persona_text.strip():
            bak = PERSONALITY_FILE.with_name(
                f"personality.txt.bak-onboard-{int(_t.time())}"
            )
            bak.write_text(existing)
            if also_print:
                print(f"   \u2139\ufe0f  backed up existing personality to {bak.name}")
                print(f"      to restore: cp {bak} {PERSONALITY_FILE}")
    PERSONALITY_FILE.write_text(persona_text)
    if also_print:
        print(f"   \u2713 wrote {PERSONALITY_FILE} (preset: {preset_name})")

    # Rules tell to daemon
    rules_msg = (
        f"user picked pet: {pet_profile['name']} ({preset_name}); "
        f"max trade size ${params['trade_size_usd']:.0f}; "
        f"interval {params['interval_seconds']}s; "
        f"stop-loss {params['stop_loss_pct']:+.2f}; "
        f"take-profit {params['take_profit_pct']:+.2f}"
    )
    try:
        _chat_call("tell", content=rules_msg, source="onboarding",
                   tags=["rules", "pet-picked"])
        if also_print:
            print(f"   \u2713 rules saved via daemon")
    except Exception:
        _append_jsonl(INBOX_FILE, {
            "kind": "instruct", "content": rules_msg,
            "source": "onboarding", "tags": ["rules", "pet-picked"],
        })
        if also_print:
            print(f"   \u2713 rules queued (daemon not running yet)")

    if also_print:
        print(f"\n\u2705 done. Suggested next:")
        print(f"   aime start --no-trade                  (manual trading)")
        print(f"   aime start --amount {params['trade_size_usd']:.0f} \\")
        print(f"              --interval {params['interval_seconds']} \\")
        print(f"              --stop-loss {params['stop_loss_pct']:.2f} \\")
        print(f"              --take-profit {params['take_profit_pct']:.2f}  (autonomous)")


def _apply_onboarding_vector(
    user_vec: dict,
    *,
    also_print: bool = False,
    force: bool = False,
) -> None:
    """Pick best preset for this user vector, derive trade params, and
    persist everything (personality.txt + rules tell).

    If personality.txt already exists with non-default content and
    `force` is False, we back it up to personality.txt.bak-onboard-<ts>
    before overwriting. Loud about it so the user sees what happened."""
    import time as _t
    preset, ranked = _derive_preset(user_vec)
    params = _derive_trade_params(user_vec)

    if also_print:
        print("\U0001f9ed Your vector:")
        for axis, val in user_vec.items():
            bar = "+" * max(0, int(round(val * 5))) + "-" * max(0, -int(round(val * 5)))
            print(f"   {axis:8s} {val:+.2f}  {bar}")
        print()
        print(f"\U0001f3ad Best-fit style: \u2728 {preset}")
        for name, score in ranked[1:3]:
            print(f"   runner-up: {name} ({score:+.2f})")

    # 1. personality preset → ~/.aime/personality.txt (with backup)
    persona_text = PERSONALITY_PRESETS[preset]
    if PERSONALITY_FILE.exists() and not force:
        existing = PERSONALITY_FILE.read_text().strip()
        # Only back up if the existing content is meaningfully different
        # (not the same preset, not empty)
        if existing and existing != persona_text.strip():
            bak = PERSONALITY_FILE.with_name(
                f"personality.txt.bak-onboard-{int(_t.time())}"
            )
            bak.write_text(existing)
            if also_print:
                print(f"   \u2139\ufe0f  backed up existing personality to {bak.name}")
                print(f"      to restore: cp {bak} {PERSONALITY_FILE}")
    PERSONALITY_FILE.write_text(persona_text)
    if also_print:
        print(f"   \u2713 wrote {PERSONALITY_FILE} (preset: {preset})")

    # 2. risk params → tell (long-lived intel daemon uses every decision)
    rules_msg = (
        f"user trading style: {preset}; "
        f"max trade size ${params['trade_size_usd']:.0f}; "
        f"interval {params['interval_seconds']}s; "
        f"stop-loss {params['stop_loss_pct']:+.2f}; "
        f"take-profit {params['take_profit_pct']:+.2f}"
    )
    try:
        _chat_call("tell", content=rules_msg, source="onboarding", tags=["rules", "style"])
        if also_print:
            print(f"   \u2713 rules saved via daemon")
    except Exception:
        _append_jsonl(INBOX_FILE, {
            "kind": "instruct", "content": rules_msg,
            "source": "onboarding", "tags": ["rules", "style"],
        })
        if also_print:
            print(f"   \u2713 rules queued (daemon not running yet)")

    if also_print:
        print()
        print(f"\u2705 done. Suggested next:")
        print(f"   aime start --no-trade                  (manual trading)")
        print(f"   aime start --amount {params['trade_size_usd']:.0f} \\")
        print(f"              --interval {params['interval_seconds']} \\")
        print(f"              --stop-loss {params['stop_loss_pct']:.2f} \\")
        print(f"              --take-profit {params['take_profit_pct']:.2f}  (autonomous)")
        print()
        print(f"   Adjust style anytime:  aime personality set <preset>")


def main() -> None:


    parser = build_parser()
    args = parser.parse_args()
    # Best-effort, non-blocking update notice (skipped on version/update/--json)
    try:
        _maybe_check_update()
    except Exception:
        pass
    try:
        args.func(args)
    except KeyboardInterrupt:
        sys.stderr.write("\nAborted.\n")
        sys.exit(130)


if __name__ == "__main__":
    main()
