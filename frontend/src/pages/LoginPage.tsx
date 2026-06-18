import { useState } from 'react'
import { useAuth } from '../contexts/AppContext'

export default function LoginPage() {
  const { login } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(''); setLoading(true)
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || '登录失败')
      login(data.token, data.username)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'var(--bg)' }}>
      <form onSubmit={handleSubmit} style={{
        background: 'var(--surface)', border: '1px solid var(--border)',
        borderRadius: 'var(--radius)', padding: '40px 36px', width: 360,
        display: 'flex', flexDirection: 'column', gap: 20,
      }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{ width: 48, height: 48, margin: '0 auto 12px',
            background: 'linear-gradient(135deg, var(--accent), var(--accent2))',
            borderRadius: 10, display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 24 }}>A</div>
          <h2 style={{ fontSize: 18, fontWeight: 600 }}>财务审计智能体平台</h2>
          <p style={{ fontSize: 12, color: 'var(--text2)', marginTop: 4 }}>请登录以继续</p>
        </div>

        {error && <div style={{ color: 'var(--danger)', fontSize: 12, textAlign: 'center' }}>{error}</div>}

        <input value={username} onChange={e => setUsername(e.target.value)}
          placeholder="用户名" autoFocus
          style={{ background: 'var(--surface2)', border: '1px solid var(--border)',
            borderRadius: 6, padding: '10px 12px', color: 'var(--text)', fontSize: 14,
            outline: 'none' }} />

        <input type="password" value={password} onChange={e => setPassword(e.target.value)}
          placeholder="密码"
          style={{ background: 'var(--surface2)', border: '1px solid var(--border)',
            borderRadius: 6, padding: '10px 12px', color: 'var(--text)', fontSize: 14,
            outline: 'none' }} />

        <button type="submit" className="btn btn-primary" disabled={loading}
          style={{ width: '100%', justifyContent: 'center' }}>
          {loading ? <><span className="spinner" /> 登录中...</> : '登 录'}
        </button>

        <p style={{ fontSize: 11, color: 'var(--text2)', textAlign: 'center' }}>
          默认账号: admin / admin123
        </p>
      </form>
    </div>
  )
}
