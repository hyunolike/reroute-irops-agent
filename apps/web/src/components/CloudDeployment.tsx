import { Bot, Boxes, Cloud, Database, GitBranch, KeyRound, Lock, Network, Server, ShieldCheck, Sparkles } from "lucide-react";
import type { ReactNode } from "react";
import { Box } from "@/components/ArchitectureDiagram";

function Region({ title, sub, children, tone }: { title: string; sub: string; children: ReactNode; tone: "aws" | "gcp" | "ext" }) {
  const border = { aws: "border-amber-500/40", gcp: "border-sky-500/40", ext: "border-ops-line" }[tone];
  const label = { aws: "text-amber-300", gcp: "text-sky-300", ext: "text-slate-300" }[tone];
  return (
    <div className={`rounded-xl border-2 border-dashed ${border} p-3`}>
      <div className={`text-xs font-bold uppercase tracking-wider ${label}`}>{title}</div>
      <div className="mb-2 text-[11px] text-ops-muted">{sub}</div>
      <div className="space-y-2">{children}</div>
    </div>
  );
}

const FLOWS: [string, string][] = [
  ["운영자 → CloudFront → ALB", "HTTPS는 CloudFront에서 끝나고, ALB는 CloudFront 대역과 비밀 헤더가 있는 요청만 받습니다"],
  ["에이전트 worker → 컨트롤 플레인", "OpenShell 정책이 허용한 경로만. worker 토큰과 NVIDIA 키는 provider가 주입(샌드박스엔 placeholder)"],
  ["worker · 컨트롤 플레인 → NVIDIA", "Nemotron 추론은 worker가, NeMo Retriever 검색은 컨트롤 플레인이 NAT를 거쳐 호출"],
  ["GCP 브리지 → ReRoute", "대시보드에서 OpenClaw로 보낸 작업을 HTTPS로 가져가고 결과를 보고 (GCP에 열린 포트 없음)"],
  ["OpenClaw → ReRoute MCP", "HTTPS /mcp로 같은 작업에 붙어 도구 호출. 승인과 실행 도구는 없음"],
  ["develop 머지 → AWS", "GitHub Actions가 CI 후 이미지를 ECR에 올리고 SSM으로 교체 (OIDC, AWS 키 저장 안 함)"],
];

/** The live cloud layout: AWS runs ReRoute, GCP runs NemoClaw/OpenClaw, both call NVIDIA's hosted endpoints. */
export function CloudDeployment() {
  return (
    <div className="space-y-3">
      <div className="grid gap-3 lg:grid-cols-3">
        <Region tone="aws" title="AWS · ap-northeast-2" sub="ReRoute 서비스 (Terraform)">
          <Box icon={<Lock className="h-4 w-4" />} title="CloudFront" sub="도메인 없이 HTTPS · 정적 파일만 캐시" />
          <Box icon={<Network className="h-4 w-4" />} title="ALB" sub="CloudFront에서 온 요청만 · /internal/* 차단" />
          <Box icon={<Server className="h-4 w-4" />} title="EC2 앱 호스트 (프라이빗)" sub="web · reroute-api(컨트롤 플레인, MCP) · airline-service · Docker 29" />
          <Box
            icon={<ShieldCheck className="h-4 w-4" />}
            title="OpenShell 샌드박스"
            sub="에이전트 worker · DB 정보와 서명 키 없음 · 재부팅 시 자동 복구"
            tone="nv"
          />
          <div className="grid grid-cols-2 gap-2">
            <Box icon={<Database className="h-4 w-4" />} title="RDS" sub="PostgreSQL 16" />
            <Box icon={<KeyRound className="h-4 w-4" />} title="Secrets" sub="Manager · ECR · SSM" />
          </div>
        </Region>
        <Region tone="gcp" title="GCP · asia-northeast3" sub="NemoClaw 호스트 (별도 프로젝트)">
          <Box icon={<Cloud className="h-4 w-4" />} title="VM e2-standard-4" sub="Ubuntu 24.04 · Docker 29 · 인바운드는 SSH만" />
          <Box icon={<ShieldCheck className="h-4 w-4" />} title="NemoClaw 샌드박스 (OpenShell)" sub="MCP 토큰은 provider가 주입" tone="nv" />
          <Box icon={<Bot className="h-4 w-4" />} title="OpenClaw" sub="Nemotron 추론 · ReRoute MCP 도구 사용" tone="violet" />
          <Box icon={<Boxes className="h-4 w-4" />} title="openclaw-bridge (systemd)" sub="대시보드 작업을 가져와 OpenClaw에 전달 · 과부하 시 재시도" />
        </Region>
        <Region tone="ext" title="공통 서비스" sub="두 클라우드가 함께 쓰는 것">
          <Box
            icon={<Sparkles className="h-4 w-4" />}
            title="NVIDIA API catalog"
            sub="Nemotron (NIM) · NeMo Retriever embed + rerank"
            tone="nv"
          />
          <Box icon={<GitBranch className="h-4 w-4" />} title="GitHub Actions" sub="develop 머지 → CI → ECR → SSM 배포 · OIDC" />
          <Box icon={<Server className="h-4 w-4" />} title="cuOpt (GPU일 때)" sub="라이브 데모는 GPU가 없어 같은 MILP를 CPU(HiGHS)로 풉니다" />
        </Region>
      </div>
      <table className="w-full text-left text-xs">
        <tbody>
          {FLOWS.map(([what, how]) => (
            <tr key={what} className="border-t border-ops-line align-top">
              <td className="w-56 py-1.5 pr-3 font-semibold text-white">{what}</td>
              <td className="py-1.5 text-slate-300">{how}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
