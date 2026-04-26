// Navigation context — exposes `navBack()` and `canGoBack` to any
// component that needs them.
//
// App.tsx maintains the in-memory history stack and wraps the page
// tree in <NavigationProvider value={{ navBack, canGoBack }}>. Mobile
// page components consume via useNavigation(); a no-op default lets
// components import the hook safely outside the provider.
import { createContext, useContext, type ReactNode } from "react";

export interface NavigationApi {
  navBack: () => void;
  canGoBack: boolean;
}

const NavCtx = createContext<NavigationApi>({
  navBack: () => {},
  canGoBack: false,
});

export function NavigationProvider({
  value,
  children,
}: {
  value: NavigationApi;
  children: ReactNode;
}) {
  return <NavCtx.Provider value={value}>{children}</NavCtx.Provider>;
}

export function useNavigation(): NavigationApi {
  return useContext(NavCtx);
}
