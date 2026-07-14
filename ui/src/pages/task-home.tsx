import { Button } from "@/components/ui/button"
import { apiFetch } from "@/lib/api"
import { Play, Home, Square } from "lucide-react"

interface TaskHomeProps {
  selected: Set<string>
  isRunning: boolean
  onLog: (msg: string, color?: string) => void
  onStatusChange: (running: boolean) => void
  onStepChange?: (step: number) => void
}

export function TaskHome({ selected, isRunning, onLog, onStatusChange, onStepChange }: TaskHomeProps) {
  const handleStart = async () => {
    if (selected.size === 0) {
      onLog("[LOI] Chua chon thiet bi", "#ef4444")
      return
    }

    onStepChange?.(0)
    try {
      const res = await apiFetch<{ results: { serial: string; ok: boolean; error?: string }[] }>("/api/tasks/home", {
        method: "POST",
        body: JSON.stringify({ serials: Array.from(selected) }),
      })
      onStepChange?.(1)
      await new Promise((r) => setTimeout(r, 500))
      onStepChange?.(2)

      const failed = res.results?.filter((r) => !r.ok) || []
      const success = res.results?.filter((r) => r.ok) || []

      if (success.length > 0) {
        onLog(`[HOME] Da ve Home tren ${success.length} thiet bi`, "#10b981")
      }
      for (const f of failed) {
        onLog(`[HOME] ${f.serial} LOI: ${f.error || "khong xac dinh"}`, "#ef4444")
      }
    } catch (e: any) {
      const msg = e?.message || String(e)
      onLog(`[LOI] ${msg}`, "#ef4444")
    }
  }

  const handleCancel = async () => {
    try {
      await apiFetch("/api/tasks/cancel", { method: "POST" })
      onLog("[HUY] Da huy workflow", "#f59e0b")
      onStatusChange(false)
    } catch (e: any) {
      onLog(`[LOI] Khong the huy: ${e?.message || String(e)}`, "#ef4444")
    }
  }

  return (
    <div className="space-y-4 animate-fade-in">
      <div className="flex items-center gap-2 mb-3">
        <Home className="w-4 h-4 text-emerald-500" />
        <h3 className="text-sm font-semibold text-slate-700">Home & Reset</h3>
      </div>

      <p className="text-xs text-slate-400">
        Ve man hinh chinh, dong tat ca app dang mo tren thiet bi.
      </p>

      <div className="bg-slate-50 rounded-lg p-3 text-xs text-slate-500 space-y-1">
        <p>Thuc hien tren moi thiet bi da chon:</p>
        <ul className="list-disc list-inside ml-2">
          <li>Force-stop YouTube</li>
          <li>Force-stop Chrome</li>
          <li>Kill tat ca app</li>
          <li>Nut Home</li>
        </ul>
      </div>

      <div className="flex justify-end gap-2 pt-2">
        {isRunning ? (
          <Button size="sm" variant="destructive" onClick={handleCancel}>
            <Square className="mr-1 h-3 w-3" /> Huy
          </Button>
        ) : (
          <Button
            size="sm"
            variant="outline"
            onClick={handleStart}
            disabled={selected.size === 0}
            className="border-emerald-200 text-emerald-600 hover:bg-emerald-50"
          >
            <Play className="mr-1 h-3 w-3" /> Bat dau
          </Button>
        )}
      </div>
    </div>
  )
}
