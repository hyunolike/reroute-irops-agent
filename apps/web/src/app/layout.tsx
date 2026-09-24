import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ReRoute · Autonomous Airline Disruption Recovery Agent",
  description: "Agentic IROPS recovery with NVIDIA Nemotron (NIM), NeMo Retriever, cuOpt and OpenShell — with human approval.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body className="min-h-screen font-sans antialiased">{children}</body>
    </html>
  );
}
