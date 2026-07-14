import { useState, useEffect, useCallback } from "react"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Badge } from "@/components/ui/badge"
import { apiFetch } from "@/lib/api"
import { History, RefreshCw } from "lucide-react"

interface TaskRun {
  id: number
  ts: string
  serial: string
  app: string
  keyword: string
  requested_loops: number
  done_loops: number
  status: string
  note?: string
}

const statusConfig: Record<string, { variant: "success" | "warning" | "destructive" | "secondary"; label: string }> = {
  ok: { variant: "success", label: "OK" },
  partial: { variant: "warning", label: "Partial" },
  fail: { variant: "destructive", label: "Fail" },
  error: { variant: "destructive", label: "Error" },
}

export default function HistoryPage() {
  const [runs, setRuns] = useState<TaskRun[]>([])

  const fetchHistory = useCallback(async () => {
    try {
      const data = await apiFetch<TaskRun[]>("/api/history?limit=200")
      setRuns(data)
    } catch {}
  }, [])

  useEffect(() => { fetchHistory() }, [fetchHistory])

  return (
    <div className="flex flex-col h-full p-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-orange-100">
          <History className="w-5 h-5 text-orange-600" />
        </div>
        <div>
          <h1 className="text-lg font-bold text-slate-900">Lich su</h1>
          <p className="text-xs text-slate-400">Cac task da chay gan day</p>
        </div>
        <Button variant="outline" size="sm" className="ml-auto" onClick={fetchHistory}>
          <RefreshCw className="mr-1 h-3 w-3" /> Reload
        </Button>
      </div>

      <Card className="border-slate-200 flex-1 flex flex-col min-h-0">
        <CardContent className="flex-1 overflow-auto p-0">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50/50">
                <th className="p-3 text-left font-medium text-slate-500">Time</th>
                <th className="p-3 text-left font-medium text-slate-500">Serial</th>
                <th className="p-3 text-left font-medium text-slate-500">App</th>
                <th className="p-3 text-left font-medium text-slate-500">Keyword</th>
                <th className="p-3 text-center font-medium text-slate-500">Requested</th>
                <th className="p-3 text-center font-medium text-slate-500">Done</th>
                <th className="p-3 text-left font-medium text-slate-500">Status</th>
              </tr>
            </thead>
            <tbody>
              {runs.length === 0 ? (
                <tr>
                  <td colSpan={7} className="p-8 text-center">
                    <div className="flex flex-col items-center gap-2">
                      <div className="w-16 h-16 rounded-2xl bg-slate-100 flex items-center justify-center">
                        <History className="w-8 h-8 text-slate-300" />
                      </div>
                      <p className="text-sm text-slate-400">Chua co du lieu</p>
                    </div>
                  </td>
                </tr>
              ) : (
                runs.map((r) => {
                  const cfg = statusConfig[r.status] || { variant: "secondary" as const, label: r.status }
                  return (
                    <tr key={r.id} className="border-b border-slate-100 hover:bg-slate-50/50 transition-colors">
                      <td className="p-3 text-xs text-slate-400">{r.ts}</td>
                      <td className="p-3 font-mono text-xs text-slate-600">{r.serial}</td>
                      <td className="p-3">{r.app}</td>
                      <td className="p-3">{r.keyword}</td>
                      <td className="p-3 text-center">{r.requested_loops}</td>
                      <td className="p-3 text-center">{r.done_loops}</td>
                      <td className="p-3">
                        <Badge variant={cfg.variant}>{cfg.label}</Badge>
                      </td>
                    </tr>
                  )
                })
              )}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  )
}
