import { useState, useEffect } from "react"
import { Outlet } from "react-router-dom"
import { Sidebar } from "./sidebar"
import { apiFetch } from "@/lib/api"
import { Download, X } from "lucide-react"

export function MainLayout() {
  const [hasUpdate, setHasUpdate] = useState(false)
  const [commitCount, setCommitCount] = useState(0)
  const [updating, setUpdating] = useState(false)
  const [dismissed, setDismissed] = useState(false)

  useEffect(() => {
    const check = async () => {
      try {
        const data = await apiFetch<{ has_update: boolean; commits: number }>("/api/update/check")
        if (data.has_update) {
          setHasUpdate(true)
          setCommitCount(data.commits)
        }
      } catch {}
    }
    check()
    const interval = setInterval(check, 5 * 60 * 1000) // check moi 5 phut
    return () => clearInterval(interval)
  }, [])

  const handleUpdate = async () => {
    setUpdating(true)
    try {
      await apiFetch("/api/update/apply", { method: "POST" })
      // Reload after 5s
      setTimeout(() => window.location.reload(), 5000)
    } catch {
      setUpdating(false)
    }
  }

  return (
    <div className="flex h-screen overflow-hidden bg-gradient-to-br from-slate-50 via-blue-50/30 to-purple-50/20">
      <Sidebar />
      <main className="flex-1 overflow-hidden flex flex-col">
        {/* Update banner */}
        {hasUpdate && !dismissed && (
          <div className="bg-gradient-to-r from-indigo-500 to-purple-500 text-white px-4 py-2.5 flex items-center gap-3 text-sm shrink-0">
            <Download className="w-4 h-4 flex-shrink-0" />
            <span className="flex-1">
              Co ban cap nhat moi ({commitCount} commit).
              {updating ? " Dang cap nhat..." : " Click de cap nhat."}
            </span>
            {!updating && (
              <button
                onClick={handleUpdate}
                className="bg-white/20 hover:bg-white/30 px-3 py-1 rounded-lg text-xs font-medium transition-colors"
              >
                Cap nhat
              </button>
            )}
            <button
              onClick={() => setDismissed(true)}
              className="hover:bg-white/20 p-1 rounded transition-colors"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        )}
        <div className="flex-1 overflow-hidden">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
