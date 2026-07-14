import { useState } from "react"
import { NavLink, useLocation } from "react-router-dom"
import { cn } from "@/lib/utils"
import {
  Play,
  Smartphone,
  History,
  Settings,
  ChevronLeft,
  ChevronRight,
  Cpu,
  Zap,
} from "lucide-react"

export function Sidebar() {
  const [collapsed, setCollapsed] = useState(false)
  const location = useLocation()

  const navItems = [
    { to: "/", icon: Play, label: "Chay tac vu", color: "from-blue-500 to-cyan-500" },
    { to: "/devices", icon: Smartphone, label: "Thiet bi", color: "from-purple-500 to-pink-500" },
    { to: "/history", icon: History, label: "Lich su", color: "from-orange-500 to-amber-500" },
    { to: "/settings", icon: Settings, label: "Settings", color: "from-slate-500 to-gray-500" },
  ]

  return (
    <aside
      className={cn(
        "flex flex-col border-r border-slate-200/80 bg-white/95 backdrop-blur-sm transition-all duration-300 ease-in-out shadow-sm",
        collapsed ? "w-[70px]" : "w-[240px]"
      )}
    >
      {/* Logo */}
      <div className="flex items-center gap-3 px-5 py-5 border-b border-slate-100">
        <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-gradient-to-br from-indigo-500 via-purple-500 to-pink-500 shadow-lg shadow-indigo-200/50 hover:shadow-xl hover:shadow-indigo-300/50 transition-all duration-300 hover:scale-105">
          <Cpu className="w-5 h-5 text-white" />
        </div>
        {!collapsed && (
          <div className="flex flex-col animate-fade-in">
            <span className="text-base font-bold bg-gradient-to-r from-indigo-600 to-purple-600 bg-clip-text text-transparent">BoxPhone</span>
            <span className="text-[11px] text-slate-400 font-medium">Automation</span>
          </div>
        )}
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-3 py-4 space-y-1">
        {!collapsed && (
          <p className="px-3 mb-2 text-[10px] font-semibold text-slate-400 uppercase tracking-wider">Menu</p>
        )}
        {navItems.map((item, index) => {
          const isActive = location.pathname === item.to
          return (
            <NavLink
              key={item.to}
              to={item.to}
              className={cn(
                "flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-all duration-200 group",
                isActive
                  ? "bg-gradient-to-r from-indigo-50 to-purple-50 text-indigo-600 shadow-sm border border-indigo-100"
                  : "text-slate-500 hover:bg-slate-50 hover:text-slate-700 border border-transparent"
              )}
              title={collapsed ? item.label : undefined}
              style={{ animationDelay: `${index * 50}ms` }}
            >
              <div className={cn(
                "flex items-center justify-center w-8 h-8 rounded-lg transition-all duration-200",
                isActive
                  ? `bg-gradient-to-br ${item.color} shadow-md`
                  : "bg-slate-100 group-hover:bg-slate-200"
              )}>
                <item.icon className={cn("w-4 h-4", isActive ? "text-white" : "text-slate-500 group-hover:text-slate-700")} />
              </div>
              {!collapsed && (
                <span className={cn("transition-all duration-200", isActive && "font-semibold")}>
                  {item.label}
                </span>
              )}
              {isActive && !collapsed && (
                <div className="ml-auto w-1.5 h-1.5 rounded-full bg-indigo-500" />
              )}
            </NavLink>
          )
        })}
      </nav>

      {/* Pro Tip */}
      <div className="px-3 pb-3">
        {!collapsed && (
          <div className="rounded-xl bg-gradient-to-br from-indigo-500 via-purple-500 to-pink-500 p-4 shadow-lg shadow-indigo-200/50 animate-fade-in">
            <div className="flex items-center gap-2 mb-2">
              <div className="w-6 h-6 rounded-full bg-white/20 flex items-center justify-center">
                <Zap className="w-3 h-3 text-white" />
              </div>
              <p className="text-[11px] font-bold text-white uppercase tracking-wider">Tip</p>
            </div>
            <p className="text-[11px] text-white/90 leading-relaxed">
              Chon thiet bi, nhap tai khoan, bam Start de bat dau automation.
            </p>
          </div>
        )}
      </div>

      {/* Collapse button */}
      <button
        onClick={() => setCollapsed(!collapsed)}
        className="flex items-center justify-center h-12 border-t border-slate-100 text-slate-400 hover:text-indigo-600 hover:bg-gradient-to-r hover:from-indigo-50 hover:to-purple-50 transition-all duration-200"
      >
        {collapsed ? (
          <ChevronRight className="w-4 h-4" />
        ) : (
          <div className="flex items-center gap-2">
            <ChevronLeft className="w-4 h-4" />
            <span className="text-xs font-medium">Thu gon</span>
          </div>
        )}
      </button>
    </aside>
  )
}
