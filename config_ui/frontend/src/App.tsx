import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import AppLayout from "./components/AppLayout";
import LoginPage from "./pages/LoginPage";
import DashboardPage from "./pages/DashboardPage";
import CamerasPage from "./pages/CamerasPage";
import SitesPage from "./pages/SitesPage";
import WeightPage from "./pages/WeightPage";
import PlatesPage from "./pages/PlatesPage";
import SystemPage from "./pages/SystemPage";
import RevisionsPage from "./pages/RevisionsPage";
import ResultsPage from "./pages/ResultsPage";
import LivePage from "./pages/LivePage";
import PlateCheckPage from "./pages/PlateCheckPage";

export default function App() {
  const { user } = useAuth();
  if (!user) {
    return (
      <Routes>
        <Route path="*" element={<LoginPage />} />
      </Routes>
    );
  }
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<DashboardPage />} />
        <Route path="results" element={<ResultsPage />} />
        <Route path="live" element={<LivePage />} />
        <Route path="plate-check" element={<PlateCheckPage />} />
        <Route path="cameras" element={<CamerasPage />} />
        <Route path="sites" element={<SitesPage />} />
        <Route path="sites/:code" element={<SitesPage />} />
        <Route path="weight" element={<WeightPage />} />
        <Route path="plates" element={<PlatesPage />} />
        <Route path="system" element={<SystemPage />} />
        <Route path="revisions" element={<RevisionsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
