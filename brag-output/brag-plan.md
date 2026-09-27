# /brag plan — ReRoute

**What is it:** 항공편이 결항되면 운영자의 한 문장을 받아 영향 승객 재배정안을 만들고, 사람이 승인하면 실제 예약까지 바꾸는 IROPS 복구 에이전트.
**Who for:** 항공사 운항 통제(OCC)·공항 운영자. 결항 한 건에 승객 35명, 규정 수십 개, 좌석 조합 수백 개를 동시에 다뤄야 하는 사람.
**What sets it apart:** LLM은 계획하고 규정을 찾을 뿐, 배정은 cuOpt MILP가 계산한다. 예약 변경은 사람 승인 없이는 백엔드에서 403으로 막힌다.
**Most impressive claim:** 같은 허용 항공편에서 선착순 수작업 대비 연결편 놓침 4 → 0, 특수지원 위반 2 → 0.
**Visual hook:** 어두운 운영 콘솔 위에 KE123 편명과 붉은 CANCELLED 배지가 찍힌다.
**Real UI:** CommandPanel(ops> 입력 + Run Agent), AgentTimeline, KPI 카드 35/31/3/1, 선착순 vs ReRoute 비교표, HumanApproval(Approve Plan) → FinalReport(32 rebooked).
**Tone:** `polished` + ops-console. 진지하고 절제된 톤, NVIDIA 그린(#76B900) 포인트.
**Share caption:** 결항 한 건, 운영자 한 문장. ReRoute는 규정을 찾고 cuOpt로 재배정안을 계산한 뒤 사람의 승인을 받아서만 예약을 바꿉니다.

## Angle
"LLM does not decide passenger allocation." — 똑똑한 에이전트인데, 정작 위험한 두 가지(배정과 예약 변경)는 LLM에게 맡기지 않는다는 점이 이 프로젝트의 매력.

## Storyboard (21.0s, 1920×1080, 30fps)

| # | Time | Scene | On screen |
|---|---|---|---|
| 1 | 0.0–3.0 | Hook | KE123 · ICN ✈ NRT 크게, CANCELLED 배지가 쾅. 아래 "결항 한 건. 승객 35명." |
| 2 | 3.0–6.6 | Reveal | ReRoute 명령 패널. `ops>`에 "KE123편이 결항됐어. 영향 승객을 확인하고 최적 재배정안을 만들어줘." 타이핑 → Run Agent 클릭 → "Agent running…". 위 캡션 "운영자는 한 문장만." |
| 3 | 6.6–11.0 | Highlight 1 | 왼쪽 Agent Activity 타임라인이 하나씩 체크(항공편 조회 → 승객 35명 → 대체편 7편 → 규정 검색 → cuOpt MILP). 오른쪽 KPI 35/31/3/1 카운트업, OPTIMAL. 캡션 "규정은 찾고, 배정은 cuOpt가 계산." |
| 4 | 11.0–15.0 | Highlight 2 | "LLM does not decide passenger allocation." + 선착순 수작업 vs ReRoute 표: 연결편 놓침 4→0, 특수지원 위반 2→0, VIP 평균 지연 5h40m→4h00m |
| 5 | 15.0–18.2 | Highlight 3 | Human Approval 카드. 승인 없이 execute → `HTTP 403 APPROVAL_REQUIRED`. 이어서 Approve Plan 클릭 → APPROVED, Final Report "32 rebooked". 캡션 "예약 변경은 사람이 승인해야만." |
| 6 | 18.2–21.0 | Outro | ReRoute 로고 + "자율 항공 비정상운항 복구 에이전트", 칩 Nemotron · NeMo Retriever · cuOpt · OpenShell, `docker compose up` |

## Visual identity
- 배경 #070b12, 패널 #0d1420 / #111a29, 선 #1e2a3d, muted #7d8aa3, NVIDIA green #76B900 / #a4d65e
- Pretendard + JetBrains Mono (프로젝트 tailwind 설정 그대로), lucide 아이콘(Waypoints 로고)
- 전환: 이전 콘텐츠 아웃 → 배경 딥 → 다음 콘텐츠 인

## Sound
A minor, 112 BPM. 부드러운 킥 + 클로즈드 햇 + 서브 베이스 + 따뜻한 패드(Am–F–C–G). 효과음은 같은 키: 타이핑 틱은 아주 작게, 클릭은 필터된 A음 블립, CANCELLED 스탬프는 저역 붐, 승인은 A–E 5도 차임.
