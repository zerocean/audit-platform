/**
 * Email Audit Worker v2
 * IMAP 轮询 → 调用 audit-platform API (8767) → SMTP 回复
 * 
 * 环境变量:
 *   EMAIL_USER / EMAIL_PASSWORD — 163 邮箱
 *   INTERNAL_API_KEY — 与 audit-platform config.py 一致
 *   LOCAL_API — audit-platform 地址 (默认 http://127.0.0.1:8767)
 *   POLL_INTERVAL_MS — 轮询间隔 (默认 60000)
 */

const Imap = require('imap');
const { simpleParser } = require('mailparser');
const nodemailer = require('nodemailer');
const axios = require('axios');
const FormData = require('form-data');
const fs = require('fs');
const path = require('path');
const os = require('os');

// Load .env (try script dir first, then /opt/.env)
const dotenvPath = path.join(__dirname, '.env');
const globalEnvPath = '/opt/.env';
const envPath = fs.existsSync(dotenvPath) ? dotenvPath : (fs.existsSync(globalEnvPath) ? globalEnvPath : null);
if (envPath) {
  require('dotenv').config({ path: envPath });
}

// ── Config ────────────────────────────────────────────
const EMAIL_USER = process.env.EMAIL_USER || 'hafoshan@163.com';
const EMAIL_PASSWORD = process.env.EMAIL_PASSWORD;
if (!EMAIL_PASSWORD) {
  console.error('[FATAL] EMAIL_PASSWORD not set');
  process.exit(1);
}
const POLL_INTERVAL_MS = parseInt(process.env.POLL_INTERVAL_MS || '60000', 10);
const LOCAL_API = process.env.LOCAL_API || 'http://127.0.0.1:8767';
const INTERNAL_KEY = process.env.INTERNAL_API_KEY || 'audit-platform-internal-key-change-me';
const PROCESSED_DB = path.join(__dirname, 'processed_emails.json');

const IMAP_CONFIG = {
  user: EMAIL_USER, password: EMAIL_PASSWORD,
  host: 'imap.163.com', port: 993, tls: true,
  tlsOptions: { rejectUnauthorized: false }, keepalive: true,
  id: { name: 'AuditEmailWorker', version: '2.0.0', vendor: 'audit-platform', 'support-email': EMAIL_USER }
};

const SMTP_CONFIG = {
  host: 'smtp.163.com', port: 465, secure: true,
  auth: { user: EMAIL_USER, pass: EMAIL_PASSWORD }
};

// ── Auth header for audit-platform ────────────────────
const AUTH_HEADERS = { 'x-internal-key': INTERNAL_KEY };

// ── Dedup ─────────────────────────────────────────────
function loadProcessed() {
  try {
    if (!fs.existsSync(PROCESSED_DB)) return new Set();
    return new Set(JSON.parse(fs.readFileSync(PROCESSED_DB, 'utf-8')));
  } catch (e) { return new Set(); }
}
function saveProcessed(set) {
  try { fs.writeFileSync(PROCESSED_DB, JSON.stringify([...set].slice(-5000), null, 2)); } catch {}
}
const processedUids = loadProcessed();
let isProcessing = false;

// ── Logger ────────────────────────────────────────────
function log(tag, msg) {
  console.log(`[${new Date().toISOString()}] [${tag}] ${msg}`);
}

// ── SMTP ──────────────────────────────────────────────
const transporter = nodemailer.createTransport(SMTP_CONFIG);

async function sendReply(to, subject, text, attachments = []) {
  await transporter.sendMail({
    from: `"Audit Review Agent" <${EMAIL_USER}>`, to,
    subject: `Re: ${subject}`, text,
    attachments: attachments.map(a => ({ filename: a.filename, path: a.path, contentType: a.contentType || 'text/markdown' }))
  });
  log('SMTP', `Reply sent to ${to}`);
}

// ── HTTP with retry ───────────────────────────────────
async function axiosWithRetry(config, maxRetries = 1) {
  let lastError;
  for (let i = 0; i <= maxRetries; i++) {
    try { return await axios(config); } catch (err) {
      lastError = err;
      if (i < maxRetries && (err.code === 'ECONNABORTED' || err.code === 'ETIMEDOUT' || err.code === 'ECONNRESET' || err.message?.includes('timeout'))) {
        log('RETRY', `Request failed, retrying ${i + 1}/${maxRetries}...`);
        await new Promise(r => setTimeout(r, 5000));
        continue;
      }
      throw err;
    }
  }
  throw lastError;
}

// ── Audit API calls ───────────────────────────────────
async function callAuditAPI(filePath, originalFilename) {
  const results = { taskId: null, parserOk: false, json: null, numericReport: '', inspectorReport: '', inspectorData: null, error: null };

  // Step 1: Vision Parser
  log('AUDIT', `Step 1/3: Parsing ${originalFilename}...`);
  const parseForm = new FormData();
  parseForm.append('pdf', fs.createReadStream(filePath), originalFilename);
  parseForm.append('source', 'email');

  try {
    const parseRes = await axiosWithRetry({
      method: 'post', url: `${LOCAL_API}/api/audit/pdf`,
      data: parseForm, headers: { ...parseForm.getHeaders(), ...AUTH_HEADERS },
      maxBodyLength: Infinity, timeout: 30 * 60 * 1000
    });
    const { task_id, parsed_json, parser_success, error: parserError } = parseRes.data;
    if (!parser_success) throw new Error(`Parser failed: ${parserError || 'unknown'}`);
    results.taskId = task_id;
    results.parserOk = true;
    results.json = parsed_json;
    log('AUDIT', `Parser done. taskId=${task_id}`);
  } catch (err) {
    results.error = `文档解析失败: ${err.message}`;
    return results;
  }

  // Step 2 & 3: Numeric audit + Inspector parallel
  log('AUDIT', `Step 2/3 & 3/3: Numeric + Inspector (parallel)...`);

  const numericPromise = (async () => {
    try {
      const auditRes = await axiosWithRetry({
        method: 'post', url: `${LOCAL_API}/api/audit/json/sync`,
        data: { data: results.json, filename: originalFilename, task_id: results.taskId },
        headers: { ...AUTH_HEADERS, 'Content-Type': 'application/json' },
        timeout: 30 * 60 * 1000
      });
      results.numericReport = auditRes.data.report || '';
    } catch (err) {
      results.numericReport = `【数值复核失败】\n${err.message}`;
    }
  })();

  const inspectorPromise = (async () => {
    const inspectForm = new FormData();
    inspectForm.append('pdf', fs.createReadStream(filePath), originalFilename);
    inspectForm.append('task_id', String(results.taskId));
    try {
      const inspectRes = await axiosWithRetry({
        method: 'post', url: `${LOCAL_API}/api/audit/inspector`,
        data: inspectForm, headers: { ...inspectForm.getHeaders(), ...AUTH_HEADERS },
        maxBodyLength: Infinity, timeout: 30 * 60 * 1000
      });
      results.inspectorData = inspectRes.data;
      results.inspectorReport = formatInspectorReport(inspectRes.data);
    } catch (err) {
      results.inspectorReport = `【视觉检查失败】\n${err.message}`;
    }
  })();

  await Promise.all([numericPromise, inspectorPromise]);
  return results;
}

function formatInspectorReport(data) {
  if (!data.issues?.length) return `未发现可视/语义级问题。总页数: ${data.total_pages}`;
  let lines = [`总页数: ${data.total_pages} | 问题数: ${data.total_issues}`, ''];
  for (const issue of data.issues) {
    const p = (typeof issue === 'string' ? issue.split('|').map(s => s.trim()) : []);
    lines.push(`- [${p[1] || ''}] ${p[3] || issue}  (页码:${p[0] || '-'}, 位置:${p[2] || '-'})`);
  }
  return lines.join('\n');
}

// ── Word Report (same as audit-platform export) ───────
function escHtml(s) { return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

function parseAuditTable(text) {
  if (!text) return null;
  const m = text.match(/<table>([\s\S]*?)<\/table>/i);
  const content = m ? m[1].trim() : text;
  const lines = content.split('\n').filter(l => l.trim().startsWith('|'));
  if (lines.length < 3) return { headers:[], rows:[], noErrors: content.toLowerCase().includes('no errors found') };
  const headers = lines[0].split('|').map(s => s.trim()).filter(Boolean);
  const rows = lines.slice(2).map(l => l.split('|').map(s => s.trim()).filter(Boolean)).filter(r => r.length);
  return { headers, rows, noErrors: false };
}

function buildWordReport(filename, results) {
  const now = new Date().toLocaleDateString('zh-CN');
  let h = `<!DOCTYPE html><html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:w="urn:schemas-microsoft-com:office:word" xmlns="http://www.w3.org/TR/REC-html40"><head><meta charset="utf-8"><title>审计复核报告</title><style>body{font-family:"微软雅黑",SimSun;font-size:11pt;line-height:1.6}h1{font-size:18pt;text-align:center}h2{font-size:14pt;border-bottom:1pt solid #ccc}table{border-collapse:collapse;width:100%;margin:8pt 0}th,td{border:1pt solid #999;padding:4pt 6pt;font-size:10pt}th{background:#f2f2f2}pre{background:#f8f8f8;padding:8pt;font-size:9pt;white-space:pre-wrap}</style></head><body><h1>财务审计复核分析报告</h1><p style="color:#666">生成日期：${now}</p><h2>一、语法/结构性分析</h2>`;

  const insp = results.inspectorData;
  if (insp?.issues?.length) {
    h += '<table><tr><th>页码</th><th>类别</th><th>位置</th><th>问题描述</th></tr>';
    for (const issue of insp.issues) {
      const p = (typeof issue === 'string' ? issue.split('|').map(s => s.trim()) : []);
      h += `<tr><td>${escHtml(p[0])}</td><td>${escHtml(p[1])}</td><td>${escHtml(p[2])}</td><td>${escHtml(p[3]||issue)}</td></tr>`;
    }
    h += '</table>';
  } else { h += '<p>✅ 未发现问题</p>'; }

  h += '<h2>二、数值复核</h2>';
  const tbl = parseAuditTable(results.numericReport);
  if (tbl && !tbl.noErrors && tbl.rows.length) {
    h += '<table><tr>' + tbl.headers.map(x => `<th>${escHtml(x)}</th>`).join('') + '</tr>';
    tbl.rows.forEach(r => { h += '<tr>' + r.map(c => `<td>${escHtml(c)}</td>`).join('') + '</tr>'; });
    h += '</table>';
  } else if (tbl?.noErrors) { h += '<p>✅ 未发现数值错误</p>'; }
  else { h += '<p>' + escHtml(results.numericReport?.slice(0,5000) || '暂无') + '</p>'; }

  h += '<h2>三、数值复核完整输出</h2><pre>' + escHtml(results.numericReport || '暂无') + '</pre>';
  h += '<h2>四、源报告解析结果</h2>';
  h += results.json ? '<pre>' + escHtml(JSON.stringify(results.json, null, 2).slice(0, 50000)) + '</pre>' : '<p>暂无</p>';
  h += '</body></html>';
  return h;
}

// ── TaxFill via API ───────────────────────────────────
async function processTaxReturnMail(uid, parsed, pdfAttachments) {
  const from = parsed.from?.text || parsed.from?.value?.[0]?.address || 'unknown';
  const subject = parsed.subject || '(no subject)';
  log('TAXFILL', `UID=${uid}: from ${from}`);

  const tmpDir = path.join(os.tmpdir(), `taxfill_${Date.now()}`);
  fs.mkdirSync(tmpDir, { recursive: true });

  try {
    const fsPath = path.join(tmpDir, 'fs_report.pdf');
    const taxcompPath = path.join(tmpDir, 'tax_computation.pdf');
    fs.writeFileSync(fsPath, pdfAttachments[0].content);
    fs.writeFileSync(taxcompPath, pdfAttachments[1].content);

    // Send ack
    await transporter.sendMail({
      from: `"Tax Return Agent" <${EMAIL_USER}>`, to: from, subject: `Re: ${subject}`,
      text: `您好，\n\n已收到税务申报文件：\n- ${pdfAttachments[0].filename}\n- ${pdfAttachments[1].filename}\n\n处理中，完成后将发回填表参考。\n\n---\nTax Return Filling Agent`
    });

    // Call audit-platform taxfill API
    const form = new FormData();
    form.append('fs_pdf', fs.readFileSync(fsPath), pdfAttachments[0].filename);
    form.append('taxcomp_pdf', fs.readFileSync(taxcompPath), pdfAttachments[1].filename);

    const res = await axiosWithRetry({
      method: 'post', url: `${LOCAL_API}/api/taxfill/pipeline`,
      data: form, headers: { ...form.getHeaders(), ...AUTH_HEADERS },
      maxBodyLength: Infinity, timeout: 60 * 60 * 1000
    });

    if (!res.data.success) throw new Error(res.data.error || 'pipeline failed');

    // Download Excel output
    const { run_id } = res.data;
    const dlRes = await axiosWithRetry({
      method: 'get', url: `${LOCAL_API}/api/taxfill/download/${run_id}/filling_reference.xlsx`,
      headers: AUTH_HEADERS, responseType: 'arraybuffer', timeout: 30000
    });

    const xlsxPath = path.join(tmpDir, 'filling_reference.xlsx');
    fs.writeFileSync(xlsxPath, dlRes.data);

    await transporter.sendMail({
      from: `"Tax Return Agent" <${EMAIL_USER}>`, to: from, subject: `Re: ${subject}`,
      text: `您好，\n\n税务申报填表参考已完成，详见附件。\n\n---\nTax Return Filling Agent`,
      attachments: [{ filename: 'filling_reference.xlsx', path: xlsxPath, contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }]
    });
    log('TAXFILL', `Excel sent to ${from}`);

  } catch (err) {
    log('TAXFILL', `Error: ${err.message}`);
    await transporter.sendMail({
      from: `"Tax Return Agent" <${EMAIL_USER}>`, to: from, subject: `Re: ${subject}`,
      text: `您好，\n\n税务申报处理遇到错误：\n\n${err.message}\n\n请检查文件后重试。\n\n---\nTax Return Filling Agent`
    });
  } finally {
    try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch {}
  }
}

// ── Process single mail ───────────────────────────────
async function processMail(uid, parsed) {
  const from = parsed.from?.text || parsed.from?.value?.[0]?.address || 'unknown';
  const subject = parsed.subject || '(no subject)';
  log('MAIL', `UID=${uid} From=${from} Subject="${subject}"`);

  const mailText = (parsed.text || parsed.html || '').toLowerCase();
  const attachments = parsed.attachments || [];
  const pdfAttachments = attachments.filter(a => (a.filename||'').toLowerCase().endsWith('.pdf'));

  // TaxFill trigger
  if (mailText.includes('tax return') && pdfAttachments.length === 2) {
    await processTaxReturnMail(uid, parsed, pdfAttachments);
    return;
  }

  // Audit trigger: keyword + PDF
  const hasKeyword = mailText.includes('复核') || mailText.includes('review');
  if (!hasKeyword) return;

  if (pdfAttachments.length === 0) {
    const docAtts = attachments.filter(a => /\.(doc|docx)$/i.test(a.filename||''));
    if (docAtts.length) {
      await sendReply(from, subject, `您好，\n\n收到文档：${docAtts.map(a=>a.filename).join(', ')}\n\n系统仅支持 PDF，请转换后重发。`);
    }
    return;
  }

  for (const att of pdfAttachments) {
    const filename = att.filename;
    const tmpDir = path.join(os.tmpdir(), `audit_email_${Date.now()}`);
    fs.mkdirSync(tmpDir, { recursive: true });
    const filePath = path.join(tmpDir, filename);
    fs.writeFileSync(filePath, att.content);
    const displaySubject = subject === '(no subject)' || !subject.trim() ? filename : subject;

    // Send ack
    await transporter.sendMail({
      from: `"Audit Review Agent" <${EMAIL_USER}>`, to: from, subject: `Re: ${displaySubject}`,
      text: `您好，\n\n已收到审计报告 "${filename}"，处理中，完成后自动回复。\n\n---\nAudit Report Review Agent`
    });

    try {
      const results = await callAuditAPI(filePath, filename);

      if (results.error && !results.parserOk) {
        await sendReply(from, displaySubject, `您好，\n\n文档解析失败：\n${results.error}\n\n请检查 PDF 后重试。`);
        continue;
      }

      // Build and send Word report
      const reportHtml = buildWordReport(filename, results);
      const base = path.basename(filename, path.extname(filename));
      const reportPath = path.join(tmpDir, `${base}_review.doc`);
      fs.writeFileSync(reportPath, '\ufeff' + reportHtml, 'utf-8');

      let statusLines = [`- 解析: ${results.parserOk?'成功':'失败'}`, '- 数值复核: 已执行', '- 视觉检查: 已执行'];
      if (results.numericReport?.includes('失败')) statusLines[1] = '- 数值复核: ⚠️ 异常';
      if (results.inspectorReport?.includes('失败')) statusLines[2] = '- 视觉检查: ⚠️ 异常';

      await sendReply(from, displaySubject,
        `您好，\n\n"${filename}" 复核完成，详见附件。\n\n${statusLines.join('\n')}\n\n---\nAudit Report Review Agent`,
        [{ filename: path.basename(reportPath), path: reportPath, contentType: 'application/msword' }]
      );
      log('DONE', `Report sent for ${filename}`);
    } catch (err) {
      await sendReply(from, displaySubject, `您好，\n\n复核过程出错：\n${err.message}\n\n请重试或联系管理员。`);
    } finally {
      try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch {}
    }
  }
}

// ── IMAP Polling ──────────────────────────────────────
const mailQueue = [];
async function processQueue() {
  while (mailQueue.length > 0) {
    const item = mailQueue.shift();
    if (processedUids.has(String(item.uid))) continue;
    (async () => {
      try {
        await processMail(item.uid, item.parsed);
        processedUids.add(String(item.uid));
        saveProcessed(processedUids);
        item.imap.addFlags(item.seqno, '\\Seen', () => {});
      } catch (e) { log('ERROR', `Process UID=${item.uid}: ${e.message}`); }
    })();
  }
}

function fetchAndProcess() {
  if (isProcessing) return;
  isProcessing = true;
  const imap = new Imap(IMAP_CONFIG);

  imap.once('ready', () => {
    // 163 requires IMAP ID command before openBox
    imap.id({ name: 'AuditEmailWorker', version: '2.0.0', vendor: 'audit-platform', 'support-email': EMAIL_USER }, (idErr) => {
      if (idErr) log('IMAP', `ID command: ${idErr.message}`);
      imap.openBox('INBOX', false, (err) => {
      if (err) { imap.end(); return; }
      imap.search(['UNSEEN'], (err2, results) => {
        if (err2 || !results?.length) { log('IMAP', err2 ? `Error: ${err2.message}` : 'No new mail'); imap.end(); return; }
        log('IMAP', `Found ${results.length} unread`);

        let idx = 0;
        function processNext() {
          if (idx >= results.length) { imap.end(); return; }
          const seqno = results[idx++];
          const fetcher = imap.fetch(seqno, { bodies: '', struct: true });
          let uid = null, bodyStream = null;

          fetcher.on('message', msg => {
            msg.on('attributes', attrs => { uid = attrs.uid; });
            msg.on('body', stream => { bodyStream = stream; });
            msg.once('end', async () => {
              if (!uid || processedUids.has(String(uid))) { processNext(); return; }
              imap.addFlags(seqno, '\\Seen', () => {});
              try {
                const parsed = await simpleParser(bodyStream);
                if (!mailQueue.some(i => String(i.uid) === String(uid))) {
                  mailQueue.push({ uid, parsed, seqno, imap });
                  processQueue();
                }
              } catch (e) { log('ERROR', `Parse UID=${uid}: ${e.message}`); }
              processNext();
            });
          });
          fetcher.once('error', err => { log('IMAP', `Fetch error: ${err.message}`); processNext(); });
        }
        processNext();
      });
    });
  });
  });  // close ready callback

  imap.once('error', err => log('IMAP', `Connection: ${err.message}`));
  imap.once('end', () => { log('IMAP', 'Disconnected'); isProcessing = false; });
  imap.connect();
}

// ── Main ──────────────────────────────────────────────
log('WORKER', `Started. API=${LOCAL_API} Poll=${POLL_INTERVAL_MS}ms`);
fetchAndProcess();
setInterval(fetchAndProcess, POLL_INTERVAL_MS);
process.on('SIGTERM', () => process.exit(0));
process.on('SIGINT', () => process.exit(0));
