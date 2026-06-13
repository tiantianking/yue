import { cn } from "@/lib/utils";

export function MetricTile({
  label,
  value,
  hint,
  tone = "neutral",
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "neutral" | "green" | "amber" | "red";
}) {
  return (
    <div className="rounded-lg border border-[#c4f1ef] bg-white p-3 shadow-sm">
      <div className="text-xs font-semibold text-zinc-500">{label}</div>
      <div
        className={cn(
          "mt-2 truncate font-mono text-xl font-bold text-zinc-900",
          tone === "green" && "text-[#008f8a]",
          tone === "amber" && "text-amber-700",
          tone === "red" && "text-rose-700",
        )}
      >
        {value}
      </div>
      {hint ? <div className="mt-1 truncate text-xs text-zinc-500">{hint}</div> : null}
    </div>
  );
}
