# PROJECT_STATE.md
_최초 작성: 2026-05-09_

## 현재 단계
**Step 1 진행 중** — 프로젝트 뼈대 + 빗썸 REST API 클라이언트

## 완료된 작업
- 프로젝트 디렉토리 구조 생성
- CLAUDE.md, config.yaml, requirements.txt, .gitignore 생성

## 다음 작업
- bithumb/client.py: 빗썸 REST API 클라이언트 (공개 API + 인증 API)
- scripts/check_balance.py: 잔고 조회 테스트

## 알려진 이슈
- 빗썸 API 키 미발급 상태 → 공개 API만 먼저 구현 후, 키 발급 시 Private API 연동
