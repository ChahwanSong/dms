import { useEffect, useState } from "react";
import { appApi, auth, type User } from "./api";
import Login from "./pages/Login";
import OperatorApp from "./interfaces/operator/OperatorApp";
import UserApp from "./interfaces/user/UserApp";
import Loading from "./components/Loading";

// Top-level role switch: the two interfaces are entirely separate trees so they
// can evolve independently. Role comes from the session (single source of truth).
export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  // Which cluster this portal serves. Fetched once here (pre-auth) so both the login
  // screen and the two logged-in shells show the same value from one request.
  const [clusterName, setClusterName] = useState("");

  useEffect(() => {
    auth
      .me()
      .then((res) => setUser(res.user))
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
    // Chrome-only: a failure must not block login, so it degrades to "no label".
    appApi
      .config()
      .then((c) => setClusterName(c.cluster_name || ""))
      .catch(() => setClusterName(""));
  }, []);

  function logout() {
    auth.logout().finally(() => setUser(null));
  }

  if (loading) {
    return <Loading center />;
  }

  if (!user) {
    return <Login onLoggedIn={setUser} clusterName={clusterName} />;
  }

  if (user.role === "operator") {
    return <OperatorApp user={user} onLogout={logout} clusterName={clusterName} />;
  }
  return <UserApp user={user} onLogout={logout} clusterName={clusterName} />;
}
