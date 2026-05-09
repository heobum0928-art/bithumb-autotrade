import hashlib
import time
import uuid
import urllib.parse
import requests
import yaml
import jwt
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_URL = "https://api.bithumb.com"


class BithumbClient:
    def __init__(self, config_path: str = "config.yaml"):
        cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
        self._api_key = cfg["bithumb"]["api_key"]
        self._api_secret = cfg["bithumb"]["api_secret"]
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})

    # ------------------------------------------------------------------
    # Public API (no auth) — legacy endpoints still active
    # ------------------------------------------------------------------

    def get_ticker(self, coin: str = "ALL") -> dict:
        """Return current price info. coin='ALL' returns every listed coin."""
        resp = self._session.get(f"{BASE_URL}/public/ticker/{coin.upper()}_KRW")
        resp.raise_for_status()
        data = resp.json()
        if data["status"] != "0000":
            raise RuntimeError(f"get_ticker error: {data}")
        return data["data"]

    def get_all_coins(self) -> set[str]:
        """Return the set of coin symbols currently listed on Bithumb."""
        ticker = self.get_ticker("ALL")
        return {k for k in ticker if k != "date"}

    def get_orderbook(self, coin: str) -> dict:
        """Return order book for a specific coin."""
        resp = self._session.get(f"{BASE_URL}/public/orderbook/{coin.upper()}_KRW")
        resp.raise_for_status()
        data = resp.json()
        if data["status"] != "0000":
            raise RuntimeError(f"get_orderbook error: {data}")
        return data["data"]

    def get_transaction_history(self, coin: str, count: int = 20) -> list:
        """Return recent trades for a specific coin."""
        resp = self._session.get(
            f"{BASE_URL}/public/transaction_history/{coin.upper()}_KRW",
            params={"count": count},
        )
        resp.raise_for_status()
        data = resp.json()
        if data["status"] != "0000":
            raise RuntimeError(f"get_transaction_history error: {data}")
        return data["data"]

    # ------------------------------------------------------------------
    # Private API (API 2.0 — JWT HS256 auth)
    # ------------------------------------------------------------------

    def _make_token(self, query_params: dict = None) -> str:
        """Generate JWT Bearer token for API 2.0 private calls."""
        payload = {
            "access_key": self._api_key,
            "nonce": str(uuid.uuid4()),
            "timestamp": round(time.time() * 1000),
        }
        if query_params:
            query_string = urllib.parse.urlencode(query_params).encode()
            query_hash = hashlib.sha512(query_string).hexdigest()
            payload["query_hash"] = query_hash
            payload["query_hash_alg"] = "SHA512"
        token = jwt.encode(payload, self._api_secret, algorithm="HS256")
        return f"Bearer {token}"

    def _get(self, path: str, params: dict = None) -> dict:
        headers = {"Authorization": self._make_token(params)}
        resp = self._session.get(f"{BASE_URL}{path}", params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()

    def _post_v2(self, path: str, body: dict = None) -> dict:
        headers = {
            "Authorization": self._make_token(body),
            "Content-Type": "application/json",
        }
        resp = self._session.post(f"{BASE_URL}{path}", json=body, headers=headers)
        resp.raise_for_status()
        return resp.json()

    def _delete(self, path: str, params: dict = None) -> dict:
        headers = {"Authorization": self._make_token(params)}
        resp = self._session.delete(f"{BASE_URL}{path}", params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Private API — account / balance
    # ------------------------------------------------------------------

    def get_accounts(self) -> list:
        """Return all asset balances (API 2.0: GET /v1/accounts)."""
        return self._get("/v1/accounts")

    def get_balance(self, coin: str = None) -> dict | list:
        """Return balance. If coin given, filter to that coin + KRW."""
        accounts = self.get_accounts()
        if coin is None:
            return accounts
        coin = coin.upper()
        return [a for a in accounts if a.get("currency") in (coin, "KRW")]

    # ------------------------------------------------------------------
    # Private API — orders
    # ------------------------------------------------------------------

    def market_buy(self, market: str, amount_krw: float) -> dict:
        """Market buy. market format: 'KRW-BTC'."""
        body = {
            "market": market.upper(),
            "side": "bid",
            "ord_type": "price",
            "price": str(amount_krw),
        }
        return self._post_v2("/v1/orders", body)

    def market_sell(self, market: str, volume: float) -> dict:
        """Market sell. market format: 'KRW-BTC'."""
        body = {
            "market": market.upper(),
            "side": "ask",
            "ord_type": "market",
            "volume": f"{volume:.8f}",  # avoid scientific notation
        }
        return self._post_v2("/v1/orders", body)

    def cancel_order(self, order_uuid: str) -> dict:
        """Cancel an open order by UUID."""
        return self._delete("/v1/order", {"uuid": order_uuid})

    def get_order(self, order_uuid: str) -> dict:
        """Get order status by UUID."""
        return self._get("/v1/order", {"uuid": order_uuid})

    def get_order_chance(self, market: str) -> dict:
        """Return available amounts and minimum order info for a market."""
        return self._get("/v1/orders/chance", {"market": market.upper()})

    # ------------------------------------------------------------------
    # Public API — market info (API 2.0)
    # ------------------------------------------------------------------

    def get_markets(self) -> list[dict]:
        """Return all KRW markets. Each item has 'market', 'korean_name', 'english_name'."""
        resp = self._session.get(f"{BASE_URL}/v1/market/all", params={"isDetails": "false"})
        resp.raise_for_status()
        return resp.json()

    def get_all_coins_v2(self) -> set[str]:
        """Return coin symbols from API 2.0 market list (e.g. {'BTC', 'ETH', ...})."""
        markets = self.get_markets()
        return {m["market"].split("-")[1] for m in markets if m["market"].startswith("KRW-")}
