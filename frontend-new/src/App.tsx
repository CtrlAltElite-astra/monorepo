import { lazy, Suspense } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import Landing from './pages/Story'

const Demo = lazy(() => import('./pages/Console'))

export default function App() {
  return (
    <>
      <BrowserRouter>
        <Suspense fallback={<div className="boot" role="status">Starting the console…</div>}>
          <Routes>
            <Route path="/" element={<Landing />} />
            <Route path="/demo" element={<Demo />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
      </BrowserRouter>
    </>
  )
}
