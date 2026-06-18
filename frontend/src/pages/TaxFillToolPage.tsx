import { useState, useRef, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Upload, FileSpreadsheet, FileJson, FileText, Download } from 'lucide-react'
import { useTasks } from '../contexts/AppContext'
import { api, TaxFillPipelineResponse } from '../api/client'

type Stage = 'upload' | 'running' | 'done'

export default function TaxFillToolPage() {
  const { addTask } = useTasks()
  const [searchParams] = useSearchParams()
  const [stage, setStage] = useState<Stage>('upload')
  const [fsFile, setFsFile] = useState<File | null>(null)
  const [taxcompFile, setTaxcompFile] = useState<File | null>(null)
  const [statusText, setStatusText] = useState('')
  const [result, setResult] = useState<TaxFillPipelineResponse | null>(null)
  const [error, setError] = useState('')
  const [progress, setProgress] = useState<string[]>([])
  const [taskId, setTaskId] = useState<number | null>(null)
  const [taskFilenames, setTaskFilenames] = useState<string[]>([])

  const fsInputRef = useRef<HTMLInputElement>(null)
  const taxcompInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => { return () => {} }, [])

  // Load from taskId
  useEffect(() => {
    const tid = searchParams.get('taskId')
    if (!tid) return
    const id = parseInt(tid)
    if (!id) return
    setTaskId(id)
    api.getTask(id).then(data => {
      if (data.tool_type !== 'taxfill') return
      const r = data.result || {}
      if (r.files?.uploaded) setTaskFilenames(r.files.uploaded.map((f: any) => f.name))
      if (data.status === 'success') {
        setStage('done'); setStatusText('✅ 已完成')
        if (r.run_id) setResult({ success: true, task_id: id, run_id: r.run_id, filling_json: r.filling_json, files: r.files } as any)
        addTask({ id, tool_type: 'taxfill', status: 'success', input_filename: data.input_filename })
      } else if (data.status === 'running') {
        setStage('running'); setStatusText('🔄 运行中')
      } else if (data.status === 'failed') {
        setStatusText('❌ 失败'); setError(data.error_message || '')
      }
    }).catch(console.error)
  }, [searchParams])

  const clearTask = () => {
    setStage('upload'); setResult(null); setError(''); setProgress([])
    setTaskId(null); setTaskFilenames([]); setFsFile(null); setTaxcompFile(null)
  }

  const handleFileChange = (setter: (f: File | null) => void) => (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0] || null
    setter(f)
    if (f && taskId) clearTask()
  }

  const handleStart = async () => {
    if (!fsFile || !taxcompFile) return
    setStage('running'); setError(''); setProgress([]); setResult(null)
    setStatusText('处理中...')
    try {
      setProgress(p => [...p, '🔄 Stage 1: Vision Parser 解析中...'])
      const data = await api.taxfillPipeline(fsFile, taxcompFile)
      if (!data.success) throw new Error(data.error || '处理失败')
      setProgress(p => [...p, '✅ Stage 1: Vision Parser 完成', '🔄 Stage 2: Filling Engine 填表中...', '✅ Stage 2: Filling Engine 完成'])
      setResult(data); setTaskId(data.task_id); setStage('done')
      setStatusText('✅ 处理完成')
      if (data.task_id) {
        addTask({ id: data.task_id, tool_type: 'taxfill', status: 'success', input_filename: fsFile?.name })
      }
    } catch (e: any) {
      setError(e.message); setProgress(p => [...p, `❌ ${e.message}`]); setStage('upload')
    }
  }

  const downloadFile = (filename: string) => {
    if (result?.run_id) window.open(`/api/taxfill/download/${result.run_id}/${filename}`, '_blank')
  }

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <div style={{ padding: '16px 24px', borderBottom: '1px solid var(--border)', background: 'var(--surface)', display: 'flex', alignItems: 'center', gap: 12 }}>
        <h2 style={{ fontSize: 16, fontWeight: 600 }}>税务填表助手</h2>
        <span style={{ fontSize: 12, color: 'var(--text2)' }}>BIR51 香港利得税报税表自动填写</span>
        <span style={{ marginLeft: 'auto', fontSize: 13, color: error ? 'var(--danger)' : 'var(--accent2)' }}>{statusText}</span>
      </div>
      <div style={{ flex: 1, overflow: 'auto', padding: 32, display: 'flex', flexDirection: 'column', gap: 24 }}>
        <div style={{ display: 'flex', gap: 20, opacity: stage === 'running' ? .6 : 1, pointerEvents: stage === 'running' ? 'none' : 'auto' }}>
          {[
            ['财务报表 PDF', fsFile, setFsFile, fsInputRef],
            ['税务计算表 PDF', taxcompFile, setTaxcompFile, taxcompInputRef],
          ].map(([label, file, setter, ref]: any, i) => (
            <div key={i} style={{ flex: 1 }}>
              <label style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '.05em', display: 'block', marginBottom: 6 }}>{label}</label>
              <input ref={ref} type="file" accept=".pdf" onChange={handleFileChange(setter)} style={{ display: 'none' }} />
              <button className="btn btn-ghost" onClick={() => ref.current?.click()}
                style={{ width: '100%', justifyContent: 'center', border: '1px dashed var(--border)', padding: '24px 16px', fontSize: 13, background: file ? 'rgba(79,142,247,.08)' : 'var(--surface)' }}>
                <Upload size={18} />{file ? file.name : `点击上传${label}`}
              </button>
            </div>
          ))}
        </div>

        {!taskId ? (
          <button className="btn btn-primary" disabled={!fsFile || !taxcompFile || stage === 'running'} onClick={handleStart} style={{ alignSelf: 'center', padding: '12px 40px', fontSize: 15 }}>
            {stage === 'running' ? <><span className="spinner" /> 处理中...</> : '▶ 开始处理'}
          </button>
        ) : (
          <div style={{ display: 'flex', gap: 8, justifyContent: 'center', flexWrap: 'wrap', marginTop: 8 }}>
            {taskFilenames.map((name, i) => (
              <a key={i} href={`/api/taxfill/download-upload/${taskId}/${encodeURIComponent(name)}`}
                className="btn btn-ghost" style={{ fontSize: 13, textDecoration: 'none' }}>
                <Download size={16} /> {name}
              </a>
            ))}
            <button className="btn btn-primary" style={{ fontSize: 13 }} onClick={() => downloadFile('filling_reference.xlsx')}>
              <FileSpreadsheet size={16} /> 下载 Excel</button>
            <button className="btn btn-ghost" style={{ fontSize: 13 }} onClick={() => downloadFile('filling_reference.json')}>
              <FileJson size={16} /> 下载 JSON</button>
            <button className="btn btn-ghost" style={{ fontSize: 13 }} onClick={() => downloadFile('filling_reference.ton')}>
              <FileText size={16} /> 下载 TON</button>
          </div>
        )}

        {progress.length > 0 && (
          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: 20 }}>
            <h3 style={{ fontSize: 13, fontWeight: 600, marginBottom: 12, color: 'var(--text2)' }}>处理进度</h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {progress.map((p, i) => (
                <div key={i} style={{ fontSize: 13, fontFamily: "'Consolas','Courier New',monospace", color: p.startsWith('✅') ? 'var(--accent2)' : p.startsWith('❌') ? 'var(--danger)' : 'var(--text)' }}>{p}</div>
              ))}
            </div>
          </div>
        )}
        {error && <div style={{ color: 'var(--danger)', fontSize: 13, padding: 12, background: 'rgba(248,113,113,.1)', borderRadius: 'var(--radius)' }}>❌ {error}</div>}
      </div>
    </div>
  )
}
