import { useEffect } from "react";
import { useAppStore } from "@/stores/app-store";

export function useApplyTheme() {
  const theme = useAppStore((s) => s.theme);
  const accent = useAppStore((s) => s.accent);
  const reducedMotion = useAppStore((s) => s.reducedMotion);

  useEffect(() => {
    const root = document.documentElement;
    const apply = () => {
      const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      const useDark = theme === "dark" || (theme === "system" && prefersDark);
      root.classList.toggle("dark", useDark);
      root.dataset.accent = accent;
      root.dataset.reducedMotion = reducedMotion ? "true" : "false";
      applyAccentVars();
    };
    apply();
    if (theme === "system") {
      const mq = window.matchMedia("(prefers-color-scheme: dark)");
      const l = () => apply();
      mq.addEventListener("change", l);
      return () => mq.removeEventListener("change", l);
    }
  }, [theme, accent, reducedMotion]);
}

export const accentTokens: Record<
  string,
  {
    light: { accent: string; onAccent: string; soft: string; border: string };
    dark: { accent: string; onAccent: string; soft: string; border: string };
  }
> = {
  violet: {
    light: { accent: "#6D28D9", onAccent: "#FFFFFF", soft: "#EFE9FC", border: "#C7B3F2" },
    dark: {
      accent: "#BD93F9",
      onAccent: "#1A1226",
      soft: "#2A1F45",
      border: "rgba(124,58,237,.5)",
    },
  },
  oceanic: {
    light: { accent: "#0E7490", onAccent: "#FFFFFF", soft: "#DFF3F7", border: "#9CD5E0" },
    dark: {
      accent: "#5EEAD4",
      onAccent: "#062530",
      soft: "#103040",
      border: "rgba(14,116,144,.5)",
    },
  },
  amber: {
    light: { accent: "#B45309", onAccent: "#FFFFFF", soft: "#FDF3E0", border: "#EBC893" },
    dark: { accent: "#FCD34D", onAccent: "#2E2106", soft: "#3A2B10", border: "rgba(180,83,9,.5)" },
  },
};

export function applyAccentVars() {
  if (typeof document === "undefined") return;
  const accent = useAppStore.getState().accent;
  const isDark = document.documentElement.classList.contains("dark");
  const t = accentTokens[accent][isDark ? "dark" : "light"];
  const root = document.documentElement;
  root.style.setProperty("--accent-c", t.accent);
  root.style.setProperty("--on-accent", t.onAccent);
  root.style.setProperty("--accent-soft", t.soft);
  root.style.setProperty("--accent-border", t.border);
}
