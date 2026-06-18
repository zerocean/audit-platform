import { useNavigate } from 'react-router-dom'
import { FileSearch, Calculator, ArrowRight } from 'lucide-react'

export default function HomePage() {
  const navigate = useNavigate()

  return (
    <div style={{
      height: '100%',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      padding: 40,
      gap: 32,
    }}>
      {/* 审计复核 */}
      <ToolCard
        icon={<FileSearch size={48} />}
        title="审计报告复核"
        description="上传审计报告 PDF，AI 自动完成结构化解析、五阶段数值复核、语法与结构性检查，生成审计错误报告。"
        features={['PDF / Word 上传', 'Vision LLM 表格提取', '五阶段审计分析', '语法/结构性检查', '导出 Word 报告']}
        onClick={() => navigate('/audit')}
      />

      {/* 税务填表 */}
      <ToolCard
        icon={<Calculator size={48} />}
        title="税务填表助手"
        description="上传财务报表 + 税务计算表 PDF，AI 自动解析数据并按 BIR51 利得税报税表逐字段智能填写，输出 Excel 参考表。"
        features={['双 PDF 上传', 'Vision Parser 解析', 'Schema 驱动填表', '127 字段智能匹配', '导出 Excel 参考表']}
        onClick={() => navigate('/taxfill')}
      />
    </div>
  )
}

function ToolCard({
  icon, title, description, features, onClick,
}: {
  icon: React.ReactNode
  title: string
  description: string
  features: string[]
  onClick: () => void
}) {
  return (
    <div
      onClick={onClick}
      style={{
        background: 'var(--surface)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius)',
        padding: '32px 28px',
        width: 360,
        cursor: 'pointer',
        transition: 'all .2s',
        display: 'flex',
        flexDirection: 'column',
        gap: 16,
      }}
      onMouseEnter={e => {
        e.currentTarget.style.borderColor = 'var(--accent)'
        e.currentTarget.style.transform = 'translateY(-2px)'
      }}
      onMouseLeave={e => {
        e.currentTarget.style.borderColor = 'var(--border)'
        e.currentTarget.style.transform = 'none'
      }}
    >
      <div style={{ color: 'var(--accent)' }}>{icon}</div>
      <h2 style={{ fontSize: 20, fontWeight: 600 }}>{title}</h2>
      <p style={{ fontSize: 13, color: 'var(--text2)', lineHeight: 1.6 }}>{description}</p>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {features.map((f, i) => (
          <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: 'var(--text2)' }}>
            <span style={{ color: 'var(--accent2)', fontSize: 10 }}>●</span>
            {f}
          </div>
        ))}
      </div>

      <button className="btn btn-primary" style={{ marginTop: 8 }}>
        进入工具
        <ArrowRight size={16} />
      </button>
    </div>
  )
}
