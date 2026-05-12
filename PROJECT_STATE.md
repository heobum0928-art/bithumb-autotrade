# PROJECT_STATE.md
_최종 업데이트: 2026-05-12_

## 봇 현재 설정
| 파라미터 | 값 |
|---------|-----|
| TP_HALF | 2% |
| TRAIL_PCT | 2% / TIGHT 1.5% |
| INITIAL_STOP_PCT | -3% |
| HOLD_MIN_SEC | 600 (수익구간 조기트레일 적용) |
| 확인 딜레이 | 30초 |
| 선진입(PRE) | OFF |
| 신규상장 감지 | ON |

## 현재 상태
- 봇 실행 중 (PID: data/bot.lock)
- 포지션 없음
- 쿨다운: MOVE(~05-14), RAD(~05-15), YGG(~05-15)

## 참조 문서
- 전략/설계 이유 → `docs/STRATEGY.md`
- 누적 성과 → `docs/PERFORMANCE.md`
- 당일 변경사항 → `docs/sessions/YYYY-MM-DD.md`
- 분석 → `python scripts/signal_stats.py`
