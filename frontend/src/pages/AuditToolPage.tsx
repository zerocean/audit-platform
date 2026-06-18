import { useState, useRef, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Upload, RefreshCw, Download, FileText } from 'lucide-react'
import { useTasks } from '../contexts/AppContext'
import { api, sseReader, AuditPdfResponse, InspectorResponse } from '../api/client'

type Step = 'idle' | 'parsing' | 'parsed' | 'auditing' | 'done'
type Tab = 'raw' | 'table' | 'inspector'

function parseMarkdownTable(md: string): { html: string; rows: string[][] } {
  const rawLines = md.split('\n'); const merged: string[] = []
  for (const line of rawLines) {
    const t = line.trim(); if (!t) continue
    if (t.startsWith('|')) merged.push(t)
    else if (merged.length > 0) merged[merged.length - 1] += ' ' + t
  }
  const tableLines = merged.filter(l => l.includes('|'))
  if (tableLines.length < 2) return { html: '', rows: [] }
  const headers = tableLines[0].split('|').map(s => s.trim()).filter(Boolean)
  const dataRows: string[][] = []
  let html = '<table class="data-table"><thead><tr>'
  headers.forEach(h => { html += `<th>${escHtml(h)}</th>` })
  html += '</tr></thead><tbody>'
  for (let i = 2; i < tableLines.length; i++) {
    const cells = tableLines[i].split('|').map(s => s.trim()).filter(Boolean)
    if (cells.length === 0) continue
    dataRows.push(cells)
    html += '<tr>'
    cells.forEach((cell, ci) => {
      html += ci === 0 ? `<td>${tagify(cell)}</td>` : `<td>${escHtml(cell)}</td>`
    })
    html += '</tr>'
  }
  html += '</tbody></table>'
  return { html, rows: dataRows }
}
function escHtml(s: string) { if (!s) return ''; return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;') }
function tagify(type: string) {
  const t = type.toLowerCase(); let cls = 'tag '
  if (t.includes('arithmetic')) cls += 'tag-arith'
  else if (t.includes('tie')) cls += 'tag-tieout'
  else if (t.includes('transcript')) cls += 'tag-trans'
  else if (t.includes('cross')) cls += 'tag-cross'
  else return escHtml(type)
  return `<span class="${cls}">${escHtml(type)}</span>`
}

export default function AuditToolPage() {
  const { addTask, removeTask } = useTasks()
  const [searchParams] = useSearchParams()
  const [step, setStep] = useState<Step>('idle')
  const [statusMsg, setStatusMsg] = useState('就绪')
  const [file, setFile] = useState<File | null>(null)
  const [parserResult, setParserResult] = useState<AuditPdfResponse | null>(null)
  const [inspectorResult, setInspectorResult] = useState<InspectorResult | null>(null)
  const [auditText, setAuditText] = useState('')
  const [auditTableHtml, setAuditTableHtml] = useState('')
  const [auditTableRows, setAuditTableRows] = useState<string[][]>([])
  const [activeTab, setActiveTab] = useState<Tab>('raw')
  const [inspectorRunning, setInspectorRunning] = useState(false)
  const [inspectorDone, setInspectorDone] = useState(false)
  const [auditDone, setAuditDone] = useState(false)
  const [parseDone, setParseDone] = useState(false)
  const [error, setError] = useState('')
  const [taskId, setTaskId] = useState<number | null>(null)
  const [taskFilename, setTaskFilename] = useState('')
  const sseRef = useRef<ReadableStreamDefaultReader<Uint8Array> | null>(null)

  const fileInputRef = useRef<HTMLInputElement>(null)
  const rawOutputRef = useRef<HTMLDivElement>(null)

  useEffect(() => { if (rawOutputRef.current) rawOutputRef.current.scrollTop = rawOutputRef.current.scrollHeight }, [auditText])

  // Load task from taskId param (from tasks list navigation)
  useEffect(() => {
    const tid = searchParams.get('taskId')
    if (!tid) return
    const id = parseInt(tid)
    if (!id) return
    setTaskId(id)
    api.getTask(id).then(data => {
      if (data.tool_type !== 'audit') return
      const r = data.result || {}
      if (r.parsed_json) setParserResult({ success: true, parsed_json: r.parsed_json, pages: r.pages || 0, parser_success: true, task_id: id })
      if (r.uploaded_file) setTaskFilename(r.uploaded_file)
      if (r.audit_text) {
        setAuditText(r.audit_text)
        if (r.audit_table) {
          const { html, rows } = parseMarkdownTable(r.audit_table)
          setAuditTableHtml(html); setAuditTableRows(rows)
        }
        setAuditDone(true)
      }
      if (r.inspector) {
        setInspectorResult(r.inspector)
        setInspectorDone(true)
      }
      if (data.status === 'success') {
        setStep('done'); setStatusMsg('✅ 已完成'); setParseDone(true); setAuditDone(true); setInspectorDone(true)
      } else if (data.status === 'running') {
        setStep('auditing'); setStatusMsg('🔄 运行中'); setParseDone(true)
      } else if (data.status === 'failed') {
        setStatusMsg('❌ 失败'); setError(data.error_message || '')
      }
    }).catch(console.error)
  }, [searchParams])

  const allDone = parseDone && auditDone && inspectorDone
  const errorCount = auditTableRows.length

  const handleFile = (f: File | null) => {
    if (!f) return
    setFile(f); setStep('idle'); setTaskFilename('')
    setParserResult(null); setInspectorResult(null); setAuditText('')
    setAuditTableHtml(''); setAuditTableRows([]); setError('')
    setParseDone(false); setAuditDone(false); setInspectorDone(false); setTaskId(null)
    if (sseRef.current) { try { sseRef.current.cancel() } catch {} }
    setStatusMsg(`已选择: ${f.name}`)
  }

  const runParser = async () => {
    if (!file) return
    setStep('parsing'); setStatusMsg('文档解析中...'); setError('')
    try {
      const data = await api.auditPdf(file)
      setParserResult(data); setParseDone(true); setTaskId(data.task_id)
      setStep('parsed'); setStatusMsg(`✅ 解析完成 (${data.pages} 页)`)
      // Register task
      addTask({ id: data.task_id, tool_type: 'audit', status: 'running', input_filename: file?.name })
    } catch (e: any) {
      setError(e.message); setStatusMsg('❌ 解析失败'); setStep('idle')
    }
  }

  const runInspector = async () => {
    if (!file) return
    setInspectorRunning(true); setInspectorDone(false)
    try {
      const data = await api.auditInspector(file)
      setInspectorResult(data); setInspectorDone(true)
      // Save to task
      if (taskId) {
        api.saveInspector(taskId, data).catch(() => {})
      }
    } catch (e: any) { console.error('Inspector:', e) }
    finally { setInspectorRunning(false) }
  }

  const runAudit = async () => {
    if (!parserResult?.parsed_json) return
    setStep('auditing'); setStatusMsg('数值复核进行中...')
    setAuditText(''); setAuditTableHtml(''); setAuditDone(false)
    setActiveTab('raw')

    try {
      sseRef.current = await sseReader(
        '/audit/json',
        { data: parserResult.parsed_json, filename: file?.name, task_id: taskId },
        (chunk) => setAuditText(p => p + chunk),
        () => {
          // onDone — parse table
          setAuditText(p => {
            const tableMatches = [...p.matchAll(/<table>([\s\S]*?)<\/table>/gi)]
            if (tableMatches.length > 0) {
              const { html, rows } = parseMarkdownTable(tableMatches[tableMatches.length - 1][1].trim())
              setAuditTableHtml(html); setAuditTableRows(rows)
            }
            return p
          })
          setAuditDone(true); setStep('done'); setStatusMsg('✅ 数值复核完成')
          if (taskId) {
            addTask({ id: taskId, tool_type: 'audit', status: 'success' })
          }
        },
        (err) => {
          setAuditText(p => p + `\n\n❌ ${err}`)
          setStatusMsg('❌ 复核失败'); setStep('parsed')
          if (taskId) addTask({ id: taskId, tool_type: 'audit', status: 'failed' })
        },
      )
    } catch (e: any) {
      setAuditText(p => p + `\n\n❌ ${e.message}`)
      setStatusMsg('❌ 复核失败'); setStep('parsed')
    }
  }

  useEffect(() => {
    if (step === 'parsed' && parserResult) { runInspector(); runAudit() }
    return () => {
      // Don't cancel SSE on unmount — let it run in background
    }
  }, [step])

  useEffect(() => {
    return () => {
      // Cleanup on final unmount
      if (sseRef.current) { try { sseRef.current.cancel() } catch {} }
      if (taskId) removeTask(taskId)
    }
  }, [])

  const exportReport = () => {
    const now = new Date(); const dateStr = now.toLocaleDateString('zh-CN')
    let html = `<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:w="urn:schemas-microsoft-com:office:word" xmlns="http://www.w3.org/TR/REC-html40">
<head><meta charset="utf-8"><title>审计复核报告</title>
<style>body{font-family:"微软雅黑",SimSun,sans-serif;font-size:11pt;line-height:1.6}
h1{font-size:18pt;text-align:center}h2{font-size:14pt;border-bottom:1pt solid #ccc}
table{border-collapse:collapse;width:100%;margin:8pt 0}
th,td{border:1pt solid #999;padding:4pt 6pt;font-size:10pt}
th{background:#f2f2f2}pre{background:#f8f8f8;padding:8pt;font-size:9pt;white-space:pre-wrap}
</style></head><body><h1>财务审计复核分析报告</h1>
<div style="color:#666">生成日期：${dateStr}</div>
<h2>一、语法/结构性分析</h2>`
    if (inspectorResult?.issues?.length) {
      html += '<table><tr><th>页码</th><th>类别</th><th>位置</th><th>问题描述</th></tr>'
      inspectorResult.issues.forEach(i => {
        const p = i.split('|').map(s => s.trim())
        html += `<tr><td>${escHtml(p[0]||'')}</td><td>${escHtml(p[1]||'')}</td><td>${escHtml(p[2]||'')}</td><td>${escHtml(p[3]||i)}</td></tr>`
      })
      html += '</table>'
    } else { html += '<p>✅ 未发现问题</p>' }
    html += '<h2>二、数值复核</h2>'
    if (auditTableRows.length > 0) html += auditTableHtml
    else if (auditText) html += '<pre>' + escHtml(auditText) + '</pre>'
    html += '<h2>三、数值复核完整输出</h2><pre>' + escHtml(auditText) + '</pre>'
    html += '<h2>四、源报告解析结果</h2>'
    if (parserResult?.parsed_json) html += '<pre>' + escHtml(JSON.stringify(parserResult.parsed_json, null, 2)) + '</pre>'
    else html += '<p>暂无</p>'
    html += '</body></html>'
    const blob = new Blob(['\ufeff' + html], { type: 'application/msword' })
    const base = file?.name?.replace(/\.[^/.]+$/, '') || '审计分析报告'
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a'); a.href = url; a.download = `${base}_review.doc`
    a.click(); URL.revokeObjectURL(url)
  }

  return (
    <div style={{ height: '100%', display: 'flex', overflow: 'hidden' }}>
      {/* Left Panel */}
      <div style={{ width: '42%', minWidth: 340, borderRight: '1px solid var(--border)',
        background: 'var(--surface)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{ padding: '10px 16px', fontSize: 11, fontWeight: 600, textTransform: 'uppercase',
          letterSpacing: '.08em', color: 'var(--text2)', borderBottom: '1px solid var(--border)',
          background: 'var(--surface2)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span>源数据</span>
          <span style={{ fontSize: 12, textTransform: 'none', letterSpacing: 0, color: 'var(--text)' }}>{statusMsg}</span>
        </div>
        <div style={{ padding: 12, borderBottom: '1px solid var(--border)', display: 'flex', gap: 8, flexWrap: 'wrap', background: 'var(--surface2)' }}>
          <input ref={fileInputRef} type="file" accept=".pdf"
            onChange={e => handleFile(e.target.files?.[0] || null)} style={{ display: 'none' }} />
          <button className="btn btn-primary" onClick={() => {
            if (taskId) {
              // Clear old task and start fresh
              setStep('idle'); setParserResult(null); setAuditText(''); setAuditTableHtml(''); setAuditTableRows([])
              setInspectorResult(null); setError(''); setParseDone(false); setAuditDone(false); setInspectorDone(false)
              setFile(null); setTaskId(null); setTaskFilename('')
              if (sseRef.current) { try { sseRef.current.cancel() } catch {} }
            }
            fileInputRef.current?.click()
          }} style={{ fontSize: 12, padding: '6px 14px' }}>
            <Upload size={14} /> 选择文件开始新任务</button>
          {(file || taskFilename) && <span style={{ fontSize: 11, color: 'var(--accent2)', alignSelf: 'center', maxWidth: 150, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{file?.name || taskFilename}</span>}
          {taskId ? (
            <a href={`/api/audit/download-upload/${taskId}`} className="btn btn-ghost"
              style={{ fontSize: 12, padding: '6px 14px', textDecoration: 'none', marginLeft: 'auto' }}>
              <Download size={14} /> 下载源文件
            </a>
          ) : (
            <button className="btn btn-ghost" onClick={runParser} disabled={!file || step === 'parsing'} style={{ fontSize: 12, padding: '6px 14px', marginLeft: 'auto' }}>
              {step === 'idle' ? '▶ 开始解析' : '⏳ 处理中...'}
            </button>
          )}
        </div>
        {error && <div style={{ padding: 10, color: 'var(--danger)', fontSize: 12, background: 'rgba(248,113,113,.1)' }}>{error}</div>}
        <pre style={{ flex: 1, overflow: 'auto', padding: 16, fontSize: 12, lineHeight: 1.6, fontFamily: "'Consolas','Courier New',monospace", color: 'var(--text)', margin: 0, whiteSpace: 'pre-wrap', minHeight: 0 }}>
          {parserResult?.parsed_json ? JSON.stringify(parserResult.parsed_json, null, 2) : '上传 PDF 文件后开始解析...'}
        </pre>
        <div style={{ padding: 10, borderTop: '1px solid var(--border)', display: 'flex', gap: 8 }}>
          <button className="btn btn-ghost" onClick={runParser} disabled={!file || step === 'parsing'} style={{ fontSize: 12, padding: '6px 12px' }}>
            <RefreshCw size={14} /> 重新解析</button>
          <button className="btn btn-ghost" onClick={() => {
            setStep('idle'); setParserResult(null); setAuditText(''); setAuditTableHtml(''); setAuditTableRows([])
            setInspectorResult(null); setError(''); setParseDone(false); setAuditDone(false); setInspectorDone(false)
            if (sseRef.current) { try { sseRef.current.cancel() } catch {} }
            if (taskId) removeTask(taskId)
          }} style={{ fontSize: 12, padding: '6px 12px' }}>清除</button>
        </div>
      </div>

      {/* Right Panel */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{ display: 'flex', borderBottom: '1px solid var(--border)', background: 'var(--surface2)', alignItems: 'center' }}>
          <div style={{ display: 'flex' }}>
            {(['raw','table','inspector'] as Tab[]).map(tab => (
              <button key={tab} onClick={() => setActiveTab(tab)} style={{
                padding: '10px 20px', fontSize: 13, cursor: 'pointer',
                color: activeTab === tab ? 'var(--accent)' : 'var(--text2)',
                borderBottom: activeTab === tab ? '2px solid var(--accent)' : '2px solid transparent',
                background: 'transparent', borderTop: 'none', borderLeft: 'none', borderRight: 'none', fontWeight: activeTab === tab ? 600 : 400,
              }}>
                {tab === 'raw' ? '原始输出' : tab === 'table' ? `复核表${errorCount > 0 ? ` (${errorCount})` : ''}` : '语法检查'}
                {tab === 'inspector' && inspectorRunning && <span className="spinner" style={{ marginLeft: 6 }} />}
              </button>
            ))}
          </div>
          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 12, paddingRight: 16 }}>
            {auditDone && <span style={{ fontSize: 11, color: 'var(--text2)' }}>发现错误: {errorCount} | Tokens: ~{Math.round(auditText.length / 4)}</span>}
            <button className="btn btn-primary" disabled={!allDone} onClick={exportReport} style={{ fontSize: 12, padding: '6px 14px' }}>
              <Download size={14} /> 导出分析报告</button>
          </div>
        </div>
        {activeTab === 'raw' && (
          <div ref={rawOutputRef} style={{ flex: 1, overflow: 'auto', padding: '20px 24px', fontSize: 13, lineHeight: 1.7, fontFamily: "'Consolas','Courier New',monospace", whiteSpace: 'pre-wrap', color: 'var(--text)', minHeight: 0 }}>
            {step === 'auditing' && !auditText && (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text2)', gap: 10 }}>
                <span className="spinner" style={{ width: 24, height: 24, borderWidth: 3 }} /><span>模型思考中...</span></div>
            )}
            {auditText && <>{auditText}{step === 'auditing' && <span className="spinner" style={{ marginLeft: 8, verticalAlign: 'middle' }} />}</>}
            {!auditText && step !== 'auditing' && (
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text2)', gap: 12 }}>
                <FileText size={48} /><span>解析完成后自动开始数值复核</span></div>
            )}
          </div>
        )}
        {activeTab === 'table' && (
          <div style={{ flex: 1, overflow: 'auto', padding: 20, minHeight: 0 }}>
            {auditTableHtml ? <div dangerouslySetInnerHTML={{ __html: auditTableHtml }} /> : (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text2)' }}>
                {step === 'auditing' ? <span><span className="spinner" /> 数值复核进行中...</span> : auditDone ? '未发现错误' : '复核完成后显示错误汇总表'}
              </div>
            )}
          </div>
        )}
        {activeTab === 'inspector' && (
          <div style={{ flex: 1, overflow: 'auto', padding: '16px 20px', minHeight: 0 }}>
            {inspectorResult ? (
              inspectorResult.total_issues === 0 ? (
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--accent2)', gap: 8 }}>
                  <span style={{ fontSize: 48 }}>✅</span><span>未发现任何语法或结构性问题</span></div>
              ) : (
                <div>
                  <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 12 }}>
                    检查页数: {inspectorResult.total_pages} | 发现问题: {inspectorResult.total_issues}</div>
                  <table className="data-table"><thead><tr><th>页码</th><th>类别</th><th>位置</th><th>问题描述</th></tr></thead><tbody>
                    {inspectorResult.issues.map((issue, i) => {
                      const parts = issue.split('|').map(s => s.trim())
                      return <tr key={i}><td>{parts[0]||'-'}</td><td>{parts[1]||'-'}</td><td>{parts[2]||'-'}</td><td>{parts[3]||issue}</td></tr>
                    })}
                  </tbody></table></div>
              )
            ) : inspectorRunning ? (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text2)', gap: 8 }}>
                <span className="spinner" /> 语法检查进行中...</div>
            ) : (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text2)' }}>解析文档后自动开始语法检查</div>
            )}
          </div>
        )}
      </div>
      <style>{`
        .tag-arith{background:rgba(248,113,113,.15);color:var(--danger)}
        .tag-tieout{background:rgba(79,142,247,.15);color:var(--accent)}
        .tag-trans{background:rgba(251,191,36,.15);color:var(--warn)}
        .tag-cross{background:rgba(110,231,183,.15);color:var(--accent2)}
      `}</style>
    </div>
  )
}
