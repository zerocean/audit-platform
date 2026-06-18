import { createContext, useContext, useState, useCallback } from 'react'

export interface ActiveTask {
  id: number
  tool_type: 'audit' | 'taxfill'
  status: 'running' | 'success' | 'failed'
  input_filename?: string
  sseReader?: ReadableStreamDefaultReader<Uint8Array>
}

interface AuthState {
  token: string | null
  username: string | null
  login: (token: string, username: string) => void
  logout: () => void
  isLoggedIn: boolean
}

interface TaskState {
  activeTasks: Map<number, ActiveTask>
  addTask: (task: ActiveTask) => void
  updateTask: (id: number, updates: Partial<ActiveTask>) => void
  removeTask: (id: number) => void
  cancelTask: (id: number) => void
}

const AuthCtx = createContext<AuthState>(null!)
const TaskCtx = createContext<TaskState>(null!)

export function useAuth() { return useContext(AuthCtx) }
export function useTasks() { return useContext(TaskCtx) }

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem('token'))
  const [username, setUsername] = useState<string | null>(() => localStorage.getItem('username'))
  const [activeTasks, setActiveTasks] = useState<Map<number, ActiveTask>>(new Map())

  const login = useCallback((t: string, u: string) => {
    localStorage.setItem('token', t); localStorage.setItem('username', u)
    setToken(t); setUsername(u)
  }, [])

  const logout = useCallback(() => {
    activeTasks.forEach(t => { if (t.sseReader) try { t.sseReader.cancel() } catch {} })
    localStorage.removeItem('token'); localStorage.removeItem('username')
    setToken(null); setUsername(null); setActiveTasks(new Map())
  }, [activeTasks])

  const addTask = useCallback((task: ActiveTask) => {
    setActiveTasks(prev => new Map(prev).set(task.id, task))
  }, [])

  const updateTask = useCallback((id: number, updates: Partial<ActiveTask>) => {
    setActiveTasks(prev => {
      const next = new Map(prev)
      const existing = next.get(id)
      if (existing) next.set(id, { ...existing, ...updates })
      return next
    })
  }, [])

  const removeTask = useCallback((id: number) => {
    setActiveTasks(prev => { const next = new Map(prev); next.delete(id); return next })
  }, [])

  const cancelTask = useCallback((id: number) => {
    const task = activeTasks.get(id)
    if (task?.sseReader) try { task.sseReader.cancel() } catch {}
    removeTask(id)
  }, [activeTasks, removeTask])

  return (
    <AuthCtx.Provider value={{ token, username, login, logout, isLoggedIn: !!token }}>
      <TaskCtx.Provider value={{ activeTasks, addTask, updateTask, removeTask, cancelTask }}>
        {children}
      </TaskCtx.Provider>
    </AuthCtx.Provider>
  )
}
