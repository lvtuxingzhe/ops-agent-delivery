/**
 * 证据展示的小工具：只把“机器字段”翻成短标签，不改数据。
 *
 * 目的：避免所有 SSH 证据都显示成同一个“SSH 主机 · 19:20”，
 * 让用户能一眼看出是“哪台主机的哪项检查”。
 */

const TARGET_PREFIX_RE = /^(?:host|node|instance|target|service|container)=(.+)$/i

export interface EvidenceGroup<T> {
  key: string
  label: string
  items: T[]
}

/** 按主机/来源分组，保持首次出现的顺序，避免证据页变成一条长平铺列表。 */
export function groupEvidence<T>(
  items: T[],
  keyOf: (item: T) => string,
  labelOf: (item: T) => string,
): EvidenceGroup<T>[] {
  const groups = new Map<string, EvidenceGroup<T>>()
  for (const item of items) {
    const key = keyOf(item) || 'other'
    const group = groups.get(key)
    if (group) {
      group.items.push(item)
    } else {
      groups.set(key, { key, label: labelOf(item) || '其他证据', items: [item] })
    }
  }
  return [...groups.values()]
}


export function evidenceTargetLabel(target: string): string {
  const raw = String(target || '').trim()
  const matched = raw.match(TARGET_PREFIX_RE)
  return matched ? matched[1].trim() : raw
}

export function evidenceProbeLabel(id: string, target: string): string {
  const host = evidenceTargetLabel(target)
  const raw = String(id || '').split(':').pop() || String(id || '')
  if (!host) return raw
  const escaped = host.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return raw.replace(new RegExp(`^${escaped}-?(?:\\d+-)?`, 'i'), '') || raw
}
