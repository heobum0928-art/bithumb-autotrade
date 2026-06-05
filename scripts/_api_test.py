import sys
sys.stdout.reconfigure(encoding='utf-8')
from bithumb.client import BithumbClient

try:
    client = BithumbClient()
    accounts = client.get_accounts()
    for a in accounts:
        if a['currency'] == 'KRW':
            print(f"KRW 잔고: {float(a['balance']):,.0f}원")
    print("API 연결 정상")
except Exception as e:
    print(f"API 오류: {e}")
