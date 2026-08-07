import { Navigate, Route, Routes } from 'react-router-dom';
import { TranslatePage } from './pages/TranslatePage';
import { EclatPage } from './pages/EclatPage';

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/TOA.ai" replace />} />
      <Route path="/TOA.ai" element={<TranslatePage />} />
      <Route path="/eclat" element={<EclatPage />} />
      <Route path="*" element={<Navigate to="/TOA.ai" replace />} />
    </Routes>
  );
}
