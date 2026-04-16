// Seshat theme — Egyptian goddess color scheme.
//
// Three brightness variants, each with distinct Egyptian character:
//   - dark:  nighttime temple — deep indigo with gold accents
//   - dim:   torchlit chamber — dark warm sand/papyrus
//   - light: sunlit papyrus — warm sand with rich bronze
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
  bg: string;   bg2: string;  bg3: string;  bg4: string;
  border: string; borderL: string; borderH: string;
  text: string; text2: string;
  tm: string; td: string; tf: string; tg: string; ti: string;
  accent: string; accentDim: string;
  abg: string; abr: string;
  jade: string; jadeDim: string;
  grn: string; grnt: string; grnb: string;
  red: string; redt: string; redb: string;
  ylw: string; ylwt: string; ylwb: string;
  pur: string; purt: string; purb: string;
  cyan: string; cyant: string; cyanb: string;
  ok: string; warn: string; err: string;
  inp: string;
  textDim: string;
}

export const THEMES: Record<string, Theme> = {
  dark: {
    name: "Dark",
    // Nighttime temple — deep indigo
    bg:  "#1a1c30",
    bg2: "#222438",
    bg3: "#1e2034",
    bg4: "#2a2c42",
    border:  "#3a3c56",
    borderL: "#2e3048",
    borderH: "#545878",
    text:  "#eceaf4",
    text2: "#d6d4e0",
    tm: "#b4b2c4", td: "#9694ac", tf: "#7c7a94",
    tg: "#62607a", ti: "#504e68",
    accent:    "#e4b868",
    accentDim: "#c09840",
    abg: "rgba(222,176,96,0.16)",
    abr: "rgba(222,176,96,0.32)",
    jade:    "#4cb888",
    jadeDim: "#3a9468",
    grn: "#4cb888", grnt: "#3a9468", grnb: "rgba(76,184,136,0.14)",
    red: "#e06060", redt: "#c04848", redb: "rgba(224,96,96,0.14)",
    ylw: "#e0c060", ylwt: "#c8a848", ylwb: "rgba(224,192,96,0.14)",
    pur: "#a088cc", purt: "#8870b4", purb: "rgba(160,136,204,0.14)",
    cyan: "#58b8cc", cyant: "#4898b0", cyanb: "rgba(88,184,204,0.14)",
    ok: "#4cb888", warn: "#e0c060", err: "#e06060",
    inp: "#222438",
    textDim: "#8886a0",
  },
  dim: {
    name: "Dim",
    // Torchlit chamber — warm sand/papyrus, brighter
    bg:  "#342e28",
    bg2: "#3e3830",
    bg3: "#383228",
    bg4: "#484038",
    border:  "#5c5448",
    borderL: "#504840",
    borderH: "#786e60",
    text:  "#f2ece2",
    text2: "#e0d8cc",
    tm: "#c4bcb0", td: "#a8a098", tf: "#908880",
    tg: "#787068", ti: "#605850",
    accent:    "#dca858",
    accentDim: "#c09040",
    abg: "rgba(220,168,88,0.20)",
    abr: "rgba(220,168,88,0.36)",
    jade:    "#52b888",
    jadeDim: "#409868",
    grn: "#52b888", grnt: "#409868", grnb: "rgba(82,184,136,0.16)",
    red: "#e06868", redt: "#c85050", redb: "rgba(224,104,104,0.16)",
    ylw: "#e0b858", ylwt: "#c8a048", ylwb: "rgba(224,184,88,0.16)",
    pur: "#a088c0", purt: "#8870a8", purb: "rgba(160,136,192,0.16)",
    cyan: "#58b8c0", cyant: "#4898a0", cyanb: "rgba(88,184,192,0.16)",
    ok: "#52b888", warn: "#e0b858", err: "#e06868",
    inp: "#3e3830",
    textDim: "#a8a098",
  },
  light: {
    name: "Light",
    // Sunlit papyrus
    bg:  "#f5f0e8",
    bg2: "#fffdf8",
    bg3: "#faf6f0",
    bg4: "#eee8e0",
    border:  "#d8d0c4",
    borderL: "#e8e0d4",
    borderH: "#b0a898",
    text:  "#1a1820",
    text2: "#2a2830",
    tm: "#504840", td: "#686058", tf: "#887868",
    tg: "#a09888", ti: "#c0b8a8",
    accent:    "#b8862d",
    accentDim: "#9c7028",
    abg: "rgba(184,134,45,0.10)",
    abr: "rgba(184,134,45,0.28)",
    jade:    "#2e8a62",
    jadeDim: "#247050",
    grn: "#2e8a62", grnt: "#247050", grnb: "rgba(46,138,98,0.10)",
    red: "#c04242", redt: "#a03636", redb: "rgba(192,66,66,0.10)",
    ylw: "#a07824", ylwt: "#886420", ylwb: "rgba(160,120,36,0.10)",
    pur: "#7856a0", purt: "#644888", purb: "rgba(120,86,160,0.10)",
    cyan: "#388898", cyant: "#307080", cyanb: "rgba(56,136,152,0.10)",
    ok: "#2e8a62", warn: "#a07824", err: "#c04242",
    inp: "#fffdf8",
    textDim: "#686058",
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
  } catch { /* ignore */ }
  return "dark";
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [themeName, setThemeNameState] = useState<string>(loadSavedTheme);
  const theme = THEMES[themeName] ?? THEMES.dark;

  const setThemeName = useCallback((name: string) => {
    if (!THEMES[name]) return;
    setThemeNameState(name);
    try { localStorage.setItem(STORAGE_KEY, name); } catch { /* ignore */ }
  }, []);

  const cycle = useCallback(() => {
    const idx = THEME_ORDER.indexOf(themeName);
    const next = THEME_ORDER[(idx + 1) % THEME_ORDER.length];
    setThemeName(next);
  }, [themeName, setThemeName]);

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
