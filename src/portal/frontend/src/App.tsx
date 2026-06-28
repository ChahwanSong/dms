import { useEffect, useState } from "react";
import { auth, type User } from "./api";
import Login from "./pages/Login";
import OperatorApp from "./interfaces/operator/OperatorApp";
import UserApp from "./interfaces/user/UserApp";
import Loading from "./components/Loading";

// Top-level role switch: the two interfaces are entirely separate trees so they
// can evolve independently. Role comes from the session (single source of truth).
export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    auth
      .me()
      .then((res) => setUser(res.user))
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  function logout() {
    auth.logout().finally(() => setUser(null));
  }

  if (loading) {
    return <Loading center />;
  }

  if (!user) {
    return <Login onLoggedIn={setUser} />;
  }

  if (user.role === "operator") {
    return <OperatorApp user={user} onLogout={logout} />;
  }
  return <UserApp user={user} onLogout={logout} />;
}
