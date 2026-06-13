import { cn } from "@/lib/utils";

export function Button({
  children,
  className,
  active,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  active?: boolean;
}) {
  return (
    <button
      className={cn(
        "inline-flex h-9 items-center justify-center gap-2 rounded-md border border-[#9be7e3] bg-white px-3 text-sm font-semibold text-zinc-700 shadow-sm transition hover:border-[#0abab5] hover:bg-[#f0fffe] disabled:cursor-not-allowed disabled:opacity-60",
        active && "border-[#0abab5] bg-[#0abab5] text-white hover:bg-[#079d99]",
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
}
