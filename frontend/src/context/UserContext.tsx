import { createContext, useContext, useState, useEffect, type ReactNode } from "react";

interface User {
  id: number;
  name: string;
}

interface UserContextType {
  user: User | null;
  loading: boolean;
  login: (name: string) => Promise<void>;
  logout: () => void;
}

const UserContext = createContext<UserContextType | null>(null);

export function UserProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const saved = localStorage.getItem("drewtopia_user");
    if (saved) {
      // Verify the saved user still matches this device
      fetch("/api/users/me")
        .then((r) => {
          if (r.ok) return r.json();
          throw new Error("not found");
        })
        .then((data) => setUser({ id: data.id, name: data.name }))
        .catch(() => {
          localStorage.removeItem("drewtopia_user");
        })
        .finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
  }, []);

  async function login(name: string) {
    const res = await fetch("/api/users/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.error || "Registration failed");
    }
    const data = await res.json();
    const u = { id: data.id, name: data.name };
    setUser(u);
    localStorage.setItem("drewtopia_user", JSON.stringify(u));
  }

  function logout() {
    setUser(null);
    localStorage.removeItem("drewtopia_user");
  }

  return (
    <UserContext.Provider value={{ user, loading, login, logout }}>
      {children}
    </UserContext.Provider>
  );
}

export function useUser() {
  const ctx = useContext(UserContext);
  if (!ctx) throw new Error("useUser must be used within UserProvider");
  return ctx;
}
