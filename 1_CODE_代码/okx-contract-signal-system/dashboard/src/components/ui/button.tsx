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
        "inline-flex h-9 items-center justify-center gap-2 rounded-md border border-zinc-200 bg-white px-3 text-sm font-semibold text-zinc-700 shadow-sm transition hover:border-zinc-300 hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-60",
        active && "border-zinc-900 bg-zinc-900 text-white hover:bg-zinc-800",
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
}
