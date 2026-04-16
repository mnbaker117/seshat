// Top-nav menu structure.
// Each entry: { id, label, icon }
// `id` is matched against the current page state in App.tsx to drive
// active highlighting and the page-rendering switch.
//
// Power-user destinations (Database, Settings, Import/Export) live in
// the right-side icon cluster instead of this list — they don't need
// horizontal navbar space and grouping them as icons keeps the main
// nav focused on the day-to-day reading-list pages.

export interface NavItem {
  id: string;
  label: string;
  icon: string;
}

export const NAV: readonly NavItem[] = [
  { id: "library",     label: "Library",     icon: "📖" },
  { id: "authors",     label: "Authors",     icon: "◉" },
  { id: "missing",     label: "Missing",     icon: "◌" },
  { id: "upcoming",    label: "Upcoming",    icon: "📅" },
  { id: "mam",         label: "MAM",         icon: "🔍" },
  { id: "suggestions", label: "Suggestions", icon: "💡" },
];
