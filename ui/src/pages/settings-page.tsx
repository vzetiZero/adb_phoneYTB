import { useState, useEffect } from "react"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { apiFetch } from "@/lib/api"
import { Settings, Save, Check, FolderOpen } from "lucide-react"

interface Config {
  adb_path?: string
  adb_server_port?: number
  device_profile?: string
  max_parallel?: number
  [key: string]: unknown
}

export default function SettingsPage() {
  const [config, setConfig] = useState<Config>({})
  const [adbPath, setAdbPath] = useState("")
  const [adbPort, setAdbPort] = useState("5037")
  const [deviceProfile, setDeviceProfile] = useState("boxphone")
  const [maxParallel, setMaxParallel] = useState("3")
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    apiFetch<Config>("/api/config").then((cfg) => {
      setConfig(cfg)
      setAdbPath(cfg.adb_path || "adb")
      setAdbPort(String(cfg.adb_server_port || 5037))
      setDeviceProfile(cfg.device_profile || "boxphone")
      setMaxParallel(String(cfg.max_parallel || 3))
    })
  }, [])

  const handleSave = async () => {
    setSaving(true)
    setSaved(false)
    try {
      const payload: Record<string, unknown> = {
        adb_path: adbPath.trim() || "adb",
        adb_server_port: parseInt(adbPort) || 5037,
        device_profile: deviceProfile.trim() || "boxphone",
        max_parallel: parseInt(maxParallel) || 3,
      }
      await apiFetch("/api/config", {
        method: "PUT",
        body: JSON.stringify(payload),
      })
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch (err) {
      alert("Loi luu config: " + (err as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="flex flex-col h-full p-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-slate-100">
          <Settings className="w-5 h-5 text-slate-600" />
        </div>
        <div>
          <h1 className="text-lg font-bold text-slate-900">Settings</h1>
          <p className="text-xs text-slate-400">
            Cau hinh he thong va duong dan ADB
          </p>
        </div>
      </div>

      <div className="max-w-2xl space-y-6">
        {/* ADB Path */}
        <Card className="border-slate-200">
          <CardContent className="p-5 space-y-4">
            <div className="flex items-center gap-2 mb-1">
              <FolderOpen className="w-4 h-4 text-indigo-500" />
              <h3 className="text-sm font-semibold text-slate-700">
                Duong dan ADB
              </h3>
            </div>

            <div className="space-y-2">
              <Label className="text-slate-600">ADB Executable Path</Label>
              <Input
                value={adbPath}
                onChange={(e) => setAdbPath(e.target.value)}
                placeholder="vd: C:\platform-tools\adb.exe hoac chi 'adb'"
                className="bg-slate-50 border-slate-200 text-sm font-mono"
              />
              <p className="text-[11px] text-slate-400">
                Day la duong dan den file adb.exe. Neu ADB da nam trong PATH, chi can nhap "adb".
              </p>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label className="text-slate-600">ADB Server Port</Label>
                <Input
                  type="number"
                  value={adbPort}
                  onChange={(e) => setAdbPort(e.target.value)}
                  className="bg-slate-50 border-slate-200 text-sm"
                />
              </div>
              <div className="space-y-2">
                <Label className="text-slate-600">Device Profile</Label>
                <Input
                  value={deviceProfile}
                  onChange={(e) => setDeviceProfile(e.target.value)}
                  className="bg-slate-50 border-slate-200 text-sm"
                />
              </div>
            </div>

            <div className="space-y-2">
              <Label className="text-slate-600">Max Parallel Workers</Label>
              <Input
                type="number"
                min={1}
                max={50}
                value={maxParallel}
                onChange={(e) => setMaxParallel(e.target.value)}
                className="bg-slate-50 border-slate-200 text-sm w-24"
              />
            </div>
          </CardContent>
        </Card>

        {/* Save button */}
        <div className="flex items-center gap-3">
          <Button
            onClick={handleSave}
            disabled={saving}
            className="bg-gradient-to-r from-indigo-500 to-purple-500 hover:from-indigo-600 hover:to-purple-600 text-white shadow-md"
          >
            {saved ? (
              <>
                <Check className="mr-2 h-4 w-4" /> Da luu!
              </>
            ) : saving ? (
              "Dang luu..."
            ) : (
              <>
                <Save className="mr-2 h-4 w-4" /> Luu cau hinh
              </>
            )}
          </Button>
          {saved && (
            <span className="text-xs text-green-600 animate-fade-in">
              Config da duoc luu vao config.json
            </span>
          )}
        </div>

        {/* Current config preview */}
        <Card className="border-slate-200 bg-slate-50/50">
          <CardContent className="p-4">
            <p className="text-xs font-medium text-slate-500 mb-2">
              Config hien tai (config.json):
            </p>
            <pre className="text-xs text-slate-600 font-mono whitespace-pre-wrap bg-white rounded-lg p-3 border border-slate-100">
              {JSON.stringify(
                {
                  adb_path: adbPath,
                  adb_server_port: parseInt(adbPort) || 5037,
                  device_profile: deviceProfile,
                  max_parallel: parseInt(maxParallel) || 3,
                },
                null,
                2
              )}
            </pre>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
