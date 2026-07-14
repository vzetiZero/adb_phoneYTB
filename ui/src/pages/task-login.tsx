import { useState, useRef } from "react"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Badge } from "@/components/ui/badge"
import { apiFetch } from "@/lib/api"
import { Play, Upload, Trash2, Cpu } from "lucide-react"

interface TaskLoginProps {
  devices: { ip: string; name?: string; email?: string; password?: string }[]
  selected: Set<string>
  isRunning: boolean
  onLog: (msg: string, color?: string) => void
  onStatusChange: (running: boolean) => void
  onStepChange?: (step: number) => void
}

export function TaskLogin({ devices, selected, isRunning, onLog, onStatusChange, onStepChange }: TaskLoginProps) {
  const [emails, setEmails] = useState("")
  const [passwords, setPasswords] = useState("")
  const [workers, setWorkers] = useState(4)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleFileImport = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = (ev) => {
      const text = ev.target?.result as string
      const lines = text.split("\n").map((l) => l.trim()).filter((l) => l && !l.startsWith("#"))
      const emailArr: string[] = []
      const passArr: string[] = []
      for (const line of lines) {
        if (line.includes("|")) {
          const idx = line.indexOf("|")
          emailArr.push(line.substring(0, idx).trim())
          passArr.push(line.substring(idx + 1).trim())
        } else {
          emailArr.push(line)
          passArr.push("")
        }
      }
      setEmails(emailArr.join("\n"))
      setPasswords(passArr.join("\n"))
      onLog(`[IMPORT] Da tai ${emailArr.length} tai khoan tu file`, "#10b981")
    }
    reader.readAsText(file)
    e.target.value = ""
  }

  const handleStart = async () => {
    if (selected.size === 0) {
      onLog("[LOI] Chua chon thiet bi", "#ef4444")
      return
    }

    const emailLines = emails.split("\n").filter((l) => l.trim())
    const passLines = passwords.split("\n").filter((l) => l.trim())
    let credentials: { serial?: string; email: string; password: string }[] = []

    if (emailLines.length > 0 && passLines.length > 0) {
      if (emailLines.length !== passLines.length) {
        onLog("[LOI] So dong email va password khong khop", "#ef4444")
        return
      }
      for (const serial of selected) {
        for (let i = 0; i < emailLines.length; i++) {
          credentials.push({ serial, email: emailLines[i].trim(), password: passLines[i].trim() })
        }
      }
    } else {
      for (const serial of selected) {
        const dev = devices.find((d) => d.ip === serial)
        if (dev?.email) {
          credentials.push({ serial, email: dev.email, password: dev.password || "" })
        }
      }
      if (credentials.length === 0) {
        onLog("[LOI] Khong co tai khoan nao duoc gan cho thiet bi", "#ef4444")
        return
      }
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
      onLog(`[PRE-FLIGHT] Loi: ${e.message}`, "#ef4444")
    }

    // Start login
    onStepChange?.(1)
    try {
      await apiFetch("/api/tasks/google-login", {
        method: "POST",
        body: JSON.stringify({ credentials, workers, per_device: true }),
      })
      onStatusChange(true)
      onLog("[START] Google Login bat dau", "#3b82f6")
    } catch (e: any) {
      onLog(`[LOI] ${e.message}`, "#ef4444")
    }
  }

  return (
    <div className="space-y-4 animate-fade-in">
      <div className="flex items-center gap-2 mb-3">
        <Cpu className="w-4 h-4 text-indigo-500" />
        <h3 className="text-sm font-semibold text-slate-700">Cau hinh Google Login</h3>
      </div>

      <p className="text-xs text-slate-400">
        Dang nhap Google tren thiet bi da chon bang cau hinh trong config.json.
      </p>

      <div className="flex items-center gap-2">
        <Button variant="outline" size="sm" onClick={() => fileInputRef.current?.click()}>
          <Upload className="mr-1 h-3 w-3" /> Chon file
        </Button>
        <span className="text-xs text-slate-400">Dinh dang: email|password</span>
        <input ref={fileInputRef} type="file" accept=".txt" className="hidden" onChange={handleFileImport} />
      </div>

      <div>
        <Label className="text-slate-600">Email (moi dong 1)</Label>
        <Textarea
          placeholder="email1@gmail.com&#10;email2@gmail.com"
          value={emails}
          onChange={(e) => setEmails(e.target.value)}
          className="mt-1 h-20 bg-slate-50 border-slate-200 text-sm"
        />
      </div>

      <div>
        <Label className="text-slate-600">Password (moi dong 1 tuong ung)</Label>
        <Textarea
          placeholder="password1&#10;password2"
          value={passwords}
          onChange={(e) => setPasswords(e.target.value)}
          className="mt-1 h-20 bg-slate-50 border-slate-200 text-sm"
        />
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
        <Button variant="outline" size="sm" onClick={() => { setEmails(""); setPasswords("") }}>
          <Trash2 className="mr-1 h-3 w-3" /> Xoa trang
        </Button>
        <Button size="sm" onClick={handleStart} disabled={isRunning || selected.size === 0}>
          <Play className="mr-1 h-3 w-3" /> Bat dau
        </Button>
      </div>
    </div>
  )
}
