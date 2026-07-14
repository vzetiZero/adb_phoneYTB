import { Button } from "@/components/ui/button"
import { apiFetch } from "@/lib/api"
import { Play, Home } from "lucide-react"

interface TaskHomeProps {
  selected: Set<string>
  isRunning: boolean
  onLog: (msg: string, color?: string) => void
  onStepChange?: (step: number) => void
}

export function TaskHome({ selected, isRunning, onLog, onStepChange }: TaskHomeProps) {
  const handleStart = async () => {
    if (selected.size === 0) {
      onLog("[LOI] Chua chon thiet bi", "#ef4444")
      return
    }

    onStepChange?.(0)
    try {
      await apiFetch("/api/tasks/home", {
        method: "POST",
        body: JSON.stringify(Array.from(selected)),
      })
      onStepChange?.(1)
      await new Promise((r) => setTimeout(r, 500))
      onStepChange?.(2)
      onLog(`[HOME] Da ve Home tren ${selected.size} thiet bi`, "#10b981")
    } catch (e: any) {
      onLog(`[LOI] ${e.message}`, "#ef4444")
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

      <div className="flex justify-end pt-2">
        <Button
          size="sm"
          variant="outline"
          onClick={handleStart}
          disabled={isRunning || selected.size === 0}
          className="border-emerald-200 text-emerald-600 hover:bg-emerald-50"
        >
          <Play className="mr-1 h-3 w-3" /> Bat dau
        </Button>
      </div>
    </div>
  )
}
