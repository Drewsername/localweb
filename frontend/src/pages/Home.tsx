import { useUser } from "../context/UserContext";
import { Link } from "react-router-dom";

const apps = [
  {
    name: "Lighting",
    description: "Control your Govee smart lights",
    path: "/lights",
    icon: "\u{1F4A1}",
  },
  {
    name: "Display",
    description: "View the e-ink display",
    path: "/display",
    icon: "\u{1F5BC}\u{FE0F}",
  },
  {
    name: "Thermostat",
    description: "Nest climate control",
    path: "/thermostat",
    icon: "\u{1F321}\u{FE0F}",
  },
];

export default function Home() {
  const { user } = useUser();

  return (
    <div className="min-h-screen bg-gray-950 text-white p-6">
      <div className="max-w-lg mx-auto space-y-8">
        <div>
          <h1 className="text-3xl font-bold">Welcome, {user?.name}!</h1>
          <p className="text-gray-400 mt-1">What would you like to control?</p>
        </div>
        <div className="grid gap-4">
          {apps.map((app) => (
            <Link
              key={app.path}
              to={app.path}
              className="block p-5 bg-gray-900 border border-gray-800 rounded-xl hover:border-gray-600 transition-colors active:bg-gray-800"
            >
              <div className="flex items-center gap-4">
                <span className="text-3xl">{app.icon}</span>
                <div>
                  <h2 className="text-lg font-semibold">{app.name}</h2>
                  <p className="text-sm text-gray-400">{app.description}</p>
                </div>
              </div>
            </Link>
          ))}
          {user?.isAdmin && (
            <Link
              to="/admin"
              className="block p-5 bg-gray-900 border border-red-900/50 rounded-xl hover:border-red-600 transition-colors active:bg-gray-800"
            >
              <div className="flex items-center gap-4">
                <span className="text-3xl">&#x2699;&#xFE0F;</span>
                <div>
                  <h2 className="text-lg font-semibold">Admin</h2>
                  <p className="text-sm text-gray-400">Database management</p>
                </div>
              </div>
            </Link>
          )}
        </div>
      </div>
    </div>
  );
}
