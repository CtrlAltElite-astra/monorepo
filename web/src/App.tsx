import { lazy, Suspense } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { ThemeProvider } from './components/Theme'
import Landing from './pages/Landing'

const Demo = lazy(() => import('./pages/Demo'))

export default function App() {
  return (
    <ThemeProvider>
      <BrowserRouter>
        <Suspense fallback={<div className="boot" role="status">Starting the console…</div>}>
          <Routes>
            <Route path="/" element={<Landing />} />
            <Route path="/demo" element={<Demo />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
      </BrowserRouter>
    </ThemeProvider>
  )
}
