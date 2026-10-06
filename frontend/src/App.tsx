import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import ReposPage from "./pages/ReposPage";
import DashboardPage from "./pages/DashboardPage";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<ReposPage />} />
        <Route path="repos/:repoId" element={<DashboardPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
