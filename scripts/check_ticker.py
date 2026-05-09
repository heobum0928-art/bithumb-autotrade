"""Public API test - no API key needed."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bithumb.client import BithumbClient


def main():
    client = BithumbClient()

    print("=== BTC/KRW 현재가 ===")
    btc = client.get_ticker("BTC")
    print(f"  현재가: {int(float(btc['closing_price'])):,} 원")
    print(f"  전일 대비: {btc['fluctate_rate_24H']}%")

    print("\n=== 전체 상장 코인 목록 ===")
    coins = client.get_all_coins()
    print(f"  총 {len(coins)}개: {sorted(coins)}")


if __name__ == "__main__":
    main()
