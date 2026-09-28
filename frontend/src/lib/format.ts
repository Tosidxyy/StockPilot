export function number(value: number | null | undefined, digits = 2): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return value.toLocaleString("zh-CN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function signed(value: number | null | undefined, suffix = "%"): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${value > 0 ? "+" : ""}${number(value)}${suffix}`;
}

export function amount(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  if (value >= 1e12) return `${number(value / 1e12)} 万亿`;
  if (value >= 1e8) return `${number(value / 1e8)} 亿`;
  if (value >= 1e4) return `${number(value / 1e4)} 万`;
  return number(value, 0);
}

export function moveClass(value: number | null | undefined): string {
  return value == null ? "" : value > 0 ? "up" : value < 0 ? "down" : "";
}

export function cachedTime(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "";
  return ` · 缓存保存于 ${date.toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false })}`;
}
