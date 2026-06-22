"""점화 ML 알림 봇 (알림 전용, 주문 없음).
점화(+3%/거래량2.5배) 감지 → 학습모델(data/igniter_model.pkl)이 '이어질 확률' 점수 →
P>=ALERT_P 이면 텔레그램 '★고확신 점화'. 전부 CSV 로그로 forward 추적(나중에 모델 재학습).
근거: #31 — 모델 P>=0.7 선별이 out-of-sample +0.58% (전체 -0.85%), 순열검정 통과.

★ 호가 캡처 (2026-06-22 신설 — "고해상도 센서"): 봉(OHLCV)으론 '매집 vs 덤프'가 똑같이
보이는 게 30개 손규칙·거래량전략 실패의 근본원인. 점화 *순간* 호가창 깊이불균형 +
최근 체결 매수/매도비를 data/micro_events.csv에 별도 누적 → 수주 뒤 ML에 microstructure
피처로 주입해 선별 승률(38%→43% 양수전환선)을 넘는지 데이터로 판정. 생존편향 0(forward).

Run: python scripts/igniter_alert.py
"""
import sys, time, json, pickle, logging, statistics as st, csv
from datetime import datetime, timezone, timedelta
from pathlib import Path
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bithumb.client import BithumbClient
from bithumb import notify

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
IG, VM = 0.03, 2.5         # 학습 모델과 동일 점화 기준
ALERT_P = 0.65             # 이 확률 이상만 텔레그램 알림 (관찰: 0.7 너무 드물어 0.65)
TOPN = 80
COOLDOWN_MIN = 30
CYCLE_SEC = 45
STABLE = {"USDT","USDC","DAI","TUSD","BUSD","FDUSD","PYUSD","USDS"}
LOGCSV = ROOT/"data"/"igniter_events.csv"
MICROCSV = ROOT/"data"/"micro_events.csv"

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [IGNITE] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("logs/igniter_alert.log", encoding="utf-8")])
log = logging.getLogger(__name__)

M = pickle.load(open(ROOT/"data"/"igniter_model.pkl","rb"))
MODEL, FEATS = M["model"], M["feats"]


def watchlist(c):
    try:
        t=c.get_ticker("ALL"); rows=[]
        for coin,d in t.items():
            if coin=="date" or coin in STABLE: continue
            try: v=float(d.get("acc_trade_value_24H",0))
            except: continue
            rows.append((coin,v))
        rows.sort(key=lambda x:-x[1]); return [x[0] for x in rows[:TOPN]]
    except Exception as e:
        log.warning(f"watchlist 실패: {e}"); return []


def btc_absmove(c, K=12):
    try:
        k=c.get_candles("KRW-BTC", unit=5, count=K+2)
        if len(k)<K+1: return 0.0
        cl=[x["trade_price"] for x in k]  # newest first
        return abs(cl[0]/cl[K]-1)
    except Exception:
        return 0.0


def feats(k_rev, i, btcmove):
    """k_rev = oldest→newest. 점화바 i 기준 12특징 (학습과 동일 순서)."""
    cl=[x["trade_price"] for x in k_rev]; hi=[x["high_price"] for x in k_rev]
    lo=[x["low_price"] for x in k_rev]; op=[x["opening_price"] for x in k_rev]
    vol=[x.get("candle_acc_trade_volume",0) for x in k_rev]; tk=k_rev[i]["candle_date_time_kst"]
    rng=max(hi[i-48:i])-min(lo[i-48:i]) or 1e-9; br=hi[i]-lo[i] or 1e-9
    gs=0
    for kk in range(i,max(i-10,0),-1):
        if cl[kk]>op[kk]: gs+=1
        else: break
    rets=[cl[kk]/cl[kk-1]-1 for kk in range(i-12,i) if cl[kk-1]>0]
    return [cl[i]/op[i]-1, cl[i]/cl[i-3]-1, vol[i]/(sum(vol[i-20:i])/20),
            cl[i]/cl[i-12]-1, cl[i]/cl[i-48]-1, st.pstdev(rets) if len(rets)>1 else 0,
            (cl[i]-min(lo[i-48:i]))/rng, gs, (cl[i]-op[i])/br, (hi[i]-cl[i])/br, btcmove, int(tk[11:13])]


def logrow(row):
    new = not LOGCSV.exists()
    with open(LOGCSV,"a",newline="",encoding="utf-8") as f:
        w=csv.writer(f)
        if new: w.writerow(["time","coin","entry","prob","bar%","vol_mult"])
        w.writerow(row)


def micro_snapshot(c, coin, levels=15, ntrades=50):
    """점화 순간 호가창+체결흐름 스냅샷 — '매집 vs 덤프' 구분용. 실패 시 None(봇 안 깨지게)."""
    try:
        ob = c.get_orderbook(coin)
        bids = [(float(b["price"]), float(b["quantity"])) for b in ob.get("bids", [])]
        asks = [(float(a["price"]), float(a["quantity"])) for a in ob.get("asks", [])]
        if not bids or not asks: return None
        best_bid = max(p for p,_ in bids); best_ask = min(p for p,_ in asks)
        mid = (best_bid+best_ask)/2 or 1e-9
        spread_pct = (best_ask-best_bid)/mid*100
        bids_s = sorted(bids, key=lambda x:-x[0])[:levels]   # 매수 상위(높은가)
        asks_s = sorted(asks, key=lambda x:x[0])[:levels]    # 매도 상위(낮은가)
        bid_krw = sum(p*q for p,q in bids_s); ask_krw = sum(p*q for p,q in asks_s)
        tot = bid_krw+ask_krw or 1e-9
        depth_imb = (bid_krw-ask_krw)/tot                    # +매수벽 우세(지지), -매도벽 우세(저항)
        bid_wall = (max(p*q for p,q in bids_s)/bid_krw) if bid_krw>0 else 0   # 단일벽 집중도
        ask_wall = (max(p*q for p,q in asks_s)/ask_krw) if ask_krw>0 else 0
    except Exception:
        return None
    buy_ratio = -1.0
    try:
        th = c.get_transaction_history(coin, count=ntrades)
        buy=sell=0.0
        for t in th:
            val=float(t["units_traded"])*float(t["price"])
            if t.get("type")=="bid": buy+=val   # 빗썸 'bid'=매수체결
            else: sell+=val
        if buy+sell>0: buy_ratio = buy/(buy+sell)            # 최근 체결 매수비중(>0.5=사는중)
    except Exception:
        pass
    return {"spread_pct":round(spread_pct,4), "bid_krw":int(bid_krw), "ask_krw":int(ask_krw),
            "depth_imb":round(depth_imb,4), "bid_wall":round(bid_wall,4),
            "ask_wall":round(ask_wall,4), "buy_ratio":round(buy_ratio,4)}


def logmicro(time_s, coin, entry, prob, m):
    new = not MICROCSV.exists()
    with open(MICROCSV,"a",newline="",encoding="utf-8") as f:
        w=csv.writer(f)
        if new: w.writerow(["time","coin","entry","prob","spread_pct","bid_krw","ask_krw","depth_imb","bid_wall","ask_wall","buy_ratio"])
        w.writerow([time_s,coin,entry,prob,m["spread_pct"],m["bid_krw"],m["ask_krw"],m["depth_imb"],m["bid_wall"],m["ask_wall"],m["buy_ratio"]])


def main():
    import numpy as np
    c=BithumbClient(); wl=watchlist(c); last={}; wl_day=datetime.now(KST).date()
    log.info(f"ML 점화알림 시작 — 감시{len(wl)} | 점화+{IG*100:.0f}%/거래량{VM}배 | 모델 P>={ALERT_P} 알림")
    try: notify.send(f"🤖 ML점화봇 시작 — 점화 감지하면 모델이 '이어질 확률' 채점, P>={ALERT_P}만 알림(★고확신). #31 모델: 선별시 +0.58%(전체-0.85%)")
    except Exception as e: log.warning(f"TG시작 실패: {e}")
    while True:
        try:
            if datetime.now(KST).date()!=wl_day:
                wl_day=datetime.now(KST).date(); wl=watchlist(c)
            bm=btc_absmove(c); now=datetime.now(KST)
            for coin in wl:
                cd=last.get(coin)
                if cd and (now-cd).total_seconds()<COOLDOWN_MIN*60: continue
                try: k=c.get_candles(f"KRW-{coin}", unit=5, count=62)
                except Exception: continue
                if not isinstance(k,list) or len(k)<62: continue
                kr=k[::-1]            # oldest→newest
                i=len(kr)-2          # 직전 완성봉 = 점화 후보
                cl=[x["trade_price"] for x in kr]; op=[x["opening_price"] for x in kr]; vol=[x.get("candle_acc_trade_volume",0) for x in kr]
                bar=cl[i]/op[i]-1 if op[i]>0 else 0; avgv=sum(vol[i-20:i])/20
                if not (bar>=IG and avgv>0 and vol[i]>=avgv*VM):
                    time.sleep(0.1); continue
                try:
                    prob=float(MODEL.predict_proba(np.array([feats(kr,i,bm)]))[0,1])
                except Exception as e:
                    log.warning(f"{coin} 채점실패: {e}"); time.sleep(0.1); continue
                entry=cl[i]; mult=vol[i]/avgv
                ts=now.strftime("%Y-%m-%d %H:%M")
                logrow([ts,coin,f"{entry:.4f}",f"{prob:.2f}",f"{bar*100:.1f}",f"{mult:.0f}"])
                m=micro_snapshot(c, coin)   # ★ 점화 순간 호가창+체결흐름 캡처
                if m:
                    logmicro(ts,coin,f"{entry:.4f}",f"{prob:.2f}",m)
                    log.info(f"{coin} 호가스냅 — 깊이불균형{m['depth_imb']:+.2f} 매수체결비{m['buy_ratio']:.2f} 스프레드{m['spread_pct']:.2f}%")
                last[coin]=now   # 감지 즉시 쿨다운 — 같은 점화 중복 기록/알림 방지
                if prob>=ALERT_P:
                    msg=(f"★ {coin} 고확신 점화 (모델 {prob*100:.0f}%)\n"
                         f"   +{bar*100:.1f}% 거래량{mult:.0f}배, 진입가 {entry:,.4f}원 ({now:%H:%M})\n"
                         f"   → 모델이 '이어질것'으로 봄. 차트확인 후 모의매도 연습")
                    log.info(msg.replace(chr(10)," | "))
                    try: notify.send(msg)
                    except Exception as e: log.warning(f"TG실패: {e}")
                    last[coin]=now
                time.sleep(0.12)
        except KeyboardInterrupt: log.info("종료"); break
        except Exception as e: log.error(f"루프오류: {e}")
        time.sleep(CYCLE_SEC)


if __name__=="__main__":
    main()
