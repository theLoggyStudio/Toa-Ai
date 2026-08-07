import { Navigate, Route, Routes } from 'react-router-dom';
import { TranslatePage } from './pages/TranslatePage';
import { FrescoPage } from './pages/FrescoPage';

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/TOA.ai" replace />} />
      <Route path="/TOA.ai" element={<TranslatePage />} />
      <Route path="/fresco" element={<FrescoPage />} />
      <Route path="/eclat" element={<Navigate to="/fresco" replace />} />
      <Route path="*" element={<Navigate to="/TOA.ai" replace />} />
    </Routes>
  );
}
