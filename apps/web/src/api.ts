export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    signal: AbortSignal.timeout(8000),
  });
  if (!response.ok) {
    if (response.status === 401)
      window.dispatchEvent(new Event("supportops-unauthorized"));
    const detail = await response.json().catch(() => null);
    throw new Error(
      typeof detail?.detail === "string"
        ? detail.detail
        : `请求失败（HTTP ${response.status}），请检查服务或输入。`,
    );
  }
  return response.json() as Promise<T>;
}
