import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { Spin } from "antd";
import { api, User } from "./api";

interface AuthState {
  user: User | null;
  isAdmin: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .me()
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
    const onUnauthorized = () => setUser(null);
    window.addEventListener("moda:unauthorized", onUnauthorized);
    return () => window.removeEventListener("moda:unauthorized", onUnauthorized);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    setUser(await api.login(username, password));
  }, []);

  const logout = useCallback(async () => {
    await api.logout().catch(() => undefined);
    setUser(null);
  }, []);

  if (loading) {
    return <Spin fullscreen />;
  }
  return (
    <AuthContext.Provider value={{ user, isAdmin: user?.role === "admin", login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth outside AuthProvider");
  return value;
}
