import { Outlet } from "react-router-dom"
import { Sidebar } from "./sidebar"

export function MainLayout() {
  return (
    <div className="flex h-screen overflow-hidden bg-gradient-to-br from-slate-50 via-blue-50/30 to-purple-50/20">
      <Sidebar />
      <main className="flex-1 overflow-hidden">
        <Outlet />
      </main>
    </div>
  )
}
