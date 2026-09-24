import { Bot, Calculator, Library, ShieldCheck, UserCheck } from "lucide-react";

const PILLARS = [
  {
    icon: Bot,
    title: "스스로 계획하고 도구를 호출",
    body: "한 문장의 목표에서 Nemotron이 다음 도구를 선택합니다. 운항 조회 → 승객 → 대체편 → 규정 → 최적화 → 제안.",
    tech: "NVIDIA Nemotron · NIM",
  },
  {
    icon: Library,
    title: "규정은 LLM 기억이 아닌 문서에서",
    body: "재예약·운임·VIP·MCT 규정을 검색하고, 검색된 규정만 제약조건으로 컴파일합니다. 모든 판단에 Policy ID가 붙습니다.",
    tech: "NeMo Retriever (RAG)",
  },
  {
    icon: Calculator,
    title: "배정은 LLM이 아닌 수리 최적화",
    body: "승객×항공편×좌석등급 0-1 정수계획 문제. 용량·MCT·목적지·정책 제약 하에서 지연·다운그레이드·VIP 비용을 최소화.",
    tech: "NVIDIA cuOpt (MILP)",
  },
  {
    icon: ShieldCheck,
    title: "샌드박스 안에서만 행동",
    body: "허용된 API만 호출 가능, 미등록 외부 호스트·자격증명 파일 접근·자기 승인 호출은 차단되고 감사 로그에 남습니다.",
    tech: "NVIDIA OpenShell",
  },
  {
    icon: UserCheck,
    title: "예약 변경은 사람이 승인",
    body: "조회·검색·최적화는 자율 실행, 예약 변경은 운영자 승인 + 서명된 1회용 토큰이 있어야만 Booking API가 수락합니다.",
    tech: "Approval Gateway",
  },
];

export function WelcomeBoard() {
  return (
    <div className="rounded-xl border border-ops-line bg-ops-panel/80 p-6">
      <div className="text-xs font-semibold uppercase tracking-[0.2em] text-nv">Problem</div>
      <h1 className="mt-2 text-2xl font-bold leading-snug text-white md:text-[28px]">
        결항 한 건이 수십 명의 승객, 수십 개의 규정, 수백 가지 좌석 조합을 동시에 만듭니다.
      </h1>
      <p className="mt-3 max-w-3xl text-sm leading-relaxed text-slate-300">
        IROPS(비정상 운항) 상황에서 운영자는 연결편, VIP, 특수지원 승객, 좌석등급, 제휴사 규정을 동시에 고려해 빠르게 재배정해야 합니다.
        <b className="text-white"> ReRoute</b>는 답변하는 챗봇이 아니라, 목표를 받아 <b className="text-white">계획 → 도구 호출 → 최적화 → 승인 요청 → 실행</b>까지 수행하는 운영 Agent입니다.
      </p>
      <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-5">
        {PILLARS.map((p) => (
          <div key={p.title} className="rounded-lg border border-ops-line bg-ops-panel2 p-4">
            <p.icon className="h-5 w-5 text-nv" />
            <div className="mt-2 text-sm font-semibold text-white">{p.title}</div>
            <p className="mt-1 text-xs leading-relaxed text-slate-400">{p.body}</p>
            <div className="mt-3 inline-block rounded bg-nv/15 px-2 py-0.5 text-[10px] font-semibold text-nv-light ring-1 ring-nv/30">{p.tech}</div>
          </div>
        ))}
      </div>
      <div className="mt-5 rounded-lg border border-nv/30 bg-nv/5 px-4 py-3 text-sm text-slate-200">
        ▶ 위의 <b className="text-nv-light">Run Agent</b>를 누르면 KE123(ICN→NRT) 결항 시나리오가 실행됩니다 — 35명 영향, 5명 연결편, 3명 VIP, 2명 특수지원.
      </div>
    </div>
  );
}
