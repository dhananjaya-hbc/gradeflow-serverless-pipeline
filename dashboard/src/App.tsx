import { Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { DatasetsPage } from "./pages/DatasetsPage";
import { NotFoundPage } from "./pages/NotFoundPage";

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<DatasetsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
