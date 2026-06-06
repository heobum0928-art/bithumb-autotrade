# Phase 04: VB Trader — Discussion Log

> **Audit trail only.** Decisions are in CONTEXT.md.

**Date:** 2026-06-06
**Phase:** 04-vb-trader
**Areas discussed:** 대상 코인, 진입금액, TP/SL, 봇 독립성, K값, 자정 청산

---

## 대상 코인

| Option | Description | Selected |
|--------|-------------|----------|
| 거래량 상위 고정 목록 | 일 거래량 20억+ 코인 20~30개 | ✓ |
| 전체 461개 | 모든 코인 모니터링 | |
| BTC/ETH 등 대형만 | 상위 10개 안정 코인 | |

**선택:** 거래량 상위 고정 목록 (20억+ KRW)

---

## 진입 금액

| Option | Selected |
|--------|----------|
| 10만원 | ✓ |
| 20만원 | |
| 50만원 | |

---

## TP / SL

| Option | Selected |
|--------|----------|
| TP +3% / SL -2% | ✓ |
| TP +5% / SL -2% | |
| 트레일링만 | |

---

## 봇 독립성

| Option | Selected |
|--------|----------|
| 완전 분리 — 독립 프로세스 | ✓ |
| alt_monitor에 모듈 통합 | |

---

## K값

| Option | Selected |
|--------|----------|
| K = 0.5 | ✓ |
| K = 0.3 | |
| K = 0.7 | |

---

## 자정 청산

| Option | Selected |
|--------|----------|
| 00:00 KST 강제 청산 | ✓ |
| 다음 날도 유지 | |
