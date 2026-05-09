"""System status check — balance, markets, order API dry-run."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bithumb.client import BithumbClient


def main():
    client = BithumbClient()

    # 1. 잔고
    print("=== 보유 자산 ===")
    accounts = client.get_accounts()
    for a in accounts:
        bal = float(a["balance"])
        if bal > 0:
            if a["currency"] == "KRW":
                print(f"  KRW: {bal:,.0f}원")
            else:
                avg = float(a["avg_buy_price"])
                print(f"  {a['currency']}: {bal:.6f}  (평균매수가 {avg:,.0f}원)")

    # 2. 마켓 수
    print("\n=== 마켓 현황 ===")
    coins = client.get_all_coins_v2()
    print(f"  KRW 마켓 상장 코인: {len(coins)}개")

    # 3. 주문 가능 정보 (BTC)
    print("\n=== BTC 주문 가능 정보 ===")
    chance = client.get_order_chance("KRW-BTC")
    print(f"  매수 가능 KRW: {float(chance['bid_account']['balance']):,.0f}원")
    print(f"  최소 주문금액: {int(float(chance['market']['bid']['min_total'])):,}원")

    # 4. 공개 API 코인 수 vs API 2.0 코인 수 비교
    print("\n=== 공개 API 코인 수 비교 ===")
    old_coins = client.get_all_coins()
    print(f"  공개 티커 API: {len(old_coins)}개")
    print(f"  API 2.0 마켓: {len(coins)}개")
    diff = old_coins - coins
    if diff:
        print(f"  공개 API에만 있는 코인: {sorted(diff)}")

    print("\n모든 API 정상 동작 확인 완료!")


if __name__ == "__main__":
    main()
