import { useState, useEffect, useCallback, useRef } from "react"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { apiFetch } from "@/lib/api"
import {
  Smartphone,
  Download,
  Pencil,
  Trash2,
  Hash,
  Upload,
  UserPlus,
  FileDown,
  FileUp,
  RotateCcw,
  X,
  Check,
} from "lucide-react"

interface Device {
  ip: string
  name?: string
  email?: string
  password?: string
  width?: number
  height?: number
  last_seen?: string
}

interface Account {
  email: string
  password: string
}

export default function DevicesPage() {
  const [devices, setDevices] = useState<Device[]>([])
  const [accountPool, setAccountPool] = useState<Account[]>([])
  const [assigningIdx, setAssigningIdx] = useState<number | null>(null)
  const [editRow, setEditRow] = useState<number | null>(null)
  const [editField, setEditField] = useState<string>("")
  const [editValue, setEditValue] = useState("")
  const fileInputRef = useRef<HTMLInputElement>(null)
  const dbFileRef = useRef<HTMLInputElement>(null)

  const fetchDevices = useCallback(async () => {
    try {
      const data = await apiFetch<Device[]>("/api/devices")
      setDevices(data)
    } catch {}
  }, [])

  useEffect(() => {
    fetchDevices()
  }, [fetchDevices])

  // ── Device actions ──────────────────────────────────────────────

  const handleImport = async () => {
    try {
      await apiFetch("/api/devices/import", { method: "POST" })
      await fetchDevices()
    } catch {}
  }

  const handleAutoNumber = async () => {
    try {
      await apiFetch("/api/devices/auto-number", { method: "POST" })
      await fetchDevices()
    } catch {}
  }

  const handleDelete = async (ip: string) => {
    if (!confirm("Xoa thiet bi nay?")) return
    try {
      await apiFetch(`/api/devices/${encodeURIComponent(ip)}`, { method: "DELETE" })
      await fetchDevices()
    } catch {}
  }

  // ── Inline edit ─────────────────────────────────────────────────

  const startEdit = (row: number, field: string, value: string) => {
    setEditRow(row)
    setEditField(field)
    setEditValue(value)
  }

  const saveEdit = async (ip: string) => {
    try {
      const body: Record<string, string> = {}
      body[editField] = editValue
      await apiFetch(`/api/devices/${encodeURIComponent(ip)}`, {
        method: "PUT",
        body: JSON.stringify(body),
      })
      setEditRow(null)
      await fetchDevices()
    } catch {}
  }

  // ── Account pool (import → manual assign) ───────────────────────

  const handleImportAccounts = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = (ev) => {
      const text = ev.target?.result as string
      const lines = text
        .split("\n")
        .map((l) => l.trim())
        .filter((l) => l && !l.startsWith("#"))
      const accounts: Account[] = lines.map((line) => {
        if (line.includes("|")) {
          const idx = line.indexOf("|")
          return {
            email: line.substring(0, idx).trim(),
            password: line.substring(idx + 1).trim(),
          }
        }
        return { email: line, password: "" }
      })
      setAccountPool((prev) => [...prev, ...accounts])
    }
    reader.readAsText(file)
    e.target.value = ""
  }

  const handleAssignToDevice = async (poolIdx: number, deviceIp: string) => {
    const acc = accountPool[poolIdx]
    try {
      await apiFetch(`/api/devices/${encodeURIComponent(deviceIp)}`, {
        method: "PUT",
        body: JSON.stringify({ email: acc.email, password: acc.password }),
      })
      setAccountPool((prev) => prev.filter((_, i) => i !== poolIdx))
      setAssigningIdx(null)
      await fetchDevices()
    } catch {}
  }

  const handleClearPool = () => {
    if (accountPool.length > 0 && !confirm("Xoa het account trong pool?")) return
    setAccountPool([])
  }

  // ── DB management ───────────────────────────────────────────────

  const handleExportDB = async () => {
    try {
      const res = await fetch("/api/devices/export-db")
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = "app_backup.json"
      a.click()
      URL.revokeObjectURL(url)
    } catch {}
  }

  const handleImportDB = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    if (
      !confirm(
        "Nhap database se XOA toan bo du lieu hien tai va thay bang du lieu moi. Ban co chac chan?"
      )
    ) {
      e.target.value = ""
      return
    }
    try {
      const text = await file.text()
      const data = JSON.parse(text)
      await apiFetch("/api/devices/import-db", {
        method: "POST",
        body: JSON.stringify(data),
      })
      await fetchDevices()
    } catch (err) {
      alert("Loi import: " + (err as Error).message)
    }
    e.target.value = ""
  }

  const handleResetDB = async () => {
    if (
      !confirm(
        "XOA TOAN BO DATABASE? Tat ca thiet bi, tai khoan, lich su se bi xoa!"
      )
    )
      return
    try {
      await apiFetch("/api/devices/reset-db", { method: "POST" })
      setAccountPool([])
      await fetchDevices()
    } catch {}
  }

  // ── Render ──────────────────────────────────────────────────────

  return (
    <div className="flex flex-col h-full p-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-purple-100">
          <Smartphone className="w-5 h-5 text-purple-600" />
        </div>
        <div>
          <h1 className="text-lg font-bold text-slate-900">Thiet bi</h1>
          <p className="text-xs text-slate-400">
            Quan ly thiet bi ADB va tai khoan Google
          </p>
        </div>
        <Badge variant="secondary" className="ml-auto">
          {devices.length} thiet bi
        </Badge>
      </div>

      {/* Device table */}
      <Card className="border-slate-200 flex-1 flex flex-col min-h-0">
        <CardContent className="flex-1 overflow-auto p-0">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50/50">
                <th className="p-3 text-left font-medium text-slate-500">
                  IP / Serial
                </th>
                <th className="p-3 text-left font-medium text-slate-500">
                  Name
                </th>
                <th className="p-3 text-left font-medium text-slate-500">
                  Email
                </th>
                <th className="p-3 text-left font-medium text-slate-500">
                  Password
                </th>
                <th className="p-3 text-left font-medium text-slate-500">
                  Size
                </th>
                <th className="p-3 text-left font-medium text-slate-500">
                  Last seen
                </th>
                <th className="p-3 text-left font-medium text-slate-500">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody>
              {devices.map((d, i) => (
                <tr
                  key={d.ip}
                  className="border-b border-slate-100 hover:bg-slate-50/50 transition-colors"
                >
                  <td className="p-3 font-mono text-xs text-slate-600">
                    {d.ip}
                  </td>
                  <td className="p-3">
                    {editRow === i && editField === "name" ? (
                      <Input
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        onBlur={() => saveEdit(d.ip)}
                        onKeyDown={(e) =>
                          e.key === "Enter" && saveEdit(d.ip)
                        }
                        className="h-7 text-xs bg-slate-50 border-slate-200"
                        autoFocus
                      />
                    ) : (
                      <span
                        className="cursor-pointer hover:text-indigo-600 transition-colors"
                        onClick={() => startEdit(i, "name", d.name || "")}
                      >
                        {d.name || (
                          <span className="text-slate-300">-</span>
                        )}
                      </span>
                    )}
                  </td>
                  <td className="p-3">
                    {editRow === i && editField === "email" ? (
                      <Input
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        onBlur={() => saveEdit(d.ip)}
                        onKeyDown={(e) =>
                          e.key === "Enter" && saveEdit(d.ip)
                        }
                        className="h-7 text-xs bg-slate-50 border-slate-200"
                        autoFocus
                      />
                    ) : (
                      <span
                        className="cursor-pointer hover:text-indigo-600 transition-colors"
                        onClick={() => startEdit(i, "email", d.email || "")}
                      >
                        {d.email || (
                          <span className="text-slate-300">-</span>
                        )}
                      </span>
                    )}
                  </td>
                  <td className="p-3">
                    {editRow === i && editField === "password" ? (
                      <Input
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        onBlur={() => saveEdit(d.ip)}
                        onKeyDown={(e) =>
                          e.key === "Enter" && saveEdit(d.ip)
                        }
                        className="h-7 text-xs bg-slate-50 border-slate-200"
                        autoFocus
                        type="password"
                      />
                    ) : (
                      <span
                        className="cursor-pointer hover:text-indigo-600 transition-colors"
                        onClick={() =>
                          startEdit(i, "password", d.password || "")
                        }
                      >
                        {d.password ? (
                          "*".repeat(Math.min(d.password.length, 8))
                        ) : (
                          <span className="text-slate-300">-</span>
                        )}
                      </span>
                    )}
                  </td>
                  <td className="p-3 text-xs text-slate-400">
                    {d.width && d.height ? `${d.width}x${d.height}` : "-"}
                  </td>
                  <td className="p-3 text-xs text-slate-400">
                    {d.last_seen || "-"}
                  </td>
                  <td className="p-3">
                    <div className="flex gap-1">
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7"
                        title="Rename"
                        onClick={() => startEdit(i, "name", d.name || "")}
                      >
                        <Pencil className="h-3 w-3" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7"
                        title="Set Account"
                        onClick={() => {
                          const email = prompt("Email:", d.email || "")
                          if (email === null) return
                          const password = prompt("Password:", d.password || "")
                          if (password === null) return
                          apiFetch(
                            `/api/devices/${encodeURIComponent(d.ip)}`,
                            {
                              method: "PUT",
                              body: JSON.stringify({ email, password }),
                            }
                          ).then(() => fetchDevices())
                        }}
                      >
                        <UserPlus className="h-3 w-3" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7 text-destructive"
                        title="Delete"
                        onClick={() => handleDelete(d.ip)}
                      >
                        <Trash2 className="h-3 w-3" />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
              {devices.length === 0 && (
                <tr>
                  <td
                    colSpan={7}
                    className="p-8 text-center text-sm text-slate-400"
                  >
                    Chua co thiet bi. Nhan "Import from ADB" de quet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </CardContent>
      </Card>

      {/* Action buttons */}
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Button variant="outline" size="sm" onClick={handleImport}>
          <Download className="mr-1 h-3 w-3" /> Import ADB
        </Button>
        <Button variant="outline" size="sm" onClick={handleAutoNumber}>
          <Hash className="mr-1 h-3 w-3" /> Auto Number
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => fileInputRef.current?.click()}
        >
          <Upload className="mr-1 h-3 w-3" /> Import Accounts
        </Button>
        <input
          ref={fileInputRef}
          type="file"
          accept=".txt"
          className="hidden"
          onChange={handleImportAccounts}
        />

        {/* DB management — right side */}
        <div className="ml-auto flex gap-2">
          <Button variant="outline" size="sm" onClick={handleExportDB}>
            <FileDown className="mr-1 h-3 w-3" /> Export DB
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => dbFileRef.current?.click()}
          >
            <FileUp className="mr-1 h-3 w-3" /> Import DB
          </Button>
          <input
            ref={dbFileRef}
            type="file"
            accept=".json"
            className="hidden"
            onChange={handleImportDB}
          />
          <Button variant="destructive" size="sm" onClick={handleResetDB}>
            <RotateCcw className="mr-1 h-3 w-3" /> Reset DB
          </Button>
        </div>
      </div>

      {/* Account Pool — manual assignment */}
      {accountPool.length > 0 && (
        <Card className="mt-4 border-indigo-200 bg-indigo-50/30">
          <CardContent className="p-4">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <UserPlus className="w-4 h-4 text-indigo-500" />
                <span className="text-sm font-semibold text-slate-700">
                  Account Pool ({accountPool.length} tai khoan)
                </span>
              </div>
              <Button
                variant="ghost"
                size="sm"
                className="text-xs text-slate-400 hover:text-red-500"
                onClick={handleClearPool}
              >
                Xoa tat ca
              </Button>
            </div>

            <div className="max-h-56 overflow-auto space-y-1">
              {accountPool.map((acc, idx) => (
                <div
                  key={idx}
                  className="flex items-center gap-2 p-2 rounded bg-white border border-slate-100 text-xs"
                >
                  <span className="font-mono text-slate-600 flex-1 truncate">
                    {acc.email}
                  </span>
                  {acc.password && (
                    <span className="text-slate-400 text-[10px]">
                      {acc.password.slice(0, 3)}***
                    </span>
                  )}

                  {assigningIdx === idx ? (
                    <div className="flex items-center gap-1">
                      <select
                        className="h-7 text-xs border border-slate-200 rounded px-2 bg-white min-w-[160px]"
                        onChange={(e) => {
                          if (e.target.value)
                            handleAssignToDevice(idx, e.target.value)
                        }}
                        defaultValue=""
                        autoFocus
                      >
                        <option value="" disabled>
                          Chon device...
                        </option>
                        {devices.map((d) => (
                          <option key={d.ip} value={d.ip}>
                            {d.name || d.ip}
                            {d.email ? ` (${d.email})` : ""}
                          </option>
                        ))}
                      </select>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-6 w-6"
                        onClick={() => setAssigningIdx(null)}
                      >
                        <X className="h-3 w-3" />
                      </Button>
                    </div>
                  ) : (
                    <div className="flex gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 text-xs text-indigo-600 hover:bg-indigo-100"
                        onClick={() => setAssigningIdx(idx)}
                      >
                        Gan cho device
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7 text-slate-400 hover:text-red-500"
                        onClick={() =>
                          setAccountPool((prev) =>
                            prev.filter((_, i) => i !== idx)
                          )
                        }
                      >
                        <X className="h-3 w-3" />
                      </Button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
