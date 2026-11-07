const configured = import.meta.env.VITE_API_URL || "";
export const apiBase = configured.replace(/\/$/, "");
export const token = () => sessionStorage.getItem("sentiment-token") || "";
async function responseFor(
  path: string,
  options: RequestInit = {},
  timeoutMs = 20000,
): Promise<Response> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${apiBase}/api${path}`, {
      ...options,
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        ...(token() ? { Authorization: `Bearer ${token()}` } : {}),
        ...options.headers,
      },
    });
    if (!response.ok) {
      if (response.status === 404 && path === '/live/analyze')
        throw new Error('分析服务版本较旧，请重启本机服务后再分析（scripts/dev.ps1）。');
      let message = `请求失败 (${response.status})`;
      try {
        const data = await response.json();
        message =
          typeof data.detail === "string"
            ? data.detail
            : "请检查输入内容和长度";
      } catch {
        /* preserve status */
      }
      if (response.status === 401) {
        sessionStorage.removeItem("sentiment-token");
        sessionStorage.removeItem("sentiment-user");
        window.dispatchEvent(new Event("session-expired"));
      }
      throw new Error(message);
    }
    return response;
  } catch (error) {
    if (error instanceof TypeError)
      throw new Error("无法连接后端，请确认分析服务已启动。");
    if (error instanceof DOMException && error.name === "AbortError")
      throw new Error("请求超时，请重试。");
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}
export async function request<T>(path: string, options: RequestInit = {}, timeoutMs = 20000): Promise<T> {
  return (await responseFor(path, options, timeoutMs)).json() as Promise<T>;
}
export async function download(path: string, filename: string) {
  const response = await responseFor(path);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url; link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export const post = <T>(path: string, data: unknown, method = "POST") =>
  request<T>(path, { method, body: JSON.stringify(data) });
export function exportURL(id: string, period: number) {
  return `${apiBase}/api/export?${new URLSearchParams({ instrument_id: id, period: String(period) })}`;
}
