import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { UserProvider, useUser } from "./context/UserContext";
import Welcome from "./pages/Welcome";
import Home from "./pages/Home";
import Lights from "./pages/Lights";
import Display from "./pages/Display";

function AppRoutes() {
  const { user, loading } = useUser();

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-950 text-white flex items-center justify-center">
        <p className="text-gray-400">Loading...</p>
      </div>
    );
  }

  return (
    <Routes>
      <Route path="/" element={user ? <Navigate to="/home" /> : <Welcome />} />
      <Route path="/home" element={user ? <Home /> : <Navigate to="/" />} />
      <Route path="/lights" element={user ? <Lights /> : <Navigate to="/" />} />
      <Route path="/display" element={user ? <Display /> : <Navigate to="/" />} />
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <UserProvider>
        <AppRoutes />
      </UserProvider>
    </BrowserRouter>
  );
}
