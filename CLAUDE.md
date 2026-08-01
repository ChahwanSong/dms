# CLAUDE.md

Guidance for Claude Code (claude.ai/code) working in this repository.

DMS를 **clean-slate로 재구현**하는 저장소다. 새 구현은 저장소 루트에서 시작한다.

DMS란: 여러 스토리지 백엔드(CephFS/GPFS/WekaFS 등)와 Kubernetes 클러스터에 걸친
**스토리지 인벤토리**와 **데이터 잡**(scan/sync/rm)을 관리하는 시스템. 정확한 범위와
요구사항은 새 설계에서 다시 정의한다.

## legacy/ — 읽기 전용, 설계 참고용

`legacy/`에는 이전 DMS 구현 전체가 보존되어 있다 — 소스(`legacy/src/`), 테스트,
설치/운영 문서(`legacy/install/`, `legacy/docs/`), 이전 CLAUDE.md(`legacy/CLAUDE.md`) 포함.

규칙:

- **읽기 전용.** `legacy/` 아래의 어떤 파일도 수정·이동·삭제하지 않는다. 새 파일도 넣지 않는다.
- **설계 참고용으로만 사용한다.** 도메인 지식, 요구사항, 운영 제약, 과거 설계 결정과
  그 이유를 파악하는 출처다. 이전 구현의 전체 맥락은 `legacy/CLAUDE.md`부터 읽는다.
- 새 구현에서 legacy 코드를 import하거나 복사해 재사용하지 않는다. 필요한 개념은
  새로 설계해서 구현한다.
