export const hm = (min: number | null | undefined) => {
  if (min === null || min === undefined) return "—";
  const m = Math.round(min);
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
};

export const hhmm = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Seoul" }) : "—";

export const clock = (iso: string) =>
  new Date(iso).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });

export const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(" ");
