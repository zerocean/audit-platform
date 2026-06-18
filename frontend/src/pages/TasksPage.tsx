import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Clock, CheckCircle, XCircle, Loader, ArrowRight, FileSearch, Calculator } from 'lucide-react'
import { api } from '../api/client'

interface TaskItem {
  id: number
  tool_type: string
  status: string
  input_filename: string
  created_at: string
  completed_at: string | null
}

export default function TasksPage() {
  const [tasks, setTasks] = useState<TaskItem[]>([])
  const [loading, setLoading] = useState(true)
  const navigate = useNavigate()

  useEffect(() => {
    loadTasks()
  }, [])

  const loadTasks = async () => {
    try {
      const data = await api.getTasks()
      setTasks(data)
    } catch (e) {
      console.error('Failed to load tasks:', e)
    } finally {
      setLoading(false)
    }
  }

  const statusIcon = (status: string) => {
    switch (status) {
      case 'running': return <Loader size={14} style={{ animation: 'spin 1s linear infinite', color: 'var(--accent)' }} />
      case 'success': return <CheckCircle size={14} style={{ color: 'var(--accent2)' }} />
      case 'failed': return <XCircle size={14} style={{ color: 'var(--danger)' }} />
      default: return <Clock size={14} style={{ color: 'var(--text2)' }} />
    }
  }

  const statusLabel = (status: string) => {
    switch (status) {
      case 'running': return '运行中'
      case 'success': return '成功'
      case 'failed': return '失败'
      case 'parsed': return '已解析'
      default: return status
    }
  }

  const formatTime = (iso: string | null) => {
    if (!iso) return '-'
    const d = new Date(iso)
    return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
  }

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <div style={{
        padding: '16px 24px', borderBottom: '1px solid var(--border)',
        background: 'var(--surface)', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <div>
          <h2 style={{ fontSize: 16, fontWeight: 600 }}>任务管理</h2>
          <p style={{ fontSize: 12, color: 'var(--text2)', marginTop: 2 }}>
            查看所有执行过的审计复核和税务填表任务
          </p>
        </div>
        <button className="btn btn-ghost" onClick={loadTasks} style={{ fontSize: 12, padding: '6px 14px' }}>
          刷新
        </button>
      </div>

      <div style={{ flex: 1, overflow: 'auto', padding: 20 }}>
        {loading ? (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', gap: 8, color: 'var(--text2)' }}>
            <span className="spinner" /> 加载中...
          </div>
        ) : tasks.length === 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
            height: '100%', color: 'var(--text2)', gap: 12 }}>
            <Clock size={48} />
            <span>暂无任务记录</span>
            <p style={{ fontSize: 12 }}>使用审计复核或税务填表工具后，任务会出现在这里</p>
          </div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th style={{ width: 50 }}>ID</th>
                <th>工具</th>
                <th>文件名</th>
                <th style={{ width: 80 }}>状态</th>
                <th style={{ width: 130 }}>创建时间</th>
                <th style={{ width: 130 }}>完成时间</th>
                <th style={{ width: 60 }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {tasks.map(task => (
                <tr key={task.id}>
                  <td style={{ color: 'var(--text2)', fontSize: 11 }}>#{task.id}</td>
                  <td>
                    <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
                      {task.tool_type === 'audit' ? <FileSearch size={14} /> : <Calculator size={14} />}
                      {task.tool_type === 'audit' ? '审计复核' : '税务填表'}
                    </span>
                  </td>
                  <td style={{ fontSize: 12, maxWidth: 250, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {task.input_filename || '-'}
                  </td>
                  <td>
                    <span style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 11 }}>
                      {statusIcon(task.status)}
                      <span className={`tag tag-${task.status === 'success' ? 'success' : task.status === 'failed' ? 'error' : 'info'}`}>
                        {statusLabel(task.status)}
                      </span>
                    </span>
                  </td>
                  <td style={{ fontSize: 11, color: 'var(--text2)' }}>{formatTime(task.created_at)}</td>
                  <td style={{ fontSize: 11, color: 'var(--text2)' }}>{formatTime(task.completed_at)}</td>
                  <td>
                    <button
                      onClick={() => navigate(`/${task.tool_type === 'audit' ? 'audit' : 'taxfill'}?taskId=${task.id}`)}
                      style={{ background: 'none', border: 'none', color: 'var(--accent)', cursor: 'pointer', padding: 4 }}
                      title="查看任务">
                      <ArrowRight size={16} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
