import { Routes, Route, Navigate } from 'react-router-dom'
import { AppProvider, useAuth } from './contexts/AppContext'
import Layout from './components/Layout'
import LoginPage from './pages/LoginPage'
import HomePage from './pages/HomePage'
import AuditToolPage from './pages/AuditToolPage'
import TaxFillToolPage from './pages/TaxFillToolPage'
import TasksPage from './pages/TasksPage'

function AppRoutes() {
  const { isLoggedIn } = useAuth()

  if (!isLoggedIn) {
    return (
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    )
  }

  return (
    <Layout>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/audit" element={<AuditToolPage />} />
        <Route path="/taxfill" element={<TaxFillToolPage />} />
        <Route path="/tasks" element={<TasksPage />} />
        <Route path="/login" element={<Navigate to="/" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  )
}

export default function App() {
  return (
    <AppProvider>
      <AppRoutes />
    </AppProvider>
  )
}
