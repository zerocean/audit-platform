const BASE = '/api'

function getToken(): string | null {
  return localStorage.getItem('token')
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { ...(options?.headers as any || {}) }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  // Don't set Content-Type for FormData
  if (!(options?.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json'
  }
  const res = await fetch(`${BASE}${path}`, { ...options, headers })
  if (res.status === 401) {
    localStorage.removeItem('token')
    localStorage.removeItem('username')
    window.location.href = '/login'
    throw new Error('登录已过期，请重新登录')
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || err.error || `HTTP ${res.status}`)
  }
  return res.json()
}

// SSE reader helper
export async function sseReader(
  path: string, body: any,
  onChunk: (text: string) => void,
  onDone: () => void,
  onError: (err: string) => void,
): Promise<ReadableStreamDefaultReader<Uint8Array>> {
  const token = getToken()
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(token ? { 'Authorization': `Bearer ${token}` } : {}) },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }))
    throw new Error(err.error || err.detail || '请求失败')
  }
  const reader = res.body!.getReader()
  const decoder = new TextDecoder()
  let buf = ''

  const pump = async () => {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      const lines = buf.split('\n'); buf = lines.pop() || ''
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        const chunk = line.slice(6)
        if (chunk === '[DONE]') { onDone(); return }
        if (chunk.startsWith('[ERROR]')) { onError(chunk.slice(8)); return }
        if (chunk.startsWith('[THINK]') && chunk.endsWith('[/THINK]')) continue
        onChunk(chunk.replace(/⏎/g, '\n'))
      }
    }
    onDone()
  }
  pump() // fire and forget
  return reader
}

export const api = {
  auditPdf: (file: File) => {
    const fd = new FormData(); fd.append('file', file)
    return request<AuditPdfResponse>('/audit/pdf', { method: 'POST', body: fd })
  },
  auditInspector: (file: File, taskId?: number) => {
    const fd = new FormData(); fd.append('file', file)
    if (taskId) fd.append('task_id', String(taskId))
    return request<InspectorResponse>('/audit/inspector', { method: 'POST', body: fd })
  },
  taxfillPipeline: (fsPdf: File, taxcompPdf: File) => {
    const fd = new FormData(); fd.append('fs_pdf', fsPdf); fd.append('taxcomp_pdf', taxcompPdf)
    return request<TaxFillPipelineResponse>('/taxfill/pipeline', { method: 'POST', body: fd })
  },
  getTasks: () => request<any[]>('/tasks'),
  getTask: (id: number) => request<any>(`/tasks/${id}`),
  saveInspector: (taskId: number, data: any) => request<any>(`/tasks/${taskId}/inspector`, {
    method: 'PUT', body: JSON.stringify(data),
  }),
}

export interface AuditPdfResponse { success: boolean; task_id: number; parsed_json?: any; pages: number; parser_success: boolean; error?: string }
export interface InspectorResponse { success: boolean; total_pages: number; total_issues: number; issues: string[] }
export interface TaxFillPipelineResponse { success: boolean; task_id: number; run_id?: string; filling_json?: any; excel_path?: string; json_path?: string; ton_path?: string; error?: string }
