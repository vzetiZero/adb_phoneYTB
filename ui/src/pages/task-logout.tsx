import { useState, useEffect } from "react"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { apiFetch } from "@/lib/api"
import { Play, LogOut, AlertTriangle, Square } from "lucide-react"

const STORAGE_KEY = "boxphone_logout_config"

interface TaskLogoutProps {
  devices: { ip: string; name?: string; email?: string; password?: string }[]
  selected: Set<string>
  isRunning: boolean
  onLog: (msg: string, color?: string) => void
  onStatusChange: (running: boolean) => void
  onStepChange?: (step: number) => void
}

export function TaskLogout({ devices, selected, isRunning, onLog, onStatusChange, onStepChange }: TaskLogoutProps) {
  const [workers, setWorkers] = useState(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY)
      if (raw) return JSON.parse(raw).workers || 4
    } catch {}
    return 4
  })

  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ workers })) } catch {}
  }, [workers])

  const handleStart = async () => {
    if (selected.size === 0) {
      onLog("[LOI] Chua chon thiet bi", "#ef4444")
      return
    }

    // Step 0: Pre-flight
    onStepChange?.(0)
    onLog("[PRE-FLIGHT] Dang ve Home + Reset...", "#f59e0b")
    try {
      await apiFetch("/api/tasks/home", {
        method: "POST",
        body: JSON.stringify({ serials: Array.from(selected) }),
      })
      await new Promise((r) => setTimeout(r, 2000))
      onLog("[PRE-FLIGHT] Home done", "#10b981")
    } catch (e: any) {
      onLog(`[PRE-FLIGHT] Loi: ${e?.message || String(e)}`, "#ef4444")
    }

    // Start logout all accounts
    onStepChange?.(1)
    try {
      await apiFetch("/api/tasks/google-logout", {
        method: "POST",
        body: JSON.stringify({ serials: Array.from(selected), workers }),
      })
      onStatusChange(true)
      onLog(`[START] Google Logout - ${selected.size} devices, ${workers} workers`, "#3b82f6")
    } catch (e: any) {
      onLog(`[LOI] ${e?.message || String(e)}`, "#ef4444")
    }
  }

  const handleCancel = async () => {
    onStatusChange(false)
    onLog("[HUY] Dang huy workflow...", "#f59e0b")
    try {
      await apiFetch("/api/tasks/cancel", { method: "POST" })
      onLog("[HUY] Da huy thanh cong", "#10b981")
    } catch (e: any) {
      onLog(`[HUY] Cancel request sent (may still be stopping)`, "#f59e0b")
    }
  }

  return (
    <div className="space-y-4 animate-fade-in">
      <div className="flex items-center gap-2 mb-3">
        <LogOut className="w-4 h-4 text-orange-500" />
        <h3 className="text-sm font-semibold text-slate-700">Google Logout - Tat ca tai khoan</h3>
      </div>

      <p className="text-xs text-slate-400">
        Xoa TAT CA cac tai khoan Google dang co tren thiet bi Samsung (tieng Han).
      </p>

      <div className="bg-orange-50 border border-orange-200 rounded-lg p-3 flex items-start gap-2">
        <AlertTriangle className="w-4 h-4 text-orange-500 mt-0.5 flex-shrink-0" />
        <div className="text-xs text-orange-700">
          <p className="font-semibold mb-1">Canh bao:</p>
          <p>Thao tac nay se xoa tat ca tai khoan Google tren thiet bi. Ban can phai dang nhap lai sau do.</p>
        </div>
      </div>

      <div className="bg-slate-50 rounded-lg p-3 text-xs text-slate-500 space-y-1">
        <p className="font-medium">Quy trinh thuc hien:</p>
        <ul className="list-disc list-inside ml-2 space-y-0.5">
          <li>Quet tat ca tai khoan Google tren thiet bi</li>
          <li>Mo Cai dat {'>'} Accounts</li>
          <li>Xoa tung tai khoan mot</li>
          <li>Xac nhan da xoa thanh cong</li>
        </ul>
      </div>

      <div>
        <Label className="text-slate-600">Workers (song song)</Label>
        <input
          type="number"
          min={1}
          max={50}
          value={workers}
          onChange={(e) => setWorkers(Number(e.target.value) || 4)}
          className="mt-1 w-20 h-8 rounded-md border border-slate-200 bg-slate-50 px-2 text-sm"
        />
      </div>

      <div className="flex justify-end gap-2 pt-2">
        {isRunning ? (
          <Button size="sm" variant="destructive" onClick={handleCancel}>
            <Square className="mr-1 h-3 w-3" /> Huy
          </Button>
        ) : (
          <Button
            size="sm"
            onClick={handleStart}
            disabled={selected.size === 0}
            className="bg-orange-500 hover:bg-orange-600"
          >
            <Play className="mr-1 h-3 w-3" /> Xoa tat ca tai khoan
          </Button>
        )}
      </div>
    </div>
  )
}
