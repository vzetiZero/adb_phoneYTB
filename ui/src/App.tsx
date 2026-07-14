import { BrowserRouter, Routes, Route } from "react-router-dom"
import { MainLayout } from "@/components/layout/main-layout"
import RunPage from "@/pages/run-page"
import DevicesPage from "@/pages/devices-page"
import HistoryPage from "@/pages/history-page"
import SettingsPage from "@/pages/settings-page"

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<MainLayout />}>
          <Route path="/" element={<RunPage />} />
          <Route path="/devices" element={<DevicesPage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default App
