/**
 * 处置命令的文本 ↔ argv 转换。
 *
 * 平台执行器只收 argv 数组，不收整行 shell；这里把一线人员习惯的
 * “一行一条命令”拆成参数，同时明确拒绝管道、重定向和组合操作符。
 */

export function splitCommand(line: string): string[] {
  if (/[|&;<>`]/.test(line)) {
    throw new Error('平台不执行管道、重定向或 &&/; 组合，请一条命令一行')
  }
  const tokens: string[] = []
  let current = ''
  let quote: '"' | "'" | null = null
  for (let index = 0; index < line.length; index += 1) {
    const char = line[index]
    if (quote) {
      if (char === quote) quote = null
      else current += char
      continue
    }
    if (char === '"' || char === "'") {
      quote = char
      continue
    }
    if (char === ' ' || char === '\t') {
      if (current) {
        tokens.push(current)
        current = ''
      }
      continue
    }
    current += char
  }
  if (quote) throw new Error('引号没有闭合')
  if (current) tokens.push(current)
  if (!tokens.length) throw new Error('命令不能为空')
  return tokens
}

export function commandFromArgv(argv: string[]): string {
  return argv.map((arg) => {
    if (!/[ \t"']/.test(arg)) return arg
    if (!arg.includes('"')) return `"${arg}"`
    if (!arg.includes("'")) return `'${arg}'`
    throw new Error('参数同时包含单双引号，请留在高级模式')
  }).join(' ')
}
