import MarkdownIt from 'markdown-it'

const markdown = new MarkdownIt({
  html: false,
  linkify: false,
  breaks: true,
})

export function renderInlineMarkdown(text: string): string {
  return markdown.renderInline(text)
}

// 初步方案里的命令经常是裸行；先识别成 fenced code，再交给 Markdown 渲染。
// 这样说明文字照常换行，命令统一等宽、可选中复制，也兼容模型以后直接输出 ``` 代码块。
const COMMAND_LINE_RE = /^\s*(?:sudo\s+)?(?:systemctl|journalctl|systemd-analyze|hostnamectl|ls|lsblk|blkid|mount|umount|grep|egrep|cat|less|tail|head|sed|awk|wc|sort|uniq|cut|tr|xargs|ip|ss|netstat|lsof|ps|top|df|du|free|uptime|dmesg|find|stat|chmod|chown|cp|mv|rm|mkdir|tar|unzip|curl|wget|yum|dnf|apt|apt-get|rpm|docker|kubectl|nginx|mysql|psql|redis-cli|keepalived|vi|vim|nano|tee|echo|install|ln|kill|pkill|python|python3|bash|sh|ping|traceroute|nc|openssl|sha256sum|md5sum|\/[\w./-]+|\.\/[\w./-]+)\b/

function isCommandText(text: string): boolean {
  return COMMAND_LINE_RE.test(text.trim()) && !/[\u4e00-\u9fa5]/.test(text)
}

interface RawSolutionStep {
  index: number
  title: string
  descriptionLines: string[]
  commands: string[]
}

export interface SolutionStep {
  index: number
  title: string
  descriptionHtml: string
  commands: string[]
}

function takeInlineCommands(line: string, commands: string[]): string {
  return line.replace(/`([^`]+)`/g, (full, inner: string) => {
    if (isCommandText(inner)) {
      commands.push(inner.trim())
      return ''
    }
    return full
  })
}

export function parseSolutionSteps(source: string): SolutionStep[] {
  const steps: RawSolutionStep[] = []
  let current: RawSolutionStep | null = null
  let inFence = false

  for (const raw of source.split(/\r?\n/)) {
    const line = raw.trimEnd()
    if (/^\s*```/.test(line)) {
      inFence = !inFence
      continue
    }
    if (inFence) {
      if (current && isCommandText(line)) current.commands.push(line.trim())
      continue
    }
    const stepMatch = line.match(/^\s*(\d+)[.、]\s*(.*)$/)
    if (stepMatch) {
      const rest = stepMatch[2].trim()
      const colon = rest.search(/[：:]/)
      const title = colon > 0 ? rest.slice(0, colon).trim() : rest
      const lead = colon > 0 ? rest.slice(colon + 1).trim() : ''
      current = {
        index: Number(stepMatch[1]),
        title: title || `步骤 ${stepMatch[1]}`,
        descriptionLines: lead ? [lead] : [],
        commands: [],
      }
      steps.push(current)
      continue
    }
    if (!current) continue
    if (isCommandText(line)) {
      current.commands.push(line.trim())
      continue
    }
    if (!line.trim()) {
      current.descriptionLines.push('')
      continue
    }
    const cleaned = takeInlineCommands(line, current.commands)
    if (cleaned.trim()) current.descriptionLines.push(cleaned)
  }

  return steps.map((step) => ({
    index: step.index,
    title: step.title,
    commands: step.commands,
    descriptionHtml: markdown.render(step.descriptionLines.join('\n').trim()),
  }))
}


export function formatSolutionMarkdown(source: string): string {
  const lines = source.split(/\r?\n/)
  const output: string[] = []
  let codeLines: string[] = []
  let inFence = false

  const isCommandLine = (line: string) =>
    COMMAND_LINE_RE.test(line) && !/[\u4e00-\u9fa5]/.test(line)

  const flushCode = () => {
    if (!codeLines.length) return
    output.push('```bash', ...codeLines, '```')
    codeLines = []
  }

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index]
    if (/^\s*```/.test(line)) {
      flushCode()
      inFence = !inFence
      output.push(line)
      continue
    }
    if (inFence) {
      output.push(line)
      continue
    }
    if (!line.trim() && codeLines.length) {
      // 命令之间可能有空行；分组到同一个代码块，避免一条命令一个灰条。
      let next = index + 1
      while (next < lines.length && !lines[next].trim()) next += 1
      if (next < lines.length && isCommandLine(lines[next])) continue
    }
    if (/^\s*\d+[.、]/.test(line)) {
      flushCode()
      // 转义步骤序号，避免 Markdown 把 1～6 重新编号成列表。
      output.push(line.replace(/^(\s*)(\d+)[.、]/, '$1$2\\.'))
      continue
    }
    if (isCommandLine(line)) {
      codeLines.push(line.trimEnd())
      continue
    }
    flushCode()
    output.push(line)
  }
  flushCode()
  return output.join('\n')
}

export function formatRootCauseMarkdown(source: string): string {
  let trimmed = source.trim()
  if (!trimmed) return ''
  // 模型有时把换行写成字面量 "\n"；只有出现 "\n2." 这类编号列表时才还原，
  // 避免误伤 Windows 路径里的 "\n"（例如 C:\nginx）。
  if (/\\n\s*\d+[.、]/.test(trimmed)) {
    trimmed = trimmed.replace(/\\r\\n|\\n/g, '\n').trim()
  }
  // 已经是编号列表的报告不再重排，避免破坏模型给出的因果顺序。
  if (/^\s*\d+[.、]\s*/m.test(trimmed)) return trimmed

  const sentences = trimmed.match(/[^。；]+[。；]?/g) ?? [trimmed]
  const parts: string[] = []
  const withPeriod = (value: string) =>
    /[。；：]$/.test(value) ? value : `${value}。`

  for (const sentence of sentences) {
    const text = sentence.trim()
    if (!text) continue
    const colon = text.indexOf('：')
    if (colon > 0) {
      const lead = text.slice(0, colon).trim()
      const detail = text.slice(colon + 1).trim()
      if (lead) parts.push(withPeriod(lead.replace(/[：:]$/, '')))
      if (detail) parts.push(withPeriod(detail))
      continue
    }
    parts.push(withPeriod(text))
  }

  return parts.map((part, index) => `${index + 1}. ${part}`).join('\n')
}

export function renderMarkdown(text: string): string {
  return markdown.render(text)
}
