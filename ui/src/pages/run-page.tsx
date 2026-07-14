import { useState, useEffect, useRef, useCallback } from "react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Badge } from "@/components/ui/badge"
import { apiFetch, createLogSocket } from "@/lib/api"
import { RefreshCw, Users, ListTodo } from "lucide-react"
import { TaskList } from "./task-list"

interface Device {
  ip: string
  name?: string
  email?: string
  password?: string
  online?: boolean
  adb_state?: string
}

interface LogEntry {
  text: string
  color: string
}

function getLogColor(msg: string): string {
  if (/[LOI]|ERROR|fail/i.test(msg)) return "#ef4444"
  if (/\[OK|ok=True|\[XONG/.test(msg)) return "#10b981"
  if (/\[HUY\]|CANCEL/.test(msg)) return "#f59e0b"
  return "#334155"
}

export default function RunPage() {
  const [devices, setDevices] = useState<Device[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [isRunning, setIsRunning] = useState(false)
  const logEndRef = useRef<HTMLDivElement>(null)
  const wsRef = useRef<WebSocket | null>(null)

  const fetchDevices = useCallback(async () => {
    try {
      const data = await apiFetch<{ adb_ok: boolean; devices: Device[] }>("/api/devices/status")
      setDevices(data.devices || [])
    } catch (e) {
      console.error("Failed to fetch devices:", e)
    }
  }, [])

  const fetchStatus = useCallback(async () => {
    try {
      const data = await apiFetch<{ running: boolean }>("/api/tasks/status")
      setIsRunning(data.running)
    } catch {}
  }, [])

  useEffect(() => {
    fetchDevices()
    fetchStatus()
    wsRef.current = createLogSocket((msg) => {
      setLogs((prev) => [...prev.slice(-500), { text: msg, color: getLogColor(msg) }])
    })
    return () => { wsRef.current?.close() }
  }, [fetchDevices, fetchStatus])

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [logs])

  useEffect(() => {
    if (!isRunning) return
    const interval = setInterval(fetchStatus, 2000)
    return () => clearInterval(interval)
  }, [isRunning, fetchStatus])

  const toggleDevice = (ip: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(ip)) next.delete(ip)
      else next.add(ip)
      return next
    })
  }

  const selectAll = () => setSelected(new Set(devices.map((d) => d.ip)))
  const clearSelection = () => setSelected(new Set())

  const handleLog = (msg: string, color?: string) => {
    setLogs((prev) => [...prev.slice(-500), { text: msg, color: color || getLogColor(msg) }])
  }

  return (
    <div className="flex flex-col h-full p-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-indigo-100">
          <ListTodo className="w-5 h-5 text-indigo-600" />
        </div>
        <div>
          <h1 className="text-lg font-bold text-slate-900">Nhiem vu</h1>
          <p className="text-xs text-slate-400">Chon thiet bi, chon task, bat dau</p>
        </div>
        <div className="ml-auto flex gap-2 items-center">
          <Badge variant={isRunning ? "warning" : "secondary"}>
            {isRunning ? "Dang chay" : "San sang"}
          </Badge>
          <span className="text-xs text-slate-400">
            {devices.filter((d) => d.online).length}/{devices.length} online
          </span>
        </div>
      </div>

      {/* Content: 3 columns */}
      <div className="grid grid-cols-[250px_1fr_350px] gap-4 flex-1 min-h-0">
        {/* Left: Device selection */}
        <div className="flex flex-col gap-4">
          <Card className="border-slate-200 flex-1 flex flex-col min-h-0">
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-sm text-slate-700">
                <Users className="w-4 h-4 text-indigo-500" />
                Thiet bi
              </CardTitle>
            </CardHeader>
            <CardContent className="flex-1 flex flex-col min-h-0 p-3 pt-0">
              <ScrollArea className="flex-1">
                {devices.length === 0 ? (
                  <p className="text-sm text-slate-400 py-4 text-center">Khong co thiet bi</p>
                ) : (
                  <div className="space-y-0.5">
                    {devices.map((d) => (
                      <label
                        key={d.ip}
                        className="flex items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-slate-50 cursor-pointer transition-colors"
                      >
                        <Checkbox
                          checked={selected.has(d.ip)}
                          onCheckedChange={() => toggleDevice(d.ip)}
                        />
                        <div className={`w-2 h-2 rounded-full flex-shrink-0 ${d.online ? "bg-emerald-500" : "bg-slate-300"}`} />
                        <div className="flex-1 min-w-0">
                          <span className="text-sm text-slate-600 block truncate">
                            {d.name || d.ip}
                          </span>
                          {d.email && (
                            <span className="text-[10px] text-slate-400 block truncate">
                              {d.email}
                            </span>
                          )}
                        </div>
                        {!d.online && d.adb_state && d.adb_state !== "offline" && (
                          <span className="text-[9px] text-amber-500">{d.adb_state}</span>
                        )}
                      </label>
                    ))}
                  </div>
                )}
              </ScrollArea>
              <div className="mt-2 flex gap-1">
                <Button variant="outline" size="sm" className="flex-1" onClick={fetchDevices}>
                  <RefreshCw className="mr-1 h-3 w-3" /> Refresh
                </Button>
                <Button variant="outline" size="sm" className="flex-1" onClick={selectAll}>All</Button>
                <Button variant="outline" size="sm" className="flex-1" onClick={clearSelection}>Clear</Button>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Center: Task list */}
        <div className="flex flex-col min-h-0">
          <TaskList
            devices={devices}
            selected={selected}
            isRunning={isRunning}
            onLog={handleLog}
            onStatusChange={setIsRunning}
          />
        </div>

        {/* Right: Log panel */}
        <Card className="border-slate-200 flex flex-col min-h-0">
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-sm text-slate-700">
              <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
              Log
              <span className="text-[10px] text-slate-400 font-normal ml-auto">{logs.length}</span>
            </CardTitle>
          </CardHeader>
          <CardContent className="flex-1 min-h-0 p-3 pt-0">
            <ScrollArea className="h-full">
              <div className="font-mono text-xs space-y-0.5">
                {logs.length === 0 && (
                  <p className="text-slate-300 py-4 text-center">Chua co log</p>
                )}
                {logs.map((entry, i) => (
                  <div key={i} style={{ color: entry.color }}>
                    {entry.text}
                  </div>
                ))}
                <div ref={logEndRef} />
              </div>
            </ScrollArea>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
