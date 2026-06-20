"""
ML 점화 트레이더 — 전략 #31 (패턴학습 자동매매, 모의 검증).

전략 (#31 검증: 워크포워드 OOS 모델 P>=0.7 선별 +0.58%/t0.63, 순열검정 통과):
  1. 점화 감지: 완성 5분봉 +3%↑ AND 거래량 2.5배↑
  2. 모델 채점: data/igniter_model.pkl 이 '이어질 확률' P 계산 (특징 12개, 룩어헤드 없음)
  3. 진입: P >= ML_P(0.7) 이면 현재가로 진입 (모의)
  4. 청산: 트레일 3%(+1.5% 후 활성) / SL -3% / 타임아웃 4h  (#31 백테스트 청산과 동일)
  5. 5슬롯(같은 코인 1슬롯), 슬롯당 20만원

⚠️ LEAD다(t0.63, 검증 아님). forward 모의로 게이트 판정 후에만 실거래.
사전등록 게이트: CLEAN [ML-DRY] n>=30, 비용0.30%후 평균>0 AND t>=2.5, 베이스라인(전체점화) 초과.
통과 → 사용자 승인 → 소액 실거래(슬롯당 5만 + 거래소 SL + 봇재기동).

Run: python scripts/ml_trader.py --dry-run
포지션: data/ml_pos.json | 로그: logs/ml_trader.log | DB 태그: [ML-DRY]
"""
import sys, os, atexit, time, json, pickle, logging, threading, argparse, socket
import statistics as st
from datetime import datetime, timedelta, timezone
from pathlib import Path
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
KST = timezone(timedelta(hours=9))

_sock = None
def _single():
    global _sock
    _sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    try:
        _sock.bind(("127.0.0.1", 47223))   # rt=47221, em=47222
    except OSError:
        print("[ERROR] ml_trader 이미 실행 중 (포트 47223)."); sys.exit(1)
    atexit.register(_sock.close)
_single()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import numpy as np
from bithumb.client import BithumbClient
from bithumb.db import log_trade
from bithumb import notify

def _args():
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--dry-run", action="store_true"); p.add_argument("--live", action="store_true")
    a,_ = p.parse_known_args(); return a
_DRY = not _args().live
_TAG = "ML-DRY" if _DRY else "ML"

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format=f"%(asctime)s [{_TAG}][%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("logs/ml_trader.log", encoding="utf-8")])
log = logging.getLogger(__name__)

# ── 전략 상수 ──
# 데이터 수집 모드(2026-06-21): 모의라 거침없이 표본 빨리. 거래는 P>=0.5로 많이 하되 확신도 기록,
# 게이트 판정은 P>=0.7 부분집합으로 엄격 유지. 청산패키지(트레일3/SL3/4h)는 #31 검증값 동결.
IG, VM = 0.025, 2.0
ML_P = 0.50            # 거래 임계(데이터수집). 게이트는 P>=0.7 부분집합으로 본다
ML_GATE_P = 0.70
TRAIL, TRAIL_ACT, SL = 0.03, 0.015, -0.03
TIMEOUT_H = 4
SLOTS, ENTRY_KRW = 8, 200_000
TOPN = 120
COOLDOWN_MIN = 20
SCAN_SEC = 45          # 점화 스캔 주기
LOOP_SEC = 5           # 청산 감시 주기
STABLE = {"USDT","USDC","DAI","TUSD","BUSD","FDUSD","PYUSD","USDS"}
WS_URL = "wss://pubwss.bithumb.com/pub/ws"
POS_PATH = Path("data/ml_pos.json")

M = pickle.load(open(ROOT/"data"/"igniter_model.pkl","rb"))
MODEL = M["model"]


def build_universe(c):
    try:
        t=c.get_ticker("ALL"); rows=[]
        for coin,d in t.items():
            if coin=="date" or coin in STABLE: continue
            try: v=float(d.get("acc_trade_value_24H",0))
            except: continue
            rows.append((coin,v))
        rows.sort(key=lambda x:-x[1]); return [x[0] for x in rows[:TOPN]]
    except Exception as e:
        log.warning(f"유니버스 실패: {e}"); return []


def btc_absmove(c, K=12):
    try:
        k=c.get_candles("KRW-BTC", unit=5, count=K+2)
        if len(k)<K+1: return 0.0
        cl=[x["trade_price"] for x in k]; return abs(cl[0]/cl[K]-1)
    except Exception: return 0.0


def feats(kr, i, btcmove):
    cl=[x["trade_price"] for x in kr]; hi=[x["high_price"] for x in kr]; lo=[x["low_price"] for x in kr]
    op=[x["opening_price"] for x in kr]; vol=[x.get("candle_acc_trade_volume",0) for x in kr]; tk=kr[i]["candle_date_time_kst"]
    rng=max(hi[i-48:i])-min(lo[i-48:i]) or 1e-9; br=hi[i]-lo[i] or 1e-9
    gs=0
    for k in range(i,max(i-10,0),-1):
        if cl[k]>op[k]: gs+=1
        else: break
    rets=[cl[k]/cl[k-1]-1 for k in range(i-12,i) if cl[k-1]>0]
    return [cl[i]/op[i]-1, cl[i]/cl[i-3]-1, vol[i]/(sum(vol[i-20:i])/20), cl[i]/cl[i-12]-1, cl[i]/cl[i-48]-1,
            st.pstdev(rets) if len(rets)>1 else 0, (cl[i]-min(lo[i-48:i]))/rng, gs,
            (cl[i]-op[i])/br, (hi[i]-cl[i])/br, btcmove, int(tk[11:13])]

# ── 포지션 I/O (retest 패턴) ──
def load_json(path):
    if not path.exists(): return None
    try:
        d=json.loads(path.read_text(encoding="utf-8")); return d if d else None
    except Exception: return None

def load_positions():
    d=load_json(POS_PATH)
    if not d: return []
    items=[d] if isinstance(d,dict) else (d if isinstance(d,list) else [])
    out=[]
    for p in items:
        if not p or not p.get("coin"): continue
        p.setdefault("highest", p.get("entry_price"))
        p.setdefault("timeout_at", (datetime.now(KST)+timedelta(hours=TIMEOUT_H)).isoformat())
        out.append(p)
    return out

def save_json(path, data):
    path.parent.mkdir(exist_ok=True)
    if not data: path.unlink(missing_ok=True); return
    tmp=path.with_suffix(path.suffix+".tmp"); tmp.write_text(json.dumps(data,default=str),encoding="utf-8"); os.replace(tmp,path)


class PriceTracker:
    def __init__(self): self._l={}; self._lock=threading.Lock(); self._ws=None; self._run=False
    def start(self, syms):
        import websocket as W; self._run=True
        def on_open(ws): ws.send(json.dumps({"type":"ticker","symbols":syms,"tickTypes":["24H"]})); log.info(f"[WS] 구독 {len(syms)}")
        def on_msg(ws,m):
            try:
                d=json.loads(m)
                if d.get("type")!="ticker": return
                c=d.get("content",{}); s=c.get("symbol","")
                if s.endswith("_KRW"):
                    pr=float(c.get("closePrice",0) or 0)
                    if pr>0:
                        with self._lock: self._l[s[:-4]]=pr
            except Exception: pass
        def run():
            while self._run:
                try: ws=W.WebSocketApp(WS_URL,on_open=on_open,on_message=on_msg); self._ws=ws; ws.run_forever(ping_interval=20,ping_timeout=10)
                except Exception as e: log.error(f"[WS] {e}")
                if self._run: time.sleep(5)
        threading.Thread(target=run,daemon=True).start()
    def stop(self): self._run=False;  self._ws and self._ws.close()
    def get(self,coin):
        with self._lock: return self._l.get(coin,0.0)


def record_exit(pos, px, reason):
    vol=pos["volume"]; recv=px*vol; pnl=recv-pos["cost_krw"]; pct=pnl/pos["cost_krw"]*100
    log.warning(f"[{pos['coin']}] 청산 @{px:,.4f} PnL={pct:+.2f}% | {reason}")
    try:
        log_trade(coin=pos["coin"], market=pos["market"], entry_price=pos["entry_price"], exit_price=px,
                  volume=vol, cost_krw=pos["cost_krw"], received_krw=recv,
                  exit_reason=f"[{_TAG}] P{int(pos.get('prob',0)*100)} {reason}",   # 확신도 기록(게이트 분석용)
                  entered_at=datetime.fromisoformat(pos["entered_at"]).replace(tzinfo=None), exited_at=datetime.now(),
                  max_price=pos.get("highest",px))
    except Exception as e: log.error(f"[DB] {e}")
    notify.send(f"[{_TAG}] {pos['coin']} 청산 @{px:,.4f} PnL={pct:+.2f}% | {reason}")


def scan_entries(c, tracker, universe, positions, cooldown, bm):
    held={p["coin"] for p in positions}; now=datetime.now(KST)
    for coin in universe:
        if len(positions)>=SLOTS: break
        if coin in held: continue
        cd=cooldown.get(coin)
        if cd and (now-cd).total_seconds()<COOLDOWN_MIN*60: continue
        try: k=c.get_candles(f"KRW-{coin}", unit=5, count=62)
        except Exception: continue
        if not isinstance(k,list) or len(k)<62: continue
        kr=k[::-1]; i=len(kr)-2
        cl=[x["trade_price"] for x in kr]; op=[x["opening_price"] for x in kr]; vol=[x.get("candle_acc_trade_volume",0) for x in kr]
        bar=cl[i]/op[i]-1 if op[i]>0 else 0; avgv=sum(vol[i-20:i])/20
        if not (bar>=IG and avgv>0 and vol[i]>=avgv*VM): time.sleep(0.1); continue
        try: prob=float(MODEL.predict_proba(np.array([feats(kr,i,bm)]))[0,1])
        except Exception: time.sleep(0.1); continue
        cooldown[coin]=now
        if prob < ML_P: time.sleep(0.1); continue
        cur=tracker.get(coin)
        if cur<=0:
            try: cur=float(c.get_ticker(coin).get("closing_price",0))
            except Exception: cur=0
        if cur<=0: continue
        pos={"coin":coin,"market":f"KRW-{coin}","entry_price":cur,"volume":ENTRY_KRW/cur,"cost_krw":ENTRY_KRW,
             "highest":cur,"entered_at":datetime.now().isoformat(),"prob":round(prob,2),"mock":_DRY,
             "timeout_at":(now+timedelta(hours=TIMEOUT_H)).isoformat()}
        positions.append(pos); held.add(coin); save_json(POS_PATH, positions)
        log.warning(f"[{coin}] ML진입 @{cur:,.4f} P={prob*100:.0f}% 슬롯{len(positions)}/{SLOTS}")
        notify.send(f"[{_TAG}] {coin} 진입 @{cur:,.4f} (모델{prob*100:.0f}%, 트레일3%/SL3%/4h) [{len(positions)}/{SLOTS}]")
        time.sleep(0.1)


def run():
    c=BithumbClient(); tracker=PriceTracker()
    universe=build_universe(c); positions=load_positions(); cooldown={}
    uni_day=datetime.now(KST).date(); last_scan=0.0
    syms=[f"{x}_KRW" for x in universe]+[f"{p['coin']}_KRW" for p in positions]
    if universe: tracker.start(syms); time.sleep(5)
    log.info(f"시작 모드={'DRY' if _DRY else 'LIVE'} | 유니버스{len(universe)} | 점화+{IG*100:.0f}%/{VM}배 모델P>={ML_P} | 포지션{len(positions)}/{SLOTS}")
    notify.send(f"[{_TAG}] ml_trader 시작 — #31 ML점화 모의매매(P>={ML_P} 진입, 트레일3%/SL3%/4h)")
    while True:
        try:
            now=time.time()
            if datetime.now(KST).date()!=uni_day:
                uni_day=datetime.now(KST).date(); universe=build_universe(c)
                tracker.stop(); tracker.start([f"{x}_KRW" for x in universe]+[f"{p['coin']}_KRW" for p in positions]); time.sleep(5)
            if now-last_scan>=SCAN_SEC and len(positions)<SLOTS:
                last_scan=now; scan_entries(c, tracker, universe, positions, cooldown, btc_absmove(c))
            for pos in positions[:]:
                coin=pos["coin"]; cur=tracker.get(coin)
                if cur<=0:
                    try: cur=float(c.get_ticker(coin).get("closing_price",0))
                    except Exception: cur=0
                if cur<=0: continue
                if cur>pos.get("highest",0): pos["highest"]=cur; save_json(POS_PATH, positions)
                entry=pos["entry_price"]; high=pos.get("highest",entry); pnl=(cur-entry)/entry
                gain=(high-entry)/entry; armed=gain>=TRAIL_ACT
                sl_px=entry*(1+SL); trail_px=high*(1-TRAIL)
                timed=datetime.now(KST)>datetime.fromisoformat(pos["timeout_at"])
                if armed and cur<=max(sl_px,trail_px):
                    record_exit(pos, max(sl_px,trail_px), f"트레일3%(고점{gain*100:+.1f}%→{pnl*100:+.1f}%)"); positions.remove(pos); save_json(POS_PATH,positions)
                elif not armed and pnl<=SL:
                    record_exit(pos, sl_px, f"SL-3%({pnl*100:+.1f}%)"); positions.remove(pos); save_json(POS_PATH,positions)
                elif timed:
                    record_exit(pos, cur, f"타임아웃4h({pnl*100:+.1f}%)"); positions.remove(pos); save_json(POS_PATH,positions)
        except KeyboardInterrupt: tracker.stop(); break
        except Exception as e: log.error(f"루프오류: {e}", exc_info=True)
        time.sleep(LOOP_SEC)


def main():
    if not _DRY:
        log.error("LIVE는 게이트(CLEAN n>=30, 비용0.30%후 t>=2.5, 베이스라인 초과) 통과 + 사용자 승인 필요."); sys.exit(1)
    log.info(f"ml_trader 시작 — 점화ML P>={ML_P} 진입 / 트레일3%(+1.5%)·SL3%·4h / 진입 {ENTRY_KRW:,}원")
    run()

if __name__=="__main__":
    from bithumb.db import init_db; init_db(); main()
