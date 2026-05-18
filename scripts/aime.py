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
    total = resp.get("total", len(markets)) if isinstance(resp, dict) else len(markets)
    print(f"📊 Markets ({len(markets)} of {total})")
    print()
    for m in markets:
        q = m.get("question", "")
        if len(q) > 80:
            q = q[:77] + "..."
        yes = fmt_pct(m.get("yes_price"))
        vol = fmt_usd(m.get("total_volume"))
        end = (m.get("end_time") or "")[:16].replace("T", " ")
        print(f"  • {m.get('id')}")
        print(f"    {q}")
        print(f"    YES {yes}  |  vol {vol}  |  ends {end}")
        print()


def cmd_market(args: argparse.Namespace) -> None:
    resp = http("GET", f"/markets/{args.market_id}", json_mode=args.json)
    if args.json:
        emit_json(resp)
        return
    if not isinstance(resp, dict):
        print(resp)
        return
    print(f"📊 {resp.get('question')}")
    print(f"   ID:       {resp.get('id')}")
    print(f"   Category: {resp.get('category')}")
    print(f"   Status:   {resp.get('status')}")
    print(f"   Ends:     {resp.get('end_time')}")
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
    body: dict[str, Any] = {
        "position": args.position.upper(),
        "reasoning": args.reasoning,
    }
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

    resp = http("POST", path, api_key=creds["api_key"], body=body, json_mode=args.json)

    if args.json:
        emit_json(resp)
        return

    verb = "Sold" if sell else "Bought"
    if isinstance(resp, dict):
        shares = resp.get("shares_received") or resp.get("shares_sold") or args.amount
        price = resp.get("price_at_trade")
        fee = resp.get("fee_amount")
        net = resp.get("net_amount") or resp.get("payout")
        print(f"✅ {verb} {args.position.upper()} on {args.market_id}")
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
    print(f"📂 Positions ({len(positions)})\n")
    for p in positions:
        shares = p.get("total_shares") or p.get("shares")
        spent = p.get("total_spent")
        cur_price = p.get("current_price") or p.get("avg_price")
        value = p.get("current_value")
        pnl = p.get("pnl") if p.get("pnl") is not None else p.get("unrealized_pnl")
        print(f"  • market: {p.get('market_id')}")
        if p.get("market_question"):
            q = p["market_question"]
            if len(q) > 80:
                q = q[:77] + "..."
            print(f"    {q}")
        print(f"    {p.get('position')} shares: {shares:.4f}" if isinstance(shares, (int, float)) else f"    {p.get('position')} shares: {shares}")
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
        print(f"  {ts}  {t.get('position')}  shares={t.get('shares_received') or t.get('shares_sold') or '-'}  "
              f"price={fmt_pct(t.get('price_at_trade'))}  market={t.get('market_id')}")


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
    if not STATUS_FILE.exists():
        msg = "agent not running (no ~/.aime/status.json yet)"
        if args.json: emit_json({"running": False, "reason": msg})
        else: print("\U0001f4a4 " + msg)
        return
    try:
        state = json.loads(STATUS_FILE.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        state = {}
    if args.json:
        emit_json(state); return
    name = state.get("agent_name", "?")
    bal  = state.get("balance")
    bal_s = f"{bal:,.2f}" if isinstance(bal, (int, float)) else str(bal)
    mood = state.get("mood", "?")
    trades = state.get("trades_this_cycle", 0)
    seen   = state.get("markets_seen", 0)
    strat  = state.get("strategy", "-")
    age = _fmt_age(state.get("updated_at", 0))
    print(f"\U0001f436 {name} — {mood} ({age})")
    print(f"   balance: {bal_s}")
    print(f"   strategy: {strat}")
    print(f"   last cycle: {trades} trade(s) over {seen} market(s)")
    if args.verbose:
        for k, v in state.items():
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


def cmd_tell(args: argparse.Namespace) -> None:
    row = _append_jsonl(INBOX_FILE, {
        "kind": "ask" if args.ask else "instruct",
        "content": args.message,
    })
    if args.json:
        emit_json(row); return
    verb = "asked" if args.ask else "told"
    print(f"\u2709\ufe0f  {verb} agent: {args.message}")


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

    sp = sub.add_parser("buy", parents=[json_parent], help="buy YES or NO shares")
    sp.add_argument("market_id")
    sp.add_argument("position", choices=["YES", "NO", "yes", "no"])
    sp.add_argument("amount", type=float, help="USD amount to spend")
    sp.add_argument("reasoning", help="reasoning text (>=10 chars)")
    sp.add_argument("--confidence", type=float, help="0.0-1.0")
    sp.add_argument("--model", help="model identifier, e.g. claude-4")
    sp.add_argument("--sources", nargs="+", help="data sources (space-separated)")
    sp.set_defaults(func=cmd_buy)

    sp = sub.add_parser("sell", parents=[json_parent], help="sell YES or NO shares")
    sp.add_argument("market_id")
    sp.add_argument("position", choices=["YES", "NO", "yes", "no"])
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
    sp.set_defaults(func=cmd_tell)

    sp = sub.add_parser("ask", parents=[json_parent], help="ask your agent a question (alias of `tell --ask`)")
    sp.add_argument("message", help="the question")
    sp.set_defaults(func=cmd_tell, ask=True)

    sp = sub.add_parser("feed", parents=[json_parent], help="recent trade decisions + reflections")
    sp.add_argument("-n", "--limit", type=int, default=10, help="how many of each to show")
    sp.set_defaults(func=cmd_feed)

    # --- v2.2.0 new commands ---

    sp = sub.add_parser("deposit", parents=[json_parent], help="top up agent balance (test/demo USDT)")
    sp.add_argument("amount", type=float, help="amount to deposit")
    sp.set_defaults(func=cmd_deposit)

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


def cmd_deposit(args: argparse.Namespace) -> None:
    """Top up agent balance (test/demo USDT)."""
    creds = load_creds(json_mode=args.json)
    resp = http("POST", "/balance/deposit",
                api_key=creds["api_key"],
                body={"amount": args.amount},
                json_mode=args.json)
    if args.json:
        emit_json(resp)
        return
    new_bal = resp.get("balance_after") if isinstance(resp, dict) else None
    print(f"💸 Deposited {fmt_usd(args.amount)}")
    if new_bal is not None:
        print(f"   new balance: {fmt_usd(new_bal)}")
    tx = (resp or {}).get("id")
    if tx:
        print(f"   tx id: {tx}")


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


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except KeyboardInterrupt:
        sys.stderr.write("\nAborted.\n")
        sys.exit(130)


if __name__ == "__main__":
    main()
