import { useState, useRef, useEffect } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { FileSearch, Calculator, Home, LogOut, Activity, ChevronDown, X, Clock } from 'lucide-react'
import { useAuth, useTasks } from '../contexts/AppContext'

export default function Layout({ children }: { children: React.ReactNode }) {
  const location = useLocation()
  const navigate = useNavigate()
  const { username, logout } = useAuth()
  const { activeTasks, cancelTask } = useTasks()
  const isHome = location.pathname === '/'
  const [taskMenuOpen, setTaskMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  const runningTasks = [...activeTasks.values()].filter(t => t.status === 'running')

  // Close menu on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setTaskMenuOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const handleLogout = () => { logout(); navigate('/login') }

  const goToTask = (task: { id: number; tool_type: string }) => {
    navigate(task.tool_type === 'audit' ? '/audit' : '/taxfill')
    setTaskMenuOpen(false)
  }

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <header style={{
        background: 'var(--surface)', borderBottom: '1px solid var(--border)',
        padding: '10px 20px', display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0,
      }}>
        <Link to="/" style={{ display: 'flex', alignItems: 'center', gap: 8, textDecoration: 'none', color: 'var(--text)' }}>
          <div style={{ width: 30, height: 30, background: 'linear-gradient(135deg, var(--accent), var(--accent2))',
            borderRadius: 7, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 16 }}>A</div>
          <h1 style={{ fontSize: 14, fontWeight: 600, margin: 0 }}>审计智能体平台</h1>
        </Link>

        <nav style={{ marginLeft: 20, display: 'flex', gap: 2 }}>
          <NLink to="/" icon={<Home size={15} />} label="首页" active={isHome} />
          <NLink to="/audit" icon={<FileSearch size={15} />} label="审计复核" active={location.pathname.startsWith('/audit')} />
          <NLink to="/taxfill" icon={<Calculator size={15} />} label="税务填表" active={location.pathname.startsWith('/taxfill')} />
          <NLink to="/tasks" icon={<Clock size={15} />} label="任务" active={location.pathname === '/tasks'} />
        </nav>

        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 16, position: 'relative' }} ref={menuRef}>
          {/* Running tasks indicator */}
          {runningTasks.length > 0 && (
            <button
              onClick={() => setTaskMenuOpen(!taskMenuOpen)}
              style={{
                display: 'flex', alignItems: 'center', gap: 6,
                background: 'rgba(110,231,183,.1)', border: '1px solid rgba(110,231,183,.25)',
                borderRadius: 6, padding: '4px 10px', cursor: 'pointer',
                color: 'var(--accent2)', fontSize: 11,
              }}>
              <Activity size={14} style={{ animation: 'spin 2s linear infinite' }} />
              {runningTasks.length} 个任务运行中
              <ChevronDown size={12} />
            </button>
          )}

          {/* Task dropdown */}
          {taskMenuOpen && runningTasks.length > 0 && (
            <div style={{
              position: 'absolute', top: '100%', right: 0, marginTop: 6, zIndex: 100,
              background: 'var(--surface)', border: '1px solid var(--border)',
              borderRadius: 8, minWidth: 280, boxShadow: '0 8px 24px rgba(0,0,0,.4)',
              overflow: 'hidden',
            }}>
              <div style={{ padding: '8px 12px', fontSize: 10, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '.05em', borderBottom: '1px solid var(--border)' }}>
                运行中的任务
              </div>
              {runningTasks.map(task => (
                <div key={task.id} style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  padding: '8px 12px', cursor: 'pointer', fontSize: 12,
                  borderBottom: '1px solid var(--border)',
                }}
                  onClick={() => goToTask(task)}
                  onMouseEnter={e => { e.currentTarget.style.background = 'var(--surface2)' }}
                  onMouseLeave={e => { e.currentTarget.style.background = 'transparent' }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span className="spinner" style={{ width: 12, height: 12, borderWidth: 2 }} />
                    <div>
                      <div style={{ color: 'var(--text)' }}>{task.tool_type === 'audit' ? '📋 审计复核' : '📊 税务填表'}</div>
                      {task.input_filename && <div style={{ color: 'var(--text2)', fontSize: 10 }}>{task.input_filename}</div>}
                    </div>
                  </div>
                  <button onClick={e => { e.stopPropagation(); cancelTask(task.id) }}
                    style={{ background: 'none', border: 'none', color: 'var(--text2)', cursor: 'pointer', padding: 2 }}
                    title="取消任务">
                    <X size={14} />
                  </button>
                </div>
              ))}
            </div>
          )}

          <span style={{ fontSize: 12, color: 'var(--text2)' }}>{username}</span>
          <button onClick={handleLogout}
            style={{ background: 'none', border: 'none', color: 'var(--text2)', cursor: 'pointer',
              display: 'flex', alignItems: 'center', gap: 4, fontSize: 12 }}>
            <LogOut size={14} /> 退出
          </button>
        </div>
      </header>

      <main style={{ flex: 1, overflow: 'hidden', minHeight: 0 }}>{children}</main>
    </div>
  )
}

function NLink({ to, icon, label, active }: { to: string; icon: React.ReactNode; label: string; active: boolean }) {
  return (
    <Link to={to} style={{
      display: 'flex', alignItems: 'center', gap: 5, padding: '5px 12px', borderRadius: 6,
      fontSize: 12, fontWeight: active ? 600 : 400,
      color: active ? 'var(--accent)' : 'var(--text2)',
      background: active ? 'rgba(79,142,247,.1)' : 'transparent',
      textDecoration: 'none', transition: 'all .15s',
    }}>{icon}{label}</Link>
  )
}
