import type { ButtonHTMLAttributes, ReactNode } from "react";

type ButtonVariant = "primary" | "secondary" | "ghost";

const variants: Record<ButtonVariant, string> = {
  primary: "bg-signal-500 text-ink-950 hover:bg-signal-400",
  secondary: "border border-slate-600/70 bg-slate-900/60 text-slate-100 hover:border-slate-400",
  ghost: "text-slate-300 hover:bg-slate-800/70 hover:text-white",
};

export function Button({
  children,
  variant = "secondary",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { children: ReactNode; variant?: ButtonVariant }) {
  return (
    <button
      className={`inline-flex items-center justify-center rounded-xl px-3 py-2 text-sm font-semibold transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-signal-400 ${variants[variant]} ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}
