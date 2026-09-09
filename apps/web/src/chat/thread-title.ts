const MAX_THREAD_TITLE_CHARS = 72;

export function threadTitle(firstMessage: string): string {
  const normalized = firstMessage.trim().replaceAll(/\s+/g, " ");
  if (normalized.length <= MAX_THREAD_TITLE_CHARS) return normalized;
  return `${normalized.slice(0, MAX_THREAD_TITLE_CHARS - 3).trimEnd()}...`;
}
