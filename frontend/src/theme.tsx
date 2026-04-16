// Theme definitions, React context, and hook.
//
// Three brightness variants mirror AthenaScout's set:
//   - dark:  near-black with soft accents, default
//   - dim:   warm charcoal, gentler than full dark
//   - light: off-white, high readability in bright rooms
//
// Usage:
//   import { useTheme } from "../theme";
//   function MyComponent() {
//     const t = useTheme();
//     return <div style={{color: t.text, background: t.bg2}}/>;
//   }
//
// Switching: `useThemeControls()` exposes the current theme name and
// a `cycle()` callback that rotates through the three variants and
// persists the selection in localStorage.
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export interface Theme {
  name: string;
  bg: string;
  bg2: string;
  bg3: string;
  bg4: string;
  border: string;
  borderL: string;
  borderH: string;
  text: string;
  text2: string;
  textDim: string;
  accent: string;
  accentDim: string;
  ok: string;
  warn: string;
  err: string;
  inp: string;
}

export const THEMES: Record<string, Theme> = {
  dark: {
    name: "Dark",
    bg: "#0e0f13",
    bg2: "#161821",
    bg3: "#1f2230",
    bg4: "#2a2e3e",
    border: "#2e3242",
    borderL: "#1c1f2c",
    borderH: "#4a4f66",
    text: "#f0f0f4",
    text2: "#d6d8df",
    textDim: "#8a8e9b",
    accent: "#6fa8ff",
    accentDim: "#4f7fcc",
    ok: "#4ec995",
    warn: "#e8c14a",
    err: "#ef6464",
    inp: "#1f2230",
  },
  dim: {
    name: "Dim",
    bg: "#2a2a30",
    bg2: "#333338",
    bg3: "#2e2e34",
    bg4: "#3a3a40",
    border: "#4a4a52",
    borderL: "#404048",
    borderH: "#66666e",
    text: "#eaeaea",
    text2: "#d8d8d8",
    textDim: "#9a9ea8",
    accent: "#7fb0ff",
    accentDim: "#5f8fcc",
    ok: "#5bcc9e",
    warn: "#e8c14a",
    err: "#ef7070",
    inp: "#333338",
  },
  light: {
    name: "Light",
    bg: "#f4f5f8",
    bg2: "#ffffff",
    bg3: "#fafaf8",
    bg4: "#eef0f4",
    border: "#d8dae0",
    borderL: "#e8eaf0",
    borderH: "#b0b3bc",
    text: "#1a1d26",
    text2: "#30343c",
    textDim: "#6a6e78",
    accent: "#3b6fcc",
    accentDim: "#2e58a0",
    ok: "#1f8a5e",
    warn: "#a07824",
    err: "#c04242",
    inp: "#ffffff",
  },
};

const THEME_ORDER: readonly string[] = ["dark", "dim", "light"] as const;
const STORAGE_KEY = "seshat_theme";

export const ThemeContext = createContext<Theme>(THEMES.dark);

export const useTheme = (): Theme => useContext(ThemeContext);

interface ThemeControls {
  theme: Theme;
  themeName: string;
  cycle: () => void;
  setThemeName: (name: string) => void;
}

const ThemeControlsContext = createContext<ThemeControls>({
  theme: THEMES.dark,
  themeName: "dark",
  cycle: () => {},
  setThemeName: () => {},
});

export const useThemeControls = (): ThemeControls =>
  useContext(ThemeControlsContext);

function loadSavedTheme(): string {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved && THEMES[saved]) return saved;
  } catch {
    /* ignore */
  }
  return "dark";
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [themeName, setThemeNameState] = useState<string>(loadSavedTheme);
  const theme = THEMES[themeName] ?? THEMES.dark;

  const setThemeName = useCallback((name: string) => {
    if (!THEMES[name]) return;
    setThemeNameState(name);
    try {
      localStorage.setItem(STORAGE_KEY, name);
    } catch {
      /* ignore */
    }
  }, []);

  const cycle = useCallback(() => {
    const idx = THEME_ORDER.indexOf(themeName);
    const next = THEME_ORDER[(idx + 1) % THEME_ORDER.length];
    setThemeName(next);
  }, [themeName, setThemeName]);

  // Keep the document background in sync with the active theme so
  // the initial paint before React mounts doesn't flash the browser
  // default. Cheap, one DOM write per theme change.
  useEffect(() => {
    document.documentElement.style.background = theme.bg;
    document.documentElement.style.colorScheme =
      themeName === "light" ? "light" : "dark";
  }, [theme.bg, themeName]);

  const controls = useMemo<ThemeControls>(
    () => ({ theme, themeName, cycle, setThemeName }),
    [theme, themeName, cycle, setThemeName],
  );

  return (
    <ThemeControlsContext.Provider value={controls}>
      <ThemeContext.Provider value={theme}>{children}</ThemeContext.Provider>
    </ThemeControlsContext.Provider>
  );
}
