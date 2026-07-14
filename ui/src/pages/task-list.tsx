import { useState } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import { Cpu, LogOut, Home, Play, Square, CheckCircle2, Circle, Loader2 } from "lucide-react"
import { TaskLogin } from "./task-login"
import { TaskLogout } from "./task-logout"
import { TaskHome } from "./task-home"

interface TaskStep {
  label: string
  description: string
}

interface Task {
  id: string
  name: string
  description: string
  icon: typeof Cpu
  color: string
  gradient: string
  steps: TaskStep[]
}

const tasks: Task[] = [
  {
    id: "google-login",
    name: "Google Login",
    description: "Dang nhap Google tren thiet bi Samsung",
    icon: Cpu,
    color: "text-indigo-500",
    gradient: "from-indigo-500 to-blue-500",
    steps: [
      { label: "Pre-flight", description: "Ve Home + Reset tat ca cua so" },
      { label: "Kiem tra", description: "Kiem tra tai khoan da ton tai" },
      { label: "Mo Play Store", description: "Khoi dong Google Play Store" },
      { label: "Nhap Email", description: "Nhap dia chi email vao truong Username" },
      { label: "Nhap Password", description: "Nhap mat khau vao truong Password" },
      { label: "Xac nhan", description: "Bam nut Dong y / Next de hoan tat" },
    ],
  },
  {
    id: "google-logout",
    name: "Google Logout",
    description: "Xoa TAT CA tai khoan Google khoi thiet bi",
    icon: LogOut,
    color: "text-orange-500",
    gradient: "from-orange-500 to-amber-500",
    steps: [
      { label: "Pre-flight", description: "Ve Home + Reset tat ca cua so" },
      { label: "Quet", description: "Tim tat ca tai khoan Google" },
      { label: "Mo Settings", description: "Mo Cai dat > Accounts" },
      { label: "Xoa", description: "Xoa tung tai khoan mot" },
      { label: "Xac nhan", description: "Xac nhan da xoa thanh cong" },
    ],
  },
  {
    id: "home-reset",
    name: "Home & Reset",
    description: "Ve man hinh chinh, dong tat ca app dang mo",
    icon: Home,
    color: "text-emerald-500",
    gradient: "from-emerald-500 to-teal-500",
    steps: [
      { label: "Force-stop", description: "Dong YouTube, Chrome va cac app khac" },
      { label: "Kill all", description: "Giet tat ca process dang chay" },
      { label: "Home", description: "Nut Home ve man hinh chinh" },
    ],
  },
]

interface TaskListProps {
  devices: { ip: string; name?: string; email?: string; password?: string; online?: boolean }[]
  selected: Set<string>
  isRunning: boolean
  onLog: (msg: string, color?: string) => void
  onStatusChange: (running: boolean) => void
}

export function TaskList({ devices, selected, isRunning, onLog, onStatusChange }: TaskListProps) {
  const [activeTab, setActiveTab] = useState<string>("google-login")
  const [currentStep, setCurrentStep] = useState<number>(-1)

  const activeTask = tasks.find((t) => t.id === activeTab)

  const renderTaskForm = () => {
    switch (activeTab) {
      case "google-login":
        return (
          <TaskLogin
            devices={devices}
            selected={selected}
            isRunning={isRunning}
            onLog={onLog}
            onStatusChange={onStatusChange}
            onStepChange={setCurrentStep}
          />
        )
      case "google-logout":
        return (
          <TaskLogout
            devices={devices}
            selected={selected}
            isRunning={isRunning}
            onLog={onLog}
            onStatusChange={onStatusChange}
            onStepChange={setCurrentStep}
          />
        )
      case "home-reset":
        return (
          <TaskHome
            selected={selected}
            isRunning={isRunning}
            onLog={onLog}
            onStepChange={setCurrentStep}
          />
        )
      default:
        return null
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Tab bar */}
      <div className="flex gap-1 mb-4 bg-slate-100 p-1 rounded-xl">
        {tasks.map((task) => {
          const Icon = task.icon
          const isActive = activeTab === task.id
          return (
            <button
              key={task.id}
              onClick={() => {
                setActiveTab(task.id)
                setCurrentStep(-1)
              }}
              className={cn(
                "flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium transition-all duration-200 flex-1",
                isActive
                  ? "bg-white shadow-sm text-slate-900"
                  : "text-slate-500 hover:text-slate-700 hover:bg-white/50"
              )}
            >
              <Icon className={cn("w-4 h-4", isActive ? task.color : "")} />
              <span className="truncate">{task.name}</span>
              {isRunning && isActive && (
                <Loader2 className="w-3 h-3 text-indigo-500 animate-spin ml-auto" />
              )}
            </button>
          )
        })}
      </div>

      {/* Task content */}
      {activeTask && (
        <div className="flex-1 flex flex-col gap-4 min-h-0">
          {/* Task header */}
          <div className="flex items-start gap-3">
            <div className={cn(
              "flex items-center justify-center w-12 h-12 rounded-xl bg-gradient-to-br shadow-lg",
              activeTask.gradient
            )}>
              <activeTask.icon className="w-6 h-6 text-white" />
            </div>
            <div className="flex-1">
              <h2 className="text-base font-bold text-slate-900">{activeTask.name}</h2>
              <p className="text-xs text-slate-400">{activeTask.description}</p>
            </div>
            {isRunning && (
              <Badge variant="warning">
                <Loader2 className="w-3 h-3 animate-spin mr-1" />
                Dang chay
              </Badge>
            )}
          </div>

          {/* Workflow steps */}
          <Card className="border-slate-200">
            <CardHeader className="pb-2">
              <CardTitle className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
                Quy trinh
              </CardTitle>
            </CardHeader>
            <CardContent className="p-3 pt-0">
              <div className="flex items-center gap-1 overflow-x-auto pb-1">
                {activeTask.steps.map((step, i) => {
                  const isCompleted = currentStep > i
                  const isCurrent = currentStep === i
                  const isPending = currentStep < i
                  return (
                    <div key={i} className="flex items-center gap-1 flex-shrink-0">
                      <div className="flex flex-col items-center gap-1">
                        <div className={cn(
                          "w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold transition-all duration-300",
                          isCompleted && "bg-emerald-500 text-white",
                          isCurrent && "bg-indigo-500 text-white animate-pulse",
                          isPending && "bg-slate-200 text-slate-400"
                        )}>
                          {isCompleted ? (
                            <CheckCircle2 className="w-4 h-4" />
                          ) : isCurrent ? (
                            <Loader2 className="w-4 h-4 animate-spin" />
                          ) : (
                            <Circle className="w-3 h-3" />
                          )}
                        </div>
                        <span className={cn(
                          "text-[9px] font-medium text-center max-w-[60px] leading-tight",
                          isCurrent ? "text-indigo-600" : "text-slate-400"
                        )}>
                          {step.label}
                        </span>
                      </div>
                      {i < activeTask.steps.length - 1 && (
                        <div className={cn(
                          "w-6 h-0.5 mt-[-14px]",
                          isCompleted ? "bg-emerald-400" : "bg-slate-200"
                        )} />
                      )}
                    </div>
                  )
                })}
              </div>
            </CardContent>
          </Card>

          {/* Task form */}
          <Card className="border-slate-200 flex-1 overflow-auto">
            <CardContent className="p-4">
              {renderTaskForm()}
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  )
}
