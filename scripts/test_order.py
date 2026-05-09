"""Small real-money order test: market buy 5000 KRW of BTC, then immediately sell."""
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bithumb.client import BithumbClient


def main():
    client = BithumbClient()
    market = "KRW-BTC"
    buy_krw = 5000

    # 잔고 확인
    accounts = client.get_accounts()
    krw = next((float(a["balance"]) for a in accounts if a["currency"] == "KRW"), 0)
    print(f"보유 KRW: {krw:,.0f}원")

    if krw < buy_krw:
        print(f"잔고 부족: {buy_krw:,}원 필요")
        return

    # --- 시장가 매수 ---
    print(f"\n[매수] {market} {buy_krw:,}원 시장가 주문...")
    buy_result = client.market_buy(market, buy_krw)
    print(f"매수 결과: {buy_result}")

    order_uuid = buy_result.get("uuid")
    if not order_uuid:
        print("UUID 없음 — 주문 실패")
        return

    # 체결 대기
    print("체결 대기 중...")
    for _ in range(10):
        time.sleep(1)
        order = client.get_order(order_uuid)
        state = order.get("state")
        print(f"  상태: {state}")
        if state == "done":
            vol = float(order.get("executed_volume", 0))
            print(f"  체결 수량: {vol:.8f} BTC")
            break
    else:
        print("  10초 내 미체결 — 취소 시도")
        client.cancel_order(order_uuid)
        return

    # --- 즉시 시장가 매도 ---
    if vol > 0:
        print(f"\n[매도] {vol:.8f} BTC 시장가 매도...")
        sell_result = client.market_sell(market, vol)
        print(f"매도 결과: {sell_result}")

        sell_uuid = sell_result.get("uuid")
        for _ in range(10):
            time.sleep(1)
            order = client.get_order(sell_uuid)
            if order.get("state") == "done":
                print("매도 체결 완료!")
                break

    print("\n테스트 완료.")


if __name__ == "__main__":
    main()
